"""The rings of a machine on the wall (static/app.js ring_plan, half_dash, run by Node; skipped without it):
CPU and RAM share one ring, a GPU's load and video memory share one, the rest are single rings."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "make_rings.mjs"


def run(arg):
    out = subprocess.run(["node", str(SCRIPT), json.dumps(arg)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def plan(bars, rest=()):
    return run({"bars": [list(b) for b in bars], "rest": [list(r) for r in rest]})


CPU = ("proxmox.pve.cpu", "CPU", "percent")
RAM = ("proxmox.pve.mem_used_of", "RAM", "used_of")
DISK = ("proxmox.pve.disk_used_of", "Disk", "used_of")


@unittest.skipUnless(shutil.which("node"), "node not found")
class MakeRings(unittest.TestCase):
    def test_cpu_and_ram_share_a_ring_and_the_temperature_goes_with_the_cpu(self):
        got = plan([CPU, RAM, DISK], [("temp.pve.cpu_temp", "CPU", "celsius")])
        self.assertEqual(got, [
            {"label": "CPU · RAM", "a": CPU[0], "b": RAM[0], "temp": "temp.pve.cpu_temp"},
            {"label": "Disk", "a": DISK[0], "b": None, "temp": None}])

    def test_each_gpu_is_one_ring_of_load_and_video_memory(self):
        temps = [("temp.pve.gpu0_temp", "GPU", "celsius"), ("temp.pve.gpu1_temp", "GPU", "celsius")]
        got = plan([CPU, RAM], temps)
        self.assertEqual([g["label"] for g in got], ["CPU · RAM", "GPU1 · VRAM", "GPU2 · VRAM"])
        self.assertEqual(got[2]["a"], "temp.pve.gpu1_util")
        self.assertEqual(got[2]["b"], "temp.pve.gpu1_mem")
        self.assertEqual(got[2]["temp"], "temp.pve.gpu1_temp")

    def test_a_single_gpu_has_no_number(self):
        got = plan([CPU], [("temp.pve.gpu0_temp", "GPU", "celsius")])
        self.assertEqual(got[1]["label"], "GPU · VRAM")

    def test_without_a_ram_share_the_cpu_stays_alone(self):
        got = plan([CPU, DISK])
        self.assertEqual([(g["label"], g["b"]) for g in got], [("CPU", None), ("Disk", None)])

    def test_half_dash_left_grows_from_its_start_right_from_its_end(self):
        left, right, empty = run({"dash": [
            {"len": 360, "share": 0.5, "from_end": False},
            {"len": 360, "share": 0.5, "from_end": True},
            {"len": 360, "share": 0, "from_end": True}]})
        self.assertEqual(left, ["82.0 360.0", "0"])      # half of 164
        self.assertEqual(right, ["82.0 360.0", "-82.0"])
        self.assertEqual(empty, ["0.0 360.0", "-164.0"])
