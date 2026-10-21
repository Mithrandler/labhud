"""api/notifications.py: a channel's settings go to config.toml, its secrets to secrets.env (and
never into an answer); the matrix; alerts-save keeps [notify.*]."""

import os
import shutil
import tempfile
import tomllib
import unittest
from unittest import mock

import managed
import notify
from api import notifications

CFG = 'title = "Lab"\n\n[notify]\nvia = "both"\n\n[[page]]\nid = "main"\ntitle = "MAIN"\n\n  [[page.group]]\n  id = "g"\n  title = "G"\n\n    [[page.group.card]]\n    id = "a"\n    name = "A"\n    ping = "192.0.2.1"\n'


class H:
    def _who(self):
        return "test"


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        with open(os.path.join(self.tmp, "config.toml"), "w") as f:
            f.write(CFG)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env["LABHUD_DATA"] = self.tmp
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        saved = dict(notify.CFG)
        self.addCleanup(lambda: (notify.CFG.clear(), notify.CFG.update(saved)))

    def raw(self):
        with open(os.path.join(self.tmp, "config.toml"), "rb") as f:
            return tomllib.load(f)

    def test_email_config_and_password(self):
        code, r = notifications.save(H(), {"channel": "email", "config": {
            "host": "smtp.example.com", "user": "me@example.com", "to": "a@example.com, b@example.com"},
            "secrets": {"password": "smtp-pw"}})
        self.assertEqual(code, 200, r)
        self.assertEqual(self.raw()["notify"]["email"], {"host": "smtp.example.com", "user": "me@example.com",
                                                         "to": ["a@example.com", "b@example.com"]})
        self.assertEqual(self.raw()["notify"]["via"], "both")
        self.assertEqual(managed.read_secrets()["LABHUD_NOTIFY_SMTP_PASSWORD"], "smtp-pw")
        self.assertEqual(os.environ["LABHUD_NOTIFY_SMTP_PASSWORD"], "smtp-pw")
        view = notifications.view()
        self.assertTrue(view["channels"]["email"]["set"])
        self.assertIs(view["channels"]["email"]["password"], True)
        self.assertNotIn("smtp-pw", repr(view))

    def test_a_wrong_config_is_refused_and_nothing_written(self):
        code, r = notifications.save(H(), {"channel": "email", "config": {"host": "h", "to": "nope"}})
        self.assertEqual(code, 409)
        self.assertNotIn("email", self.raw()["notify"])

    def test_webhook_secret_only_and_forget(self):
        code, _ = notifications.save(H(), {"channel": "webhook", "secrets": {"url": "https://hooks.example.com/t/SECRET"}})
        self.assertEqual(code, 200)
        view = notifications.view()["channels"]["webhook"]
        self.assertEqual((view["set"], view["host"]), (True, "hooks.example.com"))
        self.assertNotIn("SECRET", repr(notifications.view()))
        self.assertEqual(notifications.save(H(), {"channel": "webhook", "secrets": {"url": "ftp://x"}})[0], 409)
        self.assertEqual(notifications.save(H(), {"channel": "webhook", "forget": True})[0], 200)
        self.assertNotIn("LABHUD_NOTIFY_WEBHOOK_URL", os.environ)
        self.assertNotIn("LABHUD_NOTIFY_WEBHOOK_URL", managed.read_secrets())

    def test_events(self):
        code, r = notifications.events(H(), {"events": {"alert": ["app", "discord"], "admin_login": ["email"]}})
        self.assertEqual(code, 200, r)
        ev = self.raw()["notify"]["events"]
        self.assertEqual(ev["alert"], ["app", "discord"])
        self.assertEqual(ev["host_down"], [])
        self.assertEqual(self.raw()["notify"]["via"], "app")
        self.assertEqual(notifications.events(H(), {"events": {"alert": ["pigeon"]}})[0], 400)
        self.assertEqual(notifications.events(H(), {"events": {"nope": ["app"]}})[0], 400)

    def test_test_route_refuses_ntfy_and_unknown(self):
        self.assertEqual(notifications.test(H(), {"channel": "ntfy"})[0], 400)
        code, r = notifications.test(H(), {"channel": "discord"})
        self.assertEqual(code, 200)
        self.assertFalse(r["ok"])
        self.assertIn("not set", r["error"])


if __name__ == "__main__":
    unittest.main()
