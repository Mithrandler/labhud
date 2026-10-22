"""[admin] settings: the client's address behind trusted proxies, admin_networks, the session length, and their checks in config.py."""

import ipaddress
import unittest

import admin
import config
from tests.test_config import MINIMAL   # a page, a group, a card


def nets(*xs):
    return tuple(ipaddress.ip_network(x, strict=False) for x in xs)



class ClientIp(unittest.TestCase):
    def test_no_trusted(self):
        self.assertEqual(admin.client_ip("203.0.113.9", "10.0.0.1", ()), "203.0.113.9")

    def test_peer_not_trusted(self):
        self.assertEqual(admin.client_ip("203.0.113.9", "10.0.0.1", nets("127.0.0.1/32")), "203.0.113.9")

    def test_trusted_one_hop(self):
        self.assertEqual(admin.client_ip("127.0.0.1", "10.9.9.5", nets("127.0.0.1/32")), "10.9.9.5")

    def test_spoofed_left(self):
        self.assertEqual(admin.client_ip("127.0.0.1", "1.2.3.4, 10.9.9.5", nets("127.0.0.1/32")), "10.9.9.5")

    def test_two_trusted_proxies(self):
        self.assertEqual(admin.client_ip("127.0.0.1", "10.9.9.5, 172.18.0.2", nets("127.0.0.1/32", "172.18.0.0/16")), "10.9.9.5")

    def test_trusted_no_header(self):
        self.assertEqual(admin.client_ip("127.0.0.1", None, nets("127.0.0.1/32")), "127.0.0.1")

    def test_garbage_hop(self):
        self.assertEqual(admin.client_ip("127.0.0.1", "evil, 172.18.0.2", nets("127.0.0.1/32", "172.18.0.0/16")), "172.18.0.2")

    def test_ipv6(self):
        self.assertEqual(admin.client_ip("::1", "2001:db8::7", nets("::1/128")), "2001:db8::7")

    def test_mapped(self):
        self.assertEqual(admin.client_ip("::ffff:127.0.0.1", "10.0.0.5", nets("127.0.0.1/32")), "10.0.0.5")


class Networks(unittest.TestCase):
    def test_empty(self):
        self.assertTrue(admin.network_ok("8.8.8.8", ()))

    def test_ipv4_in_and_out(self):
        self.assertTrue(admin.network_ok("192.168.1.20", nets("192.168.1.0/24")))
        self.assertFalse(admin.network_ok("192.168.2.20", nets("192.168.1.0/24")))

    def test_ipv6(self):
        self.assertTrue(admin.network_ok("fd00::5", nets("192.168.1.0/24", "fd00::/8")))

    def test_garbage_ip(self):
        self.assertFalse(admin.network_ok("not an ip", nets("192.168.1.0/24")))


class ConfigAdmin(unittest.TestCase):
    def load(self, extra):
        return config.loads(MINIMAL + extra, "test.toml")["admin"]

    def problems(self, extra):
        with self.assertRaises(config.ConfigError) as e:
            config.loads(MINIMAL + extra, "test.toml")
        return " ".join(e.exception.problems)

    def test_defaults(self):
        self.assertEqual(self.load(""), {"session": 30 * 86400, "trusted_proxies": [], "admin_networks": [],
                                                  "servers_networks": [], "oidc": None})

    def test_session_hours(self):
        self.assertEqual(self.load('[admin]\nsession_hours = 12\n')["session"], 12 * 3600)

    def test_session_bounds(self):
        self.assertIn("session_hours", self.problems('[admin]\nsession_hours = 0\n'))
        self.assertIn("session_hours", self.problems('[admin]\nsession_hours = 721\n'))

    def test_admin_networks(self):
        self.assertEqual(
            self.load('[admin]\nadmin_networks = ["192.168.1.7/24", "10.0.0.1"]\n')["admin_networks"],
            ["192.168.1.0/24", "10.0.0.1/32"],
        )

    def test_servers_networks(self):
        self.assertEqual(
            self.load('[admin]\nservers_networks = ["192.0.2.7/24"]\n')["servers_networks"], ["192.0.2.0/24"])
        self.assertIn("not an address", self.problems('[admin]\nservers_networks = ["nope"]\n'))

    def test_trusted_proxies_garbage(self):
        self.assertIn("not an address", self.problems('[admin]\ntrusted_proxies = ["nope"]\n'))

    def test_oidc_ok(self):
        oidc = self.load('[admin.oidc]\nissuer = "https://auth.example/application/o/labhud/"\nclient_id = "abc"\nallow = ["demo"]\n')["oidc"]
        self.assertEqual(oidc["issuer"], "https://auth.example/application/o/labhud/")
        self.assertEqual(oidc["client_id"], "abc")
        self.assertEqual(oidc["allow"], ["demo"])
        self.assertEqual(oidc["allow_groups"], [])
        self.assertEqual(oidc["redirect_uri"], "")
        self.assertEqual(oidc["scopes"], ["openid", "profile", "email"])

    def test_oidc_missing_allow(self):
        self.assertIn("allow", self.problems('[admin.oidc]\nissuer = "https://a.example/"\nclient_id = "x"\n'))

    def test_oidc_plain_http(self):
        self.assertIn("https", self.problems('[admin.oidc]\nissuer = "http://auth.example/x/"\nclient_id = "x"\nallow = ["p"]\n'))

    def test_oidc_http_localhost(self):
        self.assertIsNotNone(self.load('[admin.oidc]\nissuer = "http://127.0.0.1:9000/x/"\nclient_id = "x"\nallow_groups = ["a"]\n')["oidc"])

    def test_oidc_redirect_uri(self):
        self.assertIn("redirect_uri", self.problems('[admin.oidc]\nissuer = "https://a.example/"\nclient_id = "x"\nallow = ["p"]\nredirect_uri = "https://hud.example/elsewhere"\n'))

    def test_oidc_unknown_key(self):
        with self.assertRaises(config.ConfigError):
            config.loads(MINIMAL + '[admin.oidc]\nissuer = "https://a.example/"\nclient_id = "x"\nallow = ["p"]\nsecret = "no"\n', "test.toml")


class SessionLength(unittest.TestCase):
    def setUp(self):
        self.saved = dict(admin.SETTINGS)

    def tearDown(self):
        admin.SETTINGS.clear()
        admin.SETTINGS.update(self.saved)

    def test_configure(self):
        admin.configure({"session": 7200})
        self.assertEqual(admin.Admin().idle, 7200)

    def test_clamp(self):
        admin.configure({"session": 60})
        self.assertEqual(admin.SETTINGS["session"], admin.MIN_SESSION)
        admin.configure({"session": 10**9})
        self.assertEqual(admin.SETTINGS["session"], admin.MAX_SESSION)

    def test_explicit_idle(self):
        admin.configure({"session": 7200})
        self.assertEqual(admin.Admin(idle=600).idle, 600)

    def test_configure_none(self):
        admin.configure(None)
        self.assertEqual(admin.SETTINGS["session"], admin.IDLE)
        self.assertEqual(admin.SETTINGS["admin_networks"], ())
        self.assertIsNone(admin.SETTINGS["oidc"])


if __name__ == "__main__":
    unittest.main()
