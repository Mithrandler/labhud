"""Signed actions, end to end: labhud (LABHUD_ACTION_SECRET) forwards to agents/labhud-agent.py
(LABHUD_AGENT_SECRET), which runs only signed, fresh, unseen actions from an allowed IP."""

import hashlib
import hmac
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

from tests.test_smoke import ROOT, free_port

SECRET = "test-secret-not-real"
CONFIG = """
[[page]]
id = "main"
title = "MAIN"
  [[page.group]]
  id = "g"
  title = "G"
    [[page.group.card]]
    id = "box"
    name = "BOX"
    actions = ["test-ok", "test-fail"]
"""


def start(args, env, port):
    proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read())
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("did not start")


class SignedActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        with open(os.path.join(cls.tmp, "config.toml"), "w") as f:
            f.write(CONFIG)
        with open(os.path.join(cls.tmp, "actions.toml"), "w") as f:
            f.write('[actions]\ntest-ok = ["true"]\ntest-fail = ["false"]\nnot-offered = ["true"]\n')
        base = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        base["PYTHONDONTWRITEBYTECODE"] = "1"
        cls.agent_port, cls.port = free_port(), free_port()
        cls.host = f"127.0.0.1:{cls.port}"
        cls.agent = start(["agents/labhud-agent.py"], dict(
            base, LABHUD_AGENT_PORT=str(cls.agent_port), LABHUD_AGENT_SECRET=SECRET,
            LABHUD_AGENT_ALLOW="127.0.0.1", LABHUD_AGENT_ACTIONS=os.path.join(cls.tmp, "actions.toml")), cls.agent_port)
        cls.server = start(["server.py"], dict(
            base, LABHUD_PORT=str(cls.port), LABHUD_CONFIG=os.path.join(cls.tmp, "config.toml"),
            LABHUD_HOSTS=cls.host, LABHUD_ACTION_SECRET=SECRET,
            LABHUD_ACTION_URL=f"http://127.0.0.1:{cls.agent_port}/action/"), cls.port)

    @classmethod
    def tearDownClass(cls):
        for p in (cls.server, cls.agent):
            p.terminate()
            p.wait(5)
            p.stdout.close()
        shutil.rmtree(cls.tmp)

    def post(self, port, path, headers):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        conn.request("POST", path, body=b"", headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, json.loads(body or b"{}")

    def via_labhud(self, name, origin=None):
        headers = {"Host": self.host}
        if origin is not False:
            headers["Origin"] = origin or f"http://{self.host}"
        return self.post(self.port, f"/action/{name}", headers)

    def signature(self, name, ts=None):
        ts, nonce = str(int(ts or time.time())), os.urandom(8).hex()
        return {"X-Labhud-Time": ts, "X-Labhud-Nonce": nonce,
                "X-Labhud-Signature": hmac.new(SECRET.encode(), f"{ts}.{nonce}.{name}".encode(), hashlib.sha256).hexdigest()}

    def test_config_points_the_browser_at_labhud(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/config", headers={"Host": self.host})
        self.assertEqual(json.loads(conn.getresponse().read())["action_url"], "/action/")
        conn.close()

    def test_forwarded_and_run(self):
        self.assertEqual(self.via_labhud("test-ok"), (200, {"ok": True, "action": "test-ok"}))
        self.assertEqual(self.via_labhud("test-ok")[0], 200)  # pressed again in the same second
        status, answer = self.via_labhud("test-fail")
        self.assertEqual((status, answer["ok"]), (500, False))

    def test_labhud_refuses(self):
        self.assertEqual(self.via_labhud("test-ok", origin=False)[0], 403)                   # no Origin
        self.assertEqual(self.via_labhud("test-ok", origin="http://evil.example")[0], 403)   # another page
        self.assertEqual(self.via_labhud("not-offered")[0], 403)                             # not in config.toml

    def test_agent_refuses_unsigned_old_and_replayed(self):
        self.assertEqual(self.post(self.agent_port, "/action/test-ok", {})[0], 403)
        self.assertEqual(self.post(self.agent_port, "/action/test-ok", self.signature("test-ok", time.time() - 120))[0], 403)
        self.assertEqual(self.post(self.agent_port, "/action/test-ok", self.signature("test-fail"))[0], 403)  # signed for another
        fresh = self.signature("test-ok")
        self.assertEqual(self.post(self.agent_port, "/action/test-ok", fresh)[0], 200)
        self.assertEqual(self.post(self.agent_port, "/action/test-ok", fresh)[0], 403)  # the same one again


if __name__ == "__main__":
    unittest.main()
