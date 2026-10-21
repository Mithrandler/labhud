"""The Docker source: states, problems first, labhud.* labels."""

import unittest

from sources import docker

C = [
    {"Names": ["/jellyfin"], "State": "running", "Status": "Up 3 hours (healthy)",
     "Labels": {"labhud.enable": "true", "labhud.name": "Jellyfin", "labhud.group": "Media", "labhud.url": "http://192.0.2.5:8096"}},
    {"Names": ["/db"], "State": "running", "Status": "Up 2 days (unhealthy)", "Labels": {"labhud.group": "media"}},
    {"Names": ["/loop"], "State": "restarting", "Status": "Restarting (1) 5 seconds ago", "Labels": {}},
    {"Names": ["/job"], "State": "exited", "Status": "Exited (0) 3 hours ago", "Labels": {}},
    {"Names": ["/crashed"], "State": "exited", "Status": "Exited (137) 1 hour ago", "Labels": {"labhud.url": "javascript:x"}},
]


class Docker(unittest.TestCase):
    def test_summary(self):
        d = docker.summarize(C)
        self.assertEqual((d["total"], d["running"], d["unhealthy"], d["restarting"], d["stopped"]), (5, 2, 1, 1, 2))
        names = [r["name"] for r in d["containers"]]
        self.assertEqual(names[:3], ["crashed", "db", "loop"])  # problems first, by name
        self.assertEqual(names[-1], "Jellyfin")
        self.assertEqual(d["containers"][-1], {"name": "Jellyfin", "value": "up 3 hours", "bad": False, "url": "http://192.0.2.5:8096"})
        self.assertNotIn("url", d["containers"][0])  # only http(s) links
        self.assertEqual([r["name"] for r in d["labelled"]], ["Jellyfin"])
        self.assertEqual([r["name"] for r in d["group"]["media"]], ["db", "Jellyfin"])

    def test_exit_codes(self):
        self.assertEqual(docker.state(C[3]), ("stopped", "exited (0) 3 hours ago", False))
        self.assertTrue(docker.state(C[4])[2])

    def test_empty(self):
        self.assertEqual(docker.summarize([])["containers"][0]["name"], "no containers")


if __name__ == "__main__":
    unittest.main()
