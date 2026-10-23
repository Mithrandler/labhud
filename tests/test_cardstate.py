"""cardstate.py says what the display says: the same status, red or not, summary, numbers and
their colours, and the same buttons, for every card of the demo, over a few snapshots that push
the numbers past the marks and back (the hysteresis). The display's side runs in Node, straight
from static/app.js (tests/js/cardstate_parity.mjs); without Node the comparison is skipped."""

import copy
import json
import os
import shutil
import subprocess
import time
import unittest

import cardstate
from tests.test_smoke import DemoServer, ROOT

NODE = shutil.which("node")
HARNESS = os.path.join(ROOT, "tests", "js", "cardstate_parity.mjs")
STATUS = {True: "up", False: "down", None: None}


def shifted(data, cfg, delta, to=None):
    """A copy of the snapshot with every number a card shows moved by `delta`, or set to `to`
    (a percentage for [used, total] pairs): the demo's numbers are random, the marks are not."""
    out = copy.deepcopy(data)
    for cards in cfg["cards"].values():
        for c in cards:
            for path, _, _ in c.get("metrics") or []:
                *parent, last = path.split(".")
                node = out
                for part in parent:
                    node = node.get(part) if isinstance(node, dict) else None
                if not isinstance(node, dict):
                    continue
                v = node.get(last)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    node[last] = to if to is not None else max(0, v + delta)
                elif isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v) and v[1]:
                    node[last] = [to / 100 * v[1] if to is not None else max(0, min(v[1], v[0] + delta / 100 * v[1])), v[1]]
    return out


class Parity(DemoServer):
    def test_same_as_the_display(self):
        if not NODE:
            self.skipTest("node not found")
        cfg = json.loads(self.get("/api/config")[2])
        cfg["action_url"] = "/action/"
        data = json.loads(self.get("/api/snapshot")[2])
        down = next(c["id"] for c in cardstate.state(cfg, data)["cards"] if c["status"] == "down")
        maint = copy.deepcopy(data)
        maint.setdefault("maintenance", {})[down] = int(time.time()) + 600
        # warn (85), crit (95), crit kept by the hysteresis (89), back to fine (50), then random moves
        snaps = [data, shifted(data, cfg, 0, to=85), shifted(data, cfg, 0, to=95), shifted(data, cfg, 0, to=89),
                 shifted(data, cfg, 0, to=50), shifted(data, cfg, 40), shifted(data, cfg, 36), shifted(data, cfg, -30), maint]
        # a game server ticked on a Pterodactyl agent: its buttons come from the agent
        ptero = copy.deepcopy(data)
        ptero["agents"] = {"_t": 1, "ptero": {"buttons": {"ptero": {
            "a1f3": [f"agent-9dfe8f36-ptero-a1f3-{v}" for v in ("start", "stop", "restart")]}}}}
        snaps.append(ptero)

        run = subprocess.run([NODE, HARNESS], input=json.dumps({"cfg": cfg, "snapshots": snaps}),
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr[:2000])
        rounds = json.loads(run.stdout)
        cardstate._last_sev.clear()
        seen = set()
        for n, (snap, js) in enumerate(zip(snaps, rounds)):
            st = cardstate.state(cfg, snap, actions_on=True)
            by_id = {c["id"]: c for c in st["cards"]}
            tiles = {k: v for k, v in js.items() if k.startswith("games:")}
            js = {k: v for k, v in js.items() if k not in tiles}
            got_tiles = {f"games:{p['id']}:{i}": {"title": g["title"], "line": g["line"], "running": g["running"],
                                                  "actions": [[a["name"], a["label"], a["danger"]] for a in g["actions"]]}
                         for p in st["pages"] if p.get("games") for i, g in enumerate(p["servers"])}
            self.assertTrue(tiles)
            self.assertEqual(got_tiles, tiles)
            self.assertEqual(set(by_id), set(js))
            for cid, want in js.items():
                got = by_id[cid]
                with self.subTest(round=n, card=cid):
                    status = want["status"] if isinstance(want["status"], str) else STATUS[want["status"]]
                    self.assertEqual(None if got["status"] == "unknown" else got["status"], status)
                    self.assertEqual(got["bad"], want["bad"])
                    self.assertEqual(got["summary"], want["summary"])
                    self.assertEqual([[m["value"], m["sev"], m.get("pct"), [m["warn"], m["crit"]] if "warn" in m else None]
                                      for m in got["metrics"]], want["metrics"])
                    self.assertEqual([[a["name"], a["label"], a["danger"]] for a in got["actions"]], want["actions"])
                    rows = cardstate.containers(next(c for cs in cfg["cards"].values() for c in cs if c["id"] == cid), snap)
                    self.assertEqual([[r["name"], r["up"], r["status"], r["update"], r["updating"], r["actions"], r["scan"]] for r in rows],
                                     want["containers"])
                    seen.update(("container", r[3], r[4], bool(r[5])) for r in want["containers"])
                seen.update(m[1] for m in want["metrics"])
                seen.add(("bad", want["bad"]))
                seen.add(("status", str(want["status"])))
        # the snapshots really went through every case being compared
        self.assertTrue({"warn", "crit", "", ("bad", True), ("bad", False), ("status", "maintenance"),
                         ("container", True, False, True), ("container", True, True, False),
                         ("container", False, False, True), ("container", False, False, False)} <= seen, seen)

    def test_sections_and_rows(self):
        cfg = json.loads(self.get("/api/config")[2])
        data = json.loads(self.get("/api/snapshot")[2])
        out = cardstate.state(cfg, data, full=True)
        cards = {c["id"]: c for c in out["cards"]}
        host = cards["host-pve"]
        self.assertEqual([s["title"] for s in host["sections"]][:1], ["host"])
        self.assertTrue(any(s["title"] == "guests" for s in host["sections"]))
        guest = cards["vm-pve-100"]
        self.assertEqual(guest["sections"][0]["title"], "resources")
        self.assertTrue(all(len(r) == 3 for s in guest["sections"] for r in s["rows"]))
        self.assertTrue(cards["events"]["rows"])
        self.assertEqual(out["problems"], [c["id"] for c in out["cards"] if c["bad"]])
        self.assertEqual([p["id"] for p in out["pages"]], [p["id"] for p in cfg["pages"]])
        games = next(p for p in out["pages"] if p.get("games"))
        mc = games["servers"][0]
        self.assertEqual((mc["id"], mc["game"], mc["line"], mc["players"], mc["max"]), ("a1f3", "Minecraft", "3/20 players", 3, 20))
        self.assertEqual(mc["who"], ["alex", "sam", "kim"])
        short = next(p for p in cardstate.state(cfg, data)["pages"] if p.get("games"))
        self.assertNotIn("who", short["servers"][0])

    def test_game_route(self):
        status, _, body = self.get("/api/v1/game/a1f3")
        g = json.loads(body)
        self.assertEqual((status, g["name"], g["running"]), (200, "mc", True))
        self.assertIn("who", g)
        self.assertEqual(self.get("/api/v1/game/nope")[0], 404)


class Formats(unittest.TestCase):
    def test_like_javascript(self):
        f = cardstate.format_
        self.assertEqual(f(None, "percent"), "—")
        self.assertEqual(f(12.5, "percent"), "13%")
        self.assertEqual([f(v, "percent1") for v in (3.25, 0.15, 2.45, 1234.55, -0.05)],
                         ["3.3%", "0.2%", "2.5%", "1,234.6%", "-0.1%"])   # what Intl.NumberFormat gives
        self.assertEqual(f(1536, "bytes"), "1.5K")
        self.assertEqual(f([1024 ** 3, 4 * 1024 ** 3], "used_of"), "1.0G / 4.0G")
        self.assertEqual(f(2 * 1048576, "rate"), "2.0M")
        self.assertEqual(f(1234567, "count"), "1,234,567")
        self.assertEqual(f(True, "x"), "true")
        self.assertEqual(f(2.0, "x"), "2")
        self.assertEqual(cardstate.duration(90000), "1d")


MIXED = ["agent-a1-docker-stopwatch-stop", "vm-pve2-210-reboot", "report", "agent-a1-host-host-update",
         "shutdown-gaming", "agent-a1-docker-stopwatch-start", "wol-pbs", "game-restart-1c5e781d",
         "agent-a1-docker-web-restart", "vm-pve2-210-start"]
ORDERED = ["agent-a1-docker-stopwatch-start", "wol-pbs", "vm-pve2-210-start", "agent-a1-host-host-update", "report",
           "vm-pve2-210-reboot", "game-restart-1c5e781d", "agent-a1-docker-web-restart",
           "agent-a1-docker-stopwatch-stop", "shutdown-gaming"]


class Security(unittest.TestCase):
    """Pending security updates colour the card orange: a look, not a problem (no red bar, no bell)."""

    def test_warn_not_problem(self):
        self.assertEqual(cardstate.raw_severity("live.fleet_health.security", "count", 2), "warn")
        self.assertEqual(cardstate.raw_severity("live.fleet_health.security", "count", 0), "")
        self.assertEqual(cardstate.raw_severity("live.certs.cert_under_14", "count", 1), "crit")
        c = {"id": "packages", "metrics": [["live.fleet_health.security", "Security", "count"]]}
        self.assertFalse(cardstate.card_bad(c, {"live": {"fleet_health": {"security": 2}}}))


class AgentOffline(unittest.TestCase):
    """An agent's last numbers are kept while it is offline: dimmed and "not updated", never live."""
    CARD = {"id": "nas", "check": ["agent", "nas", "", ""], "metrics": [["agents.nas.sensors.cpu", "CPU", "percent"]]}

    def test_stale_while_offline(self):
        data = {"agents": {"nas": {"online": False, "stale": "the agent is offline", "_t": 1760000000,
                                   "sensors": {"cpu": 12}}}}
        text, kind = cardstate.note(self.CARD, data)
        self.assertEqual(kind, "stale")
        self.assertTrue(text.startswith("not updated since "))
        self.assertTrue(cardstate._frozen(self.CARD, data))
        data["agents"]["nas"] = {"online": True, "_t": 1760000000, "sensors": {"cpu": 12}}
        self.assertEqual(cardstate.note(self.CARD, data), ("", ""))
        self.assertFalse(cardstate._frozen(self.CARD, data))


class ActionOrder(unittest.TestCase):
    """The same order on every card, and red only for what takes something down (from the verb,
    so a container called "stopwatch" is not red for its START)."""

    def test_python(self):
        self.assertEqual(sorted(MIXED, key=cardstate.action_rank), ORDERED)
        self.assertEqual([cardstate.action_danger(a) for a in ORDERED], [False] * 5 + [True] * 5)

    def test_the_display_says_the_same(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("no node")
        run = subprocess.run([node, os.path.join(ROOT, "tests", "js", "action_order.mjs"), json.dumps(MIXED)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr[:1000])
        self.assertEqual(json.loads(run.stdout), [ORDERED, [False] * 5 + [True] * 5])


class GameTrack(unittest.TestCase):
    """START on a game tile is followed by the server's id: "starting…" ends when that server runs,
    even though the list was sorted again and the tile now shows another one."""

    def test_followed_by_id(self):
        if not NODE:
            self.skipTest("no node")
        run = subprocess.run([NODE, os.path.join(ROOT, "tests", "js", "game_track.mjs")],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr[:1000])
        self.assertEqual(json.loads(run.stdout), {"verb": "starting", "before": False, "after": True})


class ContainerCard(unittest.TestCase):
    """A card that is one container: UPDATE only with a newer image, no buttons while updating."""

    def test_update_only_when_available(self):
        names = [f"agent-a1b2c3d4-docker-jellyfin-{v}" for v in ("start", "stop", "restart", "update")]
        c = {"id": "j", "agent": "n100", "agent_target": "docker:jellyfin", "check": ["agent", "n100", "docker", "jellyfin"],
             "actions": names}
        data = lambda **x: {"agents": {"n100": {"online": True, "containers": {"jellyfin": dict(state="running", **x)}}}}  # noqa: E731
        verbs = lambda d: [a.rsplit("-", 1)[1] for a in cardstate.useful_actions(c, d, True)]  # noqa: E731
        self.assertEqual(verbs(data()), ["restart", "stop"])
        self.assertEqual(verbs(data(update=True)), ["update", "restart", "stop"])
        self.assertEqual(verbs(data(update=True, updating=True)), [])
        host = dict(c, agent_target="host:host", actions=["agent-a1b2c3d4-host-host-update"])
        self.assertEqual(cardstate.useful_actions(host, data(), True), ["agent-a1b2c3d4-host-host-update"])


class UpdateTrack(unittest.TestCase):
    """UPDATE on a container is followed until the agent says how it ended, about that container."""

    def test_until_the_agents_event(self):
        if not NODE:
            self.skipTest("no node")
        run = subprocess.run([NODE, os.path.join(ROOT, "tests", "js", "update_track.mjs")],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr[:1000])
        ok, bad = json.loads(run.stdout)
        self.assertEqual(ok, {"verb": "updating", "timeout": 720, "before": [False, ""], "after": [True, ""]})
        self.assertEqual(bad, {"verb": "updating", "timeout": 720, "before": [False, ""],
                               "after": [False, "jellyfin: update failed: pull jellyfin/jellyfin:latest: denied"]})


if __name__ == "__main__":
    unittest.main()


class RowAndPageIcons(unittest.TestCase):
    """A list row carries `quiet` and its `icon` after the three fields, only when there is something
    to say; a page carries the `icon` the config gave it."""

    def test_row_without_icon_stays_short(self):
        self.assertEqual(cardstate.list_row({"name": "zzz-nothing", "value": "1"}), ["zzz-nothing", "1", False])

    def test_quiet_row_keeps_its_place(self):
        self.assertEqual(cardstate.list_row({"name": "zzz-nothing", "value": "1", "quiet": True}),
                         ["zzz-nothing", "1", False, True])

    def test_own_icon_wins(self):
        self.assertEqual(cardstate.list_row({"name": "x", "value": "", "icon": "nginx"}), ["x", "", False, False, "nginx"])

    def test_icon_from_image_and_name(self):
        self.assertEqual(cardstate.list_row({"name": "web", "value": "", "image": "nginx:1.27"})[4], "nginx")
        self.assertEqual(cardstate.list_row({"name": "proxmox", "value": ""})[4], "proxmox")

    def test_page_icon_in_state(self):
        cfg = {"pages": [{"id": "a", "title": "A", "icon": "proxmox", "groups": []},
                         {"id": "b", "title": "B", "groups": []}], "cards": {}}
        pages = cardstate.state(cfg, {})["pages"]
        self.assertEqual(pages[0]["icon"], "proxmox")
        self.assertNotIn("icon", pages[1])


class InternetRates(unittest.TestCase):
    CFG = {"pages": [], "cards": {}}

    def test_wan_rates_in_state(self):
        st = cardstate.state(self.CFG, {"opnsense": {"wan_dn": 1500.0, "wan_up": 20}})
        self.assertEqual(st["internet"], {"dn": 1500.0, "up": 20})

    def test_none_without_a_router(self):
        self.assertNotIn("internet", cardstate.state(self.CFG, {}))
        self.assertNotIn("internet", cardstate.state(self.CFG, {"opnsense": {"wan_dn": None, "wan_up": 3}}))


class GuestNumbers(unittest.TestCase):
    CARD = {"id": "vm-pve-100", "name": "docker", "check": ("proxmox", "pve", "100")}
    DATA = {"proxmox": {"pve": {"guests": {"100": {"running": True, "cpu": 12.5, "mem": 40, "uptime": 90061,
                                                    "disk_used": 2 * 1024 ** 3, "disk_total": 8 * 1024 ** 3}}}}}

    def test_running_guest(self):
        g = cardstate._guest(self.CARD, self.DATA)
        self.assertEqual((g["cpu"], g["mem"], g["disk"], g["uptime"], g["node"], g["vmid"]), (12.5, 40, 25.0, 90061, "pve", "100"))

    def test_ram_at_95_percent_is_not_red_without_memory_pressure(self):
        data = {"proxmox": {"pve": {"guests": {"100": {"running": True, "mem": 95}}}}}
        self.assertEqual(cardstate._guest(self.CARD, data)["mem_sev"], "")

    def test_ram_is_red_when_processes_stall_on_memory(self):
        for stall in ({"pressure": 12}, {"pressure_full": 2}):
            data = {"proxmox": {"pve": {"guests": {"100": dict({"running": True, "mem": 50}, **stall)}}}}
            self.assertEqual(cardstate._guest(self.CARD, data)["mem_sev"], "crit")

    def test_vmid_is_text_even_when_the_config_has_a_number(self):
        card = dict(self.CARD, check=("proxmox", "pve", 100))
        self.assertEqual(cardstate._guest(card, self.DATA)["vmid"], "100")

    def test_stopped_or_unknown_guest(self):
        data = {"proxmox": {"pve": {"guests": {"100": {"running": False}}}}}
        self.assertIsNone(cardstate._guest(self.CARD, data))
        self.assertIsNone(cardstate._guest(self.CARD, {}))
        self.assertIsNone(cardstate._guest({"id": "x", "check": ("tcp", "h", 1)}, self.DATA))


class NodeLink(unittest.TestCase):
    DATA = {"proxmox": {"pve": {"guests": {"100": {"running": True, "type": "qemu"}, "101": {"running": False},
                                            "200": {"running": True, "type": "lxc"}}}}}

    def test_guest_and_host_name_the_same_node(self):
        guest = {"id": "g", "check": ("proxmox", "pve", "100")}
        host = {"id": "h", "panel": "host:pve"}
        by_metrics = {"id": "h2", "metrics": [("proxmox.pve.cpu", "CPU", "percent")]}
        self.assertEqual([cardstate._node(x) for x in (guest, host, by_metrics, {"id": "x"})], ["pve", "pve", "pve", ""])

    def test_a_stopped_guest_still_has_its_node(self):
        topo = {}
        c = cardstate.card({"id": "g", "check": ("proxmox", "pve", "101")}, self.DATA, topo, False)
        self.assertEqual(c["node"], "pve")

    def test_host_counts_vms_and_lxcs(self):
        c = cardstate.card({"id": "h", "panel": "host:pve"}, self.DATA, {}, False)
        self.assertEqual(c["guest_counts"], {"vm": [1, 2], "lxc": [1, 1]})


class BackupRows(unittest.TestCase):
    NOW = 1_000_000
    G = {"100": {"name": "docker", "ts": NOW - 3 * 3600, "ok": True},
         "101": {"name": "ha", "ts": NOW - 2 * 86400, "ok": False, "error": "timeout"},
         "102": {"name": "old", "ts": NOW - 9 * 86400, "ok": True},
         "103": {"name": "iso", "excluded": True}, "104": {"name": "new"}}

    def test_failed_and_old_first_excluded_last(self):
        rows = cardstate.backup_rows(self.G, self.NOW)
        self.assertEqual([r[0] for r in rows], ["new (104)", "old (102)", "ha (101)", "docker (100)", "iso (103)"])
        self.assertEqual(rows[2][1:], ["FAILED 2d ago: timeout", True])
        self.assertEqual(rows[3][1:], ["ok 3h ago", False])
        self.assertEqual(rows[0][1:], ["never", True])

    def test_no_data(self):
        self.assertEqual(cardstate.backup_rows(None), [])

    def test_host_panel_has_a_backup_section(self):
        data = {"backups": {"nodes": {"pve": self.G}}, "proxmox": {"pve": {"guests": {}}}}
        secs = cardstate.sections({"id": "h", "panel": "host:pve"}, data)
        self.assertIn("backup", [s["title"] for s in secs])


class GpuNumbers(unittest.TestCase):
    CARD = {"id": "h", "sensors": "temp.pve"}

    def test_gpus_of_a_host(self):
        data = {"temp": {"pve": {"gpu0_util": 20.5, "gpu0_mem": 30, "gpu0_temp": 46, "gpu1_util": 1}}}
        self.assertEqual(cardstate._gpus(self.CARD, data), [
            {"util": 20.5, "mem": 30, "temp": 46, "util_sev": "", "mem_sev": "", "temp_sev": ""}, {"util": 1, "util_sev": ""}])

    def test_gpu_numbers_carry_their_colour(self):
        card = {"id": "h2", "sensors": "temp.colour"}   # its own path: the hysteresis remembers the last colour per path
        data = {"temp": {"colour": {"gpu0_util": 95, "gpu0_mem": 85, "gpu0_temp": 80}}}
        g = cardstate._gpus(card, data)[0]
        self.assertEqual((g["util_sev"], g["mem_sev"], g["temp_sev"]), ("crit", "warn", "warn"))

    def test_no_gpu(self):
        self.assertEqual(cardstate._gpus(self.CARD, {"temp": {"pve": {"cpu_temp": 50}}}), [])
        self.assertEqual(cardstate._gpus({"id": "x"}, {}), [])


class DataCheck(unittest.TestCase):
    def test_up_reads_a_boolean_and_an_absent_one_is_unknown(self):
        c = {"id": "wg", "check": ["data", "live.wireguard.up"]}
        self.assertIs(cardstate.raw_status(c, {"live": {"wireguard": {"up": True}}}), True)
        self.assertIs(cardstate.raw_status(c, {"live": {"wireguard": {"up": False}}}), False)
        self.assertIsNone(cardstate.raw_status(c, {"live": {"wireguard": {"unavailable": "x"}}}))
        self.assertIsNone(cardstate.raw_status(c, {}))


class MenuCard(unittest.TestCase):
    def test_a_menu_card_is_in_the_state_but_on_no_page(self):
        import config
        import tempfile, textwrap
        toml = textwrap.dedent("""
            [[page]]
            id = "main"
            title = "MAIN"
              [[page.group]]
              id = "g"
              title = "G"
                [[page.group.card]]
                id = "shown"
                name = "SHOWN"
                ping = "192.0.2.1"
                [[page.group.card]]
                id = "notes"
                name = "NOTES"
                notes = true
                menu = true
            """)
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
        try:
            topo = config.load(f.name)
        finally:
            os.unlink(f.name)
        st = cardstate.state(topo, {})
        self.assertEqual(st["pages"][0]["groups"][0]["cards"], ["shown"])
        self.assertTrue(next(c for c in st["cards"] if c["id"] == "notes")["menu"])
