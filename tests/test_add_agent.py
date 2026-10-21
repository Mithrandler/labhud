"""init.py agent: a pushing machine added while labhud runs, with no restart."""

import hashlib
import hmac
import http.client
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import onboard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AddAgent(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.cfg = os.path.join(self.dir, "config.toml")
        self.env = os.path.join(self.dir, ".env")
        with open(self.cfg, "w") as f:
            f.write(onboard.build_config(None, "", title="T") + '\n  [[page.group]]\n  id = "g"\n  title = "G"\n\n'
                    '    [[page.group.card]]\n    id = "c"\n    name = "C"\n    ping = "127.0.0.1"\n')
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_CONFIG=self.cfg, LABHUD_PORT=str(self.port), LABHUD_HOSTS=self.host,
                   PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (self.proc.kill(), self.proc.wait()))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        self.fail("server did not start")

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body, dict({"Host": host or self.host}, **(headers or {})))
        r = conn.getresponse()
        out = r.status, r.read()
        conn.close()
        return out

    def push(self, key, data):
        body = json.dumps(data).encode()
        ts = str(int(time.time()))
        sig = hmac.new(key.encode(), f"{ts}.nas.".encode() + body, hashlib.sha256).hexdigest()
        return self.request("POST", "/api/push/nas", body, {"Content-Type": "application/json",
                                                            "X-Labhud-Time": ts, "X-Labhud-Signature": sig})[0]

    def test_added_while_running(self):
        self.assertEqual(self.push("0" * 64, {"cpu": 1}), 403)  # no such agent yet
        key = onboard.add_agent("nas", self.cfg, self.env)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.push(key, {"cpu": 12.5}) != 200:
            time.sleep(0.5)
        self.assertEqual(self.push(key, {"cpu": 12.5, "mem_used_of": [1, 4]}), 200)
        self.assertEqual(self.push("f" * 64, {"cpu": 99}), 403)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            snap = json.loads(self.request("GET", "/api/snapshot")[1])
            if (snap.get("nas") or {}).get("cpu") == 12.5:
                break
            time.sleep(0.5)
        self.assertEqual(snap["nas"]["cpu"], 12.5)

    def test_installer_is_served_with_the_agents_checksum(self):
        status, script = self.request("GET", "/agent/install.sh", host="any-name:1")
        self.assertEqual(status, 200)
        with open(os.path.join(ROOT, "agents", "labhud-agent.py"), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest().encode()
        self.assertIn(b'SHA256="' + digest + b'"', script)
        status, agent = self.request("GET", "/agent/labhud-agent.py", host="any-name:1")
        self.assertEqual((status, hashlib.sha256(agent).hexdigest().encode()), (200, digest))
        self.assertEqual(self.request("GET", "/agent/../server.py")[0], 404)

    def test_names_and_clashes(self):
        for bad in ("", "NAS", "1nas", "a-b", "x" * 33):
            with self.assertRaises(ValueError):
                onboard.add_agent(bad, self.cfg, self.env)
        onboard.add_agent("nas", self.cfg, self.env)
        with self.assertRaises(ValueError):
            onboard.add_agent("nas", self.cfg, self.env)
        self.assertEqual(os.stat(self.env).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
