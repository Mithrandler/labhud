"""The scheduler's jobs (jobs.py): the shape of a job, the old window becoming the first one, when a job is due."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jobs  # noqa: E402


class Store:
    def __init__(self):
        self.d = {}

    def enabled(self):
        return True

    def load(self, name, default=None):
        return self.d.get(name, default)

    def save(self, name, value):
        self.d[name] = value


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


class Jobs(unittest.TestCase):
    def setUp(self):
        self.real, jobs.store = jobs.store, Store()

    def tearDown(self):
        jobs.store = self.real

    def test_the_old_window_becomes_the_first_job_once(self):
        jobs.store.save(jobs.OLD_WINDOW_KEY, {"days": [6], "at": "05:15"})
        first = jobs.load()[0]
        self.assertEqual((first["id"], first["kind"], first["agent"], first["days"], first["at"]), (jobs.UPDATE_ID, "update", "*", [6], "05:15"))
        jobs.store.save(jobs.OLD_WINDOW_KEY, {"days": [1], "at": "01:00"})     # the window is not read again
        self.assertEqual(jobs.load()[0]["at"], "05:15")

    def test_without_an_old_window_the_default_is_used(self):
        self.assertEqual(jobs.update_window(), jobs.DEFAULT)

    def test_a_job_is_rebuilt_field_by_field(self):
        bad = [None, {}, {"kind": "reboot"}, {"kind": "prune", "name": "x", "agent": "h", "days": [], "at": "03:00"},
               {"kind": "prune", "name": "x", "agent": "h", "days": [7], "at": "03:00"}, {"kind": "prune", "name": "x", "agent": "h", "days": [True], "at": "03:00"},
               {"kind": "prune", "name": "x", "agent": "h", "days": [1], "at": "24:00"}, {"kind": "prune", "name": "", "agent": "h", "days": [1], "at": "03:00"},
               {"kind": "prune", "name": "x", "agent": "*", "days": [1], "at": "03:00"}, {"kind": "host-update", "name": "x", "days": [1], "at": "03:00"}]
        for raw in bad:
            self.assertIsNone(jobs.clean(raw), raw)
        j = jobs.clean({"kind": "prune", "name": "a\x00b" + "c" * 99, "agent": "h", "days": [3, 1, 3], "at": "03:00", "id": "zz", "extra": 1, "last": {"t": "x", "ok": 1}})
        self.assertEqual((j["id"], j["days"], len(j["name"]), "extra" in j), ("", [1, 3], 60, False))
        self.assertEqual(j["last"], {"day": "", "t": 0, "ok": False, "text": ""})

    def test_put_replaces_by_id_keeps_the_last_run_and_stops_at_the_limit(self):
        job = jobs.put({"kind": "prune", "name": "p", "agent": "h", "days": [0], "at": "03:00"})
        jobs.record(job["id"], True, "freed", now=at(2026, 10, 19, 3, 0))
        again = jobs.put(dict(job, at="04:00", last={}))
        self.assertEqual((again["at"], again["last"]["text"]), ("04:00", "freed"))
        self.assertIsNone(jobs.put(dict(job, id="ffffffff")))
        self.assertIsNone(jobs.put(dict(job, kind="host-update")))
        for i in range(jobs.MAX_JOBS):
            jobs.put({"kind": "prune", "name": f"p{i}", "agent": "h", "days": [0], "at": "03:00"})
        self.assertEqual(len(jobs.load()), jobs.MAX_JOBS)

    def test_the_update_job_cannot_be_deleted_but_can_be_moved(self):
        jobs.load()
        self.assertFalse(jobs.delete(jobs.UPDATE_ID))
        self.assertTrue(jobs.set_update_window([2], "06:00"))
        self.assertEqual(jobs.update_window(), {"days": [2], "at": "06:00"})
        self.assertFalse(jobs.set_update_window([9], "06:00"))

    def test_due_is_open_for_an_hour_once_a_day_on_its_days(self):
        job = jobs.put({"kind": "prune", "name": "p", "agent": "h", "days": [6], "at": "04:00"})   # Sundays
        sunday = lambda h, m: at(2026, 10, 18, h, m)   # noqa: E731
        self.assertTrue(jobs.due(job, sunday(4, 0)))
        self.assertTrue(jobs.due(job, sunday(4, 59)))      # a restart within the hour
        self.assertFalse(jobs.due(job, sunday(5, 0)))
        self.assertFalse(jobs.due(job, sunday(3, 59)))
        self.assertFalse(jobs.due(job, at(2026, 10, 19, 4, 0)))   # Monday
        jobs.record(job["id"], True, "ok", now=sunday(4, 0))
        done = next(j for j in jobs.load() if j["id"] == job["id"])
        self.assertFalse(jobs.due(done, sunday(4, 30)))           # once a day
        self.assertFalse(jobs.due(dict(job, enabled=False), sunday(4, 0)))


if __name__ == "__main__":
    unittest.main()
