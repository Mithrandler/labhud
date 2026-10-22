"""discovery.py and api/discover.py: what has no card, approved into the config or dismissed."""

import os
import tempfile
import threading
import types
import unittest
from unittest import mock

import config
import discovery
import managed
import store
from api import discover

CFG = '''title = "Lab"

[[page]]
id = "main"
title = "MAIN"

  [[page.group]]
  id = "g"
  title = "G"

    [[page.group.card]]
    id = "known"
    name = "Known"
    proxmox = "pve/100"

    [[page.group.card]]
    id = "web"
    name = "Web"
    agent = "n1"
    agent_target = "docker:web"
'''
DATA = {
    "proxmox": {"pve": {"guests": {"100": {"name": "known"}, "101": {"name": "db", "os": "l26"},
                                   "9000": {"name": "tpl", "template": True}}}},
    "agents": {"n1": {"containers": {"web": {"image": "nginx"}, "redis": {"image": "redis:7"}}}, "_x": {}},
}


def topology():
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(CFG)
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


class Candidates(unittest.TestCase):
    def test_only_what_has_no_card(self):
        keys = [c["key"] for c in discovery.candidates(topology(), DATA)]
        self.assertEqual(keys, ["pve:pve/101", "docker:n1/redis"])   # not 100, not the template, not web

    def test_odd_entries_in_the_data_are_skipped(self):
        data = {"proxmox": {"pve": {"guests": {"101": {"name": "db"}, "102": 5}}, "_ts": 1700000000, "down": None}}
        keys = [c["key"] for c in discovery.candidates(topology(), data)]
        self.assertEqual(keys, ["pve:pve/101"])

    def test_scan_results(self):
        found = [{"source": "jellyfin", "url": "http://192.0.2.5:8096", "configured": False},
                 {"source": "gitea", "url": "http://192.0.2.6:3000", "configured": True}]
        keys = [c["key"] for c in discovery.candidates(topology(), {}, found)]
        self.assertEqual(keys, ["svc:jellyfin@http://192.0.2.5:8096"])

    def test_dismissed_are_left_out(self):
        keys = [c["key"] for c in discovery.candidates(topology(), DATA, (), ["docker:n1/redis"])]
        self.assertEqual(keys, ["pve:pve/101"])


class Approve(unittest.TestCase):
    def raw(self):
        import tomllib
        return tomllib.loads(CFG)

    def test_new_page_group_and_cards_load(self):
        raw, added = discovery.approve(self.raw(), ["pve:pve/101", "docker:n1/redis", "svc:jellyfin@http://192.0.2.5:8096"], DATA)
        self.assertEqual(added, ["pve-pve-101", "docker-n1-redis", "svc-jellyfin"])
        page = raw["page"][1]
        self.assertEqual((page["id"], page["group"][0]["id"]), ("discovered", "found"))
        self.assertEqual(page["group"][0]["card"][2]["tcp"], "192.0.2.5:8096")
        import tomlw
        config._validate(__import__("tomllib").loads(tomlw.dumps(raw, "")), "t")   # the written config is valid

    def test_into_an_existing_group_and_twice(self):
        raw, added = discovery.approve(self.raw(), ["docker:n1/redis"], DATA, "main", "g")
        raw, again = discovery.approve(raw, ["docker:n1/redis"], DATA, "main", "g")
        self.assertEqual((added, again), (["docker-n1-redis"], []))
        self.assertEqual(len(raw["page"][0]["group"][0]["card"]), 3)

    def test_refused(self):
        for keys, page, group in ((["nope"], None, None), (["docker:n1/redis"], "gone", None),
                                  (["docker:n1/redis"], "main", "gone")):
            with self.assertRaises(ValueError, msg=(keys, page, group)):
                discovery.approve(self.raw(), keys, DATA, page, group)

    def test_input_never_changed(self):
        raw = self.raw()
        discovery.approve(raw, ["docker:n1/redis"], DATA)
        self.assertEqual(len(raw["page"]), 1)


class Routes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        data = os.path.join(tmp, "data")
        os.mkdir(data)
        cfg = os.path.join(tmp, "config.toml")
        with open(cfg, "w") as f:
            f.write(CFG)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=data, LABHUD_CONFIG=cfg)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        for name, value in (("DIR", data), ("_db", None), ("PROBLEM", "")):   # the store opens its folder once, at import
            p = mock.patch.object(store, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(discover, "_audit")
        self.audit = p.start()
        self.addCleanup(p.stop)
        srv = types.SimpleNamespace(_lock=threading.Lock(), _data=DATA, TOPOLOGY=topology())
        p = mock.patch.object(discover, "_srv", return_value=srv)
        p.start()
        self.addCleanup(p.stop)
        self.h = mock.Mock()
        self.h._who.return_value = "test"
        managed.take_over()

    def test_list_approve_dismiss_restore(self):
        _, r = discover.list_items(self.h, {})
        self.assertEqual([i["key"] for i in r["items"]], ["pve:pve/101", "docker:n1/redis"])
        code, r = discover.dismiss(self.h, {"keys": ["pve:pve/101"]})
        self.assertEqual(code, 200)
        self.assertEqual([i["key"] for i in discover.list_items(self.h, {})[1]["items"]], ["docker:n1/redis"])
        self.assertEqual(discover.list_items(self.h, {})[1]["dismissed"], 1)
        discover.restore(self.h, {})
        self.assertEqual(len(discover.list_items(self.h, {})[1]["items"]), 2)
        code, r = discover.approve(self.h, {"keys": ["docker:n1/redis"]})
        self.assertEqual((code, r["added"]), (200, ["docker-n1-redis"]))
        self.assertEqual(managed.read_config()["page"][1]["group"][0]["card"][0]["agent_target"], "docker:redis")

    def test_bad_input_writes_nothing_and_is_audited(self):
        for body in ({}, {"keys": []}, {"keys": ["x"]}, {"keys": [5]}, {"keys": ["docker:n1/redis"], "page": "gone"}):
            code, r = discover.approve(self.h, body)
            self.assertEqual(code, 400, body)
        self.assertEqual(len(managed.read_config()["page"]), 1)
        self.assertEqual(self.audit.call_count, 5)
        self.assertEqual(discover.dismiss(self.h, {"keys": ["junk"]})[0], 400)


if __name__ == "__main__":
    unittest.main()
