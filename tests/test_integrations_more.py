"""CrowdSec, Gitea Actions, Nextcloud and authentik, on made-up answers."""

import time
import unittest
from unittest import mock

from sources import authentik, crowdsec, gitea, nextcloud
from tests.test_sources import SourceTest


def iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(time.time() - seconds_ago))


class MoreIntegrations(SourceTest):
    def test_crowdsec_own_ban_first(self):
        self.env(CROWDSEC_URL="http://lapi:8080", CROWDSEC_KEY="b", CROWDSEC_WATCH="198.51.100.7,home.example.test")
        fake = self.serve(crowdsec, {"/v1/decisions": [
            {"value": "203.0.113.5", "scope": "Ip", "type": "ban", "scenario": "crowdsecurity/ssh-bf", "duration": "3h59m1.2s"},
            {"value": "203.0.113.6", "scope": "Ip", "type": "ban", "scenario": "crowdsecurity/ssh-bf", "duration": "1m"},
            {"value": "198.51.100.0/24", "scope": "Range", "type": "ban", "scenario": "crowdsecurity/http-probing",
             "duration": "2h1m3s"},
            {"value": "192.0.2.9", "scope": "Ip", "type": "captcha", "scenario": "x", "duration": "1m"}]})
        with mock.patch.object(crowdsec.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.200", 0))]):
            out = crowdsec.crowdsec()
        self.assertIn("origins=crowdsec,cscli", fake.calls[0]["url"])
        self.assertEqual(fake.calls[0]["headers"], {"X-Api-Key": "b"})
        self.assertEqual((out["bans"], out["own_banned"], out["scenarios"]), (3, 1, 2))
        self.assertEqual(out["rows"][0], {"name": "YOUR 198.51.100.0/24 is banned", "value": "http-probing · 2h 1m left", "bad": True})
        self.assertEqual(out["rows"][1], {"name": "ssh-bf", "value": "2 banned"})

    def test_crowdsec_two_instances_one_down(self):
        self.env(CROWDSEC_URL="router=http://r:8080,vps=http://v:8080", CROWDSEC_KEY="router=k1==,vps=k2/",
                 CROWDSEC_WATCH="198.51.100.7")
        fake = self.serve(crowdsec, {"http://r:8080/v1/decisions": [{"value": "203.0.113.5", "scope": "Ip", "scenario": "a/x"}],
                                     "http://v:8080/v1/decisions": OSError("timed out")})
        out = crowdsec.crowdsec()
        self.assertEqual([c["headers"]["X-Api-Key"] for c in fake.calls], ["k1==", "k2/"])
        self.assertEqual((out["bans"], out["by"]), (1, {"router": 1}))
        self.assertEqual(out["rows"][0], {"name": "vps: no answer", "value": "timed out", "bad": True})
        fake.routes["http://v:8080/v1/decisions"] = [{"value": "198.51.100.7", "scope": "Ip", "scenario": "b/http-probing", "duration": "1h"}]
        out = crowdsec.crowdsec()
        self.assertEqual(out["rows"][0]["name"], "YOUR 198.51.100.7 is banned on vps")
        self.assertEqual(out["by"], {"router": 1, "vps": 1})

    def test_gitea_failed_first(self):
        self.env(GITEA_URL="https://git.example.test", GITEA_TOKEN="t", GITEA_REPOS="me/a,me/b,me/c")
        fake = self.serve(gitea, {
            "/repos/me/a/actions/runs": {"workflow_runs": [{"status": "completed", "conclusion": "success",
                                                            "display_title": "fine", "completed_at": iso(60)}]},
            "/repos/me/c/actions/runs": {"workflow_runs": [{"status": "waiting", "started_at": "0001-01-01T00:00:00Z"}]},
            "/repos/me/b/actions/runs": {"workflow_runs": [{"status": "completed", "conclusion": "failure",
                                                            "display_title": "broke", "completed_at": iso(7200)},
                                                           {"status": "queued"}]}})
        out = gitea.gitea()
        self.assertEqual(fake.calls[0]["headers"]["Authorization"], "token t")
        self.assertEqual((out["failed"], out["queued"]), (1, 1))
        self.assertEqual([(r["name"], r.get("bad", False)) for r in out["runs"]], [("b · broke", True), ("a · fine", False), ("c", False)])
        self.assertEqual(out["runs"][2], {"name": "c", "value": "no runner takes its runs", "quiet": True})
        self.assertTrue(out["runs"][0]["value"].startswith("failure · 2h"))

    def test_nextcloud(self):
        self.env(NEXTCLOUD_URL="https://cloud.example.test/", NEXTCLOUD_TOKEN="n")
        fake = self.serve(nextcloud, {"/serverinfo/api/v1/info": {"ocs": {"data": {
            "nextcloud": {"system": {"version": "35.0.2", "freespace": 2 * 1024 ** 4,
                                     "update": {"available": True, "available_version": "35.0.3"},
                                     "apps": {"num_updates_available": 2}},
                          "storage": {"num_users": 4, "num_files": 1200}},
            "activeUsers": {"last5minutes": 1, "last24hours": 3}}}}})
        out = nextcloud.nextcloud()
        self.assertEqual(fake.calls[0]["headers"]["NC-Token"], "n")
        self.assertEqual((out["version"], out["update"], out["apps_update"], out["users"]), ("35.0.2", "35.0.3", 2, 4))
        self.assertIn({"name": "users", "value": "1 now · 3 today · 4 in all"}, out["rows"])

    def test_authentik(self):
        self.env(AUTHENTIK_URL="http://auth:9000", AUTHENTIK_TOKEN="a")
        self.serve(authentik, {
            "/events/events/": {"results": [{"created": iso(60), "context": {"username": "bob"}, "client_ip": "203.0.113.9"}] * 6
                                + [{"created": iso(3 * 86400), "context": {"username": "old"}}]},
            "/core/users/": {"pagination": {"count": 7}},
            "/outposts/instances/?": {"results": [{"pk": "p1", "name": "embedded"}, {"pk": "p2", "name": "proxy"}]},
            "/outposts/instances/p1/health/": [{"last_seen": iso(30)}],
            "/outposts/instances/p2/health/": [{"last_seen": iso(3600)}]})
        out = authentik.authentik()
        self.assertEqual((out["failed_24h"], out["users"], out["outposts"], out["outposts_down"]), (6, 7, 2, 1))
        self.assertEqual(out["rows"][0], {"name": "bob from 203.0.113.9", "value": "6 failed", "bad": True})
        self.assertIn({"name": "outpost proxy", "value": "not checking in", "bad": True}, out["rows"])


if __name__ == "__main__":
    unittest.main()
