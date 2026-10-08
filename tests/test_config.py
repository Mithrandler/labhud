"""config.py: valid files load into the documented shape, invalid ones report every problem
with its location."""

import os
import tempfile
import textwrap
import unittest

import config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MINIMAL = """
[[page]]
id = "main"
title = "MAIN"

  [[page.group]]
  id = "hosts"
  title = "Hosts"

    [[page.group.card]]
    id = "nas"
    name = "NAS"
    ping = "nas.lan"
"""


def load_text(text):
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as f:
        f.write(textwrap.dedent(text))
    try:
        return config.load(f.name)
    finally:
        os.unlink(f.name)


class ValidFiles(unittest.TestCase):
    def test_shipped_files_load(self):
        for rel in ("config.example.toml", "demo/config.toml"):
            with self.subTest(rel):
                t = config.load(os.path.join(ROOT, rel))
                self.assertTrue(t["pages"])
                self.assertTrue(t["cards"])

    def test_minimal(self):
        t = load_text(MINIMAL)
        self.assertEqual(t["title"], config.DEFAULT_TITLE)
        self.assertEqual(t["pages"], [{"id": "main", "title": "MAIN", "groups": [{"id": "hosts", "title": "Hosts"}]}])
        self.assertEqual(t["cards"], {"hosts": [{"id": "nas", "name": "NAS", "check": ["ping", "nas.lan"]}]})
        self.assertEqual((t["strip"], t["quiet_hours"], t["skip_sources"], t["actions"]), ({}, {}, {}, {}))
        self.assertIsNone(t["weather"])
        self.assertIsNone(t["alerts"])

    def test_full_card_and_tables(self):
        t = load_text('title = "HOME"\n' + MINIMAL + """
    large = true
    on_demand = true
    subtitle = "storage"
    metrics = [{ key = "nas.used", label = "Used", format = "percent" }]
    list = "nas.disks"
    limit = 5
    panel = "host:nas"
    sensors = "temp.nas"
    actions = ["wake"]

    [[page.group.card]]
    id = "router"
    name = "Router"
    tcp = "[2001:db8::1]:443"

    [[page.group.card]]
    id = "vm1"
    name = "VM"
    proxmox = "pve/101"

[[strip]]
code = "NAS"
name = "nas"
card = "nas"
ip = "192.0.2.10"
wan = true

[quiet_hours.nas]
from = 23
to = 7
sources = ["synology"]

[weather]
latitude = 52.5
longitude = 13.4
city = "Berlin"

[action.wake]
label = "Wake"
confirm = "Wake the NAS?"

[alerts]
path = "live.alerts"
page = "main"
""")
        self.assertEqual(t["title"], "HOME")
        nas, router, vm = t["cards"]["hosts"]
        self.assertEqual(nas["metrics"], [["nas.used", "Used", "percent"]])
        self.assertTrue(nas["large"] and nas["on_demand"])
        self.assertEqual((nas["limit"], nas["actions"]), (5, ["wake"]))
        self.assertEqual(router["check"], ["tcp", "2001:db8::1", 443])
        self.assertEqual(vm["check"], ["proxmox", "pve", 101])
        self.assertEqual(t["strip"], {"NAS": {"name": "nas", "card": "nas", "ip": "192.0.2.10", "wan": True}})
        self.assertEqual(t["quiet_hours"], {"nas": (23, 7)})
        self.assertEqual(t["skip_sources"], {"synology": (23, 7)})
        self.assertEqual(t["weather"], {"latitude": 52.5, "longitude": 13.4, "city": "Berlin"})
        self.assertEqual(t["actions"], {"wake": {"label": "Wake", "confirm": "Wake the NAS?"}})
        self.assertEqual(t["alerts"], {"path": "live.alerts", "page": "main"})

    def test_public_hides_server_side_settings(self):
        t = load_text(MINIMAL + '\n[weather]\nlatitude = 1\nlongitude = 2\ncity = "X"\n'
                      '[quiet_hours.nas]\nfrom = 1\nto = 2\n')
        p = config.public(t)
        self.assertEqual(p["city"], "X")
        for hidden in ("weather", "quiet_hours", "skip_sources"):
            self.assertNotIn(hidden, p)

    def test_night(self):
        t = load_text(MINIMAL + '\n[night]\nfrom = 23\nto = 7\n')
        self.assertEqual(t["night"], {"from": 23, "to": 7, "dim": 30})
        self.assertEqual(config.public(t)["night"], t["night"])
        self.assertIsNone(load_text(MINIMAL)["night"])

    def test_trends(self):
        t = config.load(os.path.join(ROOT, "demo", "config.toml"))
        self.assertIn("proxmox.pve.cpu", t["trends"])           # percent
        self.assertIn("proxmox.pve.mem_used_of", t["trends"])   # used_of
        self.assertNotIn("proxmox.pve.vms", t["trends"])        # a count has no trend
        self.assertEqual(load_text('sparklines = false\n' + MINIMAL)["trends"], [])

    def test_env_path(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(MINIMAL)
        old = os.environ.get("LABHUD_CONFIG")
        os.environ["LABHUD_CONFIG"] = f.name
        try:
            self.assertEqual(config.load()["pages"][0]["id"], "main")
        finally:
            os.unlink(f.name)
            if old is None:
                del os.environ["LABHUD_CONFIG"]
            else:
                os.environ["LABHUD_CONFIG"] = old


class InvalidFiles(unittest.TestCase):
    def problems(self, text):
        with self.assertRaises(config.ConfigError) as cm:
            load_text(text)
        return cm.exception.problems

    def assertProblem(self, text, expected):
        problems = self.problems(text)
        self.assertIn(expected, problems, "\n".join(problems))

    def test_bad_url(self):
        text = MINIMAL.replace('name = "NAS"', 'name = "NAS"\nurl = "ftp://x"', 1)
        self.assertTrue(any("url must start with http:// or https://" in p for p in self.problems(text)))

    def test_bad_night(self):
        self.assertProblem(MINIMAL + '\n[night]\nfrom = 23\nto = 23\ndim = 120\n',
                           "night: 'from' and 'to' are the same hour")
        self.assertProblem(MINIMAL + '\n[night]\nfrom = 23\nto = 7\ndim = 120\n',
                           "night: dim must be from 0 to 100, got 120")

    def test_missing_file(self):
        with self.assertRaises(config.ConfigError) as cm:
            config.load("/nonexistent/labhud.toml")
        self.assertIn("file not found", cm.exception.problems[0])

    def test_not_toml(self):
        self.assertIn("not valid TOML", self.problems("[[page]\n")[0])

    def test_no_pages(self):
        self.assertProblem('title = "x"', "top level: at least one [[page]] is required")

    def test_cases(self):
        card = MINIMAL.replace('ping = "nas.lan"', "{}")
        cases = [
            ('titel = "x"\n' + MINIMAL, "top level: unknown key 'titel' (did you mean 'title'?)"),
            ("title = 3\n" + MINIMAL, "top level: 'title' must be a string"),
            (MINIMAL.replace('id = "main"', 'id = "Main1"'),
             "page 'Main1': id may only contain lowercase letters (it becomes #<id> in the URL)"),
            (MINIMAL + MINIMAL.replace('"hosts"', '"other"').replace('"nas"', '"nas2"'),
             "page 'main': duplicate page id"),
            (MINIMAL.replace('title = "Hosts"', ""), "page 'main' group 'hosts': missing 'title'"),
            (MINIMAL + MINIMAL.replace('"main"', '"other"').replace('"nas"', '"nas2"'),
             "page 'other' group 'hosts': duplicate group id (group ids are unique across all pages)"),
            (MINIMAL + "    large = 1\n",
             "page 'main' group 'hosts' card 'nas': 'large' must be true or false"),
            (card.replace("{}", 'ping = "nas.lan"\ntcp = "nas.lan:80"'),
             "page 'main' group 'hosts' card 'nas': use only one status check, found ping, tcp"),
            (card.replace("{}", 'ping = "bad host!"'),
             "page 'main' group 'hosts' card 'nas': ping must be an IP address or hostname, got 'bad host!'"),
            (card.replace("{}", 'tcp = "nas.lan"'),
             "page 'main' group 'hosts' card 'nas': tcp must be \"host:port\", got 'nas.lan'"),
            (card.replace("{}", 'tcp = "nas.lan:70000"'),
             "page 'main' group 'hosts' card 'nas': tcp must be \"host:port\", got 'nas.lan:70000'"),
            (card.replace("{}", 'proxmox = "pve"'),
             "page 'main' group 'hosts' card 'nas': proxmox must be \"node/vmid\", e.g. \"pve/101\", got 'pve'"),
            (MINIMAL.replace('id = "nas"', 'id = "x y"'),
             "page 'main' group 'hosts' card 'x y': id 'x y' may only contain letters, digits, '-' and '_'"),
            (card.replace("{}", 'metrics = [{ key = "a.b", label = "A", format = "kelvin" }]'),
             "page 'main' group 'hosts' card 'nas'.metrics[0]: format 'kelvin' is not one of: "
             + ", ".join(sorted(config.FORMATS))),
            (card.replace("{}", 'metrics = [{ key = "a.b", label = "A" }]'),
             "page 'main' group 'hosts' card 'nas'.metrics[0]: missing 'format'"),
            (card.replace("{}", 'metrics = "a.b"'),
             "page 'main' group 'hosts' card 'nas': metrics must be a list of { key, label, format } tables"),
            (card.replace("{}", "limit = 3"), "page 'main' group 'hosts' card 'nas': limit has no effect without list"),
            (card.replace("{}", 'list = "a.b"\nlimit = 99'),
             "page 'main' group 'hosts' card 'nas': limit must be between 1 and 50"),
            (card.replace("{}", "actions = [1]"),
             "page 'main' group 'hosts' card 'nas': actions must be a list of action names"),
            (card.replace("{}", 'pnael = "x"'),
             "page 'main' group 'hosts' card 'nas': unknown key 'pnael' (did you mean 'panel'?)"),
            (MINIMAL + MINIMAL.replace('"main"', '"other"').replace('"hosts"', '"g2"'),
             "page 'other' group 'g2' card 'nas': duplicate card id (card ids are unique across all pages)"),
            (MINIMAL + '[[strip]]\ncode = "X"\nname = "x"\ncard = "nope"\n', "strip 'X': card 'nope' does not exist"),
            (MINIMAL + '[[strip]]\ncode = "X"\nname = "x"\ncard = "nas"\nip = "bad ip!"\n',
             "strip 'X': ip must be an IP address, got 'bad ip!'"),
            (MINIMAL + '[[strip]]\ncode = "X"\nname = "x"\ncard = "nas"\n' * 2, "strip 'X': duplicate code"),
            (MINIMAL + '[strip]\ncode = "X"\n',
             "top level: 'strip' must be written as [[...strip]] tables"),
            (MINIMAL + "[quiet_hours.nope]\nfrom = 1\nto = 2\n", "quiet_hours.nope: card 'nope' does not exist"),
            (MINIMAL + "[quiet_hours.nas]\nfrom = 24\nto = 2\n",
             "quiet_hours.nas: 'from' must be an hour from 0 to 23, got 24"),
            (MINIMAL + "[quiet_hours.nas]\nfrom = 3\nto = 3\n", "quiet_hours.nas: 'from' and 'to' are the same hour"),
            (MINIMAL + '[quiet_hours.nas]\nfrom = 1\nto = 2\nsources = "x"\n',
             "quiet_hours.nas: sources must be a list of source names"),
            (MINIMAL + "[weather]\nlatitude = 91\nlongitude = 0\n", "weather: 'latitude' must be a number from -90 to 90"),
            (MINIMAL + "[weather]\nlatitude = 0\n", "weather: missing 'longitude'"),
            (MINIMAL + "[weather]\nlatitude = true\nlongitude = 0\n",
             "weather: 'latitude' must be a number from -90 to 90"),
            (MINIMAL + "[action.wake]\nlabel = 1\n", "action.wake: 'label' must be a string"),
            (MINIMAL + "[action.wake]\nconfrim = \"x\"\n",
             "action.wake: unknown key 'confrim' (did you mean 'confirm'?)"),
            (MINIMAL + '[alerts]\npage = "main"\n', "alerts: missing 'path'"),
            (MINIMAL + '[alerts]\npath = "a"\npage = "nope"\n', "alerts: page 'nope' does not exist"),
        ]
        for text, expected in cases:
            with self.subTest(expected):
                self.assertProblem(text, expected)

    def test_all_problems_at_once(self):
        text = 'titel = 1\n' + MINIMAL.replace('ping = "nas.lan"', 'ping = "bad host!"') + "[weather]\nlatitude = 0\n"
        self.assertEqual(len(self.problems(text)), 3)

    def test_message_names_path_and_count(self):
        with self.assertRaises(config.ConfigError) as cm:
            load_text("titel = 1\n" + MINIMAL)
        self.assertRegex(str(cm.exception), r"\.toml: 1 problem\(s\)\n  top level: unknown key 'titel'")


if __name__ == "__main__":
    unittest.main()
