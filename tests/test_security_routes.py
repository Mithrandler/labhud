"""⚙ > Security through server.py: admin_networks behind a trusted proxy, the settings saved to the
kept config (and the lock-out guard), and the Danger zone's two rows."""

import http.client
import json
import os
import shutil
import tempfile
import time
import tomllib
import unittest

import admin
from tests import signin
from tests.test_oidc import demo_config
from tests import test_smoke

INSIDE = "10.9.9.5"


class Networks(test_smoke.DemoServer):
    data = tempfile.TemporaryDirectory()
    extra_env = {"LABHUD_DATA": data.name, **signin.ENV}

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(cls.data.name, "config.toml"), "w", encoding="utf-8") as f:
            f.write(demo_config('[admin]\ntrusted_proxies = ["127.0.0.1/32"]\nadmin_networks = ["10.9.9.0/24"]\n'))
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.data.name, ignore_errors=True)

    def call(self, path, body=None, xff=None, ticket=None):
        h = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        if xff:
            h["X-Forwarded-For"] = xff
        if ticket:
            h["X-Labhud-Admin"] = ticket
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("POST", path, body=json.dumps(body or {}), headers=h)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def login(self, xff):
        code = admin.totp(signin.SECRET, int(time.time()) // admin.STEP)
        status, r = self.call("/api/admin/login", {"user": "boss", "password": "correct horse 1", "code": code}, xff)
        if status == 403 and xff == INSIDE:   # this step's code was used: the next one
            time.sleep(admin.STEP - time.time() % admin.STEP + 0.5)
            return self.login(xff)
        return status, r

    def test_all(self):
        # outside the admin networks: refused before the password is even looked at
        self.assertEqual(self.login(None)[0], 403)            # 127.0.0.1 itself
        self.assertEqual(self.login("10.9.9.5, 8.8.8.8")[0], 403)   # the proxy saw 8.8.8.8
        status, r = self.login("8.8.8.8, " + INSIDE)          # a forged left entry changes nothing
        self.assertEqual(status, 200, r)
        ticket = r["ticket"]
        self.assertEqual(self.call("/api/admin/check", xff=INSIDE, ticket=ticket)[0], 200)
        self.assertEqual(self.call("/api/admin/check", xff="8.8.8.8", ticket=ticket)[0], 403)   # a stolen ticket, elsewhere
        self.assertEqual(self.call("/api/security/get", xff="8.8.8.8", ticket=ticket)[0], 403)

        status, st = self.call("/api/security/get", xff=INSIDE, ticket=ticket)
        self.assertEqual(status, 200, st)
        self.assertEqual((st["your_ip"], st["via_proxy"], st["admin_networks"]), (INSIDE, True, ["10.9.9.0/24"]))
        self.assertIsNone(st["oidc"])
        self.assertTrue(st["kept"])

        # the lock-out guard
        status, r = self.call("/api/security/save", {"admin_networks": ["192.168.0.0/24"]}, INSIDE, ticket)
        self.assertEqual(status, 400)
        self.assertIn("lock you out", r["error"])
        status, r = self.call("/api/security/save", {"trusted_proxies": []}, INSIDE, ticket)
        self.assertEqual(status, 400)                       # without the proxy, this browser is 127.0.0.1
        self.assertEqual(self.call("/api/security/save", {"session_hours": 0}, INSIDE, ticket)[0], 400)
        self.assertEqual(self.call("/api/security/save", {"oidc": {"issuer": "http://x.example/", "client_id": "a",
                                                                    "allow": ["p"]}}, INSIDE, ticket)[0], 400)

        status, r = self.call("/api/security/save", {"session_hours": 2, "pin_minutes": 5,
                                                     "admin_networks": ["10.9.9.0/24", "192.168.1.0/24"]}, INSIDE, ticket)
        self.assertEqual(status, 200, r)
        with open(os.path.join(self.data.name, "config.toml"), "rb") as f:
            raw = tomllib.load(f)
        self.assertEqual(raw["admin"]["session_hours"], 2)
        self.assertEqual(raw["admin"]["admin_networks"], ["10.9.9.0/24", "192.168.1.0/24"])
        self.assertEqual(raw["lock"]["idle"], 5)
        deadline = time.monotonic() + 15                     # applied without a restart
        while time.monotonic() < deadline:
            st = self.call("/api/security/get", xff=INSIDE, ticket=ticket)[1]
            if st.get("session_hours") == 2:
                break
            time.sleep(0.5)
        self.assertEqual((st["session_hours"], st["pin_minutes"]), (2, 5))
        self.assertEqual(self.call("/api/admin/check", xff=INSIDE, ticket=ticket)[1]["idle"], 7200)

        # the Danger zone
        self.assertEqual(self.call("/api/security/remove-phones", {"confirm": "nope"}, INSIDE, ticket)[0], 400)
        status, r = self.call("/api/security/remove-phones", {"confirm": "REMOVE"}, INSIDE, ticket)
        self.assertEqual((status, r["phones"]), (200, 0))
        self.assertEqual(self.call("/api/security/new-keys", {"confirm": "NEW"}, INSIDE, ticket)[0], 400)
        status, r = self.call("/api/security/new-keys", {"confirm": "NEW KEYS"}, INSIDE, ticket)
        self.assertEqual(status, 200, r)


def _skip_smoke_tests(cls):
    """The demo's own smoke tests run in test_smoke, not again here. In a function, so that the
    names of the loop stay out of the module: unittest would find the class a second time."""
    for name in [n for n in dir(test_smoke.DemoServer) if n.startswith("test_")]:
        setattr(cls, name, None)


_skip_smoke_tests(Networks)


if __name__ == "__main__":
    unittest.main()
