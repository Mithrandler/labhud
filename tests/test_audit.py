"""api/audit.py: the JSONL day files, their 90 days, the filters, and the hooks that feed it."""

import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

import admin
from api import audit


class FakeHandler:
    def __init__(self, path, admin=True):
        self.path = path
        self.headers = {"User-Agent": "curl/8.5"}
        self.client_address = ("192.0.2.9", 1234)
        self.admin = admin

    def _cli(self):
        return False

    def _token(self):
        return None

    def _device(self):
        return (None, None)

    def _admin_ok(self):
        return self.admin


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"LABHUD_DATA": self.tmp})
        p.start()
        self.addCleanup(p.stop)
        audit._pruned["day"] = ""
        audit._full["day"] = ""

    def rows(self):
        folder = os.path.join(self.tmp, "audit")
        out = []
        if not os.path.isdir(folder):
            return out
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        out.append(json.loads(line))
        return out

    def test_write_appends_json_line(self):
        now = 1730000000.0
        ok = audit.write("admin", "setup/save-text", "main", "10.0.0.1", "Firefox on Linux", True, now=now)
        self.assertTrue(ok)
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        path = os.path.join(self.tmp, "audit", day + ".jsonl")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        e = json.loads(lines[0])
        self.assertEqual(set(e.keys()), {"ts", "who", "action", "target", "ip", "ua", "ok"})
        self.assertEqual(e["ts"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
        self.assertEqual(e["who"], "admin")
        self.assertEqual(e["action"], "setup/save-text")
        self.assertEqual(e["target"], "main")
        self.assertEqual(e["ip"], "10.0.0.1")
        self.assertEqual(e["ua"], "Firefox on Linux")
        self.assertTrue(e["ok"])

    def test_write_sanitizes_and_clips(self):
        now = 1730000000.0
        who = "a" * 70
        ua = "b" * 150
        audit.write(who, "act\nion", ip="10.0.0.1", ua=ua, now=now)
        e = self.rows()[-1]
        self.assertEqual(len(e["who"]), 60)
        self.assertEqual(e["who"], "a" * 60)
        self.assertEqual(e["action"], "act ion")
        self.assertEqual(len(e["ua"]), 120)
        self.assertEqual(e["ua"], "b" * 120)

    def test_write_no_data_dir(self):
        with mock.patch.dict(os.environ, {"LABHUD_DATA": ""}):
            result = audit.write("admin", "act")
        self.assertFalse(result)

    def test_prune_retention(self):
        T = time.time()
        old_now = T - 100 * 86400
        mid_now = T - 10 * 86400
        audit.write("admin", "old-act", now=old_now)
        audit.write("admin", "mid-act", now=mid_now)
        old_day = time.strftime("%Y-%m-%d", time.gmtime(old_now))
        mid_day = time.strftime("%Y-%m-%d", time.gmtime(mid_now))
        folder = os.path.join(self.tmp, "audit")
        self.assertTrue(os.path.isfile(os.path.join(folder, old_day + ".jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(folder, mid_day + ".jsonl")))
        # Reset so the next write triggers prune
        audit._pruned["day"] = ""
        audit.write("admin", "now-act", now=T)
        self.assertFalse(os.path.isfile(os.path.join(folder, old_day + ".jsonl")))
        self.assertTrue(os.path.isfile(os.path.join(folder, mid_day + ".jsonl")))
        # prune() returns the deleted names
        audit.write("admin", "old-again", now=old_now)
        self.assertEqual(audit.prune(folder, now=T), [old_day + ".jsonl"])

    def test_query_filters_pagination(self):
        T = (int(time.time()) // 86400) * 86400 + 3600   # 01:00 UTC today: five minutes, one day
        for i in range(5):
            audit.write("admin", f"action-{i}", ip="10.0.0.1", now=T + 60 * i)
        # all five, newest first
        rows, more = audit.query(limit=10)
        self.assertEqual(len(rows), 5)
        self.assertFalse(more)
        self.assertEqual(rows[0]["action"], "action-4")
        self.assertEqual(rows[-1]["action"], "action-0")
        # pagination: limit=2
        rows, more = audit.query(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertTrue(more)
        # offset=4, limit=2 -> only the oldest one left
        rows, more = audit.query(offset=4, limit=2)
        self.assertEqual(len(rows), 1)
        self.assertFalse(more)
        self.assertEqual(rows[0]["action"], "action-0")
        # who filter (case-insensitive)
        rows, _ = audit.query(who="ADM")
        self.assertEqual(len(rows), 5)
        # action filter
        rows, _ = audit.query(action="ACTION-2")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "action-2")
        # ok filter
        rows, _ = audit.query(ok=False)
        self.assertEqual(len(rows), 0)
        # since/until
        minute = lambda i: time.strftime("%Y-%m-%dT%H:%M", time.gmtime(T + 60 * i))
        rows, _ = audit.query(since=minute(3))
        self.assertEqual([r["action"] for r in rows], ["action-4", "action-3"])
        rows, _ = audit.query(since=minute(1), until=minute(2))   # both included
        self.assertEqual([r["action"] for r in rows], ["action-2", "action-1"])
        rows, _ = audit.query(since=time.strftime("%Y-%m-%d", time.gmtime(T)))
        self.assertEqual(len(rows), 5)

    def test_parse_query_string(self):
        args = audit.parse("who=admin&ok=0&from=2026-10-01&to=2026-10-18&offset=5&limit=20")
        self.assertEqual(args, {"who": "admin", "action": "", "ok": False,
                                "since": "2026-10-01", "until": "2026-10-18",
                                "offset": 5, "limit": 20})
        args = audit.parse("")
        self.assertIsNone(args["ok"])
        self.assertEqual(args["offset"], 0)
        self.assertEqual(args["limit"], 100)
        with self.assertRaises(ValueError):
            audit.parse("from=yesterday")
        with self.assertRaises(ValueError):
            audit.parse("limit=abc")

    def test_heard(self):
        h = FakeHandler("/api/setup/save-text")
        audit.heard(h, True)
        entries = self.rows()
        self.assertEqual(len(entries), 1)
        e = entries[-1]
        self.assertEqual(e["action"], "setup/save-text")
        self.assertEqual(e["who"], "admin")
        self.assertEqual(e["ip"], "192.0.2.9")
        self.assertEqual(e["ua"], "curl/8.5")
        self.assertTrue(e["ok"])
        # second call with the same handler object: dedup
        audit.heard(h, True)
        self.assertEqual(len(self.rows()), 1)
        # QUIET path, ok=True: no write
        h2 = FakeHandler("/api/setup/status")
        audit.heard(h2, True)
        self.assertEqual(len(self.rows()), 1)
        # QUIET path, ok=False: writes
        h3 = FakeHandler("/api/setup/status")
        audit.heard(h3, False)
        self.assertEqual(len(self.rows()), 2)
        self.assertFalse(self.rows()[-1]["ok"])

    def test_on_login_hook(self):
        self.assertIn(audit.on_login, admin.LOGIN_HOOKS)
        before = len(self.rows())
        admin.told("boss", "password", True, "10.0.0.1", "Firefox on Linux")
        admin.told("boss", "phone", False, "10.0.0.2", "pixel")
        entries = self.rows()[before:]
        self.assertEqual(len(entries), 2)
        ok_entry = entries[0]
        self.assertEqual(ok_entry["who"], "admin")
        self.assertEqual(ok_entry["action"], "sign-in")
        self.assertEqual(ok_entry["target"], "password")
        self.assertEqual(ok_entry["ip"], "10.0.0.1")
        self.assertTrue(ok_entry["ok"])
        bad_entry = entries[1]
        self.assertEqual(bad_entry["who"], "anonymous")
        self.assertEqual(bad_entry["action"], "sign-in")
        self.assertFalse(bad_entry["ok"])
        # the user name must not appear anywhere in the file
        folder = os.path.join(self.tmp, "audit")
        for name in os.listdir(folder):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                content = f.read()
            self.assertNotIn("boss", content)

    def test_client_ip(self):
        h = FakeHandler("/api/x")
        self.assertEqual(audit.client_ip(h), "192.0.2.9")
        h._client_ip = lambda: "10.0.0.99"
        self.assertEqual(audit.client_ip(h), "10.0.0.99")


if __name__ == "__main__":
    unittest.main()
