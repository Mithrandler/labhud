"""layout.export / layout.adopt and api/boards.py: a board as a file."""

import os
import tempfile
import unittest
from unittest import mock

import layout
import managed
from api import boards

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
    size = "l"

    [[page.group.card]]
    id = "b"
    name = "B"
    ping = "192.0.2.2"
'''


class FakeHandler:
    def _who(self):
        return "test"


class Layout(unittest.TestCase):
    def raw(self):
        import config
        import tomllib
        return tomllib.loads(CFG)

    def test_export_has_no_addresses(self):
        doc = layout.export(self.raw())
        self.assertEqual(doc["format"], "labhud-board")
        self.assertNotIn("192.0.2", str(doc))
        self.assertEqual([c["id"] for c in doc["pages"][0]["groups"][0]["cards"]], ["a", "b"])
        self.assertEqual(doc["pages"][0]["groups"][0]["cards"][0]["size"], "l")

    def test_round_trip(self):
        raw = self.raw()
        pages, report = layout.adopt(raw, layout.export(raw))
        self.assertEqual(report, {"unknown": [], "unplaced": []})
        self.assertEqual(layout.apply(raw, pages)["page"], raw["page"])

    def test_unknown_dropped_and_missing_kept(self):
        raw = self.raw()
        doc = layout.export(raw)
        cards = doc["pages"][0]["groups"][0]["cards"]
        cards[:] = [cards[0], {"id": "zzz"}]
        pages, report = layout.adopt(raw, doc)
        self.assertEqual(report, {"unknown": ["zzz"], "unplaced": ["b"]})
        out = layout.apply(raw, pages)
        ids = [c["id"] for g in out["page"][-1]["group"] for c in g["card"]]
        self.assertEqual(sorted(ids), ["a", "b"])

    def test_card_in_two_places_taken_once(self):
        raw = self.raw()
        doc = layout.export(raw)
        doc["pages"][0]["groups"][0]["cards"].append({"id": "a"})
        _, report = layout.adopt(raw, doc)
        self.assertEqual(report["unknown"], [])

    def test_not_our_document(self):
        raw = self.raw()
        for doc in (None, [], {}, {"format": "labhud-board", "version": 2, "pages": []},
                    {"format": "labhud-board", "version": 1, "pages": []},
                    {"format": "labhud-board", "version": 1, "pages": ["x"]}):
            with self.subTest(doc=doc):
                with self.assertRaises(ValueError):
                    layout.adopt(raw, doc)


class Routes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = os.path.join(self.tmp, "config.toml")
        with open(self.cfg, "w") as f:
            f.write(CFG)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data, LABHUD_CONFIG=self.cfg)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        r = mock.patch.object(boards, "_audit")
        self.audit = r.start()
        self.addCleanup(r.stop)
        self.h = FakeHandler()

    def test_import_preview_writes_nothing(self):
        managed.take_over()
        _, r = boards.export_board(self.h, {})
        doc = r["doc"]
        doc["pages"][0]["title"] = "Home"
        code, r = boards.import_board(self.h, {"doc": doc})
        self.assertEqual((code, r["applied"]), (200, False))
        self.assertEqual(managed.read_config()["page"][0]["title"], "MAIN")

    def test_import_apply(self):
        managed.take_over()
        _, r = boards.export_board(self.h, {})
        doc = r["doc"]
        doc["pages"][0]["title"] = "Home"
        doc["pages"][0]["groups"][0]["cards"].reverse()
        code, r = boards.import_board(self.h, {"doc": doc, "apply": True})
        self.assertEqual((code, r["applied"]), (200, True))
        page = managed.read_config()["page"][0]
        self.assertEqual(page["title"], "Home")
        self.assertEqual([c["id"] for c in page["group"][0]["card"]], ["b", "a"])
        self.audit.assert_called_once()

    def test_refuses_bad_document(self):
        managed.take_over()
        code, r = boards.import_board(self.h, {"doc": {"format": "other"}, "apply": True})
        self.assertEqual(code, 400)
        self.assertFalse(r["ok"])

    def test_apply_needs_take_over(self):
        code, r = boards.import_board(self.h, {"doc": {"format": "labhud-board", "version": 1, "pages": [{"id": "main", "groups": []}]}, "apply": True})
        self.assertIn(code, (400, 409))

    # --- theme and roles (Q3) ---

    def test_theme_saved_and_audited(self):
        managed.take_over()
        code, r = boards.save_theme(self.h, {"base": "grey", "accent": "#AABBCC"})
        self.assertEqual((code, r["accent"]), (200, "#aabbcc"))
        self.assertEqual(managed.read_config()["display"], {"base": "grey", "accent": "#aabbcc"})
        self.audit.assert_called_once_with(self.h, "board-theme", "grey #aabbcc")

    def test_theme_refuses_bad_values(self):
        managed.take_over()
        for body in ({"base": "white", "accent": "blue"}, {"base": "oled", "accent": "teal"},
                     {"base": "oled", "accent": "#12345"}, {"base": "oled"}):
            code, r = boards.save_theme(self.h, body)
            self.assertEqual(code, 400, body)
        self.assertNotIn("display", managed.read_config())
        self.audit.assert_not_called()

    def test_theme_needs_take_over(self):
        code, _ = boards.save_theme(self.h, {"base": "grey", "accent": "blue"})
        self.assertEqual(code, 409)
        self.audit.assert_called_once()
        self.assertFalse(self.audit.call_args[0][3])

    def test_roles_saved_and_removed(self):
        managed.take_over()
        code, r = boards.save_roles(self.h, {"role": "viewer", "pages": ["main", "main"]})
        self.assertEqual(code, 200)
        self.assertEqual(managed.read_config()["roles"], {"viewer": {"pages": ["main"]}})
        self.audit.assert_called_with(self.h, "board-roles", "viewer: main")
        code, _ = boards.save_roles(self.h, {"role": "viewer", "pages": []})
        self.assertEqual(code, 200)
        self.assertNotIn("roles", managed.read_config())

    def test_roles_refuses_unknown_page_and_role(self):
        managed.take_over()
        self.assertEqual(boards.save_roles(self.h, {"role": "viewer", "pages": ["nope"]})[0], 400)
        self.assertEqual(boards.save_roles(self.h, {"role": "root", "pages": ["main"]})[0], 400)
        self.assertEqual(boards.save_roles(self.h, {"role": "viewer", "pages": "main"})[0], 400)
        self.assertNotIn("roles", managed.read_config())

    def test_dropping_a_page_drops_it_from_the_roles(self):
        raw = Layout.raw(self)
        raw["roles"] = {"viewer": {"pages": ["main"]}}
        pages = [{"id": "other", "title": "O", "groups": [{"id": "g", "title": "G", "cards": ["a", "b"]}]}]
        out = layout.apply(raw, pages)
        self.assertNotIn("roles", out)


if __name__ == "__main__":
    unittest.main()
