"""Certificate pinning and checking (sources._common.urlopen) against a real local HTTPS server,
and secrets read from files (envfiles)."""

import hashlib
import os
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import envfiles
from sources import _common


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        _Handler.seen.append(self.headers.get("Authorization"))
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("openssl"), "needs openssl to make a certificate")
class Pinning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.cert, key = os.path.join(cls.dir, "cert.pem"), os.path.join(cls.dir, "key.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", key, "-out", cls.cert], check=True, capture_output=True)
        with open(cls.cert) as f:
            cls.fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(f.read())).hexdigest()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.cert, key)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.server.socket = ctx.wrap_socket(cls.server.socket, server_side=True)
        cls.server.handle_error = lambda *args: None  # the refused handshakes are the point
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"https://127.0.0.1:{cls.server.server_address[1]}/x"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir)

    def setUp(self):
        _Handler.seen.clear()
        _common.TLS_SEEN.clear()

    def get(self):
        return _common.request(self.url, {"Authorization": "secret-token"})

    def test_right_pin_passes(self):
        with mock.patch.dict(_common.PINS, {"127.0.0.1": self.fp}):
            self.assertEqual(self.get(), {"ok": True})
        self.assertEqual(list(_common.TLS_SEEN.values()), ["pinned"])

    def test_wrong_pin_refused_before_the_key_is_sent(self):
        port = self.server.server_address[1]
        with mock.patch.dict(_common.PINS, {f"127.0.0.1:{port}": "0" * 64}):
            with self.assertRaises(Exception) as e:
                self.get()
        self.assertIn("not the pinned one", str(e.exception))
        self.assertEqual(_Handler.seen, [], "the request reached the server")

    def test_pin_on_another_port_does_not_apply(self):
        with mock.patch.dict(_common.PINS, {"127.0.0.1:1": "0" * 64}):
            self.assertEqual(self.get(), {"ok": True})
        self.assertEqual(list(_common.TLS_SEEN.values()), ["unchecked"])

    def test_verify_with_own_ca(self):
        with mock.patch.object(_common, "_VERIFIED", ssl.create_default_context(cafile=self.cert)):
            self.assertEqual(self.get(), {"ok": True})
        self.assertEqual(list(_common.TLS_SEEN.values()), ["verified"])

    def test_verify_with_system_cas_refuses_self_signed(self):
        with mock.patch.object(_common, "_VERIFIED", ssl.create_default_context()):
            with self.assertRaises(urllib.error.URLError):
                self.get()


class Pins(unittest.TestCase):
    def test_parse(self):
        fp = "AB:" * 31 + "CD"
        self.assertEqual(_common.parse_pins(f"Host.lan:8006={fp}, other=" + "e" * 64),
                         {"host.lan:8006": "ab" * 31 + "cd", "other": "e" * 64})

    def test_bad_pin_stops_the_start(self):
        for bad in ("host=abc", "host", "=" + "a" * 64 + "z"):
            with self.assertRaises(SystemExit):
                _common.parse_pins(bad)


class SecretFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def file(self, text):
        path = os.path.join(self.dir, f"s{len(os.listdir(self.dir))}")
        with open(path, "w") as f:
            f.write(text)
        return path

    def test_file_fills_the_variable(self):
        env = {"LABHUD_PBS_TOKEN_SECRET_FILE": self.file("s3cret\n"), "OTHER_FILE": "/nope"}
        self.assertEqual(envfiles.load(env), ["LABHUD_PBS_TOKEN_SECRET"])
        self.assertEqual(env["LABHUD_PBS_TOKEN_SECRET"], "s3cret")

    def test_direct_value_wins(self):
        env = {"LABHUD_X_KEY": "direct", "LABHUD_X_KEY_FILE": self.file("from file")}
        self.assertEqual(envfiles.load(env), [])
        self.assertEqual(env["LABHUD_X_KEY"], "direct")

    def test_missing_or_empty_file_stops_the_start(self):
        for path in (os.path.join(self.dir, "missing"), self.file("  \n")):
            with self.assertRaises(SystemExit) as e:
                envfiles.load({"LABHUD_X_KEY_FILE": path})
            self.assertIn("LABHUD_X_KEY_FILE", str(e.exception))


if __name__ == "__main__":
    unittest.main()
