"""The source parsers, run on saved (made-up) API answers instead of the network.

Each fixture file in tests/fixtures maps a URL fragment to the JSON body the service would
return; the fake `request` answers with the longest fragment found in the requested URL.
"""

import json
import os
import time
import types
import unittest
import urllib.error
from unittest import mock

import sources
from sources import _common, arr, custom_json, jellyfin, navidrome, omv, opnsense, pbs, proxmox, \
    qbittorrent, seerr, synology, weather

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
GB = 1024 ** 3


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


class FakeHTTP:
    """Stands in for _common.request inside one source module; remembers every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, data=None, timeout=10, method=None, raw=False):
        self.calls.append({"url": url, "headers": headers or {}, "data": data})
        matches = [k for k in self.routes if k in url]
        if not matches:
            raise OSError(f"no fixture for {url}")
        body = self.routes[max(matches, key=len)]
        if isinstance(body, Exception):
            raise body
        return json.loads(json.dumps(body))  # a fresh copy, like a real decode


def lab_env(**variables):
    """The environment with every LABHUD_* variable replaced by `variables` (names without the prefix)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(_common.PREFIX)}
    env.update({_common.PREFIX + k: v for k, v in variables.items()})
    return mock.patch.dict(os.environ, env, clear=True)


class SourceTest(unittest.TestCase):
    def serve(self, module, routes):
        fake = FakeHTTP(routes)
        patcher = mock.patch.object(module, "request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def env(self, **variables):
        patcher = lab_env(**variables)
        patcher.start()
        self.addCleanup(patcher.stop)


class Proxmox(SourceTest):
    def setUp(self):
        proxmox._VM_DISK.clear()
        proxmox._backup_cache.clear()
        self.env(PROXMOX_NODES="alpha, beta", PROXMOX_ALPHA_URL="https://alpha.example.test:8006/",
                 PROXMOX_ALPHA_TOKEN_ID="reader@pve!wall", PROXMOX_ALPHA_TOKEN_SECRET="not-a-real-secret")
        self.http = self.serve(proxmox, fixture("proxmox"))

    def test_node(self):
        out = proxmox.proxmox()
        a = out["alpha"]
        self.assertEqual((a["vms"], a["lxc"], a["cpu"], a["mem"]), (1, 1, 12.3, 25.0))
        self.assertEqual(sorted(a["guests"]), ["100", "101", "102"])  # 200 is on another node
        web, dns, spare = a["guests"]["100"], a["guests"]["101"], a["guests"]["102"]
        # VM disk from the guest agent: each filesystem once, empty ones skipped
        self.assertEqual((web["disk_used"], web["disk_total"]), (6 * GB, 30 * GB))
        self.assertEqual((dns["disk_used"], dns["disk_total"]), (2 * GB, 8 * GB))
        self.assertNotIn("disk_used", spare)
        self.assertFalse(spare["running"])
        # memory pressure is asked only for guests at 80% or more
        self.assertEqual((dns["mem"], dns["pressure"], dns["pressure_full"], dns["free"]), (90.0, 1.5, 0.25, 100))
        self.assertNotIn("pressure", web)
        self.assertEqual([s["name"] for s in a["storage"]], ["local", "local-lvm", "nas"])  # no pbs, no inactive
        self.assertEqual([s["name"] for s in a["storage_list"]], ["local-lvm", "nas"])
        self.assertTrue(a["storage"][1]["bad"])
        self.assertEqual(a["storage"][1]["value"], "95.0G/100G 95%")
        self.assertEqual(a["disk_used_of"], [105 * GB, 200 * GB])  # NFS left out
        self.assertEqual(a["storage_all"], [{"name": "alpha lvm", "value": "95.0G/100G", "bad": True},
                                            {"name": "alpha nas", "value": "500G/1000G", "bad": False}])
        self.assertEqual(out["beta"], {"unavailable": "missing LABHUD_PROXMOX_BETA_URL/_TOKEN_ID/_TOKEN_SECRET"})
        self.assertEqual(self.http.calls[0]["headers"],
                         {"Authorization": "PVEAPIToken=reader@pve!wall=not-a-real-secret"})
        self.assertTrue(all(c["url"].startswith("https://alpha.example.test:8006/api2/json/") for c in self.http.calls))

    def test_vm_disk_is_cached(self):
        proxmox.proxmox()
        proxmox.proxmox()
        self.assertEqual(sum("get-fsinfo" in c["url"] for c in self.http.calls), 1)

    def test_backups(self):
        out = proxmox.backups()
        g = out["nodes"]["alpha"]
        self.assertEqual(g["100"]["ok"], True)
        self.assertEqual(g["100"]["ts"], 2000)
        self.assertEqual((g["101"]["ok"], g["101"]["error"]), (False, "unable to open lock file"))
        self.assertTrue(g["102"]["excluded"])
        self.assertNotIn("beta", out["nodes"])
        rows = {r["name"]: r for r in out["display"]}
        self.assertTrue(rows["web (100)"]["bad"])  # older than 8 days
        self.assertTrue(rows["dns (101)"]["value"].startswith("FAILED "))
        self.assertEqual(rows["spare (102)"]["value"], "excluded")
        self.assertEqual(out["display"][-1]["name"], "spare (102)")
        self.assertEqual(out["stale"], 2)

    def test_backup_age_is_floored(self):
        # 5.5 days after the backup is still "5d ago": rounding said "6d" for last Saturday's run
        with mock.patch.object(proxmox.time, "time", return_value=2000 + 5.5 * 86400):
            rows = {r["name"]: r for r in proxmox.backups()["display"]}
        self.assertEqual(rows["web (100)"]["value"], "5d ago")

    def test_backups_keep_last_result_when_node_sleeps(self):
        proxmox.backups()
        self.http.routes = {}
        self.assertIn("alpha", proxmox.backups()["nodes"])


class OPNsense(SourceTest):
    def setUp(self):
        opnsense._previous_traffic.clear()
        self.env(OPNSENSE_URL="https://router.example.test/", OPNSENSE_KEY="k", OPNSENSE_SECRET="s")
        self.routes = fixture("opnsense")
        self.http = self.serve(opnsense, self.routes)

    def test_cpu_mem_and_rates(self):
        with mock.patch.object(opnsense.time, "monotonic", side_effect=[100.0, 110.0]):
            first = opnsense.opnsense()
            self.assertEqual(first["cpu"], 4.0)
            # Active + Wired + Buf are used; Inact and Free are available
            self.assertEqual(first["mem"], round(100 * 1001 / 4025, 1))
            self.assertEqual((first["wan_dn"], first["wan_up"], first["interfaces"]), (None, None, {}))
            self.assertEqual(first["top_lan"], self.routes["/api/diagnostics/traffic/top/lan"])
            self.routes["/api/diagnostics/traffic/interface"]["interfaces"]["wan"] = {
                "bytes received": "2000000", "bytes transmitted": "300000"}
            second = opnsense.opnsense()
        self.assertEqual((second["wan_dn"], second["wan_up"]), (100000.0, 10000.0))
        self.assertEqual(second["interfaces"]["lan"], {"dn": 0.0, "up": 0.0})
        self.assertTrue(self.http.calls[0]["headers"]["Authorization"].startswith("Basic "))

    def test_activity_failure_keeps_traffic(self):
        self.routes["/api/diagnostics/activity/getActivity"] = OSError("403")
        out = opnsense.opnsense()
        self.assertEqual((out["cpu"], out["mem"]), (None, None))
        self.assertIn("interfaces", out)


class Arr(SourceTest):
    def setUp(self):
        self.env(SONARR_URL="http://example.test/sonarr/", SONARR_KEY="sk",
                 RADARR_URL="http://example.test/radarr", RADARR_KEY="rk",
                 PROWLARR_URL="http://example.test/prowlarr", PROWLARR_KEY="pk",
                 BAZARR_URL="http://example.test/bazarr", BAZARR_KEY="bk")
        self.routes = fixture("arr")
        self.http = self.serve(arr, self.routes)

    def test_sonarr(self):
        self.assertEqual(arr.sonarr(), {"series": 2, "wanted": 3, "queued": 1,
                                        "missing": ["Show A S02E05", "? S01E01"]})
        self.assertEqual(self.http.calls[0]["headers"], {"X-Api-Key": "sk"})

    def test_radarr(self):
        self.assertEqual(arr.radarr(), {"movies": 3, "wanted": 1, "queued": 0, "missing": ["Film 3"]})

    def test_prowlarr(self):
        out = arr.prowlarr()
        self.assertEqual((out["numberOfGrabs"], out["numberOfQueries"], out["numberOfFailGrabs"],
                          out["numberOfFailQueries"]), (15, 150, 1, 2))
        self.assertEqual(out["indexers"][1], {"name": "Indexer Two", "grabs": 5, "failed": 0})

    def test_bazarr(self):
        self.assertEqual(arr.bazarr(), {"missingEpisodes": 7, "missingMovies": 2})
        self.assertEqual(self.http.calls[0]["headers"], {"X-API-KEY": "bk"})

    def test_recent(self):
        out = arr.recent()
        self.assertEqual([x["name"] for x in out["added"]], ["Film 1", "Show A S01E03"])  # one row per title
        self.assertTrue(out["added"][0]["value"].endswith(" ago"))
        self.assertEqual(out["queue"], [{"name": "Show B S03E01", "value": "75%"}, {"name": "Film 2", "value": "—"}])

    def test_recent_empty_queue(self):
        for k in ("sonarr/api/v3/queue?pageSize=5", "radarr/api/v3/queue?pageSize=5"):
            self.routes[k] = {"records": []}
        self.assertEqual(arr.recent()["queue"], [{"name": "queue empty", "value": "—"}])

    def test_calendar(self):
        day = lambda n: time.strftime("%Y-%m-%d", time.gmtime(time.time() + n * 86400))  # noqa: E731
        self.routes["sonarr/api/v3/calendar"] = [
            {"series": {"title": "Show A"}, "seasonNumber": 1, "episodeNumber": 2, "airDate": day(10)},
            {"series": {"title": "Show A"}, "seasonNumber": 1, "episodeNumber": 1, "airDate": day(3)},
            {"series": {"title": "Show B"}, "seasonNumber": 4, "episodeNumber": 1, "airDate": day(5)},
            {"series": {"title": "Show C"}, "seasonNumber": 1, "episodeNumber": 1}]
        self.routes["radarr/api/v3/calendar"] = [
            {"title": "On Disk", "hasFile": True, "digitalRelease": day(1) + "T00:00:00Z"},
            {"title": "Film Y", "inCinemas": day(-30) + "T00:00:00Z", "digitalRelease": day(7) + "T00:00:00Z"}]
        self.assertEqual(arr.calendar()["items"], [
            {"name": "Show A S01E01", "value": day(3)[5:]},
            {"name": "Show B S04E01", "value": day(5)[5:]},
            {"name": "Film Y", "value": day(7)[5:]}])


class QBittorrent(SourceTest):
    def setUp(self):
        self.env(QBITTORRENT_URL="http://example.test:8080", QBITTORRENT_USER="u", QBITTORRENT_PASS="p")
        qbittorrent._cookie["value"] = "SID=abc"
        self.addCleanup(qbittorrent._cookie.update, value=None)
        self.routes = fixture("qbittorrent")
        self.http = self.serve(qbittorrent, self.routes)

    def test_parse(self):
        out = qbittorrent.qbittorrent()
        self.assertEqual((out["dl"], out["ul"], out["active"]), (1048576, 2048, 2))
        self.assertEqual(out["torrents"][0], {"name": "linux-distro.iso", "progress": 50.0, "remaining": 2000,
                                              "eta": 60, "speed": 1000})
        self.assertEqual(out["torrents"][1]["progress"], 0)
        self.assertEqual(self.http.calls[0]["headers"]["Cookie"], "SID=abc")

    def test_logs_in_again_on_403(self):
        expired = urllib.error.HTTPError("http://example.test", 403, "Forbidden", {}, None)
        calls = []

        def answer(url, headers=None, **kw):
            calls.append(headers["Cookie"])
            if headers["Cookie"] == "SID=abc":
                raise expired
            return {"dl_info_speed": 1}

        with mock.patch.object(qbittorrent, "request", answer), \
                mock.patch.object(qbittorrent, "_login", return_value="SID=new"):
            self.assertEqual(qbittorrent._call("/api/v2/transfer/info"), {"dl_info_speed": 1})
        self.assertEqual(calls, ["SID=abc", "SID=new"])


class Synology(SourceTest):
    def setUp(self):
        synology._sid["value"] = None
        self.addCleanup(synology._sid.update, value=None)
        self.env(SYNOLOGY_URL="http://nas.example.test:5000", SYNOLOGY_USER="reader", SYNOLOGY_PASS="pw")
        self.routes = fixture("synology")
        self.http = self.serve(synology, self.routes)

    def test_parse(self):
        out = synology.synology()
        self.assertEqual((out["cpu"], out["mem"]), (5.0, 41))
        self.assertEqual((out["used"], out["total"], out["free"], out["used_percent"]), (4000, 10000, 6000, 40.0))
        self.assertEqual(out["used_of"], [4000, 10000])
        self.assertEqual(out["traffic"], [{"device": "total", "rx": 10, "tx": 20}])
        login = self.http.calls[0]
        self.assertIn(b"passwd=pw", login["data"])  # POSTed, never in the URL
        self.assertNotIn("pw", login["url"])
        self.assertIn("_sid=fake-session", self.http.calls[1]["url"])

    def test_error_after_retry(self):
        self.routes["SYNO.Core.System.Utilization"] = {"success": False}
        with self.assertRaisesRegex(RuntimeError, "DSM SYNO.Core.System.Utilization returned an error"):
            synology.synology()
        self.assertEqual(sum("auth.cgi" in c["url"] for c in self.http.calls), 2)

    def test_login_failure(self):
        self.routes["/webapi/auth.cgi"] = {"success": False}
        with self.assertRaisesRegex(RuntimeError, "DSM login failed"):
            synology.synology()


class SmallSources(SourceTest):
    def test_jellyfin(self):
        self.env(JELLYFIN_URL="http://example.test:8096/", JELLYFIN_KEY="jk")
        http = self.serve(jellyfin, fixture("jellyfin"))
        out = jellyfin.jellyfin()
        self.assertEqual((out["MovieCount"], out["SeriesCount"], out["streams"]), (120, 30, 1))
        self.assertEqual(out["now"], [{"name": "Film 1", "value": "alice"}])
        self.assertEqual(http.calls[0]["headers"], {"Authorization": 'MediaBrowser Token="jk"'})

    def test_jellyfin_nobody(self):
        self.env(JELLYFIN_URL="http://example.test:8096", JELLYFIN_KEY="jk")
        self.serve(jellyfin, {"/Items/Counts": {}, "/Sessions": []})
        self.assertEqual(jellyfin.jellyfin()["now"], [{"name": "nobody watching", "value": "—"}])

    def test_navidrome(self):
        self.env(NAVIDROME_URL="http://example.test:4533", NAVIDROME_USER="u", NAVIDROME_PASS="p")
        self.serve(navidrome, fixture("navidrome"))
        out = navidrome.navidrome()
        self.assertEqual((out["listening"], out["users"], out["songs"]), (2, 1, 4321))
        self.assertEqual(out["now"][0], {"name": "Band – Song", "value": "alice"})

    def test_navidrome_error(self):
        self.env(NAVIDROME_URL="http://example.test:4533", NAVIDROME_USER="u", NAVIDROME_PASS="p")
        self.serve(navidrome, {"/rest/": {"subsonic-response": {"status": "failed",
                                                                 "error": {"message": "Wrong username or password"}}}})
        with self.assertRaisesRegex(RuntimeError, "Wrong username or password"):
            navidrome.navidrome()

    def test_pbs(self):
        self.env(PBS_URL="https://backup.example.test:8007", PBS_TOKEN_ID="audit@pbs!wall", PBS_TOKEN_SECRET="x")
        http = self.serve(pbs, fixture("pbs"))
        self.assertEqual(pbs.pbs(), {"used_percent": 25.0, "used": 250, "total": 1000, "failed": 2})
        self.assertEqual(http.calls[0]["headers"], {"Authorization": "PBSAPIToken=audit@pbs!wall:x"})

    def test_seerr(self):
        self.env(SEERR_URL="http://example.test:5055", SEERR_KEY="k")
        out = (self.serve(seerr, fixture("seerr")), seerr.seerr())[1]
        self.assertEqual(out["total"], 9)
        self.assertEqual(out["waiting"], [{"name": "Some Series", "value": "alice"},
                                          {"name": "Some Film", "value": ""}])

    def test_omv(self):
        self.env(OMV_URL="http://example.test:9190/")
        self.serve(omv, {"example.test:9190": {"used": 1, "total": 2}})
        self.assertEqual(omv.omv(), {"used": 1, "total": 2})

    def test_weather(self):
        http = self.serve(weather, fixture("weather"))
        out = weather.weather({"latitude": 52.5, "longitude": 13.4})
        self.assertEqual(out, {"temp": 12.5, "code": 3, "max": 15.1, "min": 6.2})
        self.assertIn("latitude=52.5", http.calls[0]["url"])
        self.assertIn("timezone=auto", http.calls[0]["url"])

    def test_custom_json_single_and_labelled(self):
        self.serve(custom_json, {"one.example.test": {"a": 1}, "two.example.test": {"b": 2},
                                 "down.example.test": OSError("unreachable")})
        self.assertEqual(custom_json._make({"": "http://one.example.test/"}, 5)(), {"a": 1})
        fetch = custom_json._make(_common.labelled_urls(
            "x=http://two.example.test/,y=http://down.example.test/"), 5)
        self.assertEqual(fetch(), {"x": {"b": 2}, "y": {}})


class Common(unittest.TestCase):
    def test_labelled_urls(self):
        self.assertEqual(_common.labelled_urls("http://a.example.test/x"), {"": "http://a.example.test/x"})
        self.assertEqual(_common.labelled_urls(" a=http://a.example.test , b = http://b.example.test/?q=1 "),
                         {"a": "http://a.example.test", "b": "http://b.example.test/?q=1"})

    def test_fmt_bytes(self):
        self.assertEqual([_common.fmt_bytes(n) for n in (None, 512, 1536, 150 * GB, 3 * 1024 ** 5)],
                         ["0B", "512B", "1.5K", "150G", "3072T"])

    def test_percent(self):
        self.assertEqual((_common.percent(1, 3), _common.percent(1, 0), _common.percent(None, 2)), (33.3, None, None))

    def test_env_name(self):
        self.assertEqual(_common.env_name("my-nas.lan"), "MY_NAS_LAN")

    def test_scrub(self):
        with lab_env(NAS_PASS="hunter2-long-password", NAS_URL="http://visible.example.test"):
            text = _common.scrub("GET http://visible.example.test/?password=abc&x=1 failed: hunter2-long-password")
        self.assertNotIn("abc", text)
        self.assertNotIn("hunter2", text)
        self.assertIn("visible.example.test", text)

    def test_in_window(self):
        for hour, window, inside in ((23, (22, 6), True), (3, (22, 6), True), (12, (22, 6), False),
                                     (9, (8, 17), True), (17, (8, 17), False)):
            with self.subTest(hour=hour, window=window), \
                    mock.patch.object(_common.time, "localtime", return_value=types.SimpleNamespace(tm_hour=hour)):
                self.assertIs(_common.in_window(window), inside)
        self.assertFalse(_common.in_window(None))


class Registry(unittest.TestCase):
    def test_load_without_settings(self):
        with lab_env():
            active, inactive = sources.load({})
        self.assertEqual(active, {})
        self.assertEqual(inactive["proxmox"], ["LABHUD_PROXMOX_NODES"])
        self.assertEqual(inactive["qbt"], ["LABHUD_QBITTORRENT_URL", "LABHUD_QBITTORRENT_USER",
                                           "LABHUD_QBITTORRENT_PASS"])
        self.assertEqual(inactive["weather"], ["[weather] in config.toml"])
        self.assertEqual(inactive["recent"], ["LABHUD_SONARR_URL", "LABHUD_SONARR_KEY"])

    def test_any_of_and_section(self):
        with lab_env(RADARR_URL="http://example.test", RADARR_KEY="k", OMV_URL="http://example.test"):
            active, inactive = sources.load({"weather": {"latitude": 1, "longitude": 2}})
        self.assertEqual(sorted(active), ["calendar", "omv", "radarr", "recent", "weather"])
        self.assertEqual(inactive["sonarr"], ["LABHUD_SONARR_URL", "LABHUD_SONARR_KEY"])


if __name__ == "__main__":
    unittest.main()
