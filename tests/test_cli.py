"""The `labhud` command (cli.py) on a real labhud: the key in LABHUD_DATA/cli.json, refused
without it, and what ⚙ does, done from the terminal through the same routes."""

import contextlib
import http.client
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import cli
from tests.test_managed import ROOT, free_port, given


class Command(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = given(os.path.join(self.tmp, "conf"))
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_CONFIG=self.cfg, LABHUD_DATA=self.data, LABHUD_HTTP="on", LABHUD_PORT=str(self.port),
                   LABHUD_HOSTS=f"{self.host},labhud.example:8095", PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (self.proc.kill(), self.proc.wait()))
        p = mock.patch.dict(os.environ, {"LABHUD_DATA": self.data})
        p.start()
        self.addCleanup(p.stop)
        self.wait_up()

    def wait_up(self, old_key=None):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                key = self.key()
                if key != old_key and self.labhud("status")[0] == 0:
                    return
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.2)
        self.fail("server did not start")

    def key(self):
        with open(os.path.join(self.data, "cli.json")) as f:
            return json.load(f)["key"]

    def labhud(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch("sys.stdin", io.StringIO(stdin)):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def post(self, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, "{}", dict({"Content-Type": "application/json"}, **headers))
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status

    def test_the_key(self):
        path = os.path.join(self.data, "cli.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as f:
            c = json.load(f)
        self.assertEqual(c["url"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(c["host"], "labhud.example:8095")   # the first name that is not loopback
        self.assertEqual(len(c["key"]), 64)
        self.assertEqual(self.post("/api/setup/status", {"Host": self.host}), 403)
        self.assertEqual(self.post("/api/setup/status", {"Host": self.host, "X-Labhud-Cli": "0" * 64}), 403)
        self.assertEqual(self.post("/api/setup/status", {"Host": "evil.example", "X-Labhud-Cli": c["key"]}), 200)
        self.assertEqual(self.post("/api/agents/list", {"Host": self.host, "X-Labhud-Cli": c["key"]}), 200)

    def test_no_server(self):
        with mock.patch.dict(os.environ, {"LABHUD_DATA": os.path.join(self.tmp, "nowhere")}):
            code, out = self.labhud("agent", "list")
        self.assertEqual(code, 1)
        self.assertIn("is labhud running", out)

    def test_usage(self):
        code, out = self.labhud()
        self.assertEqual(code, 0)
        self.assertIn("labhud agent allow", out)
        self.assertEqual(self.labhud("frobnicate")[0], 2)
        code, out = self.labhud("agent", "frobnicate")
        self.assertEqual(code, 1)
        self.assertIn("usage: labhud agent", out)

    def test_pages_alerts_config(self):
        code, out = self.labhud("status")
        self.assertIn("take over: possible", out)
        with mock.patch.dict(os.environ, {"VISUAL": "true"}):   # an editor that changes nothing
            self.assertEqual(self.labhud("config", "edit"), (0, "no change\n"))
        old = self.key()
        code, out = self.labhud("config", "take-over")
        self.assertEqual(code, 0, out)
        self.wait_up(old_key=old)   # restarted: a new key, and the command reads it again
        code, out = self.labhud("page", "list")
        self.assertEqual(code, 0, out)
        self.assertIn("main/g", out)
        self.assertIn("more/h", out)
        code, out = self.labhud("page", "move", "c", "more/h")
        self.assertEqual(code, 0, out)
        code, out = self.labhud("page", "list")
        self.assertLess(out.index("more/h"), out.index("      c"))
        code, out = self.labhud("page", "move", "nope", "more")
        self.assertEqual(code, 1)
        self.assertIn("no card 'nope'", out)

        code, out = self.labhud("alert", "list")
        self.assertEqual(code, 0, out)
        before = out.count("min")
        code, out = self.labhud("alert", "add", "*", "disk", "80")
        self.assertEqual(code, 0, out)
        code, out = self.labhud("alert", "list")
        self.assertIn("> 80%", out)
        self.assertEqual(self.labhud("alert", "add", "*", "noise", "3")[0], 1)
        n = out.count("min")
        self.assertEqual(n, before + 1)
        code, out = self.labhud("alert", "remove", str(n))
        self.assertEqual(code, 0, out)
        self.assertNotIn("> 80%", self.labhud("alert", "list")[1])

        code, out = self.labhud("config", "show")
        self.assertIn('[[page]]', out)
        self.assertIn('id = "more"', out)

        self.assertEqual(self.labhud("notify", "set", "ntfy.example/x")[0], 1)   # no scheme
        self.assertEqual(self.labhud("notify", "frobnicate")[0], 1)
        old = self.key()
        code, out = self.labhud("notify", "set", "https://ntfy.example/labhud", "--token", "tk_x")
        self.assertEqual(code, 0, out)
        self.assertIn("restarts", out)
        self.wait_up(old_key=old)
        self.assertIn("LABHUD_NOTIFY_URL", self.labhud("status")[1])
        self.assertIn("ntfy ntfy.example", self.labhud("alert", "list")[1])
        old = self.key()
        self.assertEqual(self.labhud("notify", "forget")[0], 0)
        self.wait_up(old_key=old)
        self.assertIn("ntfy not set", self.labhud("alert", "list")[1])

    def test_agents_and_phones(self):
        code, out = self.labhud("agent", "list")
        self.assertEqual(code, 0, out)
        self.assertIn("NAME", out)
        code, out = self.labhud("agent", "code")
        self.assertEqual(code, 0, out)
        self.assertIn("Pairing code:", out)
        self.assertIn("--url http://labhud.example:8095 ", out)   # a name in LABHUD_HOSTS, not loopback
        code, out = self.labhud("agent", "allow", "nobody", "docker")
        self.assertEqual(code, 1)
        self.assertIn("no agent 'nobody'", out)
        code, out = self.labhud("phone", "pair", "test phone")
        self.assertEqual(code, 0, out)
        self.assertIn("labhud", out.lower())
        self.assertIn("within", out)
        code, out = self.labhud("phone", "list")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.labhud("phone", "remove", "nobody")[0], 1)
        code, out = self.labhud("source", "list")
        self.assertEqual(code, 0, out)
        self.assertIn("jellyfin", out)


if __name__ == "__main__":
    unittest.main()
