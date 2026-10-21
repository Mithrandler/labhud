"""Small widgets: clock and calendar cards (drawn by the browser), a frame card (a page inside the card,
let through by the CSP only for the origins the config lists)."""

import os
import subprocess
import sys
import tempfile
import unittest

import config
from config import ConfigError

CARDS = '''
[[page]]
id = "main"
title = "MAIN"

  [[page.group]]
  id = "g"
  title = "G"

%s
'''


def load(card):
    text = CARDS % "\n".join("    " + line for line in ("[[page.group.card]]", 'id = "w"', 'name = "W"', *card.strip().splitlines()))
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(text)
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


class Widgets(unittest.TestCase):
    def test_clock_with_timezone(self):
        c = load('widget = "clock"\ntimezone = "Europe/Bucharest"')["cards"]["g"][0]
        self.assertEqual((c["widget"], c["timezone"]), ("clock", "Europe/Bucharest"))

    def test_calendar(self):
        self.assertEqual(load('widget = "calendar"')["cards"]["g"][0]["widget"], "calendar")

    def test_bad_widget_and_timezone(self):
        for card in ('widget = "radar"', 'widget = "clock"\ntimezone = "Mars/Base"'):
            with self.assertRaises(ConfigError, msg=card):
                load(card)

    def test_frame_needs_an_http_address_and_a_sane_height(self):
        c = load('frame = "https://grafana.lan:3000/d/abc?kiosk"\nframe_height = 300')["cards"]["g"][0]
        self.assertEqual((c["frame"], c["frame_height"]), ("https://grafana.lan:3000/d/abc?kiosk", 300))
        for card in ('frame = "javascript:alert(1)"', 'frame = "https://a.lan/x y"', 'frame = "file:///etc/passwd"',
                     'frame = "https://a.lan"\nframe_height = 5000', 'frame = "https://a.lan"\nframe_height = true',
                     'frame = "https://a.lan"\nwidget = "clock"'):
            with self.assertRaises(ConfigError, msg=card):
                load(card)

    def test_origin(self):
        self.assertEqual(config.frame_origin("https://a.lan:3000/d/x?y=1"), "https://a.lan:3000")
        self.assertIsNone(config.frame_origin(""))
        self.assertIsNone(config.frame_origin(None))

    def csp(self, card):
        """The policy a server started on a config with this card sends."""
        text = CARDS % "\n".join("    " + line for line in ("[[page.group.card]]", 'id = "w"', 'name = "W"', *card.strip().splitlines()))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            env = dict(os.environ, LABHUD_CONFIG=path, LABHUD_DATA=d)
            return subprocess.run([sys.executable, "-c", "import server; print(server._csp())"], env=env,
                                  capture_output=True, text=True, check=True, cwd=os.path.dirname(os.path.dirname(__file__))).stdout

    def test_csp_lists_only_the_cards_origins(self):
        csp = self.csp('frame = "https://a.lan:3000/d/x"')
        self.assertIn("frame-src https://a.lan:3000;", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertNotIn("frame-src", self.csp('widget = "clock"'))
