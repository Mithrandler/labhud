"""The config editor: a ticket from init.py edit, then check and save through the real server."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import editmode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Ticket(unittest.TestCase):
    def test_expiry_and_code(self):
        d = tempfile.mkdtemp()
        cfg = os.path.join(d, "config.toml")
        code = editmode.open_ticket(cfg, minutes=1)
        self.assertEqual(os.stat(editmode.ticket_path(cfg)).st_mode & 0o777, 0o600)
        self.assertTrue(editmode.check(cfg, code.lower()))
        self.assertFalse(editmode.check(cfg, code, now=time.time() + 120))
        self.assertFalse(editmode.check(cfg, "AAAA-BBBB"))


class Editor(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.toml")
        shutil.copy(os.path.join(ROOT, "config.example.toml"), self.cfg)
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_CONFIG=self.cfg, LABHUD_PORT=str(self.port), LABHUD_HOSTS=self.host, PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (self.proc.kill(), self.proc.wait()))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        self.code = editmode.open_ticket(self.cfg)

    def call(self, step, body=None, code=None, origin=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", f"/edit/api/{step}", json.dumps(body or {}),
                     {"Host": self.host, "Origin": origin or f"http://{self.host}", "Content-Type": "application/json",
                      "X-Labhud-Edit": self.code if code is None else code})
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_load_check_save(self):
        self.assertEqual(self.call("load", code="AAAA-BBBB")[0], 403)
        self.assertEqual(self.call("load", origin="http://evil.example")[0], 403)
        status, d = self.call("load")
        self.assertEqual(status, 200)
        text = d["text"]
        status, d = self.call("check", {"text": text + "\nnonsense = 1\n"})
        self.assertFalse(d["ok"])
        status, d = self.call("save", {"text": text + "\nnonsense = 1\n"})
        self.assertFalse(d["ok"])
        with open(self.cfg) as f:
            self.assertEqual(f.read(), text)  # a broken file is never written
        new = text.replace('title = "LABHUD"', 'title = "EDITED"')
        inode = os.stat(self.cfg).st_ino
        status, d = self.call("save", {"text": new})
        self.assertTrue(d["ok"])
        self.assertEqual(os.stat(self.cfg).st_ino, inode)
        with open(self.cfg + ".bak") as f:
            self.assertEqual(f.read(), text)
        deadline = time.monotonic() + 10
        title = None
        while time.monotonic() < deadline and title != "EDITED":
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", "/api/config", headers={"Host": self.host})
            title = json.loads(conn.getresponse().read())["title"]
            conn.close()
            time.sleep(0.5)
        self.assertEqual(title, "EDITED")


if __name__ == "__main__":
    unittest.main()
