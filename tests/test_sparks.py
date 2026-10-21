"""The cards' sparklines in one request (/api/v1/sparks): the last minutes of every trend."""

import collections
import os
import unittest

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class Sparks(unittest.TestCase):
    def setUp(self):
        saved = dict(server._history)
        server._history.clear()
        self.addCleanup(lambda: (server._history.clear(), server._history.update(saved)))

    def test_last_points_only(self):
        server._history["a.cpu"] = collections.deque(range(100), maxlen=server.HISTORY_POINTS)
        out = server.sparks()
        self.assertEqual(out["step"], server.HISTORY_STEP)
        self.assertEqual(out["series"]["a.cpu"], list(range(100 - server.SPARK_POINTS, 100)))

    def test_nothing_known_is_left_out(self):
        server._history["a.cpu"] = collections.deque([None] * 40)
        server._history["b.mem"] = collections.deque([None] * 39 + [5])
        self.assertEqual(list(server.sparks()["series"]), ["b.mem"])


if __name__ == "__main__":
    unittest.main()
