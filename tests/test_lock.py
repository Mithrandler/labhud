"""The display's lock: the PIN's hash, tickets, the slowed tries, and the routes on the real demo
server with a PIN set."""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest

import config
import lock
from tests import signin
from tests.test_smoke import DemoServer, ROOT
from tests.test_maintenance import load


class FakeStore:
    def __init__(self, on=True):
        self.on, self.kv = on, {}

    def enabled(self):
        return self.on

    def load(self, name, default=None):
        return self.kv.get(name, default)

    def save(self, name, value):
        self.kv[name] = value


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.real_store, self.store = lock.store, FakeStore()
        lock.store = self.store
        self.env = os.environ.pop(lock.ENV_KEY, None)
        self.clock = Clock()
        self.lk = lock.Lock(idle=600, clock=self.clock)

    def tearDown(self):
        lock.store = self.real_store
        if self.env is not None:
            os.environ[lock.ENV_KEY] = self.env
        else:
            os.environ.pop(lock.ENV_KEY, None)

    def set_pin(self, pin="1234"):
        self.store.kv[lock.STORE_KEY] = lock.hash_pin(pin)


class Hash(unittest.TestCase):
    def test_round_trip(self):
        h = lock.hash_pin("0420")
        self.assertTrue(h.startswith("scrypt$16384$8$1$"))
        self.assertNotIn("0420", h)
        self.assertTrue(lock.verify("0420", h))
        self.assertFalse(lock.verify("0421", h))
        self.assertNotEqual(h, lock.hash_pin("0420"))   # salted

    def test_bad_input(self):
        h = lock.hash_pin("1234")
        for pin in ("12a4", "12345", "123", "", None, 1234):
            with self.subTest(pin=pin):
                self.assertFalse(lock.verify(pin, h))
        for stored in ("", None, "md5$x", "scrypt$1$2", "scrypt$16384$8$1$!!$!!"):
            with self.subTest(stored=stored):
                self.assertFalse(lock.verify("1234", stored))
        with self.assertRaises(ValueError):
            lock.hash_pin("12345")


class Tickets(Base):
    def test_off_without_pin(self):
        self.assertFalse(self.lk.is_on())
        self.assertTrue(self.lk.allows(None))
        self.assertEqual(self.lk.unlock("1234"), (None, 0))

    def test_unlock_and_idle(self):
        self.set_pin()
        self.assertFalse(self.lk.allows(None))
        self.assertEqual(self.lk.unlock("9999"), (None, 0))
        t, wait = self.lk.unlock("1234")
        self.assertTrue(t and wait == 0)
        self.clock.t += 500
        self.assertTrue(self.lk.allows(t))     # used: alive another 600 s from here
        self.clock.t += 599
        self.assertTrue(self.lk.valid(t))
        self.clock.t += 601
        self.assertFalse(self.lk.valid(t))
        self.assertFalse(self.lk.valid(t))     # gone for good

    def test_close(self):
        self.set_pin()
        t, _ = self.lk.unlock("1234")
        self.lk.close(t)
        self.assertFalse(self.lk.valid(t))

    def test_at_most_eight(self):
        self.set_pin()
        first, _ = self.lk.unlock("1234")
        for _ in range(lock.MAX_TICKETS):
            self.clock.t += 1
            self.lk.unlock("1234")
        self.assertFalse(self.lk.valid(first))

    def test_env_hash(self):
        os.environ[lock.ENV_KEY] = lock.hash_pin("5555")
        self.store.on = False
        lk = lock.Lock(clock=self.clock)
        self.assertEqual(lk.public(), {"on": True, "idle": 120, "settable": False})
        self.assertTrue(lk.unlock("5555")[0])
        self.assertFalse(lk.set_pin("5555", "1111")[0])


class Tries(Base):
    def test_wait_doubles_and_resets(self):
        self.set_pin()
        blocks = []
        self.lk.on_block = blocks.append
        for _ in range(4):
            self.assertEqual(self.lk.unlock("0000"), (None, 0))
        self.assertEqual(self.lk.unlock("0000"), (None, 60))
        self.assertEqual(len(blocks), 1)
        self.assertEqual(self.lk.unlock("1234"), (None, 60))   # not even checked while waiting
        self.clock.t += 61
        for _ in range(4):
            self.lk.unlock("0000")
        self.assertEqual(self.lk.unlock("0000"), (None, 120))
        self.clock.t += 121
        self.assertTrue(self.lk.unlock("1234")[0])
        for _ in range(4):
            self.lk.unlock("0000")
        self.assertEqual(self.lk.unlock("0000"), (None, 60))   # back to the first wait

    def test_wait_has_a_ceiling(self):
        self.set_pin()
        for _ in range(12):
            for _ in range(5):
                _, wait = self.lk.unlock("0000")
            self.clock.t += wait + 1
        self.assertEqual(wait, lock.MAX_WAIT)


class SetPin(Base):
    def test_first_change_remove(self):
        ok, t, err, _ = self.lk.set_pin(None, "1234")
        self.assertTrue(ok and t and err is None)
        self.assertTrue(self.lk.is_on())
        self.assertFalse(self.lk.set_pin("9999", "4321")[0])
        ok, t2, _, _ = self.lk.set_pin("1234", "4321")
        self.assertTrue(ok)
        self.assertFalse(self.lk.valid(t))     # a change ends the other tickets
        self.assertTrue(self.lk.valid(t2))
        self.assertTrue(self.lk.unlock("4321")[0])
        ok, t3, _, _ = self.lk.set_pin("4321", None)
        self.assertTrue(ok and t3 is None)
        self.assertFalse(self.lk.is_on())

    def test_removed_in_store_beats_env(self):
        os.environ[lock.ENV_KEY] = lock.hash_pin("5555")
        self.store.kv[lock.STORE_KEY] = ""
        self.assertFalse(self.lk.is_on())

    def test_bad_new_pin(self):
        self.assertEqual(self.lk.set_pin(None, "12345")[2], "the PIN must be exactly 4 digits")
        self.assertEqual(self.lk.set_pin(None, None)[2], "no PIN is set")
        self.store.on = False
        self.assertIn("LABHUD_DATA", self.lk.set_pin(None, "1234")[2])

    def test_outside_change(self):
        self.set_pin()
        lk = lock.Lock(clock=self.clock)
        t, _ = lk.unlock("1234")
        self.assertFalse(lk.outside_change())
        self.set_pin("8888")                  # init.py pin, another process
        self.assertTrue(lk.outside_change())
        self.assertFalse(lk.valid(t))
        self.assertFalse(lk.outside_change())


class InitPin(unittest.TestCase):
    """init.py pin: --hash prints the .env line, --clear removes a PIN kept in LABHUD_DATA."""

    def run_init(self, args, data, stdin=""):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env["LABHUD_DATA"] = data
        return subprocess.run([sys.executable, os.path.join(ROOT, "init.py"), "pin", *args], input=stdin,
                              capture_output=True, text=True, env=env, timeout=30)

    def test_hash_and_clear(self):
        out = self.run_init(["--hash"], "", "1234\n1234\n").stdout.strip().splitlines()[-1]
        self.assertTrue(out.startswith(lock.ENV_KEY + "=scrypt$"))
        self.assertTrue(lock.verify("1234", out.split("=", 1)[1]))
        with tempfile.TemporaryDirectory() as d:
            r = self.run_init(["--clear"], d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PIN removed", r.stdout)
        self.assertIn("no usable LABHUD_DATA", self.run_init(["--clear"], "").stderr)


class Rules(unittest.TestCase):
    def test_idle(self):
        self.assertEqual(load("")["lock"], {"idle": 120})
        self.assertEqual(load("[lock]\nidle = 2\n")["lock"], {"idle": 120})
        with self.assertRaises(config.ConfigError) as e:
            load("[lock]\nidle = 0\nwho = 1\n")
        out = "\n".join(e.exception.problems)
        self.assertIn("idle must be from 1 to 120 minutes, got 0", out)
        self.assertIn("who", out)


class Routes(DemoServer):
    data = tempfile.TemporaryDirectory()
    extra_env = {"LABHUD_DATA": data.name, **signin.ENV}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ticket = signin.sign_in(cls.port, cls.host)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.data.cleanup()

    def post(self, path, body=None, ticket=None, origin=True, signed=True):
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{self.host}"
        if signed:
            headers["X-Labhud-Admin"] = self.ticket
        if ticket:
            headers["X-Labhud-Unlock"] = ticket
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=json.dumps(body or {}), headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_flow(self):
        cfg = json.loads(self.get("/api/config")[2])
        self.assertEqual(cfg["lock"], {"on": False, "idle": 120, "settable": True})
        maint = "/api/maintenance/vm-pve-100"
        self.assertEqual(self.post(maint, {"minutes": 0})[0], 200)            # the admin, no PIN
        self.assertEqual(self.post(maint, {"minutes": 0}, signed=False)[0], 403)   # not signed in: only shows
        self.assertEqual(self.post("/api/lock/pin", {"new": "2468"}, origin=False)[0], 403)
        self.assertEqual(self.post("/api/lock/pin", {"new": "2468"}, signed=False)[0], 403)
        status, r = self.post("/api/lock/pin", {"new": "2468"})
        self.assertEqual(status, 200)
        first = r["ticket"]
        self.assertTrue(json.loads(self.get("/api/config")[2])["lock"]["on"])
        self.assertEqual(self.post(maint, {"minutes": 0})[0], 403)            # locked
        self.assertEqual(self.post(maint, {"minutes": 0}, ticket="made-up")[0], 403)
        self.assertEqual(self.post(maint, {"minutes": 0}, ticket=first)[0], 200)
        self.assertEqual(self.post("/api/lock/pin", {"new": "1111"})[0], 403)  # a change needs the old one
        status, r = self.post("/api/lock/unlock", {"pin": "0000"})
        self.assertEqual((status, r["wait"]), (403, 0))
        status, r = self.post("/api/lock/unlock", {"pin": "2468"})
        self.assertEqual(status, 200)
        t = r["ticket"]
        self.assertEqual(self.post("/api/lock/touch", ticket=t)[0], 200)
        self.assertEqual(self.post("/api/lock/lock", ticket=t)[0], 200)
        self.assertEqual(self.post("/api/lock/touch", ticket=t)[0], 403)
        self.assertEqual(self.post("/api/lock/pin", {"old": "2468", "new": None})[0], 403)   # locked: no ⚙, PIN or not
        t = self.post("/api/lock/unlock", {"pin": "2468"})[1]["ticket"]
        self.assertEqual(self.post("/api/lock/pin", {"old": "2468", "new": None}, ticket=t)[0], 200)
        self.assertFalse(json.loads(self.get("/api/config")[2])["lock"]["on"])
        self.assertEqual(self.post(maint, {"minutes": 0})[0], 200)
        with open(os.path.join(self.data.name, "labhud.db"), "rb") as f:
            self.assertNotIn(b"2468", f.read())

    def test_refused_shapes(self):
        self.assertEqual(self.post("/api/lock/nope")[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/lock/unlock", body="x" * 300,
                     headers={"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()


if __name__ == "__main__":
    unittest.main()
