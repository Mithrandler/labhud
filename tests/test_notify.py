"""notify.py: the message shapes, what is sent, and the delay that drops a host back up."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import notify


class Receiver(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Receiver.got.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class Notify(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Receiver)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.saved = {k: getattr(notify, k) for k in ("URL", "FORMAT", "AUTH", "ALL", "DELAY")}
        notify.URL = f"http://127.0.0.1:{cls.server.server_port}/hook"
        notify.AUTH = "Bearer test-token"
        notify.DELAY = 1
        threading.Thread(target=notify.worker, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        for k, v in cls.saved.items():
            setattr(notify, k, v)

    def setUp(self):
        Receiver.got = []
        notify.FORMAT, notify.ALL = "json", False

    def wait(self, count, seconds=8):
        deadline = time.monotonic() + seconds
        while len(Receiver.got) < count and time.monotonic() < deadline:
            time.sleep(0.1)
        return Receiver.got

    def test_shapes(self):
        notify.FORMAT = "ntfy"
        data, headers = notify.body("LABHUD", "NAS down", True)
        self.assertEqual(data, b"NAS down")
        self.assertEqual((headers["Title"], headers["Priority"]), ("LABHUD", "high"))
        notify.FORMAT = "gotify"
        self.assertEqual(json.loads(notify.body("T", "x", False)[0]), {"title": "T", "message": "x", "priority": 4})
        notify.FORMAT = "discord"
        self.assertEqual(json.loads(notify.body("T", "x", True)[0]), {"content": "**T**: x"})
        notify.FORMAT = "json"
        self.assertEqual(json.loads(notify.body("T", "x", True, now=5)[0]), {"title": "T", "text": "x", "bad": True, "time": 5})

    def test_bad_news_only_unless_all(self):
        notify.submit("T", "NAS up", False)
        notify.submit("T", "NAS down", True)
        got = self.wait(1)
        time.sleep(0.5)
        self.assertEqual(len(got), 1)
        self.assertEqual(json.loads(got[0][1])["text"], "NAS down")
        self.assertEqual(got[0][0]["Authorization"], "Bearer test-token")

    def test_delay_drops_a_host_back_up(self):
        notify.submit("T", "NAS down", True, still_true=lambda: False)
        notify.submit("T", "PVE down", True, still_true=lambda: True)
        got = self.wait(1)
        time.sleep(1.5)
        self.assertEqual([json.loads(b)["text"] for _, b in got], ["PVE down"])

    def test_bad_format(self):
        notify.FORMAT = "telegram"
        self.assertIn("LABHUD_NOTIFY_FORMAT", notify.problem())


if __name__ == "__main__":
    unittest.main()
