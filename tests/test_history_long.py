"""The long history behind a panel's charts: one average per 15 minutes (server._sample_long)."""

import collections
import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class LongHistory(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.dict(server.TOPOLOGY, {"trends": ["a.cpu"]})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for d in (server._history, server._long, server._long_pending, server._year, server._year_pending):
            saved = dict(d)
            d.clear()
            self.addCleanup(lambda d=d, saved=saved: (d.clear(), d.update(saved)))

    def minute(self, value):
        server._history.setdefault("a.cpu", collections.deque(maxlen=server.HISTORY_POINTS)).append(value)
        return server._sample_long()[0]

    def test_average_of_fifteen_minutes(self):
        per_point = server.LONG_STEP // server.HISTORY_STEP
        results = [self.minute(v) for v in [10, None] + [20] * (per_point - 2)]
        self.assertTrue(all(r is None for r in results[:-1]))   # saved only when a point is added
        self.assertEqual(results[-1], {"a.cpu": [round((10 + 20 * (per_point - 2)) / (per_point - 1), 1)]})
        self.assertEqual(server._long_pending, {})

    def test_all_missing_is_a_gap(self):
        for _ in range(server.LONG_STEP // server.HISTORY_STEP):
            out = self.minute(None)
        self.assertEqual(out, {"a.cpu": [None]})

    def test_the_year_is_every_eight_long_points(self):
        per_point = server.LONG_STEP // server.HISTORY_STEP
        years = []
        for v in [10] * (4 * per_point) + [30] * (4 * per_point):
            server._history.setdefault("a.cpu", collections.deque(maxlen=server.HISTORY_POINTS)).append(v)
            years.append(server._sample_long()[1])
        self.assertTrue(all(y is None for y in years[:-1]))
        self.assertEqual(years[-1], {"a.cpu": [20.0]})
        self.assertEqual(server.history(year=True)["step"], server.YEAR_STEP)

    def test_api_shape(self):
        server._long["a.cpu"] = collections.deque([1.0, 2.0], maxlen=server.LONG_POINTS)
        self.assertEqual(server.history(long=True), {"step": server.LONG_STEP, "series": {"a.cpu": [1.0, 2.0]}})

    def test_keys_limit_the_series(self):
        server._long["a.cpu"] = collections.deque([1.0], maxlen=server.LONG_POINTS)
        server._long["b.cpu"] = collections.deque([2.0], maxlen=server.LONG_POINTS)
        self.assertEqual(server.history(long=True, keys={"b.cpu"})["series"], {"b.cpu": [2.0]})
        self.assertEqual(sorted(server.history(long=True, keys=set())["series"]), ["a.cpu", "b.cpu"])
        self.assertEqual(server.history_args("long&keys=a.cpu&keys=b.cpu"), {"long": True, "year": False, "keys": {"a.cpu", "b.cpu"}})
        self.assertEqual(server.history_args(""), {"long": False, "year": False, "keys": set()})


if __name__ == "__main__":
    unittest.main()
