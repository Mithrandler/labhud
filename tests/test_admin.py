"""The admin (admin.py): TOTP against RFC 6238, the password's hash, one use per code, slowed
tries, tickets, and the routes on the real demo server with an admin set: pairing and removing
phones, the editor and pairing from the app by signing in."""

import base64
import http.client
import json
import os
import tempfile
import time
import unittest

import admin
import devices
from tests.test_lock import Clock, FakeStore
from tests.test_smoke import DemoServer, ROOT

SECRET = admin.new_secret()
REC = admin.record("boss", "correct horse 1", SECRET)


def code(secret=SECRET, offset=0):
    return admin.totp(secret, int(time.time()) // admin.STEP + offset)


class Totp(unittest.TestCase):
    def test_rfc6238(self):
        s = base64.b32encode(b"12345678901234567890").decode()
        for t, want in ((59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (20000000000, "353130")):
            self.assertEqual(admin.totp(s, t // 30), want)

    def test_window_and_shape(self):
        now = time.time()
        self.assertIsNotNone(admin.totp_counter(code(), SECRET, now))
        self.assertIsNotNone(admin.totp_counter(code(offset=-1), SECRET, now))
        self.assertIsNone(admin.totp_counter(code(offset=-3), SECRET, now))
        self.assertIsNone(admin.totp_counter("12345", SECRET, now))
        self.assertIsNone(admin.totp_counter("abcdef", SECRET, now))
        self.assertIsNotNone(admin.totp_counter(code()[:3] + " " + code()[3:], SECRET, now))

    def test_uri(self):
        uri = admin.otpauth_uri("boss", SECRET)
        self.assertTrue(uri.startswith("otpauth://totp/LabHUD%3Aboss?"))
        self.assertIn("secret=" + SECRET, uri)


class Account(unittest.TestCase):
    def setUp(self):
        self.real, self.store = admin.store, FakeStore()
        admin.store = self.store
        self.env = os.environ.pop(admin.ENV_KEY, None)
        self.clock = Clock()
        self.a = admin.Admin(idle=600, clock=self.clock)

    def tearDown(self):
        admin.store = self.real
        os.environ.pop(admin.ENV_KEY, None)
        if self.env is not None:
            os.environ[admin.ENV_KEY] = self.env

    def test_record(self):
        with self.assertRaises(ValueError):
            admin.record("boss", "short", SECRET)
        with self.assertRaises(ValueError):
            admin.record("two words", "correct horse 1", SECRET)
        self.assertTrue(admin.verify_password("correct horse 1", REC["hash"]))
        self.assertFalse(admin.verify_password("correct horse 2", REC["hash"]))
        self.assertNotIn("correct horse", json.dumps(REC))

    def test_off_then_env_then_store(self):
        self.assertFalse(self.a.is_on())
        os.environ[admin.ENV_KEY] = admin.env_line(REC).split("=", 1)[1]
        self.assertEqual(self.a.user(), "boss")
        self.store.kv[admin.STORE_KEY] = {}          # removed in the store beats the environment
        self.assertFalse(self.a.is_on())

    def test_sign_in_once_per_code(self):
        self.store.kv[admin.STORE_KEY] = REC
        c = code()
        self.assertEqual(self.a.login("boss", "wrong password", c), (None, 0))
        ticket, _ = self.a.login("BOSS", "correct horse 1", c)
        self.assertTrue(ticket)
        self.assertEqual(self.a.login("boss", "correct horse 1", c), (None, 0))   # the same code again
        self.assertTrue(self.a.valid(ticket))
        self.clock.t += 601
        self.assertFalse(self.a.valid(ticket))

    def test_slowed(self):
        self.store.kv[admin.STORE_KEY] = REC
        for _ in range(admin.FAILS_PER_ROUND - 1):
            self.assertEqual(self.a.check("boss", "nope nope nope", "000000"), (False, 0))
        self.assertEqual(self.a.check("boss", "nope nope nope", "000000"), (False, 60))
        self.assertEqual(self.a.check("boss", "correct horse 1", code()), (False, 60))   # not even checked
        self.clock.t += 61
        self.assertTrue(self.a.check("boss", "correct horse 1", code())[0])

    def test_replaced_elsewhere_ends_tickets(self):
        self.store.kv[admin.STORE_KEY] = REC
        ticket, _ = self.a.login("boss", "correct horse 1", code())
        self.store.kv[admin.STORE_KEY] = admin.record("boss", "another one 22", admin.new_secret())
        self.assertFalse(self.a.valid(ticket))


class Routes(DemoServer):
    data = tempfile.TemporaryDirectory()
    extra_env = {"LABHUD_DATA": data.name, admin.ENV_KEY: admin.env_line(REC).split("=", 1)[1]}

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.data.cleanup()

    def call(self, path, body=None, origin=True, ticket=None, host=None, token=None):
        headers = {"Host": host or self.host, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{host or self.host}"
        if ticket:
            headers["X-Labhud-Admin"] = ticket
        if token:
            headers["Authorization"] = "Bearer " + token
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=json.dumps(body or {}), headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def fresh_code(self, after=None):
        """A code of a step not used yet: each one works once."""
        while True:
            c = code()
            if c != after:
                return c
            time.sleep(1)

    def test_flow(self):
        cfg = json.loads(self.get("/api/config")[2])
        self.assertEqual(cfg["admin"], {"on": True})
        self.assertEqual(self.call("/api/v1/pair-code")[0], 403)                # no sign-in: no QR
        self.assertEqual(self.call("/api/admin/login", {"user": "boss", "password": "x" * 12, "code": code()})[0], 403)
        self.assertEqual(self.call("/api/admin/login", {"user": "boss", "password": "correct horse 1", "code": code()},
                                   origin=False)[0], 403)
        first = self.fresh_code()
        status, r = self.call("/api/admin/login", {"user": "boss", "password": "correct horse 1", "code": first})
        self.assertEqual(status, 200, r)
        ticket = r["ticket"]
        status, p = self.call("/api/v1/pair-code", ticket=ticket)
        self.assertEqual(status, 200, p)
        status, e = self.call("/api/admin/edit", ticket=ticket)
        self.assertIn(status, (200, 409))                                        # 409: a read-only config
        if status == 200:   # the editor's one-time code sits next to the demo's config: not left behind
            os.remove(os.path.join(ROOT, "demo", ".labhud-edit"))
        self.assertEqual(self.call("/api/admin/edit")[0], 403)

        # pairing from the app by signing in: under any Host, slowed, a code works once
        far = "phone.vpn:1"
        self.assertEqual(self.call("/api/v1/login-pair", {"user": "boss", "password": "correct horse 1", "code": first,
                                                          "name": "Pixel"}, origin=False, host=far)[0], 403)
        second = self.fresh_code(after=first)
        status, r = self.call("/api/v1/login-pair", {"user": "boss", "password": "correct horse 1", "code": second,
                                                     "name": "Pixel"}, origin=False, host=far)
        self.assertEqual(status, 200, r)
        token = r["token"]
        self.assertEqual(r["info"]["admin"], True)
        # the app's token cannot manage phones, even with the admin's ticket
        self.assertEqual(self.call("/api/v1/unpair", {"name": "Pixel"}, ticket=ticket, token=token, origin=False)[0], 403)
        self.assertEqual(self.call("/api/v1/unpair", {"name": "Pixel"})[0], 403)  # not signed in
        self.assertEqual(self.call("/api/v1/unpair", {"name": "Pixel"}, ticket=ticket)[0], 200)
        self.assertEqual(self.call("/api/admin/logout", ticket=ticket)[0], 200)
        self.assertEqual(self.call("/api/v1/pair-code", ticket=ticket)[0], 403)


class InitAdmin(unittest.TestCase):
    """init.py admin: asks, shows the authenticator QR, keeps the admin only after a right code."""

    def test_make_and_remove(self):
        import contextlib
        import io
        from unittest import mock
        import init
        store = FakeStore()
        answers = iter(["boss", "short", "correct horse 1", "correct horse 1"])
        seen = {}

        def ask(question, default="", secret=False, optional=False):
            if question.startswith("The 6-digit code"):
                if "secret" not in seen:   # the first answer is wrong on purpose
                    seen["secret"] = True
                    return "000000"
                return admin.totp(seen["key"], int(time.time()) // 30)
            return next(answers)

        out = io.StringIO()
        real_new = admin.new_secret
        with mock.patch.object(admin, "store", store), mock.patch.object(init, "ask", ask), \
                mock.patch.object(admin, "new_secret", lambda: seen.setdefault("key", real_new())), \
                mock.patch.dict(os.environ, {}, clear=False), contextlib.redirect_stdout(out), \
                mock.patch("store.enabled", store.enabled), mock.patch("store.save", store.save), mock.patch("store.load", store.load):
            os.environ.pop(admin.ENV_KEY, None)
            init.admin_account([])
            rec = store.kv[admin.STORE_KEY]
            self.assertEqual(rec["user"], "boss")
            self.assertTrue(admin.verify_password("correct horse 1", rec["hash"]))
            self.assertIn("at least 10 characters", out.getvalue())
            self.assertIn("\033[30;47m", out.getvalue())          # the QR code
            init.admin_account(["--remove"])
            self.assertEqual(store.kv[admin.STORE_KEY], {})


if __name__ == "__main__":
    unittest.main()
