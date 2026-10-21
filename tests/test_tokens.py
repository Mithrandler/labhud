"""api/tokens.py: make / lookup / listing / revoke / allows, with the store behind them."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import store
from api import tokens

TOKEN_RE = re.compile(r"lh_[A-Za-z0-9_-]{43}")


class Tokens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

        # Cleanups run LIFO: rmtree (registered first) runs last, close (registered last) runs first.
        self.addCleanup(shutil.rmtree, self.tmp, True)

        p_dir = mock.patch.object(store, "DIR", self.tmp)
        p_dir.start()
        self.addCleanup(p_dir.stop)

        p_db = mock.patch.object(store, "_db", None)
        p_db.start()
        self.addCleanup(p_db.stop)

        p_prob = mock.patch.object(store, "PROBLEM", None)
        p_prob.start()
        self.addCleanup(p_prob.stop)

        def _close_db():
            if store._db is not None:
                store._db.close()
        self.addCleanup(_close_db)

        tokens._used.clear()

    # -- make ---------------------------------------------------------------

    def test_make_shape(self):
        token, rec = tokens.make("Test", "read", now=1000)
        self.assertTrue(token.startswith("lh_"))
        self.assertTrue(TOKEN_RE.fullmatch(token))
        self.assertEqual(set(rec), {"id", "name", "scope", "role", "made", "expires", "last", "ip", "expired"})
        self.assertEqual(rec["name"], "Test")
        self.assertEqual(rec["scope"], "read")
        self.assertEqual(rec["made"], 1000)
        self.assertEqual(rec["expires"], 0)
        self.assertEqual(rec["last"], 0)
        self.assertEqual(rec["ip"], "")
        self.assertFalse(rec["expired"])

    def test_make_shape_with_expiry(self):
        token, rec = tokens.make("Timed", "act", days=7, now=2000)
        self.assertEqual(rec["expires"], 2000 + 7 * 86400)

    def test_make_refuses(self):
        with self.assertRaises(ValueError):
            tokens.make("", "read")
        with self.assertRaises(ValueError):
            tokens.make("<script>", "read")
        with self.assertRaises(ValueError):
            tokens.make("ok", "bogus")
        for bad_days in (-1, 4000, "7", True):
            with self.subTest(days=bad_days):
                with self.assertRaises(ValueError):
                    tokens.make("ok", "read", days=bad_days)
        tokens.make("CI", "read")
        with self.assertRaises(ValueError):
            tokens.make("ci", "read")

    def test_max_tokens(self):
        for i in range(tokens.MAX_TOKENS):
            tokens.make(f"tok {i}", "read")
        with self.assertRaises(ValueError):
            tokens.make("one too many", "read")

    # -- lookup -------------------------------------------------------------

    def test_lookup(self):
        token, rec = tokens.make("Find me", "read")
        found = tokens.lookup(token)
        self.assertIsNotNone(found)
        tid, r = found
        self.assertEqual(tid, rec["id"])
        self.assertEqual(r["name"], "Find me")

        self.assertIsNone(tokens.lookup("lh_" + "b" * 43))
        self.assertIsNone(tokens.lookup(None))
        self.assertIsNone(tokens.lookup(123))
        self.assertIsNone(tokens.lookup("nope"))

    def test_expiry(self):
        token, _ = tokens.make("Fleeting", "read", days=1, now=1000)
        self.assertIsNotNone(tokens.lookup(token, now=1000 + 10))
        self.assertIsNone(tokens.lookup(token, now=1000 + 86400 + 1))

    def test_only_hash_kept(self):
        token, _ = tokens.make("Secret", "read")
        raw = store.load(tokens.KEY)
        self.assertIsInstance(raw, dict)
        rec = list(raw.values())[0]
        self.assertEqual(rec["hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, json.dumps(raw))

    # -- listing ------------------------------------------------------------

    def test_listing_hides_hash(self):
        tokens.make("First", "read", now=100)
        tokens.make("Second", "act", days=1, now=200)
        listing = tokens.listing(now=9999999999)
        self.assertEqual(len(listing), 2)
        self.assertEqual(listing[0]["name"], "First")
        self.assertEqual(listing[1]["name"], "Second")
        for entry in listing:
            self.assertNotIn("hash", entry)
        self.assertFalse(listing[0]["expired"])
        self.assertTrue(listing[1]["expired"])

    # -- revoke -------------------------------------------------------------

    def test_revoke(self):
        token, rec = tokens.make("Bye", "read")
        self.assertEqual(tokens.revoke(rec["id"]), "Bye")
        self.assertIsNone(tokens.lookup(token))
        self.assertIsNone(tokens.revoke(rec["id"]))
        self.assertIsNone(tokens.revoke("../x"))
        self.assertIsNone(tokens.revoke(5))

    # -- allows -------------------------------------------------------------

    def test_allows(self):
        self.assertTrue(tokens.allows("admin", "read"))
        self.assertTrue(tokens.allows("admin", "act"))
        self.assertTrue(tokens.allows("admin", "admin"))
        self.assertTrue(tokens.allows("act", "read"))
        self.assertTrue(tokens.allows("act", "act"))
        self.assertTrue(tokens.allows("read", "read"))
        self.assertFalse(tokens.allows("read", "act"))
        self.assertFalse(tokens.allows("read", "admin"))
        self.assertFalse(tokens.allows("act", "admin"))
        self.assertFalse(tokens.allows("bogus", "read"))
        self.assertFalse(tokens.allows("admin", "bogus"))

    # -- no store -----------------------------------------------------------

    def test_no_store(self):
        with mock.patch.object(store, "DIR", ""), mock.patch.object(store, "_db", None):
            with self.assertRaises(ValueError):
                tokens.make("x", "read")
            self.assertIsNone(tokens.lookup("lh_" + "a" * 43))

    # -- last used ----------------------------------------------------------

    def test_last_used(self):
        token, rec = tokens.make("Tracked", "read")
        tokens._used.clear()
        tokens.lookup(token, ip="10.0.0.5")
        entry = [e for e in tokens.listing() if e["id"] == rec["id"]][0]
        self.assertEqual(entry["ip"], "10.0.0.5")
        self.assertGreater(entry["last"], 0)


if __name__ == "__main__":
    unittest.main()
