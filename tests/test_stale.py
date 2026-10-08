"""A source that misses a poll keeps its last good answer for a while (server._keep_stale)."""

import os
import time
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class Stale(unittest.TestCase):
    def setUp(self):
        self.addCleanup(server._data.pop, "src", None)
        self.addCleanup(server._health.pop, "src", None)

    def state(self, data, last_ok):
        server._data["src"] = data
        server._health["src"] = {"last_ok": last_ok} if last_ok else {}

    def test_missed_poll_keeps_the_last_answer(self):
        self.state({"guests": {"201": {"running": False}}, "_t": 1}, time.time() - 10)
        got = server._keep_stale("src", {"unavailable": "timed out", "_t": 2})
        self.assertEqual(got, {"guests": {"201": {"running": False}}, "_t": 1, "stale": "timed out"})

    def test_too_old_gives_the_error(self):
        self.state({"x": 1}, time.time() - server.SOURCE_STALE - 5)
        self.assertEqual(server._keep_stale("src", {"unavailable": "down"}), {"unavailable": "down"})

    def test_nothing_good_yet_gives_the_error(self):
        self.state({"unavailable": "first"}, None)
        self.assertEqual(server._keep_stale("src", {"unavailable": "down"}), {"unavailable": "down"})

    def test_a_good_answer_passes_unchanged(self):
        self.state({"x": 1}, time.time())
        self.assertEqual(server._keep_stale("src", {"x": 2}), {"x": 2})

    def test_off(self):
        self.state({"x": 1}, time.time())
        with mock.patch.object(server, "SOURCE_STALE", 0):
            self.assertEqual(server._keep_stale("src", {"unavailable": "down"}), {"unavailable": "down"})


if __name__ == "__main__":
    unittest.main()
