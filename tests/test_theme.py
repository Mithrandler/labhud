"""The wall's look lives in design/tokens.json: static/app.css may not grow colours or sizes of its own."""
import os
import re
import unittest

CSS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "app.css")

# What the hand-written part of app.css still spells out (a ceiling to bring down, never to raise):
# a new `#hex` or `NNpx` makes this fail, so a value goes into design/tokens.json instead.
MAX_HEX = 0
MAX_PX = 631


def hand_written():
    text = open(CSS, encoding="utf-8").read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    # the generated blocks (colours, scale) are the tokens themselves
    text = re.sub(r"\n\s*--[\w-]+:\s*[^;]+;", "\n", text)
    return text


class ThemeTest(unittest.TestCase):
    def test_no_hex_colours(self):
        found = re.findall(r"#[0-9a-fA-F]{3,8}\b(?![^{]*\{)", hand_written())
        self.assertLessEqual(len(found), MAX_HEX, found)

    def test_px_values_do_not_grow(self):
        n = len(re.findall(r"\d+px", hand_written()))
        self.assertLessEqual(n, MAX_PX, f"{n} px values in app.css: put the new one in design/tokens.json")


if __name__ == "__main__":
    unittest.main()
