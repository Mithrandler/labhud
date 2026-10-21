"""Healthchecks, Scrutiny and Uptime Kuma, on saved (made-up) answers."""

import base64
import unittest

from sources import healthchecks, scrutiny, uptimekuma
from tests.test_sources import SourceTest, fixture


class Integrations(SourceTest):
    def test_healthchecks(self):
        self.env(HEALTHCHECKS_URL="https://hc.example.test/", HEALTHCHECKS_KEY="ro-key")
        fake = self.serve(healthchecks, fixture("healthchecks"))
        out = healthchecks.healthchecks()
        self.assertEqual(fake.calls[0]["url"], "https://hc.example.test/api/v3/checks/")
        self.assertEqual(fake.calls[0]["headers"], {"X-Api-Key": "ro-key"})
        self.assertEqual((out["total"], out["up"], out["down"], out["grace"], out["paused"]), (5, 1, 1, 1, 1))
        self.assertEqual(out["checks"][:3], [{"name": "certbot", "value": "down", "bad": True},
                                             {"name": "db dump", "value": "late", "bad": False},
                                             {"name": "nameless", "value": "new", "bad": False}])
        self.assertEqual(out["checks"][-1]["name"], "old job")

    def test_scrutiny(self):
        self.env(SCRUTINY_URL="http://scrutiny.example.test:8080")
        self.serve(scrutiny, fixture("scrutiny"))
        out = scrutiny.scrutiny()
        self.assertEqual((out["total"], out["failed"], out["hottest"]), (3, 2, 41))
        self.assertEqual(out["disks"][0], {"name": "nas sdb · ST8000VN004 7.3T", "value": "41° · failed thresholds", "bad": True})
        self.assertEqual(out["disks"][1], {"name": "nvme0", "value": "failed S.M.A.R.T.", "bad": True})
        self.assertFalse(out["disks"][2]["bad"])

    def test_uptimekuma(self):
        self.env(UPTIMEKUMA_URL="http://kuma.example.test:3001/", UPTIMEKUMA_KEY="uk1_secret")
        fake = self.serve(uptimekuma, fixture("uptimekuma"))
        out = uptimekuma.uptimekuma()
        auth = fake.calls[0]["headers"]["Authorization"]
        self.assertEqual(base64.b64decode(auth.split()[1]), b":uk1_secret")
        self.assertEqual((out["total"], out["up"], out["down"], out["pending"], out["maintenance"]), (4, 1, 1, 1, 1))
        self.assertEqual([r["name"] for r in out["monitors"]], ['Mail "MX"', "Wiki", "NAS", "Gitea"])
        self.assertEqual(out["monitors"][0], {"name": 'Mail "MX"', "value": "down", "bad": True})
        self.assertEqual(out["monitors"][-1]["value"], "48 ms")

    def test_empty(self):
        self.env(UPTIMEKUMA_URL="http://k", UPTIMEKUMA_KEY="x", HEALTHCHECKS_URL="http://h",
                 HEALTHCHECKS_KEY="x", SCRUTINY_URL="http://s")
        self.serve(uptimekuma, {"/metrics": "# nothing\n"})
        self.serve(healthchecks, {"/api/v3/checks/": {"checks": []}})
        self.serve(scrutiny, {"/api/summary": {"data": {"summary": {}}}})
        self.assertEqual(uptimekuma.uptimekuma()["monitors"], [{"name": "no monitors", "value": "—"}])
        self.assertEqual(healthchecks.healthchecks()["total"], 0)
        self.assertIsNone(scrutiny.scrutiny()["hottest"])


if __name__ == "__main__":
    unittest.main()
