"""HTTPS by default (tlscert.py): the certificate labhud makes itself, when it is used instead of
plain HTTP, and the redirect for a plain-HTTP request on the HTTPS port."""

import hashlib
import http.client
import os
import shutil
import ssl
import stat
import subprocess
import tempfile
import unittest
import unittest.mock

import devices
import tlscert
from tests import test_smoke

OPENSSL = shutil.which("openssl")


class Names(unittest.TestCase):
    def test_names(self):
        self.assertEqual(tlscert._names(["192.0.2.10:8095", "wall.lan:8095", "[2001:db8::1]:8095", "wall.lan:443", "box"]),
                         ["DNS:localhost", "IP:127.0.0.1", "IP:192.0.2.10", "DNS:wall.lan", "IP:2001:db8::1", "DNS:box"])


class Choose(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_own_certificate_wins(self):
        cert, key, line = tlscert.choose({"LABHUD_TLS_CERT": "/c.pem", "LABHUD_TLS_KEY": "/k.pem", "LABHUD_HTTP": "on"}, self.dir, [])
        self.assertEqual((cert, key), ("/c.pem", "/k.pem"))

    def test_plain_http_only_when_asked_or_impossible(self):
        self.assertEqual(tlscert.choose({"LABHUD_HTTP": "on"}, self.dir, [])[:2], ("", ""))
        self.assertEqual(tlscert.choose({}, self.dir, [], demo=True)[:2], ("", ""))
        cert, _, line = tlscert.choose({}, "", [])
        self.assertEqual(cert, "")
        self.assertIn("LABHUD_DATA", line)
        with unittest.mock.patch("shutil.which", return_value=None):
            cert, _, line = tlscert.choose({}, self.dir, [])
        self.assertEqual(cert, "")
        self.assertIn("openssl", line)

    @unittest.skipUnless(OPENSSL, "needs openssl")
    def test_made_once_and_kept(self):
        cert, key, line = tlscert.choose({}, self.dir, ["wall.lan:8095", "192.0.2.10:8095"])
        self.assertTrue(cert.endswith("tls/cert.pem"), line)
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode) & 0o077, 0)            # the key is labhud's alone
        text = subprocess.run([OPENSSL, "x509", "-in", cert, "-noout", "-text"], capture_output=True, text=True).stdout
        self.assertIn("DNS:wall.lan", text)
        self.assertIn("IP Address:192.0.2.10", text)
        self.assertIn("prime256v1", text)
        fp = devices.tls_fingerprint(cert)
        # a second start keeps it: the phones and agents pinned this one
        self.assertEqual(tlscert.choose({}, self.dir, ["other.lan:8095"])[0], cert)
        self.assertEqual(devices.tls_fingerprint(cert), fp)
        self.assertFalse([n for n in os.listdir(os.path.dirname(cert)) if n.endswith(".new")])
        # init.py (another process) prints https:// links with this one, unless told plain HTTP
        self.assertEqual(tlscert.current({}, self.dir), cert)
        self.assertEqual(tlscert.current({"LABHUD_HTTP": "on"}, self.dir), "")
        self.assertEqual(tlscert.current({"LABHUD_TLS_CERT": "/c.pem"}, self.dir), "/c.pem")
        self.assertEqual(tlscert.current({}, ""), "")


@unittest.skipUnless(OPENSSL, "needs openssl")
class Served(unittest.TestCase):
    """The demo server with a certificate: DemoServer's start and stop, not its plain-HTTP tests."""
    data = tempfile.TemporaryDirectory()
    cert, key = tlscert.make(os.path.join(data.name, "tls"), ["127.0.0.1:1"]) if OPENSSL else ("", "")
    extra_env = {"LABHUD_TLS_CERT": cert, "LABHUD_TLS_KEY": key}
    setUpClass = classmethod(test_smoke.DemoServer.setUpClass.__func__)

    @classmethod
    def tearDownClass(cls):
        test_smoke.DemoServer.tearDownClass.__func__(cls)
        cls.data.cleanup()

    def test_https_pinned(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection("127.0.0.1", self.port, timeout=10, context=ctx)
        conn.request("GET", "/api/config", headers={"Host": self.host})
        r = conn.getresponse()
        r.read()
        der = conn.sock.getpeercert(binary_form=True)
        conn.close()
        self.assertEqual(r.status, 200)
        self.assertEqual(hashlib.sha256(der).hexdigest(), devices.tls_fingerprint(self.cert))

    def test_plain_http_redirected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/status?x=1", headers={"Host": self.host})
        r = conn.getresponse()
        r.read()
        self.assertEqual((r.status, r.getheader("Location")), (301, f"https://{self.host}/status?x=1"))
        conn.close()
        # a name labhud does not answer to gets no redirect (no open redirect for DNS rebinding)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/", headers={"Host": "evil.example"})
        r = conn.getresponse()
        r.read()
        self.assertEqual((r.status, r.getheader("Location")), (400, None))
        conn.close()


if __name__ == "__main__":
    unittest.main()
