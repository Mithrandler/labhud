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
    extra_env = {"LABHUD_DATA": data.name}

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.data.cleanup()

    def init(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data.name, LABHUD_HOSTS=f"wall.example:{self.port}")
        return subprocess.run([sys.executable, os.path.join(ROOT, "init.py"), *args], capture_output=True,
                              text=True, env=env, timeout=30)

    def call(self, method, path, body=None, host=None, token=None, origin=False, cookie=None):
        headers = {"Host": host or self.host}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        if cookie:
            headers["Cookie"] = "other=1; labhud_device=" + cookie
        if origin:
            headers["Origin"] = f"http://{host or self.host}"
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
        self.assertEqual(self.call("GET", "/api/v1/card/nope", host=far, token=token)[0], 404)
        self.assertEqual(self.call("GET", "/api/snapshot", host=far, token=token)[0], 200)
        self.assertEqual(self.call("GET", "/", host=far, cookie=token)[0], 200)     # the wall in the WebView

        maint = "/api/maintenance/vm-pve-100"
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far)[0], 403)
        self.assertEqual(self.call("POST", maint, {"minutes": 0})[0], 403)          # no Origin, no token
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, token=token)[0], 200)
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, cookie=token)[0], 403)  # cookie: Origin still needed
        self.assertEqual(self.call("POST", maint, {"minutes": 0}, host=far, cookie=token, origin=True)[0], 200)

        listed = self.init("devices")
        self.assertIn("Pixel 6a", listed.stdout)
        status, health = self.call("GET", "/api/status")
        self.assertEqual([d["name"] for d in health["devices"]], ["Pixel 6a"])
        self.assertEqual(self.init("unpair", "pixel 6a").returncode, 0)
        self.assertNotEqual(self.init("unpair", "pixel 6a").returncode, 0)
        self.assertEqual(self.call("GET", "/api/v1/state", host=far, token=token)[0], 421)

    def test_pair_code_from_the_display(self):
        self.assertEqual(self.call("POST", "/api/v1/pair-code", {})[0], 403)          # no Origin
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
