"""API tokens and the audit log on the real demo server: what each scope opens, what it does not,
and that both the refusals and the sign-ins land in the log (api/tokens.py, api/audit.py)."""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest

from tests import signin
from tests.test_smoke import DemoServer

DATA = tempfile.mkdtemp(prefix="labhud-g-")


class Access(DemoServer):
    extra_env = dict(signin.ENV, LABHUD_DATA=DATA)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.addClassCleanup(shutil.rmtree, DATA, True)
        with open(os.path.join(DATA, "cli.json")) as f:
            cls.cli = json.load(f)["key"]
        cls.tok = {s: cls.make(f"t-{s}", s)["token"] for s in ("read", "act", "admin")}

    @classmethod
    def call(cls, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=10)
        h = {"Host": cls.host, **(headers or {})}
        data = None
        if method == "POST":
            h["Content-Type"] = "application/json"
            data = json.dumps(body or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw or b"{}")
        except ValueError:
            return r.status, {}

    @classmethod
    def make(cls, name, scope, days=None):
        code, out = cls.call("POST", "/api/tokens/create", {"name": name, "scope": scope, "days": days},
                             {"X-Labhud-Cli": cls.cli})
        assert code == 200, out
        return out

    def bearer(self, scope, host=None):
        h = {"Authorization": "Bearer " + self.tok[scope]}
        if host:
            h["Host"] = host
        return h

    def audit(self, **q):
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        code, out = self.call("GET", "/api/v1/audit?" + qs, headers={"X-Labhud-Cli": self.cli})
        self.assertEqual(code, 200, out)
        return out["rows"]

    # --- the plan's check: read on state 200, on setup 403, and it is in the log ---

    def test_read_sees_state_not_setup(self):
        code, state = self.call("GET", "/api/v1/state", headers=self.bearer("read"))
        self.assertEqual(code, 200)
        self.assertIn("cards", state)
        self.assertEqual(self.call("POST", "/api/setup/status", {}, self.bearer("read"))[0], 403)
        self.assertEqual(self.call("POST", "/api/setup/save-text", {"text": "x"}, self.bearer("read"))[0], 403)
        rows = self.audit(who="t-read", action="setup/")
        self.assertTrue(rows)
        self.assertEqual({(r["who"], r["ok"]) for r in rows}, {("token t-read", False)})
        self.assertIn("setup/status", {r["action"] for r in rows})

    def test_token_stands_in_for_the_host_check(self):
        self.assertEqual(self.call("GET", "/api/v1/state", headers={"Host": "evil.example"})[0], 421)
        self.assertEqual(self.call("GET", "/api/v1/state", headers=self.bearer("read", "evil.example"))[0], 200)

    def test_bad_tokens_are_nobody(self):
        for t in ("lh_" + "A" * 43, "lh_short", self.tok["read"] + "x", self.tok["read"][3:]):
            with self.subTest(t=t):
                self.assertEqual(self.call("GET", "/api/v1/state", headers={"Authorization": "Bearer " + t,
                                                                           "Host": "evil.example"})[0], 421)
                self.assertEqual(self.call("POST", "/api/setup/status", {}, {"Authorization": "Bearer " + t})[0], 403)

    # --- act: the buttons, still not ⚙ ---

    def test_act_presses_buttons(self):
        for scope, want in (("read", 403), ("act", 200), ("admin", 200)):
            with self.subTest(scope=scope):
                code = self.call("POST", "/api/maintenance/vm-pve-100", {"minutes": 0}, self.bearer(scope))[0]
                self.assertEqual(code, want)
        self.assertEqual(self.call("POST", "/api/setup/status", {}, self.bearer("act"))[0], 403)

    def test_action_is_logged(self):
        self.assertEqual(self.call("POST", "/action/not-offered", {}, self.bearer("act"))[0], 403)
        rows = self.audit(action="action", who="t-act")
        self.assertTrue(any(r["target"] == "not-offered" and not r["ok"] for r in rows))

    # --- admin: ⚙'s routes and the audit itself ---

    def test_admin_manages(self):
        self.assertEqual(self.call("POST", "/api/setup/status", {}, self.bearer("admin"))[0], 200)
        self.assertEqual(self.call("POST", "/api/tokens/list", {}, self.bearer("admin"))[0], 200)
        for scope in ("read", "act"):
            self.assertEqual(self.call("POST", "/api/tokens/list", {}, self.bearer(scope))[0], 403)

    def test_audit_needs_admin(self):
        self.assertEqual(self.call("GET", "/api/v1/audit")[0], 403)
        self.assertEqual(self.call("GET", "/api/v1/audit", headers=self.bearer("read"))[0], 403)
        self.assertEqual(self.call("GET", "/api/v1/audit", headers=self.bearer("act"))[0], 403)
        code, out = self.call("GET", "/api/v1/audit?limit=5", headers=self.bearer("admin"))
        self.assertEqual(code, 200)
        self.assertLessEqual(len(out["rows"]), 5)
        self.assertEqual(self.call("GET", "/api/v1/audit?from=yesterday", headers=self.bearer("admin"))[0], 400)

    def test_list_never_shows_a_token(self):
        out = self.call("POST", "/api/tokens/list", {}, {"X-Labhud-Cli": self.cli})[1]
        text = json.dumps(out)
        for t in self.tok.values():
            self.assertNotIn(t[3:], text)
        self.assertNotIn("hash", text)
        self.assertLessEqual({"t-read", "t-act", "t-admin"}, {t["name"] for t in out["tokens"]})

    def test_revoke_ends_it(self):
        made = self.make("short-lived", "read")
        h = {"Authorization": "Bearer " + made["token"], "Host": "evil.example"}
        self.assertEqual(self.call("GET", "/api/v1/state", headers=h)[0], 200)
        self.assertEqual(self.call("POST", "/api/tokens/revoke", {"id": made["id"]}, {"X-Labhud-Cli": self.cli})[0], 200)
        self.assertEqual(self.call("GET", "/api/v1/state", headers=h)[0], 421)
        self.assertTrue(self.audit(action="token-revoke"))

    def test_sign_ins_are_logged_without_secrets(self):
        signin.sign_in(self.port, self.host)
        rows = self.audit(action="sign-in", ok="1")
        self.assertTrue(any(r["target"] == "password" and r["who"] == "admin" for r in rows))
        raw = ""
        for n in os.listdir(os.path.join(DATA, "audit")):
            with open(os.path.join(DATA, "audit", n)) as f:
                raw += f.read()
        self.assertNotIn("correct horse", raw)
        for t in self.tok.values():
            self.assertNotIn(t, raw)

    def test_quiet_reads_only_when_refused(self):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        self.call("POST", "/api/agents/list", {}, self.bearer("admin"))
        self.call("POST", "/api/agents/list", {}, self.bearer("read"))
        rows = self.audit(action="agents/list", **{"from": stamp})
        self.assertEqual([r["ok"] for r in rows], [False])


if __name__ == "__main__":
    unittest.main()
