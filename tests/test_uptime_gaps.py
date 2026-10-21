"""Uptime that said 100% for a host that was off: an agent gone quiet, a guest off without on_demand, labhud's own downtime (coverage), the running 15 minutes across a restart."""

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402

N = server.UPTIME_STEP // server.HISTORY_STEP


class Gaps(unittest.TestCase):
    def setUp(self):
        for d in (server._uptime, server._uptime_pending):
            saved = dict(d)
            d.clear()
            self.addCleanup(lambda d=d, saved=saved: (d.clear(), d.update(saved)))

    def use(self, *cards):
        self.patch = mock.patch.dict(server.TOPOLOGY, {"cards": {"g": list(cards)}, "trends": []})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def minutes(self, data, n=N):
        out = None
        for _ in range(n):
            out = server._sample_uptime(data)
        return out

    def test_agent_quiet_for_30_minutes_is_down(self):
        self.use({"id": "nas", "check": ["agent", "nas", "", ""]})
        data = {"agents": {"nas": {"online": False, "seen": time.time() - 1800}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["nas"]), [0.0])
        u = server.uptime(86400)
        self.assertEqual(u["percent"]["nas"], 0.0)
        self.assertLess(u["percent"]["nas"], 100)

    def test_agent_quiet_for_a_minute_is_unknown(self):
        self.use({"id": "nas", "check": ["agent", "nas", "", ""]})
        data = {"agents": {"nas": {"online": False, "seen": time.time() - 60}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["nas"]), [None])

    def test_agent_never_seen_is_unknown(self):
        self.use({"id": "nas", "check": ["agent", "nas", "", ""]})
        data = {"agents": {"nas": {"online": False, "seen": 0}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["nas"]), [None])

    def test_agent_off_on_purpose(self):
        self.use(
            {"id": "a", "check": ["agent", "nas", "", ""], "on_demand": True},
            {"id": "b", "check": ["agent", "nas", "", ""], "wake": "pve2:aa:bb:cc:dd:ee:ff"},
        )
        data = {"agents": {"nas": {"online": False, "seen": time.time() - 1800}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["a"]), [-1])
        self.assertEqual(list(server._uptime["b"]), [-1])

    def test_agent_online_is_up(self):
        self.use({"id": "nas", "check": ["agent", "nas", "", ""]})
        data = {"agents": {"nas": {"online": True, "seen": time.time()}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["nas"]), [1.0])

    def test_guest_off_without_on_demand_is_down(self):
        self.use({"id": "vm", "check": ["proxmox", "pve", "101"]})
        data = {"proxmox": {"pve": {"guests": {"101": {"running": False, "onboot": False}}}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["vm"]), [0.0])

    def test_guest_off_with_on_demand_is_on_purpose(self):
        self.use({"id": "vm", "check": ["proxmox", "pve", "101"], "on_demand": True})
        data = {"proxmox": {"pve": {"guests": {"101": {"running": False, "onboot": False}}}}}
        self.minutes(data)
        self.assertEqual(list(server._uptime["vm"]), [-1])

    def test_coverage(self):
        self.use({"id": "web01", "check": ["ping", "192.0.2.10"]})
        server._uptime["web01"] = server.collections.deque([1.0] * 48 + [None] * 48, maxlen=server.UPTIME_POINTS)
        u = server.uptime(86400)
        self.assertEqual(u["percent"]["web01"], 100.0)
        self.assertEqual(u["coverage"]["web01"], 50.0)

    def test_pending_is_saved(self):
        self.use({"id": "web01", "check": ["ping", "192.0.2.10"]})
        server._uptime_pending["web01"] = [1, 1, 1]
        s = server._uptime_saved()
        self.assertEqual(s["pending"], {"web01": [1, 1, 1]})
        self.assertIn("series", s)
        self.assertIn("t", s)

    def test_restart_keeps_the_running_minutes(self):
        self.use({"id": "web01", "check": ["ping", "192.0.2.10"]})
        saved = {
            "t": int(time.time()) - 2 * server.HISTORY_STEP - 1,
            "series": {"web01": [1.0]},
            "pending": {"web01": [1, 1, 1]},
        }
        with mock.patch.object(server.store, "load", side_effect=lambda name: saved if name == "uptime" else None):
            server.restore_history()
        self.assertEqual(list(server._uptime["web01"]), [1.0])
        self.assertEqual(server._uptime_pending["web01"], [1, 1, 1, None, None])

    def test_long_restart_folds_the_minutes(self):
        self.use({"id": "web01", "check": ["ping", "192.0.2.10"]})
        saved = {
            "t": int(time.time()) - (N + 2) * server.HISTORY_STEP - 1,
            "series": {"web01": [1.0]},
            "pending": {"web01": [1, 1, 1]},
        }
        with mock.patch.object(server.store, "load", side_effect=lambda name: saved if name == "uptime" else None):
            server.restore_history()
        self.assertEqual(list(server._uptime["web01"]), [1.0, 1.0])
        self.assertEqual(server._uptime_pending["web01"], [None] * 5)

    def test_old_save_without_pending(self):
        self.use({"id": "web01", "check": ["ping", "192.0.2.10"]})
        saved = {"t": int(time.time()) - 3 * server.UPTIME_STEP - 1, "series": {"web01": [1.0]}}
        with mock.patch.object(server.store, "load", side_effect=lambda name: saved if name == "uptime" else None):
            server.restore_history()
        self.assertEqual(list(server._uptime["web01"]), [1.0, None, None, None])


if __name__ == "__main__":
    unittest.main()
