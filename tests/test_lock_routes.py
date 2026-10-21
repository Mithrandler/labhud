"""With a PIN set, a browser signed in as the admin is refused ⚙ until it unlocks (server._may_manage), and /api/v1/devices answers only a phone, the admin or the labhud command."""

import http.client
import json
import tempfile
import time
import unittest

import admin
import lock
from tests.test_smoke import DemoServer

SECRET = admin.new_secret()
REC = admin.record("boss", "correct horse 1", SECRET)
PIN = "4821"


def code():
    return admin.totp(SECRET, int(time.time()) // admin.STEP)


class PinRoutes(DemoServer):
    data = tempfile.TemporaryDirectory()
    extra_env = {
        "LABHUD_DATA": data.name,
        admin.ENV_KEY: admin.env_line(REC).split("=", 1)[1],
        lock.ENV_KEY: lock.hash_pin(PIN),
    }

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.data.cleanup()

    def request(self, method, path, body=None, ticket=None, unlock=None, origin=True):
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{self.host}"
        if ticket:
            headers["X-Labhud-Admin"] = ticket
        if unlock:
            headers["X-Labhud-Unlock"] = unlock
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        if method == "POST":
            conn.request("POST", path, body=json.dumps(body or {}), headers=headers)
        else:
            conn.request("GET", path, body=None, headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def sign_in(self):
        status, r = self.request("POST", "/api/admin/login",
                                 {"user": "boss", "password": "correct horse 1", "code": code()})
        self.assertEqual(status, 200, r)
        return r["ticket"]

    def test_pin_and_devices(self):
        status, _ = self.request("GET", "/api/v1/devices", origin=False)
        self.assertEqual(status, 403)   # nobody

        ticket = self.sign_in()

        self.assertEqual(self.request("GET", "/api/v1/devices", ticket=ticket, origin=False)[0], 200)   # the admin may list them

        self.assertEqual(self.request("POST", "/api/setup/status", ticket=ticket)[0], 403)   # signed in, but locked: no ⚙
        self.assertEqual(self.request("POST", "/api/agents/list", ticket=ticket)[0], 403)

        status, r = self.request("POST", "/api/lock/unlock", {"pin": "0000"}, ticket=ticket)
        self.assertEqual(status, 403)   # wrong PIN

        status, r = self.request("POST", "/api/lock/unlock", {"pin": PIN}, ticket=ticket)
        self.assertEqual(status, 200, r)
        unlock = r["ticket"]
        self.assertTrue(unlock)

        self.assertEqual(self.request("POST", "/api/setup/status", ticket=ticket, unlock=unlock)[0], 200)
        self.assertEqual(self.request("POST", "/api/agents/list", ticket=ticket, unlock=unlock)[0], 200)

        self.request("POST", "/api/lock/lock", ticket=ticket, unlock=unlock)
        self.assertEqual(self.request("POST", "/api/setup/status", ticket=ticket, unlock=unlock)[0], 403)   # locked again: the ticket is gone


if __name__ == "__main__":
    unittest.main()
