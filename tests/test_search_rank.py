"""The search box's ranking (static/search.js search_rank, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "search_rank.mjs"


def rank(labels, q):
    items = [{"label": x, "text": x.lower()} for x in labels]
    out = subprocess.run(["node", str(SCRIPT), json.dumps({"items": items, "q": q})],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node not found")
class SearchRank(unittest.TestCase):
    def test_start_of_label_first_then_word_then_inside(self):
        got = rank(["my restart", "restart · web", "prerestart", "restart · db"], "restart")
        self.assertEqual(got, ["restart · web", "restart · db", "my restart", "prerestart"])

    def test_every_word_must_match(self):
        self.assertEqual(rank(["restart · web", "stop · web", "restart · db"], "restart web"), ["restart · web"])

    def test_empty_query_and_no_match(self):
        self.assertEqual(rank(["a", "b"], ""), [])
        self.assertEqual(rank(["a", "b"], "zzz"), [])

    def test_ties_keep_the_given_order(self):
        self.assertEqual(rank(["web 2", "web 1"], "web"), ["web 2", "web 1"])
