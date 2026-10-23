"""A pushed source that pushes less often than it goes stale: its history repeats the last number for a while instead of holding single points."""

import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402
from sources import push  # noqa: E402


class PushCarry(unittest.TestCase):
    def setUp(self):
        server._carried.clear()
        self.addCleanup(server._carried.clear)

    def test_the_last_pushed_number_is_repeated_up_to_the_limit(self):
        with mock.patch.dict(push.KEYS, {"vps": b"k"}), mock.patch.object(server, "_value", return_value=None):
            series = [41.0]
            for _ in range(server.PUSH_CARRY):
                series.append(server._sample_value("live.vps.resources.cpu", series))
            self.assertEqual(series, [41.0] * (server.PUSH_CARRY + 1))
            self.assertIsNone(server._sample_value("live.vps.resources.cpu", series))   # a long silence is a gap again

    def test_a_fresh_number_resets_and_other_sources_are_never_carried(self):
        with mock.patch.dict(push.KEYS, {"vps": b"k"}):
            with mock.patch.object(server, "_value", return_value=None):
                self.assertEqual(server._sample_value("live.vps.resources.cpu", [5.0]), 5.0)
                self.assertIsNone(server._sample_value("live.proxmox.cpu", [5.0]))       # not pushed
                self.assertIsNone(server._sample_value("live.vps.resources.cpu", []))     # nothing before it
            with mock.patch.object(server, "_value", return_value=7.0):
                self.assertEqual(server._sample_value("live.vps.resources.cpu", [5.0]), 7.0)
            self.assertEqual(server._carried["live.vps.resources.cpu"], 0)


if __name__ == "__main__":
    unittest.main()
