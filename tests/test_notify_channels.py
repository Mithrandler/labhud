"""notify.py's other channels (webhook, Discord, Gotify) against a fake server: the shapes, the secrets in headers and never in an error, the matrix of events."""

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import notify


class Receiver(BaseHTTPRequestHandler):
    got = []
    status = 200

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Receiver.got.append((self.path, dict(self.headers), json.loads(body)))
        self.send_response(Receiver.status)
        self.end_headers()

    def log_message(self, *args):
        pass


class Channels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        Receiver.got = []
        Receiver.status = 200
        p = mock.patch.dict(os.environ, {
            "LABHUD_NOTIFY_WEBHOOK_URL": self.base + "/hook/secret-path-token",
            "LABHUD_NOTIFY_WEBHOOK_AUTH": "Bearer hook-token",
            "LABHUD_NOTIFY_DISCORD_URL": self.base + "/api/webhooks/1/discord-token",
            "LABHUD_NOTIFY_GOTIFY_TOKEN": "gotify-token",
        }, clear=False)
        p.start()
        self.addCleanup(p.stop)
        saved = dict(notify.CFG)
        self.addCleanup(lambda: (notify.CFG.clear(), notify.CFG.update(saved)))
        notify.CFG["gotify"] = {"url": self.base + "/gotify"}
        notify.CFG["email"] = None
        notify.CFG["events"] = None
        saved_via, saved_url = notify.VIA, notify.URL
        self.addCleanup(lambda: setattr(notify, "VIA", saved_via) or setattr(notify, "URL", saved_url))
        saved_muted = notify.MUTED
        self.addCleanup(lambda: setattr(notify, "MUTED", saved_muted))
        notify.URL = ""
        notify.VIA = "app"
        notify.MUTED = False   # the demo mutes its made-up news; these tests want the channels

    def test_webhook_shape(self):
        notify.send_to("webhook", "Lab", "NAS down", True, card="nas", event="host_down", now=1000)
        self.assertEqual(len(Receiver.got), 1)
        path, headers, body = Receiver.got[0]
        self.assertEqual(path, "/hook/secret-path-token")
        self.assertEqual(headers["Authorization"], "Bearer hook-token")
        self.assertEqual(body, {"title": "Lab", "text": "NAS down", "bad": True, "time": 1000, "event": "host_down", "card": "nas"})

    def test_discord_shape(self):
        notify.send_to("discord", "Lab", "@everyone NAS down", True)
        body = Receiver.got[-1][2]
        self.assertEqual(body["username"], "LabHUD")
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertIn("NAS down", body["content"])
        self.assertIn("**Lab**", body["content"])
        notify.send_to("discord", "Lab", "x" * 5000, False)
        self.assertLessEqual(len(Receiver.got[-1][2]["content"]), 2000)

    def test_gotify_shape(self):
        notify.send_to("gotify", "Lab", "NAS down", True)
        path, headers, body = Receiver.got[-1]
        self.assertEqual(path, "/gotify/message")
        self.assertEqual(headers["X-Gotify-Key"], "gotify-token")
        self.assertNotIn("gotify-token", path)
        self.assertEqual(body, {"title": "Lab", "message": "NAS down", "priority": 8})

    def test_an_error_never_shows_the_secret(self):
        Receiver.status = 500
        err = notify.test_channel("webhook")
        self.assertIsNotNone(err)
        self.assertNotIn("secret-path-token", err)
        self.assertNotIn("hook-token", err)
        with mock.patch.dict(os.environ, {"LABHUD_NOTIFY_DISCORD_URL": "http://127.0.0.1:9/api/webhooks/1/discord-token"}):
            err = notify.test_channel("discord")
        self.assertIsNotNone(err)
        self.assertNotIn("discord-token", err)

    def test_test_uses_what_is_typed(self):
        err = notify.test_channel("webhook", env={"url": self.base + "/typed"})
        self.assertIsNone(err)
        self.assertEqual(Receiver.got[-1][0], "/typed")

    def test_not_set(self):
        with mock.patch.dict(os.environ, {"LABHUD_NOTIFY_WEBHOOK_URL": ""}):
            self.assertEqual(notify.channel_problem("webhook"), "not set")
            self.assertTrue(notify.test_channel("webhook").startswith("nothing to test"))
        self.assertEqual(notify.channel_problem("email"), "not set")
        self.assertIsNotNone(notify.channel_problem("email", cfg={"host": "smtp.example.com", "to": ["a@example.com"], "security": "none"}, env={"password": "pw"}))

    def test_matrix_default_and_explicit(self):
        notify.CFG["events"] = None
        notify.VIA = "app"
        m = notify.matrix()
        self.assertEqual(m["host_down"], ["app", "webhook", "discord", "gotify"])
        self.assertEqual(m["admin_login"], [])
        self.assertEqual(m["update_available"], [])
        notify.CFG["events"] = {"host_down": ["discord"], "admin_login": ["webhook"]}
        self.assertEqual(notify.route("host_down"), ["discord"])
        self.assertEqual(notify.route("admin_login"), ["webhook"])
        self.assertEqual(notify.route("alert"), [])
        self.assertEqual(notify.effective_via(), "ntfy")

    def test_deliver_goes_to_the_routed_channels_and_counts(self):
        notify.CFG["events"] = {"alert": ["webhook", "gotify"]}
        notify.deliver("Lab", "disk full", True, event="alert", now=2000)
        self.assertEqual(sorted(p for p, _, _ in Receiver.got), ["/gotify/message", "/hook/secret-path-token"])
        self.assertGreaterEqual(notify.stats["webhook"]["sent"], 1)
        Receiver.status = 500
        Receiver.got = []
        notify.deliver("Lab", "again", True, event="alert")
        self.assertIsNotNone(notify.stats["gotify"]["last_error"])
        self.assertNotIn("gotify-token", notify.stats["gotify"]["last_error"])

    def test_apply_via(self):
        events = {"alert": ["app", "email"], "admin_login": ["webhook"], "host_down": ["ntfy"]}
        out = notify.apply_via(events, "both")
        self.assertEqual(out, {"alert": ["app", "ntfy", "email"], "admin_login": ["webhook"], "host_down": ["app", "ntfy"]})
        self.assertEqual(notify.apply_via(events, "ntfy")["alert"], ["ntfy", "email"])


class Login(unittest.TestCase):
    def setUp(self):
        saved = dict(notify.CFG)
        self.addCleanup(lambda: (notify.CFG.clear(), notify.CFG.update(saved)))
        notify.CFG["events"] = {"admin_login": ["app"]}
        notify._failed.update(at=0.0, skipped=0)

    def test_signed_in_and_failed_once_a_minute(self):
        with mock.patch("notify.submit") as submit:
            notify.on_login(user="boss", how="password", ok=True, where="10.0.0.5")
            for _ in range(3):
                notify.on_login(user="x", how="password", ok=False, where="10.0.0.6")
            self.assertEqual(submit.call_count, 2)
            first, second = submit.call_args_list
            self.assertIn("boss", first[0][1])
            self.assertIn("10.0.0.5", first[0][1])
            self.assertIs(first[0][2], False)
            self.assertEqual(first[1]["event"], "admin_login")
            self.assertIs(second[0][2], True)
            self.assertEqual(second[1]["event"], "admin_login")
            notify._failed["at"] -= notify.FAILED_EVERY   # a minute later
            notify.on_login(user="x", how="password", ok=False, where="10.0.0.6")
            self.assertEqual(submit.call_count, 3)
            self.assertIn("2 more", submit.call_args[0][1])


class Updates(unittest.TestCase):
    def setUp(self):
        notify._updates["seen"] = None
        self.addCleanup(notify._updates.__setitem__, "seen", None)
        p1 = mock.patch("store.load", return_value=None)
        p2 = mock.patch("store.save")
        p1.start(); self.addCleanup(p1.stop)
        p2.start(); self.addCleanup(p2.stop)

    def test_first_time_is_taken_as_known_then_new_ones_count(self):
        rows = [{"host": "big", "name": "jellyfin", "image": "j:1"}]
        self.assertEqual(notify.new_updates(rows, now=1000), [])
        rows2 = rows + [{"host": "big", "name": "sonarr", "image": "s:1"}]
        fresh = notify.new_updates(rows2, now=1100)
        self.assertEqual([r["name"] for r in fresh], ["sonarr"])
        self.assertEqual(notify.new_updates(rows2, now=1200), [])
        rows3 = rows2 + [{"host": "big", "name": "radarr", "image": "r:1", "updating": True}]
        self.assertEqual(notify.new_updates(rows3, now=1300), [])

    def test_text(self):
        self.assertEqual(notify.updates_text([{"host": "big", "name": "jellyfin"}]), "Update available: jellyfin on big")
        self.assertTrue(notify.updates_text([{"host": "a", "name": "x"}, {"host": "b", "name": "y"}]).startswith("2 updates available:"))


if __name__ == "__main__":
    unittest.main()
