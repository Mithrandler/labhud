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


if __name__ == "__main__":
    unittest.main()
