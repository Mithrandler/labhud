"""POST /api/boards/widget: clock / calendar / frame cards from ⚙ > Dashboard, checked and audited."""

import copy
import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")
import config  # noqa: E402
import tomlw  # noqa: E402
from api import boards  # noqa: E402

RAW = {"page": [
    {"id": "general", "title": "G", "group": [{"id": "a", "title": "A", "card": [{"id": "x", "name": "X", "ping": "h"}]},
                                              {"id": "b", "title": "B"}]},
    {"id": "games", "title": "GAMES", "games": "live.games.servers"},
]}


class H:
    def _who(self):
        return "test"


class Widget(unittest.TestCase):
    def setUp(self):
        self.raw = copy.deepcopy(RAW)
        self.audit = []

        def save(raw):
            try:
                config.loads(tomlw.dumps(raw, ""))   # what managed.save_config checks
            except config.ConfigError as e:
                raise ValueError("; ".join(e.problems)) from None
            self.raw = copy.deepcopy(raw)

        for target, new in (("managed.read_config", lambda: copy.deepcopy(self.raw)), ("managed.save_config", save),
                            ("_audit", lambda h, a, t, ok=True: self.audit.append((a, t, ok)))):
            p = mock.patch(f"api.boards.{target}", new)
            p.start()
            self.addCleanup(p.stop)

    def call(self, **body):
        return boards.widget(H(), body)

    def test_clock_calendar_frame_are_saved_and_listed(self):
        for body in ({"kind": "clock", "name": "Bucharest", "timezone": "Europe/Bucharest"},
                     {"kind": "calendar", "name": "Month"},
                     {"kind": "frame", "name": "Grafana", "url": "https://g.example:3000/d/1", "height": 300}):
            status, r = self.call(op="save", page="general", group="b", **body)
            self.assertEqual(status, 200, r)
        status, r = self.call(op="list")
        self.assertEqual([(w["id"], w["kind"], w["timezone"], w["url"], w["height"]) for w in r["widgets"]],
                         [("w-bucharest", "clock", "Europe/Bucharest", "", 0), ("w-month", "calendar", "", "", 0),
                          ("w-grafana", "frame", "", "https://g.example:3000/d/1", 300)])
        self.assertEqual(len(self.audit), 3)
        self.assertTrue(all(a[0] == "board-widget" and a[2] for a in self.audit))

    def test_bad_input_is_refused_and_audited(self):
        for body in ({"kind": "clock", "name": "C", "timezone": "Mars/Base"},
                     {"kind": "frame", "name": "F", "url": "javascript:alert(1)"},
                     {"kind": "frame", "name": "F", "url": "https://x.example", "height": 5000},
                     {"kind": "iframe", "name": "F"}, {"kind": "calendar", "name": " "},
                     {"kind": "calendar", "name": "C", "page": "games", "group": "b"},
                     {"kind": "calendar", "name": "C", "page": "general", "group": "nope"}):
            body = {"page": "general", "group": "b", **body}
            status, r = self.call(op="save", **body)
            self.assertEqual(status, 400, (body, r))
        self.assertEqual(self.raw, RAW)
        self.assertTrue(all(not a[2] for a in self.audit) and self.audit)

    def test_change_move_and_remove_only_widgets(self):
        self.call(op="save", page="general", group="b", kind="clock", name="Clock", timezone="UTC")
        status, r = self.call(op="save", id="w-clock", page="general", group="a", kind="frame", name="Clock", url="https://x.example")
        self.assertEqual(status, 200, r)
        w = self.call(op="list")[1]["widgets"]
        self.assertEqual([(x["id"], x["group"], x["kind"], x["timezone"]) for x in w], [("w-clock", "a", "frame", "")])
        self.assertEqual(self.call(op="remove", id="x")[0], 400)        # a normal card is not a widget
        self.assertEqual(self.call(op="remove", id="w-clock")[0], 200)
        self.assertEqual(self.call(op="list")[1]["widgets"], [])
        self.assertEqual([c["id"] for c in self.raw["page"][0]["group"][0]["card"]], ["x"])

    def test_same_names_get_their_own_ids(self):
        for _ in range(2):
            self.call(op="save", page="general", group="b", kind="calendar", name="Month")
        self.assertEqual([w["id"] for w in self.call(op="list")[1]["widgets"]], ["w-month", "w-month-2"])


if __name__ == "__main__":
    unittest.main()
