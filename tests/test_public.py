"""The public status page: its own port, names and states only."""

import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Public(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "demo", "config.toml")) as f:
            text = f.read()
        group = re.search(r'\[\[page\.group\]\]\s*\n\s*id = "([^"]+)"', text).group(1)
        cls.cfg = tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False)
        cls.cfg.write(text + f'\n[public]\ntitle = "Lab status"\ngroups = ["{group}"]\n')
        cls.cfg.close()
        cls.port, cls.public = free_port(), free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DEMO="1", LABHUD_CONFIG=cls.cfg.name, LABHUD_PORT=str(cls.port),
                   LABHUD_PUBLIC_PORT=str(cls.public), PYTHONDONTWRITEBYTECODE="1")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", cls.public), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        time.sleep(3)  # the first status poll

    @classmethod
    def tearDownClass(cls):
        cls.proc.kill()
        cls.proc.wait()
        os.unlink(cls.cfg.name)

    def get(self, path, host="status.example.org"):
        conn = http.client.HTTPConnection("127.0.0.1", self.public, timeout=10)
        conn.request("GET", path, headers={"Host": host})
        r = conn.getresponse()
        out = r.status, r.read()
        conn.close()
        return out

    def test_only_names_and_states(self):
        status, body = self.get("/api/public")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["title"], "Lab status")
        self.assertTrue(d["groups"] and d["groups"][0]["items"])
        for item in d["groups"][0]["items"]:
            self.assertEqual(set(item), {"name", "state"})
        self.assertNotRegex(body.decode(), r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")

    def test_nothing_else(self):
        self.assertEqual(self.get("/")[0], 200)
        self.assertEqual(self.get("/public.js")[0], 200)
        for path in ("/api/snapshot", "/api/config", "/api/status", "/status", "/app.js", "/agent/install.sh", "/../server.py"):
            self.assertEqual(self.get(path)[0], 404, path)


if __name__ == "__main__":
    unittest.main()
