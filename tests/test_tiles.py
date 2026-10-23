"""The wall's tiles (static/tiles.js: host_line, tile_headline, tile_trouble, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "tiles.mjs"


def run(card, data):
    out = subprocess.run(["node", str(SCRIPT), json.dumps({"card": card, "data": data})],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


HOST = {"id": "host-pve", "name": "PVE", "panel": "host:pve"}


@unittest.skipUnless(shutil.which("node"), "node not found")
class Tiles(unittest.TestCase):
    def test_a_host_counts_its_guests_by_kind(self):
        guests = {"1": {"running": True}, "2": {"running": True}, "3": {"running": True, "type": "lxc"}}
        r = run(HOST, {"proxmox.pve": {"guests": guests}})
        self.assertEqual(r["node"], "pve")
        self.assertEqual(r["line"], "2 VM · 1 LXC · all fine")

    def test_a_stopped_guest_is_said_not_hidden(self):
        r = run(HOST, {"proxmox.pve": {"guests": {"1": {"running": True}, "2": {"running": False}}}})
        self.assertEqual(r["line"], "2 VM · 1 stopped")

    def test_a_host_shows_the_higher_of_cpu_and_ram(self):
        r = run(HOST, {"proxmox.pve.cpu": 10, "proxmox.pve.mem": 85})
        self.assertEqual(r["head"], ["85%", "RAM", "warn"])
        self.assertEqual(r["trouble"], "warn")

    def test_a_card_shows_its_worst_number(self):
        card = {"id": "x", "name": "X", "metrics": [["a", "CPU", "percent"], ["b", "Disk", "percent"]]}
        r = run(card, {"a": 20, "b": 95})
        self.assertEqual(r["head"], ["95%", "Disk", "crit"])
        self.assertEqual(r["trouble"], "crit")

    def test_a_quiet_card_is_not_trouble(self):
        card = {"id": "x", "name": "X", "metrics": [["a", "CPU", "percent"]]}
        self.assertEqual(run(card, {"a": 20})["trouble"], "")
