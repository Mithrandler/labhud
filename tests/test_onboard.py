"""onboard.py: the source catalog, trying values without touching the environment, .env merging,
and a config limited to the chosen guests."""

import os
import unittest
from unittest import mock

import config
import onboard
from sources import _common
from sources._common import REGISTRY, env, source

from tests.test_init import fake_get


class Catalog(unittest.TestCase):
    def test_every_form_names_its_variables(self):
        entries = {e["name"]: e for e in onboard.catalog()}
        for skipped in onboard.OWN_STEP:
            self.assertNotIn(skipped, entries)
        hc = entries["healthchecks"]
        self.assertEqual(hc["title"], "Healthchecks")
        self.assertEqual([f["name"] for f in hc["fields"]], ["LABHUD_HEALTHCHECKS_URL", "LABHUD_HEALTHCHECKS_KEY"])
        self.assertEqual([f["secret"] for f in hc["fields"]], [False, True])
        self.assertTrue(all(f["hint"] for f in hc["fields"]))
        # derived from Sonarr and Radarr: no form of its own
        self.assertEqual(entries["recent"]["fields"], [])
        self.assertEqual(sorted(entries["recent"]["with"]), ["radarr", "sonarr"])

    def test_no_values_in_the_catalog(self):
        with mock.patch.dict(os.environ, {"LABHUD_JELLYFIN_URL": "http://192.0.2.5:8096",
                                          "LABHUD_JELLYFIN_KEY": "very-secret-key"}):
            entries = {e["name"]: e for e in onboard.catalog()}
            self.assertTrue(entries["jellyfin"]["configured"])
            self.assertNotIn("very-secret-key", repr(entries))

    def test_labels(self):
        self.assertEqual(_common.field_label("PBS_TOKEN_ID"), "Token ID")
        self.assertEqual(_common.field_label("NAVIDROME_PASS"), "Password")
        self.assertEqual(_common.field_label("JELLYFIN_KEY"), "API key")


class Try(unittest.TestCase):
    def setUp(self):
        onboard.catalog()  # loads every module first, so they cannot overwrite the fake below
        self.seen = {}

        @source("fake", every=60, env=("FAKE_URL", "FAKE_KEY"), title="Fake")
        def fake():
            self.seen["url"], self.seen["key"] = env("FAKE_URL"), env("FAKE_KEY")
            if env("FAKE_KEY") == "wrong-key-12345":
                raise RuntimeError("401 for key=wrong-key-12345")
            return {"total": 3, "up": 3}
        self.addCleanup(REGISTRY.pop, "fake", None)

    def test_values_are_used_then_forgotten(self):
        got = onboard.try_source("fake", {"LABHUD_FAKE_URL": "http://192.0.2.9", "LABHUD_FAKE_KEY": "k",
                                          "LABHUD_PINS": "x"})
        self.assertEqual(got, {"ok": True, "keys": ["total", "up"]})
        self.assertEqual(self.seen, {"url": "http://192.0.2.9", "key": "k"})
        self.assertEqual(env("FAKE_URL"), "")
        self.assertNotIn("LABHUD_FAKE_URL", os.environ)

    def test_the_error_hides_the_key(self):
        got = onboard.try_source("fake", {"LABHUD_FAKE_URL": "http://192.0.2.9", "LABHUD_FAKE_KEY": "wrong-key-12345"})
        self.assertFalse(got["ok"])
        self.assertIn("401", got["error"])
        self.assertNotIn("wrong-key-12345", got["error"])

    def test_missing_and_unknown(self):
        self.assertEqual(onboard.try_source("fake", {"LABHUD_FAKE_URL": "http://192.0.2.9"}),
                         {"ok": False, "error": "missing: LABHUD_FAKE_KEY"})
        self.assertFalse(onboard.try_source("proxmox", {})["ok"])
        self.assertFalse(onboard.try_source("nope", {})["ok"])


class MergeEnv(unittest.TestCase):
    def test_replace_add_remove(self):
        old = "# mine\nLABHUD_HOSTS=a:8095\nLABHUD_JELLYFIN_KEY=old\nOTHER=1\nLABHUD_SEERR_URL=http://x\n"
        new = onboard.merge_env(old, {"LABHUD_JELLYFIN_KEY": "new", "LABHUD_SEERR_URL": "",
                                      "LABHUD_PBS_URL": "https://192.0.2.3:8007", "PATH": "/evil",
                                      "LABHUD_BAD": "two\nlines"})
        self.assertEqual(new, "# mine\nLABHUD_HOSTS=a:8095\nLABHUD_JELLYFIN_KEY=new\nOTHER=1\n\n"
                              "LABHUD_PBS_URL=https://192.0.2.3:8007\n")

    def test_from_nothing(self):
        self.assertEqual(onboard.merge_env("", {"LABHUD_HOSTS": "a:8095"}), "LABHUD_HOSTS=a:8095\n")


class Selection(unittest.TestCase):
    def test_only_the_chosen_guests(self):
        with mock.patch.object(onboard, "get", fake_get):
            found = onboard.discover("https://192.0.2.10:8006", "reader@pve!labhud", "not-a-secret")
        text = onboard.build_config(found, "192.0.2.10", include={("alpha", 101)})
        self.assertIn('proxmox = "alpha/101"', text)
        self.assertNotIn('proxmox = "alpha/100"', text)
        self.assertNotIn('proxmox = "beta/200"', text)
        self.assertIn('id = "host-beta"', text)  # nodes always stay
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        self.addCleanup(os.unlink, f.name)
        config.load(f.name)


if __name__ == "__main__":
    unittest.main()


class Suggest(unittest.TestCase):
    def test_found_by_its_answer_not_its_port(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                b = b'{"ProductName":"Jellyfin Server"}' if self.path.startswith("/System") else b"other"
                self.send_response(200)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        known = [("jellyfin", port, "http", "/System/Info/Public", "jellyfin"),
                 ("sonarr", port, "http", "/", "sonarr")]  # same port, wrong answer: not found
        with mock.patch.object(onboard, "KNOWN", known):
            self.assertEqual(onboard.suggest(["127.0.0.1", "127.0.0.1"]),
                             [{"source": "jellyfin", "url": f"http://127.0.0.1:{port}"}])

    def test_only_hosts(self):
        for bad in (["192.0.2.0/24"], ["a b"], ["h"] * 33):
            with self.assertRaises(ValueError):
                onboard.suggest(bad)


class NameClash(unittest.TestCase):
    def test_a_configured_source_is_not_replaced_by_an_unconfigured_one(self):
        with mock.patch.dict(os.environ, {"LABHUD_MINE_URL": "http://192.0.2.1"}):
            @source("clash", every=10, env=("MINE_URL",))
            def mine():
                return {}

            @source("clash", every=10, env=("BUILTIN_URL",))
            def builtin():
                return {}
            self.assertIs(REGISTRY["clash"].fetch, mine)
        REGISTRY.pop("clash", None)
