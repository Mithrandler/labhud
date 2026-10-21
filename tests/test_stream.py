"""The stream (/api/stream): a screen too slow for it is let go by _publish, and its connection
then ends, so the page comes back for a full snapshot instead of staying open with no news."""

import os
import socket
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class Dropped(unittest.TestCase):
    def test_a_dropped_screen_is_closed(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        host = f"127.0.0.1:{httpd.server_port}"
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        with mock.patch.object(server, "ALLOWED_HOSTS", server.ALLOWED_HOSTS | {host}), \
                mock.patch.object(server, "STREAM_PING", 0.2):
            before = list(server._subscribers)
            sock = socket.create_connection(("127.0.0.1", httpd.server_port), timeout=5)
            self.addCleanup(sock.close)
            sock.sendall(f"GET /api/stream HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
            got = b""
            while b"event: full" not in got:
                got += sock.recv(65536)
            with server._subscribers_lock:   # what _publish does to a screen whose queue is full
                for q in [q for q in server._subscribers if q not in before]:
                    server._subscribers.remove(q)
            sock.settimeout(3)
            deadline = time.monotonic() + 3
            while sock.recv(65536):   # the pings stop and the connection ends, instead of going on for ever
                self.assertLess(time.monotonic(), deadline, "the stream of a dropped screen is still open")


if __name__ == "__main__":
    unittest.main()
