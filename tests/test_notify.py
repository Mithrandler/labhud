"""notify.py: the message shapes, what is sent, and the delay that drops a host back up."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import notify


class Receiver(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Receiver.got.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class Notify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = {k: getattr(notify, k) for k in ("URL", "FORMAT", "AUTH", "ALL", "DELAY")}
        notify.URL = f"http://127.0.0.1:{cls.server.server_port}/hook"
        notify.AUTH = "Bearer test-token"
        notify.DELAY = 1
        threading.Thread(target=notify.worker, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for k, v in cls.saved.items():
            setattr(notify, k, v)

    def setUp(self):
        Receiver.got = []
        notify.FORMAT, notify.ALL = "json", False
        while not notify._queue.empty():   # what other tests' servers logged is not this test's
            notify._queue.get_nowait()

    def wait(self, count, seconds=8):
        deadline = time.monotonic() + seconds
        while len(Receiver.got) < count and time.monotonic() < deadline:
            time.sleep(0.1)
        return Receiver.got

    def test_shapes(self):
        notify.FORMAT = "ntfy"
        data, headers = notify.body("LABHUD", "NAS down", True)
        self.assertEqual(data, b"NAS down")
        self.assertEqual((headers["Title"], headers["Priority"]), ("LABHUD", "high"))
        self.assertNotIn("Click", headers)
        notify.CLICK = "app"
        try:
            self.assertEqual(notify.body("L", "NAS down", True, card="nas")[1]["Click"], "labhud://card/nas")
            self.assertEqual(notify.body("L", "LabHUD started", False)[1]["Click"], "labhud://open")
        finally:
            notify.CLICK = ""
        notify.FORMAT = "gotify"
        self.assertEqual(json.loads(notify.body("T", "x", False)[0]), {"title": "T", "message": "x", "priority": 4})
        notify.FORMAT = "discord"
        self.assertEqual(json.loads(notify.body("T", "x", True)[0]), {"content": "**T**: x"})
        notify.FORMAT = "json"
        self.assertEqual(json.loads(notify.body("T", "x", True, now=5)[0]), {"title": "T", "text": "x", "bad": True, "time": 5})

    def test_bad_news_only_unless_all(self):
        notify.submit("T", "NAS up", False)
        notify.submit("T", "NAS down", True)
        got = self.wait(1)
        time.sleep(0.5)
        # (a demo server of another test may send its own alerts meanwhile: only these two count)
        mine = [g for g in got if json.loads(g[1])["text"].startswith("NAS ")]
        self.assertEqual([json.loads(g[1])["text"] for g in mine], ["NAS down"])
        self.assertEqual(mine[0][0]["Authorization"], "Bearer test-token")

    def test_delay_drops_a_host_back_up(self):
        notify.submit("T", "NAS down", True, still_true=lambda: False)
        notify.submit("T", "PVE down", True, still_true=lambda: True)
        got = self.wait(1)
        time.sleep(1.5)
        self.assertEqual([json.loads(b)["text"] for _, b in got], ["PVE down"])

    def test_bad_format(self):
        notify.FORMAT = "telegram"
        self.assertIn("LABHUD_NOTIFY_FORMAT", notify.problem())



class Wake(unittest.TestCase):
    """The app's own notifications: a content-free POST to each phone's UnifiedPush endpoint, only
    when `via` names the app; an endpoint its server calls gone is forgotten."""

    def test_wake(self):
        import urllib.error
        from unittest import mock
        sent, dropped = [], []

        def urlopen(req, timeout=0):
            sent.append((req.full_url, req.data))
            if "gone" in req.full_url:
                raise urllib.error.HTTPError(req.full_url, 410, "Gone", {}, None)
            return mock.MagicMock()

        endpoints = {"https://up.example/a": "d1", "https://up.example/gone": "d2"}
        with mock.patch("devices.push_endpoints", return_value=endpoints), \
                mock.patch("devices.drop_push", side_effect=dropped.append), \
                mock.patch("urllib.request.urlopen", urlopen):
            notify.wake_phones()
        self.assertEqual(sorted(u for u, _ in sent), sorted(endpoints))
        self.assertTrue(all(d == notify.WAKE for _, d in sent))
        self.assertNotIn(b"title", notify.WAKE)
        self.assertEqual(dropped, ["https://up.example/gone"])

    def test_only_when_via_names_the_app(self):
        from unittest import mock
        saved = notify.VIA, notify.URL
        try:
            notify.URL = ""
            for via, woken in (("ntfy", 0), ("app", 1), ("both", 1)):
                notify.VIA = via
                with mock.patch("notify.wake_phones") as wake:
                    notify.deliver("t", "x", True)
                    time.sleep(0.05)
                self.assertEqual(wake.call_count, woken, via)
        finally:
            notify.VIA, notify.URL = saved


if __name__ == "__main__":
    unittest.main()
