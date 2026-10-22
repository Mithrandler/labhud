"""Glance tiles cut into pages by height (static/app.js glance_pack, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "glance_pack.mjs"


def pack(counts, titles, cols=2, t=100, gap=10, avail=300):
    arg = {"counts": counts, "titles": titles, "cols": cols, "t": t, "gap": gap, "avail": avail}
    out = subprocess.run(["node", str(SCRIPT), json.dumps(arg)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node not found")
class GlancePack(unittest.TestCase):
    def test_all_fit_on_one_page(self):
        # title 30 + a row 110 = 140, +10 between sections, a second section 30 + 110 = 150: 300 in all
        self.assertEqual(pack([2, 2], [30, 30]), [[0, 0], [0, 0]])

    def test_the_next_row_that_does_not_fit_goes_to_the_next_page(self):
        # 6 tiles, 2 a row = 3 rows of 110 + a title of 30: 360 > 300
        self.assertEqual(pack([6], [30]), [[0, 0, 0, 0, 1, 1]])

    def test_a_section_that_continues_keeps_its_row_together(self):
        # the two tiles of a row are never split
        self.assertEqual(pack([4, 2], [30, 30]), [[0, 0, 0, 0], [1, 1]])

    def test_a_single_tall_row_still_gets_a_page(self):
        self.assertEqual(pack([2], [30], avail=50), [[0, 0]])

    def test_no_tiles_no_pages(self):
        self.assertEqual(pack([], []), [])
