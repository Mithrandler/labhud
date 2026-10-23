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
        "LABHUD_SERVERS_HTTP": "on",
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
        if getattr(PinRoutes, "_ticket", None):   # a TOTP code works once per step
            return PinRoutes._ticket
        status, r = self.request("POST", "/api/admin/login",
                                 {"user": "boss", "password": "correct horse 1", "code": code()})
        self.assertEqual(status, 200, r)
        PinRoutes._ticket = r["ticket"]
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

    def test_servers_locked_is_not_forbidden(self):
        """No ticket: "locked" (ask for the PIN), a different answer from "forbidden", and it never blocks the address."""
        ticket = self.sign_in()
        for _ in range(12):   # more than the 8 refusals that block an address
            status, r = self.request("POST", "/api/servers/action", {}, ticket=ticket)
            self.assertEqual((status, r.get("error"), r.get("pin")), (403, "locked", True))
        status, r = self.request("POST", "/api/servers/action", {})   # not signed in at all
        self.assertEqual((status, r.get("error")), (403, "forbidden"))
        status, r = self.request("POST", "/api/lock/unlock", {"pin": PIN}, ticket=ticket)
        unlock = r["ticket"]
        status, r = self.request("POST", "/api/servers/action", {}, ticket=ticket, unlock=unlock)
        self.assertNotIn(r.get("error"), ("locked", "forbidden"))   # past the gate, not blocked (429) either
        self.assertNotEqual(status, 429)

    def test_reading_keeps_the_ticket_alive(self):
        ticket = self.sign_in()
        unlock = self.request("POST", "/api/lock/unlock", {"pin": PIN}, ticket=ticket)[1]["ticket"]
        # Any request that carries the ticket renews it, reads too (server._touch_ticket); here only that it still works after.
        for _ in range(3):
            self.assertEqual(self.request("GET", "/api/status", ticket=ticket, unlock=unlock)[0], 200)
        self.assertEqual(self.request("POST", "/api/setup/status", ticket=ticket, unlock=unlock)[0], 200)


if __name__ == "__main__":
    unittest.main()
