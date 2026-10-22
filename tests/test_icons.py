"""Cards' icons: their own, their source's, their kind's; every name maps to a file or a line icon."""

import json
import os
import unittest

import cardtype

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CardIcon(unittest.TestCase):

    def test_own_icon_wins(self):
        self.assertEqual(
            cardtype.icon({"id": "a", "name": "A", "icon": "plex", "metrics": [["jellyfin.x", "X", "count"]]}),
            "plex",
        )

    def test_source_icon_from_metric(self):
        self.assertEqual(
            cardtype.icon({"id": "a", "name": "A", "metrics": [["jellyfin.MovieCount", "Movies", "count"]]}),
            "jellyfin",
        )

    def test_source_icon_from_list(self):
        self.assertEqual(
            cardtype.icon({"id": "a", "name": "A", "list": "crowdsec.rows"}),
            "crowdsec",
        )

    def test_docker_container_card(self):
        self.assertEqual(
            cardtype.icon({"id": "a", "name": "A", "check": ["agent", "host1", "docker", "web"]}),
            "docker",
        )

    def test_guest_is_vm(self):
        self.assertEqual(
            cardtype.icon({"id": "a", "name": "web", "check": ["proxmox", "pve", 101]}),
            "vm",
        )

    def test_ping_pc(self):
        self.assertEqual(
            cardtype.icon({"id": "desk", "name": "DESKTOP", "check": ["ping", "10.0.0.5"]}),
            "pc",
        )

    def test_nas_by_name(self):
        self.assertEqual(
            cardtype.icon({"id": "nas", "name": "NAS", "check": ["ping", "10.0.0.6"]}),
            "nas",
        )

    def test_not_a_machine(self):
        self.assertEqual(
            cardtype.icon({"id": "recent", "name": "RECENT", "list": "events.recent"}),
            "",
        )


class IconFiles(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "design", "tokens.json")) as f:
            tokens = json.load(f)
        cls.line = set(tokens["icons"])
        cls.svc = os.path.join(ROOT, "static", "icons", "svc")

    def exists(self, name):
        if name in self.line:
            return True
        return (
            os.path.isfile(os.path.join(self.svc, name + ".svg"))
            and os.path.isfile(os.path.join(self.svc, name + ".png"))
        )

    def test_every_source_icon_exists(self):
        for v in cardtype.SOURCE_ICONS.values():
            with self.subTest(icon=v):
                self.assertTrue(self.exists(v), msg=v)

    def test_every_kind_icon_exists(self):
        for v in cardtype.KIND_ICONS.values():
            with self.subTest(icon=v):
                self.assertTrue(self.exists(v), msg=v)

    def test_every_listed_icon_was_fetched(self):
        with open(os.path.join(ROOT, "design", "icons.txt")) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                self.assertTrue(os.path.isfile(os.path.join(self.svc, line + ".svg")), msg=line)
                self.assertTrue(os.path.isfile(os.path.join(self.svc, line + ".png")), msg=line)

    def test_license_shipped(self):
        self.assertTrue(os.path.isfile(os.path.join(self.svc, "LICENSE")))

    def test_no_script_in_svg(self):
        for fname in os.listdir(self.svc):
            if not fname.endswith(".svg"):
                continue
            with open(os.path.join(self.svc, fname), "rb") as f:
                content = f.read().lower()
            self.assertNotIn(b"<script", content, msg=fname)


class IconFor(unittest.TestCase):
    """cardtype.icon_for: tag, OS, image, game, then the words of the name."""

    def test_order_tag_os_image_name(self):
        self.assertEqual(cardtype.icon_for("Kali", os="debian", image="plex", tags=["icon-sonarr"]), "sonarr")
        self.assertEqual(cardtype.icon_for("Kali", os="debian", image="plex"), "debian-linux")
        self.assertEqual(cardtype.icon_for("Kali", image="plex"), "plex")
        self.assertEqual(cardtype.icon_for("Kali"), "kali-linux")
        self.assertEqual(cardtype.icon_for("thing nobody has"), "")

    def test_image_names(self):
        self.assertEqual(cardtype.image_name("lscr.io/linuxserver/sonarr:latest"), "sonarr")
        self.assertEqual(cardtype.image_name("docker.io/library/postgres:16"), "postgres")
        self.assertEqual(cardtype.image_name("ghcr.io/goauthentik/server:2025.8"), "goauthentik")
        self.assertEqual(cardtype.image_name("localhost:5000/team/app@sha256:abc"), "team")
        self.assertEqual(cardtype.image_name("redis"), "redis")
        self.assertEqual(cardtype.image_name(""), "")

    def test_every_alias_points_at_a_shipped_icon(self):
        shipped = cardtype.shipped()
        self.assertGreater(len(cardtype.aliases()), 50)
        for k, v in cardtype.aliases().items():
            with self.subTest(alias=k):
                self.assertIn(v, shipped)

    def test_tag_must_be_a_valid_slug(self):
        self.assertEqual(cardtype.icon_for("x", tags=["icon-../etc"]), "")

    def test_table(self):
        cases = [
            ({"name": "x", "os": "ubuntu"}, "ubuntu-linux"),
            ({"name": "x", "os": "alpine"}, "alpine-linux"),
            ({"name": "x", "os": "archlinux"}, "arch-linux"),
            ({"name": "x", "os": "win11"}, "windows-11"),
            ({"name": "x", "os": "win10"}, "windows-10"),
            ({"name": "x", "os": "l26"}, ""),
            ({"name": "x", "os": "nixos"}, "nixos"),
            ({"name": "x", "image": "docker.io/library/redis:7"}, "redis"),
            ({"name": "x", "image": "lscr.io/linuxserver/radarr"}, "radarr"),
            ({"name": "x", "image": "ghcr.io/home-assistant/home-assistant:stable"}, "home-assistant"),
            ({"name": "x", "image": "pihole/pihole:latest"}, "pi-hole"),
            ({"name": "x", "image": "mongo:6"}, "mongodb"),
            ({"name": "x", "image": "jc21/nginx-proxy-manager"}, "nginx-proxy-manager"),
            ({"name": "Pi-hole"}, "pi-hole"),
            ({"name": "pve2"}, "proxmox"),
            ({"name": "Uptime Kuma"}, "uptime-kuma"),
            ({"name": "Windows 11 box"}, "windows-11"),
            ({"name": "qbt"}, "qbittorrent"),
            ({"name": "Jellyfin"}, "jellyfin"),
            ({"name": "x", "tags": ["icon-plex"]}, "plex"),
            ({"name": "x", "tags": ["web", "prod"]}, ""),
            ({"name": ""}, ""),
            ({"name": "x", "game": "Minecraft"}, "minecraft"),
            ({"name": "Valheim"}, "valheim"),
        ]
        for kwargs, expected in cases:
            with self.subTest(**{"case": repr(kwargs)}):
                self.assertEqual(cardtype.icon_for(**kwargs), expected)

    def test_game_servers_always_get_an_icon(self):
        for game, name, expected in (("Minecraft", "mc", "minecraft"), ("Rust", "Carbon", "rust"),
                                     ("Valheim Vanilla", "Pykeron Valheim World", "valheim"),
                                     ("Counter-Strike 1.6", "Zombie Plague", "counter-strike"), ("", "CS2 Deathmatch", "counter-strike"),
                                     ("Palworld", "pal", "palworld"), ("Terraria", "t", "terraria"), ("7 Days to Die", "", "7-days-to-die"),
                                     ("Factorio", "f", "factorio"), ("Some Steam Game", "gta", "steam"),
                                     ("Some Unknown Game", "x", "games"), ("", "", "games")):
            with self.subTest(game=game, name=name):
                self.assertEqual(cardtype.game_icon(game, name), expected)

    def test_every_game_icon_is_shipped(self):
        for _, icon in cardtype.GAME_ICONS:
            with self.subTest(icon=icon):
                self.assertIn(icon, cardtype.shipped())

    def test_the_icons_the_containers_and_games_carry(self):
        import cardstate
        data = {"agents": {"h": {"containers": {"tv": {"state": "running", "image": "lscr.io/linuxserver/sonarr:latest"}}}}}
        box = cardstate.containers({"id": "c", "agent": "h"}, data)[0]
        self.assertEqual((box["icon"], box["image"]), ("sonarr", "lscr.io/linuxserver/sonarr:latest"))
        game = cardstate.game({"id": "g1", "game": "Minecraft", "state": "running"}, 0, {}, {"actions": {}})
        self.assertEqual(game["icon"], "minecraft")
        self.assertEqual(cardstate.game({"id": "g2", "game": "Valheim", "state": "running"}, 0, {}, {"actions": {}})["icon"], "valheim")


class IconCache(unittest.TestCase):
    """iconcache.get: a downloaded icon is kept, a bad one refused, a missing one not asked again."""

    SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'

    def setUp(self):
        import tempfile
        import iconcache
        from tests.test_lock import FakeStore
        self.m = iconcache
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (iconcache.store, iconcache._download, iconcache.directory)
        iconcache.store = FakeStore()
        iconcache.directory = lambda: self.tmp.name
        iconcache._missing.clear()
        self.asked = []
        self.answer = self.SVG
        iconcache._download = lambda slug, ext: (self.asked.append((slug, ext)), self.answer)[1]

    def tearDown(self):
        self.m.store, self.m._download, self.m.directory = self.old
        self.tmp.cleanup()

    def test_fetched_once_then_from_disk(self):
        self.assertEqual(self.m.get("zot", "svg"), (self.SVG, "image/svg+xml"))
        self.assertEqual(self.m.get("zot", "svg"), (self.SVG, "image/svg+xml"))
        self.assertEqual(self.asked, [("zot", "svg")])

    def test_refused_names(self):
        for slug in ("../x", "A", "x" * 80, "", "a_b"):
            self.assertIsNone(self.m.get(slug, "svg"))
        self.assertIsNone(self.m.get("zot", "gif"))
        self.assertEqual(self.asked, [])

    def test_active_svg_is_refused_and_remembered(self):
        for bad in (b"<svg><script>alert(1)</script></svg>", b'<svg onload="x()"></svg>', b'<svg><a href="javascript:x()"/></svg>',
                    b"<html></html>", b"", b"<svg>" + b"x" * (300 * 1024)):
            self.m._missing.clear()
            self.answer = bad
            self.assertIsNone(self.m.get("zot", "svg"), msg=bad[:30])
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_a_missing_icon_is_not_asked_again_for_a_day(self):
        self.answer = None
        self.assertIsNone(self.m.get("nope", "svg", now=1000))
        self.assertIsNone(self.m.get("nope", "svg", now=1000 + 3600))
        self.assertEqual(len(self.asked), 1)
        self.assertIsNone(self.m.get("nope", "svg", now=1000 + 86401))
        self.assertEqual(len(self.asked), 2)

    def test_offline_asks_nobody(self):
        self.assertIsNone(self.m.get("zot", "svg", online=False))
        self.assertEqual(self.asked, [])

    def test_png_must_be_a_png(self):
        self.answer = b"\x89PNG\r\n\x1a\n" + b"0" * 10
        self.assertEqual(self.m.get("zot", "png")[1], "image/png")
        self.m._missing.clear()
        self.answer = b"GIF89a"
        self.assertIsNone(self.m.get("other", "png"))

    def test_without_a_data_folder_only_the_shipped_set(self):
        self.m.directory = lambda: ""
        self.assertIsNone(self.m.get("zot", "svg"))
        self.assertEqual(self.asked, [])


if __name__ == "__main__":
    unittest.main()
