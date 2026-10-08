"""Maintenance and actions with a question: the config rules, the server's checks, and a
MAINTENANCE press on the real demo server."""

import http.client
import json
import os
import tempfile
import time
import unittest

import config
from tests.test_smoke import DemoServer, ROOT

with open(os.path.join(ROOT, "demo", "config.toml"), encoding="utf-8") as f:
    DEMO = f.read()


def load(extra):
    """The demo config, with an action on vm-pve-100 (the demo has none) and `extra` at the end."""
    text = DEMO.replace('id = "vm-pve-100"', 'id = "vm-pve-100"\n    actions = ["vm-pve-100-reboot"]', 1)
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write(text + "\n" + extra)
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


class Rules(unittest.TestCase):
    def problems(self, extra):
        with self.assertRaises(config.ConfigError) as e:
            load(extra)
        return "\n".join(e.exception.problems)

    def test_window(self):
        t = load('[[maintenance.window]]\ncards = ["nas"]\nfrom = "2026-10-12 08:00"\nuntil = "2026-10-12 10:00"\nreason = "disks"\n')
        w = t["maintenance"]["windows"][0]
        self.assertEqual((w["cards"], w["until"] - w["from"], w["reason"]), (["nas"], 7200, "disks"))
        self.assertTrue(t["maintenance"]["buttons"])
        self.assertTrue(config.public(t)["maintenance_buttons"])

    def test_window_problems(self):
        out = self.problems('[[maintenance.window]]\ncards = ["nass"]\nuntil = "tomorrow"\n')
        self.assertIn("card 'nass' does not exist (did you mean 'nas'?)", out)
        self.assertIn('until must be a time like "2026-10-12 08:00"', out)

    def test_choices(self):
        t = load('[action.vm-pve-100-reboot]\nconfirm = "When?"\nchoices = [{label = "NOW", action = "vm-pve-100-reboot"},'
                 ' {label = "TONIGHT", action = "reboot-tonight"}]\n')
        self.assertEqual([c["action"] for c in t["actions"]["vm-pve-100-reboot"]["choices"]],
                         ["vm-pve-100-reboot", "reboot-tonight"])

    def test_choices_problems(self):
        out = self.problems('[action.nobody-has-it]\nchoices = [{label = "X", action = "Bad Name"}]\n')
        self.assertIn("action 'Bad Name' may only use a-z", out)
        self.assertIn("has choices but no card offers it", out)


class ChoiceAllowed(unittest.TestCase):
    def test_only_the_written_choices(self):
        os.environ.setdefault("LABHUD_DEMO", "1")
        import server
        topo = load('[action.vm-pve-100-reboot]\nchoices = [{label = "TONIGHT", action = "reboot-tonight"}]\n')
        old = server.TOPOLOGY
        server.TOPOLOGY = topo
        try:
            self.assertTrue(server._action_allowed("vm-pve-100-reboot"))
            self.assertTrue(server._action_allowed("reboot-tonight"))
            self.assertFalse(server._action_allowed("reboot-tomorrow"))
        finally:
            server.TOPOLOGY = old


class Press(DemoServer):
    def post(self, path, body, origin=True):
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{self.host}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=json.dumps(body), headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def maintenance(self):
        return json.loads(self.get("/api/snapshot")[2]).get("maintenance", {})

    def test_press_and_end(self):
        status, answer = self.post("/api/maintenance/vm-pve-100", {"minutes": 60})
        self.assertEqual(status, 200)
        self.assertAlmostEqual(answer["until"], time.time() + 3600, delta=5)
        self.assertIn("vm-pve-100", self.maintenance())
        recent = json.loads(self.get("/api/snapshot")[2])["events"]["recent"]
        self.assertTrue(any("maintenance until" in e["name"] for e in recent))
        self.assertEqual(self.post("/api/maintenance/vm-pve-100", {"minutes": 0})[0], 200)
        self.assertNotIn("vm-pve-100", self.maintenance())

    def test_refused(self):
        for path, body, origin in (("/api/maintenance/vm-pve-100", {"minutes": 60}, False),
                                   ("/api/maintenance/no-such-card", {"minutes": 60}, True),
                                   ("/api/maintenance/vm-pve-100", {"minutes": 99999}, True),
                                   ("/api/maintenance/vm-pve-100", {"minutes": "x"}, True)):
            with self.subTest(path=path, body=body, origin=origin):
                self.assertEqual(self.post(path, body, origin)[0], 403)


if __name__ == "__main__":
    unittest.main()
