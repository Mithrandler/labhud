"""Setup mode: the code, the checks on every call, and a first start that ends as the display."""

import http.client
import json
import os
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import config
import envfiles
import onboard
import setupmode
from sources._common import REGISTRY, env, source

from tests.test_init import fake_get

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Code(unittest.TestCase):
    def test_shape(self):
        code = setupmode.new_code()
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertFalse(set(code) & set("01IOLU"))

    def test_lock_after_too_many(self):
        st = setupmode.State("/nonexistent/config.toml", code="ABCD-EFGH")
        self.assertTrue(st.check_code("abcd-efgh"))  # typed in lowercase: still the code
        for _ in range(setupmode.MAX_FAILS):
            self.assertFalse(st.check_code("WRONG"))
        self.assertFalse(st.check_code("ABCD-EFGH"))  # locked, even with the right one


class Build(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.st = setupmode.State(os.path.join(self.dir, "config.toml"), code="ABCD-EFGH")
        onboard.catalog()

        @source("fakesvc", every=60, env=("FAKESVC_URL", "FAKESVC_KEY"), title="Fake")
        def fakesvc():
            if env("FAKESVC_KEY") != "good-key-123":
                raise RuntimeError("401")
            return {"up": 1}
        self.addCleanup(REGISTRY.pop, "fakesvc", None)

    def test_proxmox_and_selection(self):
        with mock.patch.object(onboard, "get", fake_get), mock.patch.object(onboard, "fingerprint", lambda u: ("192.0.2.10:8006", "ab" * 32)):
            r = self.st.set_proxmox({"url": "192.0.2.10", "token_id": "reader@pve!labhud", "secret": "pve-secret-1"})
        self.assertTrue(r["ok"])
        self.assertEqual(r["url"], "https://192.0.2.10:8006")
        self.assertNotIn("pve-secret-1", json.dumps(r))
        self.assertNotIn("pve-secret-1", json.dumps(self.st.hello("x:1")))
        text, envtext, problems = self.st.build({"hosts": ["192.0.2.50:8095"], "include": [["alpha", 101]], "pin": True})
        self.assertEqual(problems, [])
        self.assertIn('proxmox = "alpha/101"', text)
        self.assertNotIn('proxmox = "alpha/100"', text)
        self.assertIn("LABHUD_PROXMOX_ALPHA_TOKEN_SECRET=pve-secret-1", envtext)
        self.assertIn("LABHUD_PINS=192.0.2.10:8006=" + "ab" * 32, envtext)
        preview = self.st.preview({"hosts": ["192.0.2.50:8095"]})
        self.assertNotIn("pve-secret-1", json.dumps(preview))

    def test_a_source_is_kept_only_when_it_works(self):
        bad = self.st.try_source({"name": "fakesvc", "values": {"LABHUD_FAKESVC_URL": "http://192.0.2.7", "LABHUD_FAKESVC_KEY": "nope-nope-1"}})
        self.assertFalse(bad["ok"])
        self.assertNotIn("fakesvc", self.st.sources)
        good = self.st.try_source({"name": "fakesvc", "values": {"LABHUD_FAKESVC_URL": "http://192.0.2.7", "LABHUD_FAKESVC_KEY": "good-key-123"}})
        self.assertTrue(good["ok"])
        # a second try with the key left empty keeps the one that worked
        again = self.st.try_source({"name": "fakesvc", "values": {"LABHUD_FAKESVC_URL": "http://192.0.2.7", "LABHUD_FAKESVC_KEY": ""}})
        self.assertTrue(again["ok"])
        _, envtext, problems = self.st.build({"hosts": ["192.0.2.50:8095"], "services": ["fakesvc"]})
        self.assertEqual(problems, [])
        self.assertIn("LABHUD_FAKESVC_KEY=good-key-123", envtext)

    def test_bad_hosts(self):
        _, _, problems = self.st.build({"hosts": ["evil host"]})
        self.assertTrue(problems)
        self.assertFalse(self.st.finish({"hosts": []})["ok"])
        self.assertFalse(os.path.exists(self.st.path))

    def test_finish_writes_both_files(self):
        with open(os.path.join(self.dir, ".env"), "w") as f:
            f.write("# mine\nLABHUD_HOSTS=old:1\nLABHUD_OTHER=keep\n")
        self.assertTrue(self.st.finish({"hosts": ["192.0.2.50:8095"], "title": "HOME"})["ok"])
        config.load(self.st.path)
        mode = stat.S_IMODE(os.stat(os.path.join(self.dir, ".env")).st_mode)
        self.assertEqual(mode, 0o600)
        with open(os.path.join(self.dir, ".env")) as f:
            text = f.read()
        self.assertIn("# mine", text)
        self.assertIn("LABHUD_OTHER=keep", text)
        self.assertIn("LABHUD_HOSTS=192.0.2.50:8095", text)
        self.assertNotIn("old:1", text)
        self.assertFalse(self.st.finish({"hosts": ["192.0.2.50:8095"]})["ok"])  # never twice


class Dotenv(unittest.TestCase):
    def test_fills_only_what_is_unset(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
            f.write("# c\nexport LABHUD_A=1\nLABHUD_B='two words'\nLABHUD_C=file\nPATH=/evil\nLABHUD_bad name=x\n")
        self.addCleanup(os.unlink, f.name)
        environ = {"LABHUD_C": "env"}
        self.assertEqual(envfiles.load_dotenv(f.name, environ), ["LABHUD_A", "LABHUD_B"])
        self.assertEqual(environ, {"LABHUD_A": "1", "LABHUD_B": "two words", "LABHUD_C": "env"})

    def test_missing(self):
        self.assertEqual(envfiles.load_dotenv("/nonexistent/.env", {}), [])
        with self.assertRaises(SystemExit):
            envfiles.load_dotenv("/nonexistent/.env", {"LABHUD_ENV_FILE": "/nonexistent/.env"})


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FirstStart(unittest.TestCase):
    """The real server, started on an empty folder."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_CONFIG=os.path.join(self.dir, "config.toml"), LABHUD_PORT=str(self.port),
                   PYTHONDONTWRITEBYTECODE="1", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(self._stop)
        self.code = None
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not self.code:
            line = self.proc.stdout.readline()
            if not line:
                break
            m = re.search(r"setup code:\s+([A-Z2-9]{4}-[A-Z2-9]{4})", line)
            self.code = m and m.group(1)
        self.assertTrue(self.code, "no setup code in the log")
        self._wait_port()

    def _wait_port(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        self.fail("server did not listen")

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()

    def call(self, step, body=None, code=None, origin=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        data = json.dumps(body or {}).encode()
        conn.request("POST", f"/setup/api/{step}", data, {
            "Host": self.host, "Origin": origin or f"http://{self.host}", "Content-Type": ctype,
            "X-Labhud-Setup": self.code if code is None else code})
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={"Host": self.host})
        r = conn.getresponse()
        out = r.status, r.getheader("Content-Security-Policy"), r.read()
        conn.close()
        return out

    def test_setup_then_display(self):
        status, csp, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"setup.js", body)
        self.assertIn("default-src 'self'", csp)
        self.assertEqual(self.get("/setup.js")[0], 200)
        self.assertEqual(self.get("/../server.py")[0], 404)
        self.assertEqual(self.call("hello", code="WRNG-CODE")[0], 403)
        self.assertEqual(self.call("hello", origin="http://evil.example")[0], 403)
        self.assertEqual(self.call("hello", ctype="text/plain")[0], 403)
        status, hello = self.call("hello")
        self.assertEqual(status, 200)
        self.assertTrue(hello["writable"])
        self.assertTrue(any(e["name"] == "healthchecks" for e in hello["catalog"]))
        status, done = self.call("finish", {"hosts": [self.host], "title": "FIRST"})
        self.assertEqual((status, done), (200, {"ok": True}))
        # the same process, started again as the display
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
                conn.request("GET", "/api/config", headers={"Host": self.host})
                r = conn.getresponse()
                cfg = json.loads(r.read())
                conn.close()
                if r.status == 200:
                    self.assertEqual(cfg["title"], "FIRST")
                    self.assertIsNone(self.proc.poll())
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        self.fail("the display did not come up after setup")


if __name__ == "__main__":
    unittest.main()
