"""Portainer, Paperless-ngx, Tautulli and Mealie, on made-up answers; and that the setup page lists them."""

import unittest

import onboard
from sources import apps
from tests.test_more_sources import Base
from tests.test_sources import lab_env


class Apps(Base):
    def test_portainer(self):
        fake = self.serve(apps, {"/api/endpoints": [
            {"Name": "bigdocker", "Status": 1, "Snapshots": [{"RunningContainerCount": 12, "StoppedContainerCount": 2, "UnhealthyContainerCount": 1}]},
            {"Name": "minidocker", "Status": 2, "Snapshots": []},
            {"Name": "colddocker", "Status": 1, "Snapshots": [{"RunningContainerCount": 5}]}]})
        with lab_env(PORTAINER_URL="http://192.0.2.9:9000/", PORTAINER_KEY="ptr_k"):
            got = apps.portainer()
        self.assertEqual(fake.calls[0]["headers"], {"X-API-Key": "ptr_k"})
        self.assertEqual((got["environments"], got["down"], got["running"], got["stopped"], got["unhealthy"]), (3, 1, 17, 2, 1))
        self.assertEqual([r["name"] for r in got["rows"]], ["bigdocker", "minidocker", "colddocker"])   # problems first
        self.assertEqual([r["bad"] for r in got["rows"]], [True, True, False])

    def test_portainer_empty(self):
        self.serve(apps, {"/api/endpoints": []})
        with lab_env(PORTAINER_URL="http://p", PORTAINER_KEY="k"):
            self.assertEqual(apps.portainer()["rows"][0]["name"], "no environments")

    def test_paperless(self):
        fake = self.serve(apps, {"/api/statistics/": {"documents_total": 812, "documents_inbox": 7, "character_count": 99}})
        with lab_env(PAPERLESS_URL="http://p:8000", PAPERLESS_TOKEN="tok"):
            got = apps.paperless()
        self.assertEqual(fake.calls[0]["headers"]["Authorization"], "Token tok")
        self.assertEqual((got["documents"], got["inbox"]), (812, 7))

    def test_tautulli(self):
        self.serve(apps, {"cmd=get_activity": {"response": {"result": "success", "data": {
            "stream_count": "2", "stream_count_transcode": "1", "total_bandwidth": 8000,
            "sessions": [{"friendly_name": "Ana", "full_title": "Alien", "state": "playing"},
                         {"friendly_name": "Bob", "full_title": "Heat", "state": "paused"}]}}}})
        with lab_env(TAUTULLI_URL="http://t:8181", TAUTULLI_KEY="k"):
            got = apps.tautulli()
        self.assertEqual((got["streams"], got["transcoding"], got["bandwidth"]), (2, 1, 1000000))
        self.assertEqual(got["now"][1], {"name": "Bob", "value": "Heat · paused"})

    def test_tautulli_refuses_a_wrong_key(self):
        self.serve(apps, {"cmd=get_activity": {"response": {"result": "error", "message": "Invalid apikey"}}})
        with lab_env(TAUTULLI_URL="http://t:8181", TAUTULLI_KEY="bad"), self.assertRaises(RuntimeError):
            apps.tautulli()

    def test_mealie(self):
        fake = self.serve(apps, {"/api/admin/about/statistics": {"totalRecipes": 120, "totalUsers": 3,
                                                                  "totalCategories": 9, "uncategorizedRecipes": 4}})
        with lab_env(MEALIE_URL="http://m:9925", MEALIE_TOKEN="jwt"):
            got = apps.mealie()
        self.assertEqual(fake.calls[0]["headers"]["Authorization"], "Bearer jwt")
        self.assertEqual((got["recipes"], got["uncategorized"]), (120, 4))


class Catalog(unittest.TestCase):
    def test_in_the_setup_catalog_with_secret_fields_and_a_card(self):
        by = {c["name"]: c for c in onboard.catalog()}
        for name in ("portainer", "paperless", "tautulli", "mealie"):
            self.assertIn(name, by)
            self.assertTrue(by[name]["fields"], name)
            self.assertIsNotNone(onboard.source_card(name), name)
        secrets = {f["name"]: f.get("secret") for f in by["portainer"]["fields"]}
        self.assertTrue(secrets["LABHUD_PORTAINER_KEY"])
        self.assertIn("portainer", {k[0] for k in onboard.KNOWN})

    def test_not_configured_without_settings(self):
        with lab_env():
            by = {c["name"]: c for c in onboard.catalog()}
        self.assertFalse(by["mealie"]["configured"])


if __name__ == "__main__":
    unittest.main()
