"""The wall's cells (static/app.js card_span and duration, run by Node; skipped without it): a big card takes its section's whole
row, a small one a cell, and the uptime chip reads short."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "call_fn.mjs"
SIZES = {"s": [1, 1], "m": [2, 1], "l": [2, 2], "xl": [4, 2]}     # static/app.js SIZES


def call(name, *args):
    out = subprocess.run(["node", str(SCRIPT), name, json.dumps(list(args))], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node not found")
class WallCells(unittest.TestCase):
    def test_a_small_card_is_one_cell_and_never_wider_than_its_section(self):
        self.assertEqual(call("card_span", SIZES["s"], 6), 1)
        self.assertEqual(call("card_span", SIZES["m"], 6), 2)
        self.assertEqual(call("card_span", SIZES["m"], 1), 1)

    def test_a_big_card_takes_the_whole_row_of_its_section(self):
        for k in (1, 2, 4, 7):
            self.assertEqual(call("card_span", SIZES["l"], k), k)
            self.assertEqual(call("card_span", SIZES["xl"], k), k)

    def test_the_uptime_chip_reads_short(self):
        self.assertEqual([call("duration", s) for s in (0, 45, 600, 7200, 86400 * 3, 860000)], ["—", "45s", "10m", "2h", "3d", "10d"])
