"""/api/v1/state carries the bookmarks and the search url, for the app's palette."""
import os
import unittest

os.environ.setdefault("LABHUD_DEMO", "1")
import cardstate  # noqa: E402

BASE = {"title": "T", "pages": [], "cards": {}, "strip": [], "actions": {}, "alerts": {}, "alert_rules": [],
        "night": None, "trends": None}


class StateLinks(unittest.TestCase):
    def state(self, **extra):
        return cardstate.state({**BASE, **extra}, {})

    def test_bookmarks_and_search(self):
        st = self.state(bookmarks=[{"group": "Docs", "name": "Wiki", "url": "https://w.example"}],
                        search={"url": "https://s.example/?q=%s"})
        self.assertEqual(st["bookmarks"], [{"group": "Docs", "name": "Wiki", "url": "https://w.example"}])
        self.assertEqual(st["search"], "https://s.example/?q=%s")

    def test_none_is_empty(self):
        st = self.state()
        self.assertEqual((st["bookmarks"], st["search"]), ([], ""))


if __name__ == "__main__":
    unittest.main()
