"""DNS filters, Beszel, What's Up Docker, Traefik, Speedtest Tracker, Immich, Home Assistant,
on made-up answers."""

import unittest

from sources import beszel, dns, services
from tests.test_sources import FakeHTTP, lab_env
from unittest import mock


class Base(unittest.TestCase):
    def serve(self, module, routes):
        fake = FakeHTTP(routes)
        p = mock.patch.object(module, "request", fake)
        p.start()
        self.addCleanup(p.stop)
        return fake


class Dns(Base):
    def test_technitium(self):
        self.serve(dns, {"/api/dashboard/stats/get": {"status": "ok", "response": {
            "stats": {"totalQueries": 2000, "totalBlocked": 500, "totalClients": 9, "cachedEntries": 40},
            "topBlockedDomains": [{"name": "ads.example", "hits": 120}]}}})
        with lab_env(TECHNITIUM_URL="http://192.0.2.53:5380", TECHNITIUM_TOKEN="t"):
            got = dns.technitium()
        self.assertEqual((got["queries"], got["blocked"], got["blocked_percent"]), (2000, 500, 25.0))
        self.assertEqual(got["top_blocked"], [{"name": "ads.example", "value": "120"}])

    def test_technitium_refuses(self):
        self.serve(dns, {"/api/dashboard": {"status": "invalid-token", "errorMessage": "Invalid token"}})
        with lab_env(TECHNITIUM_URL="http://192.0.2.53:5380", TECHNITIUM_TOKEN="t"), self.assertRaises(RuntimeError):
            dns.technitium()

    def test_pihole_logs_in_once(self):
        dns._pihole["sid"] = None
        fake = self.serve(dns, {"/api/auth": {"session": {"valid": True, "sid": "abc"}},
                                "/api/stats/summary": {"queries": {"total": 100, "blocked": 12, "percent_blocked": 12.0},
                                                       "clients": {"active": 4}, "gravity": {"domains_being_blocked": 90000}}})
        with lab_env(PIHOLE_URL="http://192.0.2.54", PIHOLE_PASS="p"):
            dns.pihole()
            got = dns.pihole()
        self.assertEqual(got, {"queries": 100, "blocked": 12, "blocked_percent": 12.0, "clients": 4, "gravity": 90000})
        self.assertEqual([c["url"].rsplit("/", 1)[-1] for c in fake.calls], ["auth", "summary", "summary"])
        self.assertEqual(fake.calls[1]["headers"], {"X-FTL-SID": "abc"})

    def test_adguard(self):
        self.serve(dns, {"/control/stats": {"num_dns_queries": 400, "num_blocked_filtering": 40, "num_replaced_safebrowsing": 0,
                                            "avg_processing_time": 0.0123, "top_blocked_domains": [{"t.example": 30}]}})
        with lab_env(ADGUARD_URL="http://192.0.2.55:3000", ADGUARD_USER="u", ADGUARD_PASS="p"):
            got = dns.adguard()
        self.assertEqual((got["blocked_percent"], got["avg_ms"], got["top_blocked"]), (10.0, 12.3, [{"name": "t.example", "value": "30"}]))


class Beszel(Base):
    def test_systems(self):
        beszel._token["value"] = None
        self.serve(beszel, {"/auth-with-password": {"token": "tok"}, "/systems/records": {"items": [
            {"name": "NAS", "status": "up", "info": {"cpu": 12.4, "mp": 95.1, "dp": 40}},
            {"name": "pi", "status": "down", "info": {}},
            {"name": "old", "status": "paused", "info": {}}]}})
        with lab_env(BESZEL_URL="http://192.0.2.60:8090", BESZEL_USER="a@b.c", BESZEL_PASS="p"):
            got = beszel.beszel()
        self.assertEqual((got["total"], got["up"], got["down"]), (3, 1, 1))
        self.assertEqual(got["nas"], {"up": True, "cpu": 12.4, "mem": 95.1, "disk": 40})
        self.assertEqual(got["systems"][0], {"name": "NAS", "value": "RAM 95%", "bad": True})
        self.assertEqual(got["systems"][-1], {"name": "old", "value": "paused", "bad": False})


class Services(Base):
    def test_updates(self):
        self.serve(services, {"/api/containers": [
            {"name": "jellyfin", "image": {"tag": {"value": "10.10"}}, "updateAvailable": True, "result": {"tag": "10.11"}},
            {"name": "db", "image": {"tag": {"value": "16"}}, "updateAvailable": False}]})
        with lab_env(WUD_URL="http://192.0.2.61:3000"):
            got = services.updates()
        self.assertEqual(got["available"], 1)
        self.assertEqual(got["containers"][0], {"name": "jellyfin", "value": "10.10 → 10.11", "bad": False})

    def test_traefik(self):
        self.serve(services, {"/api/overview": {"http": {"routers": {"total": 30, "warnings": 0, "errors": 1},
                                                         "services": {"total": 28, "errors": 0}},
                                                "tcp": {"routers": {"total": 2}}},
                              "/api/http/routers": [{"name": "ok@file", "status": "enabled"},
                                                    {"name": "bad@docker", "status": "disabled", "error": ["service x does not exist"]}]})
        with lab_env(TRAEFIK_URL="http://192.0.2.62:8080"):
            got = services.traefik()
        self.assertEqual((got["routers"], got["router_errors"], got["services"]), (32, 1, 28))
        self.assertEqual(got["problems"], [{"name": "bad@docker", "value": "service x does not exist", "bad": True}])

    def test_speedtest_immich(self):
        self.serve(services, {"/api/v1/results/latest": {"data": {"download": 116250000, "upload": 98125000, "ping": 4.2,
                                                                  "created_at": "2026-10-09T08:00:00Z", "healthy": True}},
                              "/api/server/statistics": {"photos": 1200, "videos": 30, "usage": 5 << 30, "usageByUser": [{}, {}]}})
        with lab_env(SPEEDTEST_URL="http://192.0.2.63:8765", SPEEDTEST_TOKEN="t", IMMICH_URL="http://192.0.2.64:2283", IMMICH_KEY="k"):
            self.assertEqual(services.speedtest()["download"], 116250000)
            self.assertEqual(services.immich(), {"photos": 1200, "videos": 30, "usage": 5 << 30, "users": 2})

    def test_homeassistant(self):
        self.serve(services, {"/api/states/sensor.rack": {"state": "27.5", "attributes": {"unit_of_measurement": "°C"}},
                              "/api/states/binary_sensor.door": {"state": "on"},
                              "/api/states/sensor.gone": OSError("404")})
        with lab_env(HA_URL="http://192.0.2.65:8123", HA_TOKEN="t", HA_ENTITIES="sensor.rack, binary_sensor.door,sensor.gone"):
            got = services.homeassistant()
        self.assertEqual(got["sensor"]["rack"], {"state": "27.5", "value": 27.5, "unit": "°C"})
        self.assertEqual(got["binary_sensor"]["door"]["value"], 1)
        self.assertEqual(got["sensor"]["gone"]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
