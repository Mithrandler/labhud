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
                    self.assertEqual([[m["value"], m["sev"]] for m in got["metrics"]], want["metrics"])
                    self.assertEqual([[a["name"], a["label"], a["danger"]] for a in got["actions"]], want["actions"])
                    rows = cardstate.containers(next(c for cs in cfg["cards"].values() for c in cs if c["id"] == cid), snap)
                    self.assertEqual([[r["name"], r["up"], r["status"], r["update"], r["updating"], r["actions"]] for r in rows],
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
