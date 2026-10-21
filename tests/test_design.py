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
        # ⚙'s sections: register_section({ id: "x", …, icon: "y" }) in any script; the icon is the id's
        # when none is named (settings/registry.js)
        js = ""
        for root, _, files in os.walk(os.path.join(ROOT, "static")):
            for name in files:
                if name.endswith(".js"):
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        js += f.read()
        js = re.sub(r"/\*.*?\*/", "", js, flags=re.S)   # registry.js's example is a comment
        # each call up to its render (whose body has braces of its own), or its end
        sections = [x.split("render:")[0] for x in re.findall(r"register_section\(\{(.*?)\}\);", js, re.S)]
        ids = [re.search(r'id: "([a-z0-9-]+)"', x).group(1) for x in sections]
        for want in ("look", "dashboard", "hosts", "containers", "integrations", "alerts", "notifications",
                     "security", "phones", "api", "system"):
            self.assertIn(want, ids)
        for x in sections:
            own = re.search(r'icon: "([a-z_]+)"', x)
            wanted.add(own.group(1) if own else re.search(r'id: "([a-z0-9-]+)"', x).group(1))
        wanted |= {"person"}
        self.assertEqual(wanted - set(icons), set())


if __name__ == "__main__":
    unittest.main()
