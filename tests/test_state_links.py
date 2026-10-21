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


class StateContainers(unittest.TestCase):
    HOST = {"id": "pve", "name": "PVE", "agent": "box", "ping": "192.0.2.1"}
    OWN = {"id": "web", "name": "Web", "agent": "box", "agent_target": "docker:web", "ping": "192.0.2.2"}
    DATA = {"agents": {"box": {"containers": {"web": {"state": "running"}, "db": {"state": "exited", "image": "postgres"}},
                               "buttons": {"docker": {"db": ["agent-box-docker-db-start"], "web": ["agent-box-docker-web-restart"]}}}}}

    def topology(self):
        return {**BASE, "pages": [{"id": "g", "title": "G", "groups": [{"id": "a", "title": "A"}]}],
                "cards": {"a": [dict(self.HOST), dict(self.OWN)]}}

    def test_containers_without_a_card_of_their_own(self):
        st = cardstate.state(self.topology(), self.DATA, actions_on=True)
        self.assertEqual([(c["card"], c["name"], c["up"], [a["name"] for a in c["actions"]]) for c in st["containers"]],
                         [("pve", "db", False, ["agent-box-docker-db-start"])])   # web is a card

    def test_no_buttons_when_actions_are_off(self):
        st = cardstate.state(self.topology(), self.DATA, actions_on=False)
        self.assertEqual([(c["name"], c["actions"]) for c in st["containers"]], [("db", [])])

    def test_none_without_an_agent(self):
        self.assertEqual(cardstate.state(BASE, {})["containers"], [])


if __name__ == "__main__":
    unittest.main()
