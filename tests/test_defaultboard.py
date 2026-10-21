"""The board a first run gets (defaultboard.py), and seed_board() writing it once."""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")
import config  # noqa: E402
import defaultboard  # noqa: E402
import tomlw  # noqa: E402

EMPTY = {"title": "T", "page": [{"id": "general", "title": "GENERAL"}]}
DATA = {
    "proxmox": {"pve": {"guests": {"100": {"name": "web"}, "101": {"name": "tmpl", "template": True}}}},
    "agents": {"box": {"containers": {"nginx": {"image": "nginx"}}}, "_t": 1},
    "live": {"games": {"servers": [{"name": "rust"}]}},
}


def loads(raw):
    return config.loads(tomlw.dumps(raw, ""))


class Build(unittest.TestCase):
    def test_needs_only_an_empty_board(self):
        self.assertTrue(defaultboard.needs(EMPTY))
        full = {"page": [{"id": "g", "title": "G", "group": [{"id": "a", "title": "A", "card": [{"id": "x", "name": "X", "ping": "h"}]}]}]}
        self.assertFalse(defaultboard.needs(full))
        self.assertFalse(defaultboard.needs({"page": [{"id": "g", "title": "G", "games": "live.games.servers"}]}))

    def test_board_from_what_the_server_has(self):
        raw, added = defaultboard.build(EMPTY, DATA)
        self.assertEqual(added, ["docker-box-nginx", "host-pve", "pve-pve-100", "games"])
        t = loads(raw)
        self.assertEqual([p["id"] for p in t["pages"]], ["general", "games"])
        self.assertEqual([g["id"] for g in t["pages"][0]["groups"]], ["nodes", "containers-box", "guests-pve"])
        self.assertEqual([c["id"] for c in t["cards"]["guests-pve"]], ["pve-pve-100"])   # the template is left out
        self.assertEqual(EMPTY, {"title": "T", "page": [{"id": "general", "title": "GENERAL"}]})  # the input is kept

    def test_nothing_known_changes_nothing(self):
        raw, added = defaultboard.build(EMPTY, {})
        self.assertEqual((raw, added), (EMPTY, []))


class Seed(unittest.TestCase):
    def run_seed(self, raw, stored=None):
        import server
        saved = []
        store = {} if stored is None else stored
        with mock.patch.object(server.managed, "active", return_value=True), \
                mock.patch.object(server.managed, "read_config", return_value=raw), \
                mock.patch.object(server.managed, "save_config", side_effect=saved.append), \
                mock.patch.object(server.store, "load", side_effect=lambda k, d=None: store.get(k, d)), \
                mock.patch.object(server.store, "save", side_effect=store.__setitem__), \
                mock.patch.object(server.scan, "status", return_value={"found": []}), \
                mock.patch.dict(server._data, DATA, clear=True):
            server.seed_board()
            server.seed_board()
        return saved, store

    def test_made_once(self):
        saved, store = self.run_seed(EMPTY)
        self.assertEqual(len(saved), 1)
        self.assertTrue(store[defaultboard.STORE_KEY])

    def test_an_existing_board_is_never_overwritten(self):
        full = {"page": [{"id": "g", "title": "G", "group": [{"id": "a", "title": "A", "card": [{"id": "x", "name": "X", "ping": "h"}]}]}]}
        saved, store = self.run_seed(full)
        self.assertEqual(saved, [])
        self.assertTrue(store[defaultboard.STORE_KEY])   # and not looked at again

    def test_a_board_emptied_on_purpose_stays_empty(self):
        saved, _ = self.run_seed(EMPTY, {defaultboard.STORE_KEY: True})
        self.assertEqual(saved, [])


if __name__ == "__main__":
    unittest.main()
