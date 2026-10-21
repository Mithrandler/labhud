"""notify.py's email channel against a fake SMTP server on 127.0.0.1: plain (no password), STARTTLS
and SSL with a certificate made for the test, the login, the message."""

import base64
import email
import os
import shutil
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import notify


class Smtp(socketserver.StreamRequestHandler):
    """Just enough SMTP: EHLO, STARTTLS, AUTH PLAIN, MAIL, RCPT, DATA, QUIT."""
    got = []
    ctx = None      # the server's TLS context (STARTTLS and SSL)
    wrap = False    # SSL from the first byte

    def setup(self):
        if Smtp.wrap:
            self.request = Smtp.ctx.wrap_socket(self.request, server_side=True)
        super().setup()

    def say(self, line):
        self.wfile.write(line.encode() + b"\r\n")
        self.wfile.flush()

    def handle(self):
        mail = {"auth": None, "rcpt": [], "tls": Smtp.wrap}
        self.say("220 fake ESMTP")
        while True:
            line = self.rfile.readline().decode().strip()
            if not line:
                return
            cmd = line.split(" ")[0].upper()
            if cmd == "EHLO":
                ext = ["AUTH PLAIN"] + ([] if mail["tls"] or not Smtp.ctx else ["STARTTLS"])
                self.say("250-fake")
                for i, e in enumerate(ext):
                    self.say(("250 " if i == len(ext) - 1 else "250-") + e)
            elif cmd == "STARTTLS":
                self.say("220 go ahead")
                self.request = Smtp.ctx.wrap_socket(self.request, server_side=True)
                self.rfile, self.wfile = self.request.makefile("rb"), self.request.makefile("wb")
                mail["tls"] = True
            elif cmd == "AUTH":
                mail["auth"] = base64.b64decode(line.split(" ")[2]).split(b"\0")[1:]
                self.say("235 ok")
            elif cmd == "MAIL":
                self.say("250 ok")
            elif cmd == "RCPT":
                mail["rcpt"].append(line.split(":", 1)[1].strip("<> "))
                self.say("250 ok")
            elif cmd == "DATA":
                self.say("354 go")
                data = b""
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b".\r\n", b""):
                        break
                    data += chunk
                mail["msg"] = email.message_from_bytes(data, policy=email.policy.default)
                Smtp.got.append(mail)
                self.say("250 queued")
            elif cmd == "QUIT":
                self.say("221 bye")
                return
            else:
                self.say("250 ok")


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass   # the untrusted certificate's test fails the handshake on purpose


@unittest.skipUnless(shutil.which("openssl"), "openssl makes the test's certificate")
class Email(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cert, key = os.path.join(cls.tmp, "c.pem"), os.path.join(cls.tmp, "k.pem")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", key, "-out", cert, "-days", "1", "-subj", "/CN=127.0.0.1",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
        cls.server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.server_ctx.load_cert_chain(cert, key)
        cls.client_ctx = ssl.create_default_context(cafile=cert)
        cls.server = Server(("127.0.0.1", 0), Smtp)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp)

    def setUp(self):
        Smtp.got, Smtp.ctx, Smtp.wrap = [], None, False
        p = mock.patch.object(notify, "tls_context", lambda: self.client_ctx)
        p.start()
        self.addCleanup(p.stop)
        self.cfg = {"host": "127.0.0.1", "port": self.port, "security": "none", "user": "",
                    "from": "labhud@example.com", "to": ["me@example.com", "you@example.com"]}

    def test_plain_without_password(self):
        notify.send_email("Lab", "NAS down\nsince 10:00", True, self.cfg)
        mail = Smtp.got[0]
        self.assertEqual(mail["rcpt"], ["me@example.com", "you@example.com"])
        self.assertIsNone(mail["auth"])
        self.assertEqual(mail["msg"]["Subject"], "⚠ Lab: NAS down")
        self.assertIn("since 10:00", mail["msg"].get_content())

    def test_starttls_with_login(self):
        Smtp.ctx = self.server_ctx
        cfg = dict(self.cfg, security="starttls", user="labhud")
        with mock.patch.dict(os.environ, {"LABHUD_NOTIFY_SMTP_PASSWORD": "smtp-pw"}):
            notify.send_email("Lab", "hello", False, cfg)
        mail = Smtp.got[0]
        self.assertTrue(mail["tls"])
        self.assertEqual(mail["auth"], [b"labhud", b"smtp-pw"])

    def test_ssl(self):
        Smtp.ctx, Smtp.wrap = self.server_ctx, True
        notify.send_email("Lab", "hello", False, dict(self.cfg, security="ssl"))
        self.assertTrue(Smtp.got[0]["tls"])

    def test_a_certificate_not_trusted_fails(self):
        Smtp.ctx = self.server_ctx
        with mock.patch.object(notify, "tls_context", ssl.create_default_context):
            err = notify.test_channel("email", dict(self.cfg, security="starttls"))
        self.assertIsNotNone(err)
        self.assertEqual(Smtp.got, [])

    def test_the_channel_through_the_test_button_and_the_password_is_not_in_errors(self):
        self.assertIsNone(notify.test_channel("email", self.cfg))
        bad = dict(self.cfg, port=9, security="starttls", user="u")
        err = notify.test_channel("email", bad, {"password": "smtp-pw"})
        self.assertIsNotNone(err)
        self.assertNotIn("smtp-pw", err)


class Config(unittest.TestCase):
    def check(self, table):
        import config
        ck = config._Checker()
        return notify.check(ck, {"email": table}).get("email"), ck.problems

    def test_defaults_and_checks(self):
        got, problems = self.check({"host": "smtp.example.com", "user": "me@example.com", "to": "you@example.com"})
        self.assertEqual(problems, [])
        self.assertEqual(got, {"host": "smtp.example.com", "port": 587, "security": "starttls", "user": "me@example.com",
                               "from": "me@example.com", "to": ["you@example.com"]})
        self.assertEqual(self.check({"host": "h", "security": "ssl", "from": "a@b.c", "to": ["x@y.z"]})[0]["port"], 465)
        for bad in ({"host": "h", "to": "nope"}, {"host": "h", "from": "a@b.c", "to": ["x@y.z"], "security": "tls"},
                    {"host": "h", "from": "a@b.c", "to": ["x@y.z"], "port": 0}, {"to": ["x@y.z"], "from": "a@b.c"},
                    {"host": "h", "from": "a@b.c", "to": ["x@y.z"], "password": "no"}):
            self.assertTrue(self.check(bad)[1], bad)


if __name__ == "__main__":
    unittest.main()
