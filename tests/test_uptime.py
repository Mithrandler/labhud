"""Uptime per card: each minute up / down / off on purpose, folded every 15 minutes (server._sample_uptime)."""

import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402

CARD = {"id": "web01", "check": ["ping", "192.0.2.10"]}


class Uptime(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.dict(server.TOPOLOGY, {"cards": {"g": [CARD, {"id": "text-only"}]}})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for d in (server._uptime, server._uptime_pending):
            saved = dict(d)
            d.clear()
            self.addCleanup(lambda d=d, saved=saved: (d.clear(), d.update(saved)))

    def run_minutes(self, statuses):
        out = None
        with mock.patch.object(server.cardstate, "card_status", side_effect=list(statuses)):
            for _ in statuses:
                out = server._sample_uptime({})
        return out

    def test_share_of_known_minutes(self):
        n = server.UPTIME_STEP // server.HISTORY_STEP
        out = self.run_minutes([True] * (n - 3) + [False, False, None])
        self.assertEqual(out, {"web01": [round((n - 3) / (n - 1), 3)]})   # unknown minutes do not count
        self.assertEqual(server._uptime_pending, {})

    def test_off_on_purpose_and_unknown(self):
        n = server.UPTIME_STEP // server.HISTORY_STEP
        self.assertEqual(self.run_minutes(["scheduled"] * n), {"web01": [-1]})
        self.assertEqual(self.run_minutes([None] * n), {"web01": [-1, None]})

    def test_only_cards_with_a_check(self):
        n = server.UPTIME_STEP // server.HISTORY_STEP
        self.assertEqual(list(self.run_minutes([True] * n)), ["web01"])

    def test_api_percent_and_range(self):
        server._uptime["web01"] = server.collections.deque([1.0] * 200 + [0.5, -1, None, 1.0], maxlen=server.UPTIME_POINTS)
        day = server.uptime(86400)
        self.assertEqual(day["step"], server.UPTIME_STEP)
        self.assertEqual(len(day["cards"]["web01"]), 86400 // server.UPTIME_STEP)
        self.assertEqual(day["percent"]["web01"], round(100 * (93 + 0.5) / 94, 2))
        self.assertEqual(len(server.uptime(7 * 86400)["cards"]["web01"]), 204)


if __name__ == "__main__":
    unittest.main()
