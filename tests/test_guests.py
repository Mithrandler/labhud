"""Guest cards added from Proxmox (`guests = "<node>"`), and /status's adoption list."""

import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")
import config  # noqa: E402
import server  # noqa: E402

TOML = '''
[[page]]
id = "general"
title = "G"
  [[page.group]]
  id = "pve"
  title = "PVE"
  guests = "pve"
    [[page.group.card]]
    id = "mine"
    name = "kept by hand"
    proxmox = "pve/100"
  [[page.group]]
  id = "tagged"
  title = "T"
  guests = "lab"
  guests_tag = "labhud"
'''

PX = {
    "pve": {"guests": {"100": {"name": "a"}, "101": {"name": "b", "tags": []}, "102": {"name": "tmpl", "template": True}}},
    "lab": {"guests": {"200": {"name": "c", "tags": ["labhud"]}, "201": {"name": "d", "tags": ["other"]}}},
}


def ids(topology, group):
    return [c["id"] for c in topology["cards"][group]]


class Guests(unittest.TestCase):
    def setUp(self):
        self.topo = config.loads(TOML)

    def test_cards_follow_the_guests(self):
        t = server.auto_guests(self.topo, PX)
        self.assertEqual(ids(t, "pve"), ["mine", "auto-pve-101"])  # 100 has a card, 102 is a template
        self.assertEqual(ids(t, "tagged"), ["auto-lab-200"])
        self.assertEqual(t["cards"]["pve"][1]["check"], ["proxmox", "pve", 101])
        gone = {"pve": {"guests": {"100": {"name": "a"}}}, "lab": {"guests": {}}}
        t2 = server.auto_guests(self.topo, gone, t)
        self.assertEqual(ids(t2, "pve"), ["mine"])
        self.assertEqual(ids(t2, "tagged"), [])

    def test_a_silent_node_keeps_its_cards(self):
        t = server.auto_guests(self.topo, PX)
        t2 = server.auto_guests(self.topo, {"pve": {"unavailable": "timeout"}, "lab": PX["lab"]}, t)
        self.assertEqual(ids(t2, "pve"), ["mine", "auto-pve-101"])

    def test_config_checks(self):
        with self.assertRaises(config.ConfigError) as e:
            config.loads('[[page]]\nid = "g"\ntitle = "G"\n  [[page.group]]\n  id = "x"\n  title = "X"\n  guests_tag = "labhud"\n')
        self.assertIn("guests_tag has no effect without guests", str(e.exception.problems))

    def test_adoption(self):
        with mock.patch.object(server, "TOPOLOGY", self.topo), mock.patch.dict(server._data, {"proxmox": {
                "pve": {"guests": {"101": {"name": "b", "type": "lxc"}}}, "down": {"unavailable": "x"}}}):
            a = server.adoption()
        self.assertEqual(a["new"], [{"node": "pve", "vmid": 101, "name": "b", "type": "lxc", "tags": []}])
        self.assertEqual(a["gone"], [{"card": "mine", "node": "pve", "vmid": 100}])


if __name__ == "__main__":
    unittest.main()
