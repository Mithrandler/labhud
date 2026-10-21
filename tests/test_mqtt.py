"""MQTT: the packets themselves, and the sender against a small fake broker."""

import json
import socket
import threading
import time
import unittest
from unittest import mock

import mqtt


def read_packet(sock):
    first = sock.recv(1)
    if not first:
        return None, b""
    n, mult = 0, 1
    while True:
        b = sock.recv(1)[0]
        n += (b & 0x7F) * mult
        mult *= 128
        if not b & 0x80:
            break
    body = b""
    while len(body) < n:
        body += sock.recv(n - len(body))
    return first[0], body


class Broker:
    """Accepts connections, answers CONNACK with `code`, records (user, topic, payload, retain)."""

    def __init__(self, code=0):
        self.code, self.got, self.logins = code, [], []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                kind, body = read_packet(conn)
                assert kind == 0x10 and body[2:6] == b"MQTT" and body[6] == 4
                flags = body[7]
                rest = body[10:]
                cid_len = int.from_bytes(rest[:2], "big")
                rest = rest[2 + cid_len:]
                user = ""
                if flags & 0x80:
                    ulen = int.from_bytes(rest[:2], "big")
                    user = rest[2:2 + ulen].decode()
                self.logins.append(user)
                conn.sendall(bytes([0x20, 2, 0, self.code]))
                if self.code:
                    continue
                while True:
                    kind, body = read_packet(conn)
                    if kind is None or kind == 0xE0:
                        break
                    tlen = int.from_bytes(body[:2], "big")
                    self.got.append((body[2:2 + tlen].decode(), body[2 + tlen:], bool(kind & 1)))

    def close(self):
        self.sock.close()


class Packets(unittest.TestCase):
    def test_remaining_length(self):
        self.assertEqual(mqtt._length(0), b"\x00")
        self.assertEqual(mqtt._length(127), b"\x7f")
        self.assertEqual(mqtt._length(128), b"\x80\x01")
        self.assertEqual(mqtt._length(16383), b"\xff\x7f")

    def test_publish(self):
        self.assertEqual(mqtt.publish_packet("a/b", b"up", retain=True), b"\x31\x07\x00\x03a/bup")

    def test_discovery(self):
        msgs = mqtt.discovery({"vm-node1-101": "docker"}, "LABHUD")
        topic, payload, retain = msgs[0]
        self.assertEqual(topic, "homeassistant/binary_sensor/labhud/vm-node1-101/config")
        cfg = json.loads(payload)
        self.assertEqual((cfg["name"], cfg["state_topic"], cfg["device_class"]), ("docker", "labhud/card/vm-node1-101", "problem"))
        self.assertTrue(retain)


class Sender(unittest.TestCase):
    def run_with(self, broker, steps, until, seconds=10):
        url = f"mqtt://127.0.0.1:{broker.port}"
        with mock.patch.multiple(mqtt, URL=url, USER="labhud", PASS="pw", _states={}, _pending={}), \
                mock.patch.object(mqtt, "_queue", mqtt.queue.Queue(maxsize=500)):
            mqtt.start()
            for step in steps:
                step()
            deadline = time.monotonic() + seconds
            while not until() and time.monotonic() < deadline:
                time.sleep(0.1)

    def test_states_events_and_discovery(self):
        broker = Broker()
        self.addCleanup(broker.close)
        topics = lambda: {t for t, _, _ in broker.got}  # noqa: E731
        self.run_with(broker, [
            lambda: mqtt.announce({"nas": "NAS"}, "LABHUD"),
            lambda: mqtt.update({"nas": "down", "pve": "up"}),
            lambda: mqtt.update({"nas": "down", "pve": "up"}),  # nothing new: not sent again
            lambda: mqtt.event("NAS down", True, t=5),
        ], until=lambda: {"labhud/card/nas", "labhud/event", "homeassistant/binary_sensor/labhud/nas/config"} <= topics())
        got = {t: (p, r) for t, p, r in broker.got}
        self.assertEqual(got["labhud/card/nas"], (b"down", True))
        self.assertEqual(got["labhud/card/pve"], (b"up", True))
        self.assertEqual(json.loads(got["labhud/event"][0]), {"text": "NAS down", "bad": True, "t": 5})
        self.assertEqual(sum(t == "labhud/card/nas" for t, _, _ in broker.got), 1)
        self.assertEqual(broker.logins[0], "labhud")

    def test_refused_login_is_reported(self):
        broker = Broker(code=4)
        self.addCleanup(broker.close)
        self.run_with(broker, [lambda: mqtt.update({"nas": "down"})],
                      until=lambda: mqtt.state["last_error"] is not None, seconds=5)
        self.assertIn("bad user name or password", mqtt.state["last_error"])


if __name__ == "__main__":
    unittest.main()
