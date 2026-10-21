"""init.py: discovery on made-up Proxmox answers, and files that labhud accepts."""

import os
import tempfile
import unittest
from unittest import mock

import config
import init
import onboard

ANSWERS = {
    "/version": {"data": {"version": "9.0.3"}},
    "/nodes": {"data": [{"node": "beta"}, {"node": "alpha"}]},
    "/cluster/resources": {"data": [
        {"node": "alpha", "vmid": 101, "name": "docker", "type": "qemu"},
        {"node": "alpha", "vmid": 100, "name": "dns \"main\"", "type": "lxc"},
        {"node": "beta", "vmid": 200, "name": "backup", "type": "qemu"},
        {"node": "beta", "vmid": 9000, "name": "tmpl", "type": "qemu", "template": 1},
    ]},
    "/access/permissions": {"data": {"/": {"Sys.Audit": 1, "VM.Audit": 1}}},
    "/cluster/status": {"data": [{"type": "cluster", "name": "lab"},
                                 {"type": "node", "name": "beta", "ip": "192.0.2.10", "local": 1},
                                 {"type": "node", "name": "alpha", "ip": "192.0.2.11", "local": 0}]},
}


def fake_get(url, header=None, timeout=10):
    return ANSWERS[max((k for k in ANSWERS if k in url), key=len)]


class Init(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(onboard, "get", fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.found = init.discover("https://192.0.2.10:8006", "reader@pve!labhud", "not-a-secret")

    def test_discover(self):
        self.assertEqual(self.found["version"], "9.0.3")
        self.assertEqual(self.found["nodes"], ["alpha", "beta"])
        self.assertEqual(self.found["guests"]["alpha"], [(100, 'dns "main"', "lxc"), (101, "docker", "qemu")])
        self.assertEqual(self.found["guests"]["beta"], [(200, "backup", "qemu")])  # no template
        self.assertEqual(self.found["extra"], [])

    def test_config_loads(self):
        text = init.build_config(self.found, "192.0.2.10", (52.52, 13.41, "Europe/Berlin", "Berlin"))
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        try:
            t = config.load(f.name)
        finally:
            os.unlink(f.name)
        cards = {c["id"]: c for cs in t["cards"].values() for c in cs}
        self.assertEqual(cards["host-alpha"]["check"], ["ping", "192.0.2.11"])  # its own IP
        self.assertEqual(cards["host-beta"]["check"], ["ping", "192.0.2.10"])   # the address given
        self.assertEqual(cards["vm-alpha-100"]["name"], 'dns "main"')
        self.assertEqual(cards["vm-alpha-100"]["check"], ["proxmox", "alpha", 100])
        self.assertIn(["proxmox.alpha.cpu", "CPU", "percent"], cards["host-alpha"]["metrics"])
        self.assertEqual(sorted(t["strip"]), ["ALPH", "BETA"])
        self.assertEqual(t["weather"]["city"], "Berlin")

    def test_env(self):
        text = init.build_env(self.found, "https://192.0.2.10:8006", "reader@pve!labhud", "s3cret",
                              ["192.0.2.10:8095", "localhost:8095"])
        self.assertIn("LABHUD_PROXMOX_NODES=alpha,beta\n", text)
        self.assertIn("LABHUD_PROXMOX_BETA_TOKEN_SECRET=s3cret\n", text)
        self.assertIn("LABHUD_HOSTS=192.0.2.10:8095,localhost:8095\n", text)

    def test_existing_files_are_kept(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "config.toml"), "w") as f:
                f.write("mine")
            path = init.write(d, "config.toml", "new", 0o644)
            self.assertTrue(path.endswith("config.toml.new"))
            with open(os.path.join(d, "config.toml")) as f:
                self.assertEqual(f.read(), "mine")


if __name__ == "__main__":
    unittest.main()
