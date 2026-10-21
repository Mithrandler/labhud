"""fleet.updates: every container with a newer image, on every agent (server.fleet_updates)."""

import os
import unittest

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class FleetUpdates(unittest.TestCase):
    def test_rows_host_by_host_with_their_button(self):
        every = {
            "_t": 1,
            "rrlab": {"containers": {"sonarr": {"update": True, "image": "sonarr:latest"}, "radarr": {}},
                      "buttons": {"docker": {"sonarr": ["agent-a-docker-sonarr-start", "agent-a-docker-sonarr-update"]}}},
            "big": {"containers": {"act": {"updating": True, "update": True, "image": "act:1"},
                                   "web": {"update": True}}, "buttons": {}},
        }
        f = server.fleet_updates(every)
        self.assertEqual([(r["host"], r["name"], r["action"]) for r in f["updates"]],
                         [("big", "act", None), ("big", "web", None), ("rrlab", "sonarr", "agent-a-docker-sonarr-update")])
        self.assertEqual(f["update_count"], 3)
        self.assertEqual(f["updates"][0]["value"], "updating…")


if __name__ == "__main__":
    unittest.main()


class LabelCards(unittest.TestCase):
    TOP = {"pages": [{"id": "media", "title": "MEDIA", "groups": [{"id": "apps", "title": "Apps"}]}],
           "cards": {"apps": [{"id": "own", "name": "Own", "agent": "box", "agent_target": "docker:own"}]}}

    def test_cards_where_the_labels_say(self):
        every = {"_t": 1, "box": {"containers": {
            "jf": {"labels": {"labhud.name": "Jellyfin", "labhud.page": "Media", "labhud.url": "https://jf.lan"}},
            "pt": {"labels": {"labhud.name": "Portainer", "labhud.url": "javascript:alert(1)"}},
            "own": {"labels": {"labhud.name": "Twice"}},
            "plain": {"labels": {"traefik.enable": "true"}}}}}
        t = server.label_cards(self.TOP, every)
        made = {c["id"]: (gid, c.get("url")) for gid, cs in t["cards"].items() for c in cs if c.get("labels")}
        self.assertEqual(made, {"docker-box-jf": ("apps", "https://jf.lan"), "docker-box-pt": ("discovered", None)})
        self.assertEqual([p["id"] for p in t["pages"]], ["media", "discovered"])
        self.assertEqual(self.TOP["pages"][0]["groups"], [{"id": "apps", "title": "Apps"}])   # the file's stays as it was
        jf = next(c for c in t["cards"]["apps"] if c["id"] == "docker-box-jf")
        self.assertEqual((jf["agent"], jf["agent_target"], jf["check"]), ("box", "docker:jf", ["agent", "box", "docker", "jf"]))

    def test_nothing_labelled_nothing_changes(self):
        self.assertIs(server.label_cards(self.TOP, {"box": {"containers": {"a": {}}}}), self.TOP)
