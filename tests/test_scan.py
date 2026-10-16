"""scan.py: only private subnets, slowly, and an open port counts only when the page names the service."""

import socket
import unittest

import scan


class Check(unittest.TestCase):
    def test_only_private_and_not_too_big(self):
        self.assertEqual(str(scan.check("192.168.1.7/24")), "192.168.1.0/24")
        self.assertEqual(str(scan.check("10.0.0.0/22")), "10.0.0.0/22")
        for bad in ("8.8.8.0/24", "10.0.0.0/16", "127.0.0.0/24", "169.254.1.0/24", "nope", ""):
            with self.assertRaises(ValueError, msg=bad):
                scan.check(bad)

    def test_default_is_the_display_address(self):
        self.assertEqual(scan.default_cidr("wall.lan:8095,192.168.1.210:8095"), "192.168.1.0/24")
        self.assertEqual(scan.default_cidr("wall.lan", local_ip="10.9.8.3"), "10.9.8.0/24")
        self.assertEqual(scan.default_cidr("127.0.0.1:8095", local_ip="10.9.8.3"), "10.9.8.0/24")


class Run(unittest.TestCase):
    def setUp(self):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.addCleanup(self.srv.close)
        self.port = self.srv.getsockname()[1]
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.closed = s.getsockname()[1]   # nothing listens there: refused

    def test_open_port_and_marker(self):
        known = [("sonarr", self.port, "http", "/", "sonarr"), ("radarr", self.closed, "http", "/", "radarr"),
                 ("other", self.port, "http", "/", "other")]
        probed = []
        def probe(host, port, scheme, path, marker):
            probed.append(marker)
            return marker == "sonarr"
        found = scan.run(["127.0.0.1"], configured={"sonarr"}, probe=probe, known=known)
        self.assertEqual(found, [{"source": "sonarr", "url": f"http://127.0.0.1:{self.port}", "configured": True}])
        self.assertNotIn("radarr", probed)   # a closed port is not asked anything

    def test_a_silent_host_is_left_after_one_round(self):
        tried = []
        real = scan._knock
        scan._knock = lambda h, p: tried.append(p)   # None: no answer at all
        try:
            known = [(f"s{i}", 1000 + i, "http", "/", "x") for i in range(20)]
            self.assertEqual(scan.run(["192.0.2.9"], probe=lambda *a: True, known=known), [])
        finally:
            scan._knock = real
        self.assertEqual(len(tried), scan.PER_HOST)


if __name__ == "__main__":
    unittest.main()
