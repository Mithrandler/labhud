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
