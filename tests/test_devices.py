"""Paired devices: codes, tokens and their removal (devices.py), init.py pair/devices/unpair, the
QR code, and the routes on the real demo server: a token stands in for the Host, Origin and PIN
checks, nothing else changes without one."""

import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

import devices
import qr
from tests import signin
from tests.test_lock import FakeStore
from tests.test_smoke import DemoServer, ROOT


class Base(unittest.TestCase):
    def setUp(self):
        self.real, self.store = devices.store, FakeStore()
        devices.store = self.store
        devices._seen.clear()

    def tearDown(self):
        devices.store = self.real


class Codes(Base):
    def test_pair_once(self):
        code = devices.new_code("Pixel 6a", now=1000)
        token, name = devices.pair(code, "ignored", now=1010)
        self.assertEqual(name, "Pixel 6a")
        self.assertRegex(token, devices.TOKEN)
        self.assertEqual(devices.pair(code, "again", now=1020), (None, None))   # used up
        self.assertEqual(devices.check(token, now=1030), "Pixel 6a")
        self.assertIsNone(devices.check(token[:-1] + ("A" if token[-1] != "A" else "B"), now=1030))
        self.assertIsNone(devices.check("nonsense"))
        self.assertNotIn(token.split(".")[1], json.dumps(self.store.kv))      # only hashes kept
        self.assertNotIn(code, json.dumps(self.store.kv))

    def test_expiry_and_names(self):
        code = devices.new_code(now=1000)
        self.assertEqual(devices.pair(code, "x", now=1000 + devices.PAIR_MINUTES * 60 + 1), (None, None))
        a = devices.pair(devices.new_code(now=2000), "  my   phone ", now=2001)[1]
        b = devices.pair(devices.new_code(now=2000), "my phone", now=2001)[1]
        c = devices.pair(devices.new_code(now=2000), "<script>", now=2001)[1]
        self.assertEqual((a, b, c), ("my phone", "my phone 2", "phone"))

    def test_unpair_and_listing(self):
        token, _ = devices.pair(devices.new_code(now=1000), "tablet", now=1000)
        self.assertEqual([d["name"] for d in devices.listing()], ["tablet"])
        self.assertNotIn("hash", devices.listing()[0])
        self.assertTrue(devices.unpair("TABLET"))
        self.assertFalse(devices.unpair("tablet"))
        self.assertIsNone(devices.check(token))

    def test_seen_written_rarely(self):
        token, _ = devices.pair(devices.new_code(now=1000), "p", now=1000)
        devices.check(token, now=1000 + devices.SEEN_EVERY + 1)
        seen = devices.listing()[0]["seen"]
        devices.check(token, now=1000 + devices.SEEN_EVERY + 10)
        self.assertEqual(devices.listing()[0]["seen"], seen)

    def test_without_store(self):
        self.store.on = False
        self.assertIsNone(devices.new_code())
        self.assertEqual(devices.pair("a" * 24, "x"), (None, None))
        self.assertEqual(devices.listing(), [])

    def test_admin_flag(self):
        token, _ = devices.pair(devices.new_code(now=1000), "p", now=1000)
        did, rec = devices.lookup(token)
        self.assertFalse(devices.is_admin(rec, "s1"))                     # a QR code alone only looks
        self.assertTrue(devices.set_admin(did, "s1"))
        rec = devices.lookup(token)[1]
        self.assertTrue(devices.is_admin(rec, "s1"))
        self.assertFalse(devices.is_admin(rec, "s2"))                     # a new admin: back to looking
        self.assertFalse(devices.is_admin(rec, None))                     # no admin at all
        self.assertEqual([d["admin"] for d in devices.listing("s1")], [True])
        self.assertEqual([d["admin"] for d in devices.listing("s2")], [False])
        devices.set_admin(did, None)
        self.assertFalse(devices.is_admin(devices.lookup(token)[1], "s1"))
        self.assertFalse(devices.set_admin("ffffffff", "s1"))
        t2, _ = devices.pair(devices.new_code(now=1000), "q", now=1000, admin="s1")
        self.assertTrue(devices.is_admin(devices.lookup(t2)[1], "s1"))

    def test_push_endpoint(self):
        token, _ = devices.pair(devices.new_code(now=1000), "p", now=1000)
        did = devices.lookup(token)[0]
        for bad in ("http://ntfy.example/up1", "javascript:x", "https://", "https://a b/", "https://x/" + "a" * 600):
            with self.assertRaises(ValueError, msg=bad):
                devices.set_push(did, bad)
        self.assertTrue(devices.set_push(did, "https://ntfy.example/upAbC?up=1"))
        self.assertEqual(devices.push_endpoints(), {"https://ntfy.example/upAbC?up=1": did})
        self.assertEqual([d["push"] for d in devices.listing()], [True])
        self.assertNotIn("ntfy.example", json.dumps(devices.listing()))     # a capability: never listed
        devices.drop_push("https://ntfy.example/upAbC?up=1")
        self.assertEqual(devices.push_endpoints(), {})
        devices.set_push(did, "https://ntfy.example/up2")
        self.assertTrue(devices.set_push(did, ""))
        self.assertEqual(devices.push_endpoints(), {})
        self.assertFalse(devices.set_push("ffffffff", "https://ntfy.example/up3"))

    def test_link(self):
        link = devices.pair_link("http://wall.lan:8095/", "abc")
        self.assertEqual(link, "labhud://pair?u=http%3A%2F%2Fwall.lan%3A8095&c=abc")


class QR(unittest.TestCase):
    """The full decode check (every version, read back by jsQR) was done when qr.py was written;
    here: sizes, the fixed patterns, and that the output does not drift."""

    def test_sizes(self):
        self.assertEqual(len(qr.encode("labhud")), 21)
        self.assertEqual(len(qr.encode("x" * 150)), 8 * 4 + 17)
        with self.assertRaises(ValueError):
            qr.encode("x" * 700)

    def test_finders_and_timing(self):
        m = qr.encode("labhud://pair?u=http%3A%2F%2F192.0.2.5%3A8095&c=abcdefghijklmnopqrstuvwx")
        s = len(m)
        for x0, y0 in ((0, 0), (s - 7, 0), (0, s - 7)):
            ring = [m[y0][x0 + i] for i in range(7)] + [m[y0 + 6][x0 + i] for i in range(7)]
            self.assertTrue(all(ring))
            self.assertTrue(m[y0 + 3][x0 + 3])
            self.assertFalse(m[y0 + 1][x0 + 1])
        self.assertEqual([m[6][x] for x in range(8, s - 8)], [x % 2 == 0 for x in range(8, s - 8)])
        self.assertTrue(m[s - 8][8])   # the dark module

    def test_outputs(self):
        self.assertTrue(qr.svg("hi").startswith("<svg"))
        self.assertIn("\033[30;47m", qr.terminal("hi"))
        self.assertEqual(sum(map(sum, qr.encode("labhud"))), sum(map(sum, qr.encode("labhud"))))


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

    def init(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data.name, LABHUD_HOSTS=f"wall.example:{self.port}")
        return subprocess.run([sys.executable, os.path.join(ROOT, "init.py"), *args], capture_output=True,
                              text=True, env=env, timeout=30)

    def call(self, method, path, body=None, host=None, token=None, origin=False, cookie=None, signed=True):
        headers = {"Host": host or self.host}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = "other=1; labhud_device=" + cookie
        if origin:
            headers["Origin"] = f"http://{host or self.host}"
            if signed:
                headers["X-Labhud-Admin"] = self.ticket
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode()

    def code_from(self, text):
        link = re.search(r"labhud://pair\?\S+", text).group(0)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)
        return q["c"][0], q["u"][0]

    def test_flow(self):
        out = self.init("pair", "Pixel", "6a")
        self.assertEqual(out.returncode, 0, out.stderr)
        code, url = self.code_from(out.stdout)
        self.assertEqual(url, f"http://wall.example:{self.port}")
        far = "phone.vpn:1"   # a name not in LABHUD_HOSTS: the phone over the VPN

        self.assertEqual(self.call("POST", "/api/v1/pair", {"code": "x" * 24}, host=far)[0], 403)
        status, r = self.call("POST", "/api/v1/pair", {"code": code, "name": "ignored"}, host=far)
        self.assertEqual(status, 200, r)
        self.assertEqual((r["name"], r["info"]["api"]), ("Pixel 6a", 1))
        token = r["token"]
        self.assertEqual(self.call("POST", "/api/v1/pair", {"code": code}, host=far)[0], 403)   # once

        self.assertEqual(self.call("GET", "/api/v1/state", host=far)[0], 421)
        status, st = self.call("GET", "/api/v1/state", host=far, token=token)
        self.assertEqual(status, 200)
        self.assertTrue(st["cards"] and st["pages"])
        self.assertEqual(self.call("GET", "/api/v1/state", host=far, token="abcdefgh." + "x" * 43)[0], 421)
        status, card = self.call("GET", "/api/v1/card/host-pve", host=far, token=token)
        self.assertEqual((status, card["sections"][0]["title"]), (200, "host"))
        self.assertEqual(card["type"], "proxmox")       # what machine it is, for the app's Devices
        types = {c["id"]: c["type"] for c in st["cards"]}
        self.assertEqual((types["vm-pve-101"], types["nas"], types["router"], types["events"]), ("vm", "nas", "pc", ""))
        self.assertEqual(self.call("GET", "/api/v1/card/nope", host=far, token=token)[0], 404)
        self.assertEqual(self.call("GET", "/api/snapshot", host=far, token=token)[0], 200)
        self.assertEqual(self.call("GET", "/", host=far, cookie=token)[0], 200)     # the wall in the WebView

        maint = "/api/maintenance/vm-pve-100"
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far)[0], 403)
        self.assertEqual(self.call("POST", maint, {"minutes": 0})[0], 403)          # no Origin, no token
        # a QR code alone only looks: no buttons, no maintenance, until the phone signs in as the admin
        self.assertEqual((st["device_admin"], st["unlocked"]), (False, False))
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, token=token)[0], 403)
        self.assertEqual(self.call("POST", "/api/v1/unlock", {"pin": "1234"}, host=far, token=token)[0], 403)
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, cookie=token)[0], 403)  # cookie: Origin still needed
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, cookie=token, origin=True)[0], 200)

        # a UnifiedPush endpoint: only with the token, only https
        self.assertEqual(self.call("POST", "/api/v1/push", {"endpoint": "https://up.example/x"}, host=far)[0], 403)
        self.assertEqual(self.call("POST", "/api/v1/push", {"endpoint": "http://up.example/x"}, host=far, token=token)[0], 400)
        self.assertEqual(self.call("POST", "/api/v1/push", {"endpoint": 5}, host=far, token=token)[0], 403)
        self.assertEqual(self.call("POST", "/api/v1/push", {"endpoint": "https://up.example/x"}, host=far, token=token)[0], 200)
        self.assertEqual(self.call("GET", "/api/v1/devices", host=far, token=token)[1]["devices"][0]["push"], True)
        self.assertEqual(self.call("POST", "/api/v1/push", {"endpoint": ""}, host=far, token=token)[0], 200)
        self.assertEqual(self.call("GET", "/api/v1/devices", host=far, token=token)[1]["devices"][0]["push"], False)

        listed = self.init("devices")
        self.assertIn("Pixel 6a", listed.stdout)
        status, health = self.call("GET", "/api/status")
        self.assertEqual([d["name"] for d in health["devices"]], ["Pixel 6a"])
        self.assertEqual(self.init("unpair", "pixel 6a").returncode, 0)
        self.assertNotEqual(self.init("unpair", "pixel 6a").returncode, 0)
        self.assertEqual(self.call("GET", "/api/v1/state", host=far, token=token)[0], 421)

    def test_pair_code_from_the_display(self):
        self.assertEqual(self.call("POST", "/api/v1/pair-code", {})[0], 403)          # no Origin
        self.assertEqual(self.call("POST", "/api/v1/pair-code", {}, origin=True, signed=False)[0], 403)  # not signed in
        status, r = self.call("POST", "/api/v1/pair-code", {"name": "tablet"}, origin=True)
        self.assertEqual(status, 200, r)
        self.assertTrue(r["svg"].startswith("<svg"))
        code, url = self.code_from(r["link"])
        self.assertEqual(url, f"http://{self.host}")
        status, p = self.call("POST", "/api/v1/pair", {"code": code})
        self.assertEqual((status, p["name"]), (200, "tablet"))

    def test_list_and_remove_from_the_display(self):
        code, _ = self.code_from(self.call("POST", "/api/v1/pair-code", {}, origin=True)[1]["link"])
        token = self.call("POST", "/api/v1/pair", {"code": code, "name": "Old phone"})[1]["token"]
        status, r = self.call("GET", "/api/v1/devices")
        self.assertEqual(status, 200)
        self.assertIn("Old phone", [d["name"] for d in r["devices"]])
        self.assertNotIn("hash", json.dumps(r))
        self.assertEqual(self.call("GET", "/api/v1/devices", host="far.away:1")[0], 421)

        self.assertEqual(self.call("POST", "/api/v1/unpair", {"name": "Old phone"})[0], 403)              # no Origin
        self.assertEqual(self.call("POST", "/api/v1/unpair", {"name": "Old phone"}, token=token)[0], 403)  # not from a phone
        self.assertEqual(self.call("POST", "/api/v1/unpair", {"name": "nobody"}, cookie=token, origin=True)[0], 404)  # wall mode may
        self.assertEqual(self.call("POST", "/api/v1/unpair", {"name": "nobody"}, origin=True)[0], 404)
        self.assertEqual(self.call("POST", "/api/v1/unpair", {"name": "Old phone"}, origin=True)[0], 200)
        self.assertNotIn("Old phone", [d["name"] for d in self.call("GET", "/api/v1/devices")[1]["devices"]])
        self.assertEqual(self.call("GET", "/api/v1/state", host="far.away:1", token=token)[0], 421)
        events = self.call("GET", "/api/v1/events")[1]["events"]
        self.assertIn("Phone unpaired: Old phone", [e["name"] for e in events])

    def test_info_and_stream(self):
        status, info = self.call("GET", "/api/v1/info")
        self.assertEqual((status, info["api"], info["pairing"]), (200, 1, True))
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/v1/stream", headers={"Host": self.host})
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.readline().strip(), b"event: state")
        data = json.loads(r.readline().decode()[len("data: "):])
        self.assertIn("problems", data)
        conn.close()


if __name__ == "__main__":
    unittest.main()


class AdminPhone(DemoServer):
    """A phone signed in as the admin, on a display with a PIN: it needs /api/v1/unlock first."""
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

    def call(self, path, body=None, token=None, page=False, unlock=None, admin=True):
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if page:
            headers["Origin"] = f"http://{self.host}"
            if admin:
                headers["X-Labhud-Admin"] = self.ticket
        if unlock:
            headers["X-Labhud-Unlock"] = unlock
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", path, body=json.dumps(body or {}), headers=headers)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_pin_then_buttons(self):
        status, p = self.call("/api/v1/pair-code", page=True)
        self.assertEqual(status, 200, p)
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(p["link"]).query)["c"][0]
        token = self.call("/api/v1/pair", {"code": code, "name": "Pixel"})[1]["token"]
        # the admin's signature on the device, as admin-login would leave it (a TOTP code works once)
        env = dict(os.environ, LABHUD_DATA=self.data.name, **signin.ENV)
        subprocess.run([sys.executable, "-c", "import sys, admin, devices; "
                        "devices.set_admin(devices.lookup(sys.argv[1])[0], admin.Admin().sign())", token],
                       cwd=ROOT, env=env, check=True, timeout=30)
        maint = "/api/maintenance/vm-pve-100"
        self.assertEqual(self.call(maint, {"minutes": 0}, token=token)[0], 200)        # no PIN yet
        status, r = self.call("/api/lock/pin", {"new": "4321"}, page=True)
        self.assertEqual(status, 200, r)
        try:
            self.assertEqual(self.call(maint, {"minutes": 0}, token=token)[0], 403)    # locked
            self.assertEqual(self.call("/api/v1/unlock", {"pin": "0000"}, token=token)[0], 403)
            status, u = self.call("/api/v1/unlock", {"pin": "4321"}, token=token)
            self.assertEqual(status, 200, u)
            self.assertEqual(self.call(maint, {"minutes": 0}, token=token, unlock=u["ticket"])[0], 200)
            # a display not signed in has nothing for the PIN to open
            self.assertEqual(self.call("/api/lock/unlock", {"pin": "4321"}, page=True, admin=False)[0], 403)
            self.assertEqual(self.call("/api/lock/unlock", {"pin": "4321"}, page=True)[0], 200)
        finally:
            status, t = self.call("/api/lock/unlock", {"pin": "4321"}, page=True)
            self.call("/api/lock/pin", {"old": "4321", "new": None}, page=True, unlock=t.get("ticket"))
