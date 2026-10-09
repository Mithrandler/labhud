"""include = [...]: pages kept in files of their own."""

import os
import tempfile
import unittest

import config

MAIN = '''title = "T"
include = ["pages/*.toml"]

[[page]]
id = "general"
title = "GENERAL"
  [[page.group]]
  id = "g1"
  title = "G1"
'''
MEDIA = '''[[page]]
id = "media"
title = "MEDIA"
  [[page.group]]
  id = "g2"
  title = "G2"
    [[page.group.card]]
    id = "jf"
    name = "Jellyfin"
    ping = "192.0.2.5"

[action.wake-nas]
label = "Wake"
'''


class Include(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.dir, "pages"))
        self.main = os.path.join(self.dir, "config.toml")
        self.write("config.toml", MAIN)
        self.write("pages/20-media.toml", MEDIA)

    def write(self, name, text):
        with open(os.path.join(self.dir, name), "w") as f:
            f.write(text)

    def test_pages_are_added_in_order(self):
        t = config.load(self.main)
        self.assertEqual([p["id"] for p in t["pages"]], ["general", "media"])
        self.assertEqual(t["cards"]["g2"][0]["id"], "jf")
        self.assertIn("wake-nas", t["actions"])
        self.assertEqual(config.included(self.main), [os.path.join(self.dir, "pages", "20-media.toml")])

    def test_what_an_include_may_not_hold(self):
        self.write("pages/30-bad.toml", 'title = "nope"\n')
        with self.assertRaises(config.ConfigError) as e:
            config.load(self.main)
        self.assertIn("30-bad.toml: 'title' belongs in config.toml", "\n".join(e.exception.problems))

    def test_duplicates_are_caught(self):
        self.write("pages/30-dup.toml", MEDIA.replace('id = "media"', 'id = "other"'))
        with self.assertRaises(config.ConfigError) as e:
            config.load(self.main)
        text = "\n".join(e.exception.problems)
        self.assertIn("[action.wake-nas] is already defined", text)


if __name__ == "__main__":
    unittest.main()
