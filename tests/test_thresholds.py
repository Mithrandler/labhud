"""Critical marks as events: held for a while before, cleared with a margin after."""

import unittest

import thresholds

CARDS = [{"id": "pve", "name": "PVE", "metrics": [["proxmox.pve.disk_used_of", "Disk", "used_of"],
                                                  ["proxmox.pve.cpu", "CPU", "percent"],
                                                  ["proxmox.pve.vms", "VM", "count"]]},
         {"id": "nas", "name": "NAS", "metrics": [["synology.temp", "Temp", "celsius"]]}]


class Thresholds(unittest.TestCase):
    def run_seq(self, values, path="proxmox.pve.cpu", source="proxmox", step=60, skip=()):
        w = thresholds.Watch(hold=300)
        out = []
        for i, v in enumerate(values):
            out.append(w.check(CARDS, lambda p, v=v: v if p == path else None, source, skip, now=1000 + i * step))
        return out

    def test_held_then_reported_once(self):
        got = self.run_seq([95, 96, 97, 95, 94, 99, 99])
        self.assertEqual([e for es in got for e in es], [("PVE CPU 99%", True)])  # at 300 s, once

    def test_a_short_spike_is_not_news(self):
        self.assertEqual([e for es in self.run_seq([95, 96, 50, 95, 96]) for e in es], [])

    def test_cleared_only_under_the_margin(self):
        got = [e for es in self.run_seq([95] * 6 + [90, 88, 86]) for e in es]
        self.assertEqual(got, [("PVE CPU 95%", True), ("PVE CPU back to 86%", False)])

    def test_used_of_and_celsius(self):
        got = self.run_seq([[95, 100]] * 6, path="proxmox.pve.disk_used_of")
        self.assertEqual(got[5], [("PVE Disk 95%", True)])
        got = self.run_seq([90] * 6, path="synology.temp", source="synology")
        self.assertEqual(got[5], [("NAS Temp 90°C", True)])

    def test_counts_and_maintenance_are_ignored(self):
        self.assertEqual([e for es in self.run_seq([500] * 8, path="proxmox.pve.vms") for e in es], [])
        self.assertEqual([e for es in self.run_seq([99] * 8, skip={"pve"}) for e in es], [])


if __name__ == "__main__":
    unittest.main()
