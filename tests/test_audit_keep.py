"""[audit] keep_days (config.py, api/audit.py) and the role names of API tokens (api/tokens.py)."""

import os
import tempfile
import unittest
from unittest import mock

import config
import managed
import store
from api import audit, tokens

CFG = 'title = "Lab"\n\n[[page]]\nid = "main"\ntitle = "MAIN"\n\n  [[page.group]]\n  id = "g"\n  title = "G"\n\n    [[page.group.card]]\n    id = "a"\n    name = "A"\n    ping = "192.0.2.1"\n'


class FakeHandler:
    def _who(self):
        return "test"


class KeepDays(unittest.TestCase):
    def test_default_and_value(self):
        self.assertEqual(config.loads(CFG)["audit"], {"keep_days": 90})
        self.assertEqual(config.loads(CFG + "\n[audit]\nkeep_days = 30\n")["audit"], {"keep_days": 30})

    def test_refuses_bad_values(self):
        for v in ("6", "731", "true", '"30"', "30.5"):
            with self.subTest(v=v):
                with self.assertRaises(config.ConfigError):
                    config.loads(CFG + f"\n[audit]\nkeep_days = {v}\n")

    def test_unknown_key(self):
        with self.assertRaises(config.ConfigError):
            config.loads(CFG + "\n[audit]\nkeep = 30\n")

    def test_configure(self):
        self.addCleanup(audit.configure, None)
        audit.configure({"keep_days": 14})
        self.assertEqual(audit.KEEP_DAYS, 14)
        audit.configure(None)
        self.assertEqual(audit.KEEP_DAYS, 90)


class Settings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = os.path.join(self.tmp, "config.toml")
        with open(self.cfg, "w") as f:
            f.write(CFG)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data, LABHUD_CONFIG=self.cfg)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        r = mock.patch.object(audit, "record")
        self.record = r.start()
        self.addCleanup(r.stop)

    def test_save_and_read_back(self):
        managed.take_over()
        code, r = audit.audit_settings(FakeHandler(), {"keep_days": 45})
        self.assertEqual((code, r), (200, {"ok": True, "keep_days": 45}))
        self.assertEqual(managed.read_config()["audit"], {"keep_days": 45})
        self.record.assert_called_once()

    def test_refuses_bad_values(self):
        managed.take_over()
        for body in ({}, {"keep_days": 3}, {"keep_days": 1000}, {"keep_days": True}, {"keep_days": "30"}):
            with self.subTest(body=body):
                code, r = audit.audit_settings(FakeHandler(), body)
                self.assertEqual(code, 400)
                self.assertFalse(r["ok"])
        self.assertNotIn("audit", managed.read_config())


class Roles(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        for p in (mock.patch.object(store, "DIR", self.data), mock.patch.object(store, "_db", None),
                  mock.patch.object(store, "PROBLEM", None)):
            p.start()
            self.addCleanup(p.stop)

    def test_role_names_are_scopes(self):
        for role, scope in (("viewer", "read"), ("operator", "act"), ("admin", "admin")):
            with self.subTest(role=role):
                _, rec = tokens.make(f"t-{role}", role)
                self.assertEqual(rec["scope"], scope)
        listed = {t["name"]: t["role"] for t in tokens.listing()}
        self.assertEqual(listed, {"t-viewer": "viewer", "t-operator": "operator", "t-admin": "admin"})

    def test_old_scope_names_still_work(self):
        _, rec = tokens.make("old", "act")
        self.assertEqual(rec["scope"], "act")

    def test_bad_role(self):
        with self.assertRaises(ValueError):
            tokens.make("x", "root")


if __name__ == "__main__":
    unittest.main()
