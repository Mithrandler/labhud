"""layout.py and cardtype.py: ⚙ > Pages moves what exists, never loses a card, and proposes groups
by kind (Proxmox, NAS & backup, Docker hosts, VMs & LXC, PCs & other)."""

import os
import tomllib
import unittest

import cardtype
import config
import layout
import tomlw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a lab like the wall's: hosts with their guests, storage, a PC
LAB = '''title = "Lab"

[[page]]
id = "general"
title = "GENERAL"

  [[page.group]]
  id = "pve2"
  title = "pve2"

    [[page.group.card]]
    id = "host-pve2"
    name = "PVE2"
    ping = "192.0.2.160"
    panel = "host:pve2"
    agent = "pve2"

    [[page.group.card]]
    id = "vm-pve2-306"
    name = "rrlab"
    proxmox = "pve2/306"
    agent = "rrlab"

    [[page.group.card]]
    id = "vm-pve2-312"
    name = "gitea"
    proxmox = "pve2/312"

  [[page.group]]
  id = "storage"
  title = "Storage"
  keep_together = true

    [[page.group.card]]
    id = "synology"
    name = "SYNOLOGY"
    ping = "192.0.2.228"

    [[page.group.card]]
    id = "pbs"
    name = "PBS"
    ping = "192.0.2.102"
    agent = "pve2"
    agent_target = "wol:7c:83:34:bb:36:47"

  [[page.group]]
  id = "pve"
  title = "PVE"

    [[page.group.card]]
    id = "host-pve"
    name = "PVE"
    ping = "192.0.2.101"
    panel = "host:PVE"

    [[page.group.card]]
    id = "vm-PVE-200"
    name = "bigdocker"
    proxmox = "PVE/200"
    agent = "bigdocker"

    [[page.group.card]]
    id = "vm-PVE-205"
    name = "omv"
    proxmox = "PVE/205"

  [[page.group]]
  id = "workstations"
  title = "Workstations"

    [[page.group.card]]
    id = "gaming"
    name = "GAMING"
    tcp = "192.0.2.196:47989"

[[page]]
id = "net"
title = "NETWORK"

  [[page.group]]
  id = "lan"
  title = "LAN"

    [[page.group.card]]
    id = "router"
    name = "ROUTER"
    ping = "192.0.2.1"

  [[page.group]]
  id = "guests"
  title = "Guests"
  guests = "pve2"

  [[page.group]]
  id = "recent"
  title = "Recent"

    [[page.group.card]]
    id = "events"
    name = "RECENT"
    list = "events"
'''
AGENTS = {"pve2": ["sensors", "pve", "wol", "host"], "rrlab": ["docker", "host"], "bigdocker": ["docker", "host"]}


def ids(v, page):
    p = next(p for p in v["pages"] if p["id"] == page)
    return [(g["title"], [c["id"] for c in g["cards"]]) for g in p["groups"]]


class Kinds(unittest.TestCase):
    def test_guesses(self):
        raw = tomllib.loads(LAB)
        cards = {c["id"]: c for p in raw["page"] for g in p.get("group", []) for c in g.get("card", [])}
        got = {cid: cardtype.kind(c, AGENTS) for cid, c in cards.items()}
        self.assertEqual(got, {"host-pve2": "proxmox", "host-pve": "proxmox", "synology": "nas", "pbs": "nas",
                               "vm-pve2-306": "docker", "vm-PVE-200": "docker", "vm-PVE-205": "nas",
                               "vm-pve2-312": "vm", "gaming": "pc", "router": "pc", "events": ""})

    def test_own_kind_wins_and_is_checked(self):
        self.assertEqual(cardtype.kind({"id": "x", "name": "synology", "kind": "pc"}), "pc")
        with self.assertRaises(config.ConfigError):
            config.loads(LAB.replace('name = "GAMING"', 'name = "GAMING"\n    kind = "toaster"'))
        self.assertEqual(config.CARD_KINDS, cardtype.KINDS)


class Apply(unittest.TestCase):
    def setUp(self):
        self.raw = tomllib.loads(LAB)

    def pages(self):
        return [{"id": p["id"], "title": p["title"],
                 "groups": [{"id": g["id"], "title": g["title"], "cards": [c["id"] for c in g["cards"]]}
                            for g in p["groups"]]} for p in layout.view(self.raw)["pages"]]

    def test_the_same_layout_gives_the_same_config(self):
        self.assertEqual(layout.apply(self.raw, self.pages()), self.raw)
        with open(os.path.join(ROOT, "demo", "config.toml"), "rb") as f:
            demo = tomllib.load(f)
        v = [{"id": p["id"], "title": p["title"], "groups": [{"id": g["id"], "title": g["title"],
              "cards": [c["id"] for c in g["cards"]]} for g in p["groups"]]} for p in layout.view(demo)["pages"]]
        self.assertEqual(layout.apply(demo, v), demo)

    def test_moves_renames_and_new_groups(self):
        pages = self.pages()
        gen = pages[0]
        gen["groups"][1]["cards"].remove("pbs")
        gen["groups"].insert(0, {"id": "backup", "title": "Backup", "cards": ["pbs"]})
        gen["title"] = "HOME"
        pages.append({"id": "extra", "title": "EXTRA", "groups": [{"id": "pcs", "title": "PCs", "cards": []}]})
        pages.reverse()
        out = layout.apply(self.raw, pages)
        self.assertEqual([p["id"] for p in out["page"]], ["net", "general"])   # the empty page is left out
        home = out["page"][1]
        self.assertEqual(home["title"], "HOME")
        self.assertEqual(home["group"][0], {"id": "backup", "title": "Backup", "card": [self.raw["page"][0]["group"][1]["card"][1]]})
        self.assertTrue(home["group"][2]["keep_together"])          # other keys stay
        config.loads(tomlw.dumps(out))

    def test_refuses_to_lose_repeat_or_invent(self):
        pages = self.pages()
        pages[0]["groups"][0]["cards"].pop()
        with self.assertRaisesRegex(ValueError, "left out: vm-pve2-312"):
            layout.apply(self.raw, pages)
        pages = self.pages()
        pages[0]["groups"][0]["cards"].append("gaming")
        with self.assertRaisesRegex(ValueError, "two places"):
            layout.apply(self.raw, pages)
        pages = self.pages()
        pages[0]["groups"][0]["cards"].append("nope")
        with self.assertRaisesRegex(ValueError, "no card"):
            layout.apply(self.raw, pages)
        pages = self.pages()
        pages[1]["groups"] = [g for g in pages[1]["groups"] if g["id"] != "guests"]
        with self.assertRaisesRegex(ValueError, "guests"):
            layout.apply(self.raw, pages)
        for bad in ({"id": "Bad1", "title": "x"}, {"id": "general", "title": ""}):
            pages = self.pages()
            pages[0].update(bad)
            with self.assertRaises(ValueError):
                layout.apply(self.raw, pages)

    def test_kind_is_set_and_removed(self):
        pages = self.pages()
        pages[0]["groups"][3]["cards"] = [{"id": "gaming", "kind": "docker"}]
        out = layout.apply(self.raw, pages)
        self.assertEqual(out["page"][0]["group"][3]["card"][0]["kind"], "docker")
        pages[0]["groups"][3]["cards"] = [{"id": "gaming", "kind": ""}]
        self.assertNotIn("kind", layout.apply(out, pages)["page"][0]["group"][3]["card"][0])

    def test_icon_and_size_are_set_kept_and_removed(self):
        pages = self.pages()
        pages[0]["icon"] = "proxmox"
        pages[0]["groups"][3]["cards"] = [{"id": "gaming", "icon": "steam", "size": "xl"}]
        out = layout.apply(self.raw, pages)
        self.assertEqual(out["page"][0]["icon"], "proxmox")
        card = out["page"][0]["group"][3]["card"][0]
        self.assertEqual((card["icon"], card["size"]), ("steam", "xl"))
        config.loads(tomlw.dumps(out))
        # a layout without the keys leaves them as they are
        self.assertEqual(layout.apply(out, self.pages()), out)
        view = layout.view(out)["pages"][0]
        self.assertEqual(view["icon"], "proxmox")
        self.assertEqual(view["groups"][3]["cards"][0]["size"], "xl")
        # "" removes them
        pages[0]["icon"] = ""
        pages[0]["groups"][3]["cards"] = [{"id": "gaming", "icon": "", "size": ""}]
        out = layout.apply(out, pages)
        self.assertNotIn("icon", out["page"][0])
        self.assertNotIn("icon", out["page"][0]["group"][3]["card"][0])
        self.assertNotIn("size", out["page"][0]["group"][3]["card"][0])

    def test_size_replaces_large(self):
        self.raw["page"][0]["group"][3]["card"][0]["large"] = True
        self.assertEqual(layout.view(self.raw)["pages"][0]["groups"][3]["cards"][0]["size"], "l")
        pages = self.pages()
        pages[0]["groups"][3]["cards"] = [{"id": "gaming", "size": "m"}]
        card = layout.apply(self.raw, pages)["page"][0]["group"][3]["card"][0]
        self.assertEqual(card["size"], "m")
        self.assertNotIn("large", card)

    def test_bad_icon_or_size_is_refused(self):
        for bad in ({"icon": "Bad Name"}, {"icon": 3}, {"size": "huge"}):
            pages = self.pages()
            pages[0]["groups"][3]["cards"] = [{"id": "gaming", **bad}]
            with self.assertRaises(ValueError):
                layout.apply(self.raw, pages)
        pages = self.pages()
        pages[0]["icon"] = "../x"
        with self.assertRaises(ValueError):
            layout.apply(self.raw, pages)


class Organize(unittest.TestCase):
    def test_general_by_kind(self):
        raw = tomllib.loads(LAB)
        v = layout.organize(raw, "general", AGENTS)
        self.assertEqual(ids(v, "general"), [
            ("Proxmox", ["host-pve2", "host-pve"]),
            ("NAS & backup", ["synology", "pbs", "vm-PVE-205"]),
            ("Docker hosts", ["vm-pve2-306", "vm-PVE-200"]),
            ("VMs & LXC", ["vm-pve2-312"]),
            ("PCs & other", ["gaming"])])
        self.assertEqual(ids(v, "net"), ids(layout.view(raw), "net"))   # other pages untouched
        # what the editor sends back is a config that loads
        pages = [{"id": p["id"], "title": p["title"], "groups": [{"id": g["id"], "title": g["title"],
                  "cards": [c["id"] for c in g["cards"]]} for g in p["groups"]]} for p in v["pages"]]
        config.loads(tomlw.dumps(layout.apply(raw, pages)))

    def test_guest_groups_stay_first(self):
        v = layout.organize(tomllib.loads(LAB), "net", AGENTS)
        self.assertEqual(ids(v, "net"), [("Guests", []), ("PCs & other", ["router"]), ("Recent", ["events"])])
        with self.assertRaises(ValueError):
            layout.organize(tomllib.loads(LAB), "nope")


class HiddenColumnsDensity(unittest.TestCase):
    CFG = '''title = "Lab"

[[page]]
id = "main"
title = "MAIN"

  [[page.group]]
  id = "g"
  title = "G"

    [[page.group.card]]
    id = "a"
    name = "A"
    ping = "192.0.2.1"

    [[page.group.card]]
    id = "b"
    name = "B"
    ping = "192.0.2.2"
    hidden = true

  [[page.group]]
  id = "h"
  title = "H"
  hidden = true

    [[page.group.card]]
    id = "c"
    name = "C"
    ping = "192.0.2.3"
'''

    def raw(self):
        return tomllib.loads(self.CFG)

    def pages(self, raw):
        return layout.view(raw)["pages"]

    def send(self, raw, mutate):
        """apply() with the editor's own pages (as view gives them), changed by `mutate`."""
        pages = [{"id": p["id"], "title": p["title"], "columns": p["columns"], "density": p["density"],
                  "groups": [{"id": g["id"], "title": g["title"], "hidden": g["hidden"],
                              "cards": [{"id": c["id"], "hidden": c["hidden"]} for c in g["cards"]]} for g in p["groups"]]}
                 for p in layout.view(raw)["pages"]]
        mutate(pages)
        return layout.apply(raw, pages)

    def test_view_shows_flags(self):
        p = self.pages(self.raw())[0]
        self.assertEqual([c["hidden"] for c in p["groups"][0]["cards"]], [False, True])
        self.assertEqual([g["hidden"] for g in p["groups"]], [False, True])
        self.assertEqual((p["columns"], p["density"]), (0, ""))

    def test_untouched_keeps_everything(self):
        raw = self.raw()
        self.assertEqual(self.send(raw, lambda ps: None)["page"], raw["page"])

    def test_hide_and_show(self):
        def flip(ps):
            ps[0]["groups"][0]["cards"][0]["hidden"] = True
            ps[0]["groups"][0]["cards"][1]["hidden"] = False
            ps[0]["groups"][1]["hidden"] = False
        out = self.send(self.raw(), flip)["page"][0]["group"]
        self.assertTrue(out[0]["card"][0]["hidden"])
        self.assertNotIn("hidden", out[0]["card"][1])
        self.assertNotIn("hidden", out[1])

    def test_missing_leaves_it(self):
        raw = self.raw()
        out = layout.apply(raw, [{"id": "main", "title": "MAIN", "groups": [
            {"id": "g", "title": "G", "cards": ["a", "b"]}, {"id": "h", "title": "H", "cards": ["c"]}]}])
        self.assertTrue(out["page"][0]["group"][0]["card"][1]["hidden"])
        self.assertTrue(out["page"][0]["group"][1]["hidden"])

    def test_columns_and_density(self):
        def look(ps):
            ps[0]["columns"] = 4
            ps[0]["density"] = "compact"
        out = self.send(self.raw(), look)
        self.assertEqual((out["page"][0]["columns"], out["page"][0]["density"]), (4, "compact"))
        self.assertEqual(config.loads(tomlw.dumps(out))["pages"][0]["columns"], 4)
        out = self.send(out, lambda ps: ps[0].update(columns=0, density=""))
        self.assertNotIn("columns", out["page"][0])
        self.assertNotIn("density", out["page"][0])

    def test_bad_values_refused(self):
        raw = self.raw()
        for key, v in (("columns", 7), ("columns", -1), ("columns", True), ("columns", "3"), ("density", "airy"), ("density", 1)):
            with self.subTest(key=key, v=v):
                with self.assertRaises(ValueError):
                    self.send(raw, lambda ps: ps[0].update({key: v}))
        for where in ("group", "card"):
            with self.subTest(where=where):
                def bad(ps):
                    (ps[0]["groups"][0] if where == "group" else ps[0]["groups"][0]["cards"][0])["hidden"] = "yes"
                with self.assertRaises(ValueError):
                    self.send(raw, bad)

    def test_export_import_round_trip(self):
        raw = self.send(self.raw(), lambda ps: ps[0].update(columns=2, density="compact"))
        doc = layout.export(raw)
        self.assertEqual((doc["pages"][0]["columns"], doc["pages"][0]["density"]), (2, "compact"))
        self.assertTrue(doc["pages"][0]["groups"][1]["hidden"])
        self.assertTrue(doc["pages"][0]["groups"][0]["cards"][1]["hidden"])
        pages, report = layout.adopt(raw, doc)
        self.assertEqual(report, {"unknown": [], "unplaced": []})
        self.assertEqual(layout.apply(raw, pages)["page"], raw["page"])

    def test_import_carries_hidden_to_another_labhud(self):
        raw = self.raw()
        doc = layout.export(raw)
        doc["pages"][0]["groups"][0]["cards"][0]["hidden"] = True
        pages, _ = layout.adopt(raw, doc)
        self.assertTrue(layout.apply(raw, pages)["page"][0]["group"][0]["card"][0]["hidden"])

    def test_organize_keeps_hidden(self):
        v = layout.organize(self.raw(), "main")
        cards = {c["id"]: c for g in v["pages"][0]["groups"] for c in g["cards"]}
        self.assertTrue(cards["b"]["hidden"])


if __name__ == "__main__":
    unittest.main()
