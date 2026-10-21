"""util.py: the helpers the stores of phones, agents, tokens and sign-ins share."""

import re
import unittest

import util

NAME = re.compile(r"[\w .-]{1,40}")


class Helpers(unittest.TestCase):
    def test_hash_is_sha256_of_the_text(self):
        self.assertEqual(util.sha256_hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(util.sha256_hex(5), util.sha256_hex("5"))

    def test_clean_name(self):
        self.assertEqual(util.clean_name("  Pixel   6a ", NAME, "phone"), "Pixel 6a")
        self.assertEqual(util.clean_name("", NAME, "phone"), "phone")
        self.assertEqual(util.clean_name(None, NAME), "")
        self.assertEqual(util.clean_name("a<b", NAME, "x"), "x")
        self.assertEqual(util.clean_name("pve2.lan", NAME, "agent", dots=True), "pve2-lan")
        self.assertEqual(len(util.clean_name("x" * 90, NAME)), 40)

    def test_unique_name(self):
        self.assertEqual(util.unique_name("tablet", []), "tablet")
        self.assertEqual(util.unique_name("tablet", ["Tablet"]), "tablet 2")
        self.assertEqual(util.unique_name("tablet", ["tablet", "TABLET 2"]), "tablet 3")


if __name__ == "__main__":
    unittest.main()
