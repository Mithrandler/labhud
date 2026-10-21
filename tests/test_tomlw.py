"""tomlw.py writes what tomllib reads back the same: the demo's config and config.example.toml."""

import os
import tomllib
import unittest

import config
import tomlw
from tests.test_smoke import ROOT


class RoundTrip(unittest.TestCase):
    def test_configs(self):
        for name in ("demo/config.toml", "config.example.toml"):
            with self.subTest(name=name):
                with open(os.path.join(ROOT, name), "rb") as f:
                    raw = tomllib.load(f)
                text = tomlw.dumps(raw)
                self.assertEqual(tomllib.loads(text), raw)
                config.loads(text, os.path.join(ROOT, name))   # still a valid config

    def test_values(self):
        data = {"a b": 'say "hi"\n\tok', "n": 3, "f": 0.5, "t": True, "empty": {}, "list": [1, [2, "x"]],
                "inline": [{"k": "v"}], "nested": {"deep": {"x": 1}}, "many": [{"x": 1, "sub": [{"y": 2}]}, {"x": 3}]}
        self.assertEqual(tomllib.loads(tomlw.dumps(data)), data)
        with self.assertRaises(TypeError):
            tomlw.value(object())
