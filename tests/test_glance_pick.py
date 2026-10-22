"""Which metrics a glance tile shows as CPU / GPU / RAM / Disk (static/app.js glance_pick, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "glance_pick.mjs"


def pick(labels):
    metrics = [[f"x.{i}", label, "percent"] for i, label in enumerate(labels)]
    out = subprocess.run(["node", str(SCRIPT), json.dumps({"metrics": metrics})],
                         capture_output=True, text=True, check=True).stdout
    return [(g["kind"], g["index"]) for g in json.loads(out)]


@unittest.skipUnless(shutil.which("node"), "node not found")
class GlancePick(unittest.TestCase):
    def test_the_four_come_in_the_tiles_order(self):
        self.assertEqual(pick(["Disk", "RAM", "GPU0", "CPU"]),
                         [("cpu", 3), ("gpu", 2), ("ram", 1), ("disk", 0)])

    def test_other_names_for_the_same_things(self):
        self.assertEqual(pick(["Memory", "Storage"]), [("ram", 0), ("disk", 1)])
        self.assertEqual(pick(["Used", "Processor"]), [("cpu", 1), ("disk", 0)])

    def test_the_first_of_each_kind_wins_and_the_rest_are_ignored(self):
        self.assertEqual(pick(["CPU", "Temp", "CPU temp", "Uptime"]), [("cpu", 0)])

    def test_nothing_recognised_is_empty(self):
        self.assertEqual(pick(["Latency", "Containers"]), [])
