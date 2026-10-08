"""Pushed sources, end to end: the real server, and the real agent pushing its sensors to it.
Also the signature rules of sources/push.py on their own."""

import hashlib
import hmac
import http.client
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from sources import push
from tests.test_smoke import ROOT, free_port

KEY = "test-push-key-0123456789"


def signed(name, body, key=KEY, ts=None):
    ts = str(int(ts or time.time()))
    return {"X-Labhud-Time": ts, "Content-Type": "application/json",
            "X-Labhud-Signature": hmac.new(key.encode(), f"{ts}.{name}.".encode() + body, hashlib.sha256).hexdigest()}


class Verify(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(push.KEYS, {"box": KEY.encode()})
        patch.start()
        self.addCleanup(patch.stop)

    def test_genuine(self):
        self.assertIsNone(push.verify("box", signed("box", b"{}"), b"{}"))

    def test_refused(self):
        body = b'{"a":1}'
        cases = {
            "other body": (signed("box", b"{}"), "box"),
            "other key": (signed("box", body, key="nope"), "box"),
            "old": (signed("box", body, ts=time.time() - 60), "box"),
            "unknown source": (signed("other", body), "other"),
            "no headers": ({}, "box"),
            "non-ascii signature": (dict(signed("box", body), **{"X-Labhud-Signature": "é" * 64}), "box"),
        }
        for why, (headers, name) in cases.items():
            with self.subTest(why):
                self.assertIsNotNone(push.verify(name, headers, body))


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        base = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        base["PYTHONDONTWRITEBYTECODE"] = "1"
        cls.server = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server.py")], cwd=ROOT,
            env=dict(base, LABHUD_PORT=str(cls.port), LABHUD_CONFIG=os.path.join(ROOT, "demo", "config.toml"),
                     LABHUD_PUSH_SENSORS_KEY=KEY, LABHUD_PUSH_MANUAL_KEY=KEY),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        cls.agent = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "agents", "labhud-agent.py")], cwd=ROOT,
            env=dict(base, LABHUD_AGENT_PORT="0", LABHUD_AGENT_PUSH_EVERY="2", LABHUD_AGENT_PUSH_KEY=KEY,
                     LABHUD_AGENT_PUSH_URL=f"http://127.0.0.1:{cls.port}/api/push/sensors"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.agent, cls.server):
            p.terminate()
            p.wait(5)
            p.stdout.close()

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=body, headers=dict({"Host": f"127.0.0.1:{self.port}"}, **(headers or {})))
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out

    def wait_snapshot(self, check, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                snap = json.loads(self.request("GET", "/api/snapshot")[1])
                if check(snap):
                    return snap
            except OSError:
                pass
            time.sleep(0.5)
        self.fail("timed out; server log:\n" + "".join(self.server.stdout.readline() for _ in range(5)))

    def test_agent_push_arrives(self):
        snap = self.wait_snapshot(lambda s: "gpu_count" in s.get("sensors", {}))
        self.assertIn("cpu_temp", snap["sensors"])

    def test_forged_push_refused(self):
        body = b'{"cpu_temp": 99}'
        for headers in (signed("sensors", body, key="wrong"), {}, signed("other", body)):
            path = "/api/push/" + ("other" if "other" in str(headers) else "sensors")
            self.assertEqual(self.request("POST", path, body, headers)[0], 403)

    def test_own_push_with_any_host_name(self):
        body = b'{"gpu_count": 7, "cpu_temp": 1}'
        status, _ = self.request("POST", "/api/push/manual", body, dict(signed("manual", body), Host="elsewhere:1"))
        self.assertEqual(status, 200)
        self.wait_snapshot(lambda s: s.get("manual", {}).get("gpu_count") == 7)


if __name__ == "__main__":
    unittest.main()
