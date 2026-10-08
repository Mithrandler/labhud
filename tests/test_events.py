"""events.py: which differences between two results of a source become history rows."""

import unittest

import events

TOPOLOGY = {
    "cards": {"g": [
        {"id": "nas", "name": "NAS"},
        {"id": "pbs", "name": "BACKUP", "on_demand": True},
    ]},
    "alerts": {"path": "live.alerts"},
}


class Changes(unittest.TestCase):
    def test_first_result_says_nothing(self):
        self.assertEqual(events.changes("status", None, {"nas": True}, TOPOLOGY), [])

    def test_host_down_and_up(self):
        self.assertEqual(events.changes("status", {"nas": True}, {"nas": False}, TOPOLOGY), [("NAS down", True)])
        self.assertEqual(events.changes("status", {"nas": False}, {"nas": True}, TOPOLOGY), [("NAS up", False)])

    def test_on_demand_host_off_is_not_bad(self):
        self.assertEqual(events.changes("status", {"pbs": True}, {"pbs": False}, TOPOLOGY), [("BACKUP off", False)])

    def test_scheduled_and_unchanged_say_nothing(self):
        self.assertEqual(events.changes("status", {"nas": True}, {"nas": "scheduled"}, TOPOLOGY), [])
        self.assertEqual(events.changes("status", {"nas": True, "_t": 1}, {"nas": True, "_t": 2}, TOPOLOGY), [])

    def test_failed_result_says_nothing(self):
        self.assertEqual(events.changes("proxmox", {"pve": {"guests": {}}}, {"unavailable": "timed out"}, TOPOLOGY), [])

    def test_guest_started_and_stopped(self):
        old = {"pve": {"guests": {"100": {"name": "docker", "running": True}, "101": {"name": "win", "running": False}}}}
        new = {"pve": {"guests": {"100": {"name": "docker", "running": False}, "101": {"name": "win", "running": True}}}}
        self.assertEqual(events.changes("proxmox", old, new, TOPOLOGY), [("docker stopped", False), ("win started", False)])

    def test_new_guest_says_nothing(self):
        new = {"pve": {"guests": {"100": {"name": "docker", "running": True}}}}
        self.assertEqual(events.changes("proxmox", {"pve": {"guests": {}}}, new, TOPOLOGY), [])

    def test_new_alert(self):
        old = {"alerts": [{"id": "a", "text": "old"}]}
        new = {"alerts": [{"id": "a", "text": "old"}, {"id": "b", "text": "Disk almost full"}]}
        self.assertEqual(events.changes("live", old, new, TOPOLOGY), [("Disk almost full", True)])
        self.assertEqual(events.changes("live", old, new, dict(TOPOLOGY, alerts=None)), [])


class Log(unittest.TestCase):
    def test_newest_first_and_capped(self):
        log = events.Log()
        log.add("one", t=1)
        for i in range(events.MAX_EVENTS + 5):
            log.add(f"e{i}", t=10 + i)
        rows = log.snapshot()["recent"]
        self.assertEqual(len(rows), events.MAX_EVENTS)
        last = events.MAX_EVENTS + 4
        self.assertEqual(rows[0], {"name": f"e{last}", "t": 10 + last, "bad": False})

    def test_seed(self):
        log = events.Log([{"name": "x", "t": 5, "bad": True}])
        log.add("y", bad=True, t=6)
        self.assertEqual([r["name"] for r in log.snapshot()["recent"]], ["y", "x"])


if __name__ == "__main__":
    unittest.main()
