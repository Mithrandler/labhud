"""Sources that speak their own protocol: NUT, A2S, Minecraft; and the certificate list."""

import json
import socket
import struct
import threading
import unittest
from unittest import mock

from sources import certs, games, nut
from tests.test_sources import lab_env


def tcp_server(handler):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)

    def run():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                handler(conn)
    threading.Thread(target=run, daemon=True).start()
    return srv


class Nut(unittest.TestCase):
    def test_on_battery(self):
        def handler(conn):
            data = b""
            while not data.endswith(b"\n"):
                data += conn.recv(1024)
            if data.startswith(b"LIST UPS"):
                conn.sendall(b'BEGIN LIST UPS\nUPS eaton "Eaton 5E"\nEND LIST UPS\n')
            else:
                conn.sendall(b'BEGIN LIST VAR eaton\nVAR eaton ups.status "OB DISCHRG"\nVAR eaton battery.charge "64"\n'
                             b'VAR eaton battery.runtime "1260"\nVAR eaton ups.load "31"\nVAR eaton device.mfr "EATON"\n'
                             b'VAR eaton device.model "5E 850i"\nEND LIST VAR eaton\n')
        srv = tcp_server(handler)
        self.addCleanup(srv.close)
        with lab_env(NUT_HOST=f"127.0.0.1:{srv.getsockname()[1]}"):
            got = nut.nut()
        self.assertEqual(got, {"status": "ON BATTERY, discharging", "on_battery": True, "low_battery": False,
                               "charge": 64.0, "runtime_min": 21, "load": 31.0, "input_voltage": None,
                               "model": "EATON 5E 850i", "name": "eaton"})

    def test_error(self):
        with self.assertRaises(RuntimeError):
            nut.parse_vars(["ERR UNKNOWN-UPS"])


def a2s_answer(players):
    return (b"\xff\xff\xff\xffI\x11" + b"My Rust\x00" + b"Procedural Map\x00" + b"rust\x00" + b"Rust\x00"
            + struct.pack("<H", 0) + bytes([players, 100, 0]) + b"d")


class A2S(unittest.TestCase):
    def test_with_challenge(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        srv.bind(("127.0.0.1", 0))
        self.addCleanup(srv.close)

        def run():
            for _ in range(2):
                data, addr = srv.recvfrom(1400)
                if data == games.A2S_INFO:
                    srv.sendto(b"\xff\xff\xff\xffA\x01\x02\x03\x04", addr)
                elif data == games.A2S_INFO + b"\x01\x02\x03\x04":
                    srv.sendto(a2s_answer(7), addr)
        threading.Thread(target=run, daemon=True).start()
        got = games.query_a2s("127.0.0.1", srv.getsockname()[1])
        self.assertEqual(got, {"name": "My Rust", "map": "Procedural Map", "game": "Rust", "players": 7, "max": 100, "bots": 0})


class Minecraft(unittest.TestCase):
    def test_status(self):
        status = json.dumps({"version": {"name": "1.21.4"}, "players": {"online": 2, "max": 20},
                             "description": {"text": "hello"}}).encode()

        def handler(conn):
            conn.recv(1024)
            body = games._varint(0) + games._varint(len(status)) + status
            conn.sendall(games._varint(len(body)) + body)
        srv = tcp_server(handler)
        self.addCleanup(srv.close)
        got = games.query_minecraft("127.0.0.1", srv.getsockname()[1])
        self.assertEqual(got, {"name": "hello", "players": 2, "max": 20, "version": "1.21.4"})

    def test_list_and_offline(self):
        with lab_env(GAMES="mc=minecraft://127.0.0.1:1,rust=a2s://127.0.0.1:2"), \
                mock.patch.object(games, "query_a2s", lambda h, p: {"players": 3, "max": 50, "map": "Barren"}):
            got = games.games()
        self.assertEqual(got["servers"], [{"name": "mc", "value": "offline", "bad": True},
                                          {"name": "rust", "value": "3/50 · Barren"}])
        self.assertEqual((got["online"], got["total"], got["players"]), (1, 2, 3))

    def test_bad_setting(self):
        with self.assertRaises(ValueError):
            games.servers("rust=http://192.0.2.1:1")


class Certs(unittest.TestCase):
    def test_soonest_first(self):
        days = {"a.example": 80, "b.example": 9}

        def fake(host, port):
            if host == "bad.example":
                raise OSError("connection refused")
            return days[host]
        with lab_env(CERTS="a.example, b.example:8443, bad.example"), mock.patch.object(certs, "days_left", fake):
            got = certs.certs()
        self.assertEqual(got["cert_min_days"], 9)
        self.assertEqual(got["cert_under_14"], 1)
        self.assertEqual(got["failed"], 1)
        self.assertEqual([r["name"] for r in got["list"]], ["bad.example", "b.example:8443", "a.example"])


if __name__ == "__main__":
    unittest.main()
