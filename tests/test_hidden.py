"""Hidden cards and groups and a page's columns and density: /api/v1/state leaves the hidden out
(and keeps the alerts about them), ⚙ saves them through /api/setup/layout-save for admins only,
and the audit log hears it."""

import http.client
import json
import os
import shutil
import tempfile
import time
import tomllib
import unittest

import cardstate
import config
from tests import signin
from tests import test_smoke
from tests.test_oidc import demo_config

CFG = '''
[[page]]
id = "main"
title = "MAIN"
columns = 2
density = "compact"

  [[page.group]]
  id = "g"
  title = "G"

    [[page.group.card]]
    id = "a"
    name = "A"
    ping = "192.0.2.1"

    [[page.group.card]]
    id = "b"
    name = "B"
    ping = "192.0.2.2"
    hidden = true

  [[page.group]]
  id = "h"
  title = "H"
  hidden = true

    [[page.group.card]]
    id = "c"
    name = "C"
    ping = "192.0.2.3"

[[page]]
id = "other"
title = "OTHER"

  [[page.group]]
  id = "o"
  title = "O"

    [[page.group.card]]
    id = "d"
    name = "D"
    ping = "192.0.2.4"
    hidden = true
'''


def load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


class State(unittest.TestCase):
    def setUp(self):
        self.topology = load(CFG)
        self.data = {"alerting": {"active": [{"id": "r1", "text": "B is down", "card": "b", "since": 1}]},
                     "checks": {}}

    def test_hidden_are_not_in_the_state(self):
        st = cardstate.state(self.topology, self.data)
        self.assertEqual([c["id"] for c in st["cards"]], ["a"])
        main, other = st["pages"]
        self.assertEqual([(g["id"], g["cards"]) for g in main["groups"]], [("g", ["a"])])
        self.assertEqual(other["groups"], [])   # every card of its one group is hidden

    def test_problems_leave_out_hidden_cards(self):
        st = cardstate.state(self.topology, self.data)
        self.assertNotIn("b", st["problems"])

    def test_alerts_of_a_hidden_card_stay(self):
        st = cardstate.state(self.topology, self.data)
        self.assertEqual([(a["id"], a["card"]) for a in st["alerts"]], [("r1", "b")])

    def test_columns_and_density_in_the_state(self):
        main, other = cardstate.state(self.topology, self.data)["pages"]
        self.assertEqual((main["columns"], main["density"]), (2, "compact"))
        self.assertNotIn("columns", other)
        self.assertNotIn("density", other)

    def test_nothing_hidden_changes_nothing(self):
        plain = load(CFG.replace("hidden = true", "").replace('columns = 2\ndensity = "compact"', ""))
        st = cardstate.state(plain, self.data)
        self.assertEqual([c["id"] for c in st["cards"]], ["a", "b", "c", "d"])
        self.assertTrue(all("columns" not in p for p in st["pages"]))


class Routes(test_smoke.DemoServer):
    """The whole way through server.py, as an admin and as no one."""
    data = tempfile.TemporaryDirectory()
    extra_env = {"LABHUD_DATA": data.name, **signin.ENV}

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(cls.data.name, "config.toml"), "w", encoding="utf-8") as f:
            f.write(demo_config(""))
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.data.name, ignore_errors=True)

    def call(self, method, path, body=None, ticket=None):
        h = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        if ticket:
            h["X-Labhud-Admin"] = ticket
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def test_all(self):
        # without an admin: refused, nothing written
        self.assertEqual(self.call("POST", "/api/setup/layout", {})[0], 403)
        self.assertEqual(self.call("POST", "/api/setup/layout-save", {"pages": []})[0], 403)

        ticket = signin.sign_in(self.port, self.host)
        status, r = self.call("POST", "/api/setup/status", {}, ticket)
        if not r.get("managed"):
            status, r = self.call("POST", "/api/setup/take-over", {}, ticket)
            self.assertEqual(status, 200, r)
            deadline = time.monotonic() + 20   # it restarts
            while time.monotonic() < deadline:
                try:
                    if self.call("POST", "/api/setup/status", {}, ticket)[1].get("managed"):
                        break
                except OSError:
                    pass
                time.sleep(0.5)
        status, lay = self.call("POST", "/api/setup/layout", {}, ticket)
        self.assertEqual(status, 200, lay)
        pages = lay["pages"]
        first = pages[0]
        card = first["groups"][0]["cards"][0]
        group = first["groups"][-1]
        self.assertFalse(card["hidden"])
        self.assertEqual((first["columns"], first["density"]), (0, ""))

        def body(card_hidden, group_hidden, columns, density):
            return [{"id": p["id"], "title": p["title"], "columns": columns if p is first else 0,
                     "density": density if p is first else "",
                     "groups": [{"id": g["id"], "title": g["title"], "hidden": group_hidden if g is group else False,
                                 "cards": [{"id": c["id"], "hidden": card_hidden if c is card else False} for c in g["cards"]]}
                                for g in p["groups"]]} for p in pages]

        # bad values are refused and write nothing
        self.assertEqual(self.call("POST", "/api/setup/layout-save", {"pages": body(False, False, 9, "")}, ticket)[0], 409)
        self.assertEqual(self.call("POST", "/api/setup/layout-save", {"pages": body(False, False, 2, "airy")}, ticket)[0], 409)
        status, r = self.call("POST", "/api/setup/layout-save", {"pages": body(True, True, 3, "compact")}, ticket)
        self.assertEqual(status, 200, r)

        with open(os.path.join(self.data.name, "config.toml"), "rb") as f:
            raw = tomllib.load(f)
        self.assertTrue(raw["page"][0]["group"][0]["card"][0]["hidden"])
        self.assertTrue(raw["page"][0]["group"][-1]["hidden"])
        self.assertEqual((raw["page"][0]["columns"], raw["page"][0]["density"]), (3, "compact"))

        # the state leaves them out and says the page's columns and density (a reload: wait for it)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, st = self.call("GET", "/api/v1/state", None, ticket)
            if status == 200 and st["pages"][0].get("columns") == 3:
                break
            time.sleep(0.5)
        self.assertEqual(status, 200, st)
        self.assertEqual((st["pages"][0]["columns"], st["pages"][0]["density"]), (3, "compact"))
        ids = {c["id"] for c in st["cards"]}
        self.assertNotIn(card["id"], ids)
        self.assertFalse(ids & {c["id"] for c in group["cards"]})
        self.assertNotIn(group["id"], [g["id"] for g in st["pages"][0]["groups"]])

        # and the audit heard the save, the refusals too
        status, aud = self.call("GET", "/api/v1/audit?limit=100", None, ticket)
        self.assertEqual(status, 200, aud)
        saves = [(a["action"], a["ok"]) for a in aud["rows"] if a["action"] == "setup/layout-save"]
        self.assertIn(("setup/layout-save", True), saves)
        self.assertIn(("setup/layout-save", False), saves)


def _skip_smoke_tests(cls):
    """The demo's own smoke tests run in test_smoke, not again here."""
    for name in [n for n in dir(test_smoke.DemoServer) if n.startswith("test_")]:
        setattr(cls, name, None)


_skip_smoke_tests(Routes)


if __name__ == "__main__":
    unittest.main()
