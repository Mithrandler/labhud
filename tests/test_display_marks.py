"""api/display.py: ⚙ > Alerts > Thresholds writes [display] warn/crit into the kept config."""

import os
import tempfile
import unittest
from unittest import mock

import managed
from api import display

CFG = 'title = "Lab"\n\n[[page]]\nid = "main"\ntitle = "MAIN"\n\n  [[page.group]]\n  id = "g"\n  title = "G"\n\n    [[page.group.card]]\n    id = "a"\n    name = "A"\n    ping = "192.0.2.1"\n'


class FakeHandler:
    def _who(self):
        return "test"


class Marks(unittest.TestCase):
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
        self.h = FakeHandler()

    def test_nothing_sent(self):
        code, r = display.save_marks(self.h, {})
        self.assertEqual(code, 400)
        self.assertFalse(r["ok"])

    def test_save_needs_take_over(self):
        code, r = display.save_marks(self.h, {"warn": 70, "crit": 90})
        self.assertEqual(code, 409)
        self.assertFalse(r["ok"])

    def test_save_and_read_back(self):
        managed.take_over()
        code, r = display.save_marks(self.h, {"warn": 70, "crit": 90})
        self.assertEqual((code, r), (200, {"ok": True, "warn": 70, "crit": 90}))
        self.assertEqual(managed.read_config()["display"], {"warn": 70, "crit": 90})

    def test_refuses_bad_values(self):
        managed.take_over()
        for body in ({"warn": 90, "crit": 80}, {"warn": 0, "crit": 50},
                     {"warn": 50, "crit": 101}, {"warn": "50", "crit": 90},
                     {"warn": True, "crit": 90}, {"warn": 50}):
            with self.subTest(body=body):
                code, r = display.save_marks(self.h, body)
                self.assertEqual(code, 400)
                self.assertFalse(r["ok"])
        self.assertNotIn("display", managed.read_config())

    def test_icons(self):
        code, r = display.icons(self.h, {})
        self.assertEqual(code, 200)
        self.assertIn("proxmox", r["icons"])
        self.assertEqual(r["icons"], sorted(set(r["icons"])))


if __name__ == "__main__":
    unittest.main()
