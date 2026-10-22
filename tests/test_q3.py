"""Q3: the board's theme ([display] base / accent) and the pages each role sees ([roles.*]):
config validation, /api/v1/state by role (the alerts stay), and the routes through server.py
(refused without an admin, written, heard by the audit log)."""

import http.client
import json
import os
import shutil
import tempfile
import time
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

  [[page.group]]
  id = "g"
  title = "G"

    [[page.group.card]]
    id = "a"
    name = "A"
    ping = "192.0.2.1"

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
'''


def load(text):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


def refused(text):
    with unittest.TestCase().assertRaises(config.ConfigError) as cm:
        load(text)
    return "\n".join(cm.exception.problems)


class Theme(unittest.TestCase):
    def test_defaults(self):
        d = load(CFG)["display"]
        self.assertEqual((d["base"], d["accent"]), ("oled", "blue"))

    def test_set(self):
        d = load(CFG + '\n[display]\nbase = "grey"\naccent = "#AA00FF"\n')["display"]
        self.assertEqual((d["base"], d["accent"]), ("grey", "#aa00ff"))

    def test_bad_base_and_accent(self):
        self.assertIn("base must be one of", refused(CFG + '\n[display]\nbase = "white"\n'))
        self.assertIn("accent must be one of", refused(CFG + '\n[display]\naccent = "teal"\n'))
        self.assertIn("accent must be one of", refused(CFG + '\n[display]\naccent = "#abc"\n'))
        self.assertIn("accent must be one of", refused(CFG + '\n[display]\naccent = 5\n'))

    def test_in_state_and_public(self):
        t = load(CFG + '\n[display]\nbase = "grey"\naccent = "green"\n')
        self.assertEqual(cardstate.state(t, {})["theme"], {"base": "grey", "accent": "green"})
        self.assertEqual(config.public(t)["display"]["base"], "grey")


class Roles(unittest.TestCase):
    ROLES = '\n[roles.viewer]\npages = ["main"]\n'

    def test_valid(self):
        self.assertEqual(load(CFG + self.ROLES)["roles"], {"viewer": ["main"]})
        self.assertEqual(load(CFG)["roles"], {})

    def test_refused(self):
        self.assertIn("does not exist", refused(CFG + '\n[roles.viewer]\npages = ["nope"]\n'))
        self.assertIn("at least one", refused(CFG + '\n[roles.viewer]\npages = []\n'))
        self.assertIn("at least one", refused(CFG + '\n[roles.viewer]\n'))
        self.assertIn("roles", refused(CFG + '\n[roles.boss]\npages = ["main"]\n'))
        self.assertIn("unknown", refused(CFG + '\n[roles.viewer]\npages = ["main"]\ncolour = 1\n'))

    def test_view_per_role(self):
        t = load(CFG + '\n[roles.viewer]\nview = "glance"\n[roles.operator]\npages = ["main"]\nview = "actions"\n')
        self.assertEqual(t["roles"], {"operator": ["main"]})
        self.assertEqual(t["role_views"], {"viewer": "glance", "operator": "actions"})
        self.assertEqual(cardstate.state(t, {}, role="viewer")["view"], "glance")
        self.assertEqual(len(cardstate.state(t, {}, role="viewer")["pages"]), 2)   # no pages: every page
        self.assertIn("view must be", refused(CFG + '\n[roles.viewer]\nview = "huge"\n'))

    def test_state_by_role(self):
        t = load(CFG + self.ROLES)
        self.assertEqual([p["id"] for p in cardstate.state(t, {}, role="viewer")["pages"]], ["main"])
        self.assertEqual([c["id"] for c in cardstate.state(t, {}, role="viewer")["cards"]], ["a"])
        # a role with no table, and no role at all, see everything
        self.assertEqual([p["id"] for p in cardstate.state(t, {}, role="operator")["pages"]], ["main", "other"])
        self.assertEqual(len(cardstate.state(t, {})["cards"]), 2)

    def test_alerts_of_a_card_the_role_does_not_see_stay(self):
        t = load(CFG + self.ROLES)
        data = {"alerting": {"active": [{"id": "r1", "text": "D is down", "card": "d", "since": 1}]}}
        st = cardstate.state(t, data, role="viewer")
        self.assertNotIn("d", [c["id"] for c in st["cards"]])
        self.assertEqual([(a["id"], a["card"]) for a in st["alerts"]], [("r1", "d")])

    def test_state_names_the_role(self):
        t = load(CFG + self.ROLES)
        self.assertEqual(cardstate.state(t, {}, role="viewer")["role"], "viewer")
        self.assertNotIn("role", cardstate.state(t, {}))


class Routes(test_smoke.DemoServer):
    """The whole way through server.py: as no one, as the admin, and with a token of each role."""
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

    def call(self, method, path, body=None, ticket=None, token=None):
        h = {"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json"}
        if ticket:
            h["X-Labhud-Admin"] = ticket
        if token:
            h["Authorization"] = "Bearer " + token
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = conn.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        conn.close()
        return out

    def wait_state(self, token, ok):
        deadline = time.monotonic() + 20   # a save is applied by a reload
        while time.monotonic() < deadline:
            status, st = self.call("GET", "/api/v1/state", None, None, token)
            if status == 200 and ok(st):
                return st
            time.sleep(0.5)
        self.fail(f"the state never showed the change: {st}")

    def test_all(self):
        # without an admin: refused
        self.assertEqual(self.call("POST", "/api/boards/theme", {"base": "grey", "accent": "blue"})[0], 403)
        self.assertEqual(self.call("POST", "/api/boards/roles", {"role": "viewer", "pages": ["general"]})[0], 403)

        ticket = signin.sign_in(self.port, self.host)
        status, r = self.call("POST", "/api/setup/status", {}, ticket)
        if not r.get("managed"):
            self.assertEqual(self.call("POST", "/api/setup/take-over", {}, ticket)[0], 200)
            deadline = time.monotonic() + 20   # it restarts
            while time.monotonic() < deadline:
                try:
                    if self.call("POST", "/api/setup/status", {}, ticket)[1].get("managed"):
                        break
                except OSError:
                    pass
                time.sleep(0.5)

        # the theme: bad values refused, good ones written and shown by the state
        self.assertEqual(self.call("POST", "/api/boards/theme", {"base": "white", "accent": "blue"}, ticket)[0], 400)
        status, r = self.call("POST", "/api/boards/theme", {"base": "grey", "accent": "#112233"}, ticket)
        self.assertEqual(status, 200, r)
        st = self.wait_state(None, lambda s: s["theme"]["base"] == "grey")
        self.assertEqual(st["theme"], {"base": "grey", "accent": "#112233"})

        # the roles: one token per role
        tokens = {}
        for role in ("viewer", "operator", "admin"):
            status, r = self.call("POST", "/api/tokens/create", {"name": f"q3-{role}", "scope": role}, ticket)
            self.assertEqual(status, 200, r)
            tokens[role] = r["token"]
        self.assertEqual(self.call("GET", "/api/boards/roles")[0], 403)
        self.assertEqual(self.call("POST", "/api/boards/roles", {"role": "viewer", "pages": ["nope"]}, ticket)[0], 400)
        status, r = self.call("POST", "/api/boards/roles", {"role": "viewer", "pages": ["general"]}, ticket)
        self.assertEqual(status, 200, r)
        self.assertEqual(self.call("GET", "/api/boards/roles", None, ticket)[1]["roles"], {"viewer": ["general"]})
        st = self.wait_state(tokens["viewer"], lambda s: len(s["pages"]) == 1)
        self.assertEqual((st["pages"][0]["id"], st["role"]), ("general", "viewer"))
        # the other roles still see every page, and the viewer's card detail follows its pages
        self.assertGreater(len(self.call("GET", "/api/v1/state", None, None, tokens["operator"])[1]["pages"]), 1)
        admin_state = self.call("GET", "/api/v1/state", None, None, tokens["admin"])[1]
        self.assertEqual(admin_state["role"], "admin")
        hidden = next(c["id"] for c in admin_state["cards"] if c["id"] not in {x["id"] for x in st["cards"]})
        self.assertEqual(self.call("GET", f"/api/v1/card/{hidden}", None, None, tokens["viewer"])[0], 404)
        self.assertEqual(self.call("GET", f"/api/v1/card/{hidden}", None, None, tokens["admin"])[0], 200)
        # a viewer token cannot write them
        self.assertEqual(self.call("POST", "/api/boards/roles", {"role": "viewer", "pages": []}, None, tokens["viewer"])[0], 403)

        # and the audit heard it all, the refusals too
        status, aud = self.call("GET", "/api/v1/audit?limit=200", None, ticket)
        self.assertEqual(status, 200, aud)
        seen = {(a["action"], a["ok"]) for a in aud["rows"]}
        self.assertIn(("board-theme", True), seen)
        self.assertIn(("board-roles", True), seen)
        self.assertIn(("board-roles", False), seen)


for _name in [n for n in dir(test_smoke.DemoServer) if n.startswith("test_")]:
    setattr(Routes, _name, None)   # the demo's own smoke tests run in test_smoke


if __name__ == "__main__":
    unittest.main()
