"""design/tokens.json is the one place for the colours and icons: what it makes must be in the repo."""

import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "design"))
import build  # noqa: E402


class Design(unittest.TestCase):
    def test_files_are_made_from_the_tokens(self):
        self.assertEqual(build.stale(), [], "run python3 design/build.py")

    def test_every_icon_becomes_paths(self):
        for name, markup in build.load()["icons"].items():
            for d, _ in build.shapes(markup):
                self.assertRegex(d, r"^[Mm][-\d.]", name)

    def test_shapes_as_paths(self):
        self.assertEqual(build.as_path("circle", {"cx": "12", "cy": "8", "r": "2"}), "M10 8a2 2 0 1 0 4 0a2 2 0 1 0 -4 0Z")
        self.assertEqual(build.as_path("rect", {"x": "1", "y": "2", "width": "3", "height": "4"}), "M1 2h3v4h-3Z")
        self.assertTrue(build.as_path("rect", {"x": "0", "y": "0", "width": "4", "height": "4", "rx": "1"}).startswith("M1 0H3A1 1"))

    def test_icons_the_pages_ask_for_exist(self):
        icons = build.load()["icons"]
        with open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8") as f:
            wanted = set(re.findall(r'data-icon="([a-z_]+)"', f.read()))
        with open(os.path.join(ROOT, "static", "settings.js"), encoding="utf-8") as f:
            js = f.read()
        aliases = json.loads(re.search(r"const TAB_ICONS = (\{[^}]*\})", js).group(1).replace("alerts", '"alerts"'))
        groups = re.search(r"const SETTINGS_GROUPS = \[(.*?)\n\];", js, re.S).group(1)
        tabs = re.findall(r'\["([a-z]+)", "[A-Z]', groups)
        self.assertIn("security", tabs)
        wanted |= {aliases.get(k, k) for k in tabs}
        wanted |= {"person"}
        self.assertEqual(wanted - set(icons), set())


if __name__ == "__main__":
    unittest.main()
