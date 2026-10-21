"""Fully Kiosk control: what the screen should be, and that only changes are sent."""

import unittest
from unittest import mock

import screen


class Screen(unittest.TestCase):
    def setUp(self):
        screen.state.update(sent=None, error=None, wake_until=0)

    def test_wanted(self):
        night = {"from": 23, "to": 7, "dim": 0}
        with mock.patch.object(screen, "in_window", lambda w: True):
            self.assertEqual(screen.wanted(night, now=100), ("off", None))
            self.assertEqual(screen.wanted(dict(night, dim=40), now=100), ("dim", 102))
            screen.state["wake_until"] = 200
            self.assertEqual(screen.wanted(night, now=100)[0], "on")  # a new problem lights it
        with mock.patch.object(screen, "in_window", lambda w: False), mock.patch.object(screen, "DAY_BRIGHTNESS", "200"):
            self.assertEqual(screen.wanted(night, now=100), ("on", 200))

    def test_commands(self):
        sent = []
        with mock.patch.object(screen, "request", lambda url, timeout: sent.append(url) or {"status": "OK"}), \
                mock.patch.object(screen, "URL", "http://192.0.2.90:2323"), mock.patch.object(screen, "PASSWORD", "p w"):
            screen.apply(("dim", 77))
            screen.apply(("off", None))
        self.assertIn("cmd=screenOn", sent[0])
        self.assertIn("key=screenBrightness&value=77", sent[1])
        self.assertIn("password=p+w", sent[1])
        self.assertIn("cmd=screenOff", sent[2])

    def test_refusal(self):
        with mock.patch.object(screen, "request", lambda url, timeout: {"status": "Error", "statustext": "wrong password"}), \
                self.assertRaises(RuntimeError):
            screen.apply(("off", None))


if __name__ == "__main__":
    unittest.main()
