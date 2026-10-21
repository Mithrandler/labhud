"""Proxmox upkeep: storage "full in N days" and snapshots left behind."""

import unittest
from unittest import mock

from sources import proxmox

DAY = 86400


class FullIn(unittest.TestCase):
    def setUp(self):
        proxmox._GROWTH.clear()

    def test_needs_twelve_hours(self):
        self.assertIsNone(proxmox.full_in("k", 100, 1000, now=0))
        self.assertIsNone(proxmox.full_in("k", 110, 1000, now=6 * 3600))
        # 10 more after half a day = 20 a day; 890 left -> 44 days
        self.assertEqual(proxmox.full_in("k", 110, 1000, now=12 * 3600), 44)

    def test_pace(self):
        for h in range(0, 25):
            got = proxmox.full_in("s", 100 + h, 1000, now=h * 3600)
        # 1 unit an hour = 24 a day; at hour 24 used is 124 -> 876 / 24 = 36 days
        self.assertEqual(got, 36)

    def test_not_growing(self):
        proxmox.full_in("f", 500, 1000, now=0)
        self.assertIsNone(proxmox.full_in("f", 400, 1000, now=DAY))

    def test_old_samples_go(self):
        proxmox.full_in("o", 0, 1000, now=0)
        proxmox.full_in("o", 500, 1000, now=8 * DAY)
        self.assertEqual(len(proxmox._GROWTH["o"]), 1)


class Snapshots(unittest.TestCase):
    def test_only_old_ones(self):
        now = 100 * DAY
        answers = {
            "/cluster/resources": {"data": [{"node": "pve", "vmid": 101, "name": "web", "type": "qemu"},
                                            {"node": "pve", "vmid": 9000, "template": 1},
                                            {"node": "other", "vmid": 300, "name": "x"}]},
            "/qemu/101/snapshot": {"data": [{"name": "current"}, {"name": "before-upgrade", "snaptime": now - 40 * DAY},
                                            {"name": "today", "snaptime": now - 3600}]},
        }

        def fake(url, headers=None, timeout=10, **kw):
            return answers[max((k for k in answers if k in url), key=len)]
        with mock.patch.object(proxmox, "request", fake):
            got = proxmox._node_snapshots("pve", "https://192.0.2.10:8006", {}, now)
        self.assertEqual(got, [("web", "before-upgrade", 40)])


if __name__ == "__main__":
    unittest.main()
