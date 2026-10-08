"""Smoke test: the real server in demo mode, over HTTP.

Starts `server.py` with LABHUD_DEMO=1 on a free port, then checks the page, the config, the first
SSE event, the host allowlist, and that every data path named in demo/config.toml has a value in
the snapshot. pbs.* is skipped: the demo keeps that host off on purpose.
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import tomllib
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = ("pbs.",)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def data_paths(cfg):
    """Every dotted path into the snapshot that the config names."""
    paths = set()
    for page in cfg.get("page", []):
        if "games" in page:
            paths.add(page["games"])
        for group in page.get("group", []):
            for card in group.get("card", []):
                paths.update(m["key"] for m in card.get("metrics", []))
                paths.update(card[k] for k in ("list", "torrents", "sensors") if k in card)
    if "alerts" in cfg:
        paths.add(cfg["alerts"]["path"])
    return sorted(p for p in paths if not p.startswith(SKIP))


def lookup(data, path):
    """Same walk as get() in static/app.js."""
    for part in path.split("."):
        if not isinstance(data, dict) or part not in data:
            return None
        data = data[part]
    return data


class DemoServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.host = f"127.0.0.1:{cls.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DEMO="1", LABHUD_PORT=str(cls.port), PYTHONDONTWRITEBYTECODE="1")
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError("server exited:\n" + cls.proc.stdout.read())
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        cls.proc.kill()
        raise RuntimeError("server did not start in 15 s")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        cls.proc.stdout.close()

    def get(self, path, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", path, headers={"Host": host or self.host})
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, r.getheader("Content-Type"), body

    def test_page(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"app.js", body)
        for asset in ("/app.js", "/app.css"):
            self.assertEqual(self.get(asset)[0], 200, asset)

    def test_config(self):
        status, ctype, body = self.get("/api/config")
        self.assertEqual(status, 200)
        cfg = json.loads(body)
        self.assertEqual(cfg["action_url"], "")  # actions are always off in the demo
        self.assertTrue(cfg["pages"])
        self.assertNotIn("weather", cfg)

    def test_unknown_host_and_path(self):
        self.assertEqual(self.get("/", host="evil.example.test")[0], 421)
        self.assertEqual(self.get("/../server.py")[0], 404)

    def test_stream(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            sock.sendall(f"GET /api/stream HTTP/1.1\r\nHost: {self.host}\r\n\r\n".encode())
            buf = b""
            # the response headers, then the first event (it ends with a blank line)
            while b"\n\n" not in buf.partition(b"\r\n\r\n")[2]:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
        finally:
            sock.close()
        head, _, body = buf.partition(b"\r\n\r\n")
        self.assertIn(b"200", head.split(b"\r\n")[0])
        self.assertIn(b"text/event-stream", head)
        event = body.split(b"\n\n")[0].decode()
        self.assertTrue(event.startswith("event: full\ndata: "), event[:80])
        self.assertIsInstance(json.loads(event.split("data: ", 1)[1]), dict)

    def test_every_config_path_has_data(self):
        with open(os.path.join(ROOT, "demo", "config.toml"), "rb") as f:
            paths = data_paths(tomllib.load(f))
        self.assertGreater(len(paths), 20)
        missing = paths
        deadline = time.monotonic() + 20
        while missing and time.monotonic() < deadline:
            snap = json.loads(self.get("/api/snapshot")[2])
            missing = [p for p in paths if lookup(snap, p) is None]
            if missing:
                time.sleep(0.5)
        self.assertEqual(missing, [], "paths without data in the demo snapshot")


if __name__ == "__main__":
    unittest.main()
