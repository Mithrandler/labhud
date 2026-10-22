"""The week of uptime as one square a day (static/app.js uptime_day_shares, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "uptime_days.mjs"


def shares(values):
    out = subprocess.run(["node", str(SCRIPT), json.dumps({"values": values})], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@unittest.skipUnless(shutil.which("node"), "node not found")
class UptimeDays(unittest.TestCase):
    def test_mean_of_a_day_off_and_unknown(self):
        # 2 slices a day: all up, half down, off on purpose, unknown, then three up days
        v = [1, 1, 1, 0, -1, -1, None, None, 1, 1, 1, 1, 1, 1]
        self.assertEqual(shares(v), [1, 0.5, -1, None, 1, 1, 1])

    def test_no_history_is_seven_unknown_days(self):
        self.assertEqual(shares([]), [None] * 7)

    def test_a_day_half_known_counts_only_what_is_known(self):
        # day 4 holds one unknown and one up slice: it is up
        self.assertEqual(shares([None] * 7 + [1] * 7), [None, None, None, 1, 1, 1, 1])
