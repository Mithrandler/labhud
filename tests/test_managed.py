"""managed.py: the config and keys labhud keeps in LABHUD_DATA (⚙ > Advanced, ⚙ > Setup)."""

import http.client
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

import config
import envfiles
import managed
from tests import signin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = '''title = "T"
include = ["pages/*.toml"]

[[page]]
id = "main"
title = "MAIN"

  [[page.group]]
  id = "g"
  title = "G"

    [[page.group.card]]
    id = "c"
    name = "C"
    ping = "127.0.0.1"
'''
PART = '''[[page]]
id = "more"
title = "MORE"

  [[page.group]]
  id = "h"
  title = "H"

    [[page.group.card]]
    id = "d"
    name = "D"
    ping = "127.0.0.2"
'''


def given(folder):
    """A mounted config with one included file, in `folder`."""
    os.makedirs(os.path.join(folder, "pages"))
    with open(os.path.join(folder, "config.toml"), "w") as f:
        f.write(CFG)
    with open(os.path.join(folder, "pages", "more.toml"), "w") as f:
        f.write(PART)
    return os.path.join(folder, "config.toml")


class Managed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = given(os.path.join(self.tmp, "conf"))
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data, LABHUD_CONFIG=self.cfg)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)

    def test_before_a_take_over_the_given_config_is_read(self):
        self.assertFalse(managed.active())
        self.assertEqual(config.config_path(), self.cfg)
        st = managed.status()
        self.assertTrue(st["can_take_over"])
        self.assertEqual(st["includes"], [os.path.join("pages", "more.toml")])

    def test_take_over_merges_the_includes_and_switches_the_path(self):
        path = managed.take_over()
        self.assertEqual(path, os.path.join(self.data, "config.toml"))
        self.assertEqual(config.config_path(), path)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        topo = config.load(path)
        self.assertEqual([p["id"] for p in topo["pages"]], ["main", "more"])
        with open(path, "rb") as f:
            self.assertNotIn("include", tomllib.load(f))
        with self.assertRaises(ValueError):
            managed.take_over()   # once only

    def test_a_config_that_would_not_load_is_not_taken_over(self):
        with open(os.path.join(os.path.dirname(self.cfg), "pages", "more.toml"), "a") as f:
            f.write('title = "not here"\n')
        with self.assertRaises(ValueError):
            managed.take_over()
        self.assertFalse(managed.active())

    def test_save_checks_first(self):
        managed.take_over()
        raw = managed.read_config()
        raw["page"][0]["title"] = "HOME"
        managed.save_config(raw)
        self.assertEqual(config.load(config.config_path())["pages"][0]["title"], "HOME")
        raw["page"][0]["group"][0]["card"][0]["ping"] = 5
        with self.assertRaises(ValueError):
            managed.save_config(raw)
        self.assertEqual(config.load(config.config_path())["pages"][0]["title"], "HOME")
        with open(config.config_path()) as f:
            self.assertTrue(f.read().startswith("# Kept by labhud"))   # the header stays

    def test_save_text_keeps_what_was_typed(self):
        managed.take_over()
        managed.save_text(CFG.replace('include = ["pages/*.toml"]\n', "# mine\n"))
        with open(config.config_path()) as f:
            self.assertIn("# mine", f.read())
        with self.assertRaises(ValueError):
            managed.save_text("title = 1\n")

    def test_secrets_are_kept_600_and_names_are_checked(self):
        self.assertEqual(managed.set_secrets({"LABHUD_SONARR_KEY": "abc"}), ["LABHUD_SONARR_KEY"])
        self.assertEqual(managed.set_secrets({"LABHUD_SONARR_KEY": "abc"}), [])
        self.assertEqual(managed.read_secrets(), {"LABHUD_SONARR_KEY": "abc"})
        self.assertEqual(stat.S_IMODE(os.stat(managed.secrets_file()).st_mode), 0o600)
        for bad in ({"LABHUD_DATA": "/tmp"}, {"LABHUD_ACTION_URL": "http://x"}, {"PATH": "/x"},
                    {"LABHUD_X_FILE": "/etc/shadow"}, {"LABHUD_X": "a\nLABHUD_DATA=/"}):
            with self.assertRaises(ValueError, msg=bad):
                managed.set_secrets(bad)
        managed.set_secrets({"LABHUD_SONARR_KEY": ""})
        self.assertEqual(managed.read_secrets(), {})

    def test_secrets_win_over_the_environment_but_not_for_how_labhud_starts(self):
        path = os.path.join(self.data, "secrets.env")
        with open(path, "w") as f:
            f.write("LABHUD_SONARR_KEY=new\nLABHUD_DATA=/elsewhere\nLABHUD_HOSTS=evil\n")
        env = {"LABHUD_SONARR_KEY": "old", "LABHUD_DATA": self.data, "LABHUD_HOSTS": "a"}
        self.assertEqual(envfiles.load_dotenv(path, env, override=True), ["LABHUD_SONARR_KEY"])
        self.assertEqual(env, {"LABHUD_SONARR_KEY": "new", "LABHUD_DATA": self.data, "LABHUD_HOSTS": "a"})

    def test_new_keys_go_to_secrets_once_managed(self):
        self.assertEqual(envfiles.dotenv_path(), os.path.join(os.path.dirname(self.cfg), ".env"))
        managed.take_over()
        self.assertEqual(envfiles.dotenv_path(), os.path.join(self.data, "secrets.env"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Routes(unittest.TestCase):
    """/api/setup/*: only for the admin, and a take over restarts the same process on the new file."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = given(os.path.join(self.tmp, "conf"))
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_CONFIG=self.cfg, LABHUD_DATA=self.data, LABHUD_HTTP="on", LABHUD_PORT=str(self.port),
                   LABHUD_HOSTS=self.host, PYTHONDONTWRITEBYTECODE="1", **signin.ENV)
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (self.proc.kill(), self.proc.wait()))
        self.wait_up()
        self.ticket = signin.sign_in(self.port, self.host)

    def wait_up(self, after=0):
        time.sleep(after)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                if self.post("status", ticket=None)[0] in (200, 403):
                    return
            except OSError:
                pass
            time.sleep(0.2)
        self.fail("server did not start")

    def post(self, op, body=None, ticket=""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        if ticket is not None:
            h["X-Labhud-Admin"] = ticket or self.ticket
        conn.request("POST", "/api/setup/" + op, json.dumps(body or {}), h)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_refused_without_the_admin(self):
        for op in ("status", "text", "save-text", "take-over", "secrets", "catalog"):
            self.assertEqual(self.post(op, ticket=None)[0], 403, op)
            self.assertEqual(self.post(op, ticket="nope")[0], 403, op)

    def test_edit_take_over_then_edit(self):
        code, st = self.post("status")
        self.assertEqual(code, 200)
        self.assertFalse(st["managed"])
        code, t = self.post("text")
        self.assertIn('include = ["pages/*.toml"]', t["text"])
        # a read-only mount: the editor says to take over (here the file is writable, so it saves)
        self.assertEqual(self.post("save-text", {"text": "title = 1\n", "check": True})[1]["ok"], False)
        code, r = self.post("take-over")
        self.assertTrue(r["ok"], r)
        self.wait_up(after=2.5)
        code, st = self.post("status")
        self.assertTrue(st["managed"], st)
        self.assertEqual(st["path"], os.path.join(self.data, "config.toml"))
        code, t = self.post("text")
        self.assertIn('"MORE"', t["text"])
        code, r = self.post("save-text", {"text": t["text"].replace('"MORE"', '"EXTRA"')})
        self.assertTrue(r["ok"], r)
        with open(os.path.join(self.data, "config.toml")) as f:
            self.assertIn('"EXTRA"', f.read())
        with open(self.cfg) as f:
            self.assertNotIn("EXTRA", f.read())   # the mounted file is left alone
        # ⚙ > Pages: the layout, a move, organize
        code, v = self.post("layout")
        self.assertEqual([p["id"] for p in v["pages"]], ["main", "more"])
        pages = [{"id": p["id"], "title": p["title"], "groups": [{"id": g["id"], "title": g["title"],
                  "cards": [c["id"] for c in g["cards"]]} for g in p["groups"]]} for p in v["pages"]]
        pages[0]["groups"][0]["cards"].append(pages[1]["groups"][0]["cards"].pop())
        code, r = self.post("layout-save", {"pages": pages})
        self.assertTrue(r["ok"], r)
        code, v = self.post("layout")
        self.assertEqual([p["id"] for p in v["pages"]], ["main"])   # MORE emptied, so left out
        self.assertEqual([c["id"] for c in v["pages"][0]["groups"][0]["cards"]], ["c", "d"])
        pages[0]["groups"][0]["cards"].pop()
        code, r = self.post("layout-save", {"pages": pages[:1]})
        self.assertEqual((code, r["ok"]), (409, False))                # d would be lost
        code, o = self.post("organize", {"page": "main"})
        self.assertEqual([g["title"] for g in o["pages"][0]["groups"]], ["PCs & other"])


if __name__ == "__main__":
    unittest.main()
