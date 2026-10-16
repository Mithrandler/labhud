"""A minimal WebSocket server side (RFC 6455), standard library only: the handshake, text frames,
ping/pong and close. Enough for the agents (agents.py); not a general implementation: no
extensions, no binary messages, one message at most MAX_MESSAGE bytes.
"""

import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE = 1 << 20
TEXT, CLOSE, PING, PONG, CONT, BINARY = 0x1, 0x8, 0x9, 0xA, 0x0, 0x2


class Closed(Exception):
    """The connection ended: by the peer, by a protocol error, or by us."""


def accept_key(key):
    # RFC 6455 fixes SHA-1 here: a handshake echo, not a security check
    return base64.b64encode(hashlib.sha1((key + GUID).encode(), usedforsecurity=False).digest()).decode()


def handshake_ok(headers):
    """The Sec-WebSocket-Key of a valid upgrade request, else None."""
    key = headers.get("Sec-WebSocket-Key") or ""
    upgrade = (headers.get("Upgrade") or "").lower() == "websocket"
    conn = "upgrade" in [p.strip().lower() for p in (headers.get("Connection") or "").split(",")]
    try:
        good_key = len(base64.b64decode(key, validate=True)) == 16
    except ValueError:
        good_key = False
    if upgrade and conn and good_key and headers.get("Sec-WebSocket-Version") == "13":
        return key
    return None


class Conn:
    """One accepted WebSocket over a socket's buffered reader and writer. recv() from one thread,
    send()/ping()/close() from any."""

    def __init__(self, rfile, wfile, max_message=MAX_MESSAGE, kill=None):
        self.r, self.w, self.max = rfile, wfile, max_message
        self.kill = kill          # wakes a recv() blocked in another thread (socket shutdown)
        self._wlock = threading.Lock()
        self.closed = False

    def _read(self, n):
        data = self.r.read(n)
        if data is None or len(data) < n:
            raise Closed("connection lost")
        return data

    def _frame(self):
        b0, b1 = self._read(2)
        fin, op, masked, n = b0 & 0x80, b0 & 0x0F, b1 & 0x80, b1 & 0x7F
        if b0 & 0x70:
            raise Closed("reserved bits set")
        if not masked:
            raise Closed("client frame not masked")
        if n == 126:
            n = struct.unpack("!H", self._read(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read(8))[0]
        if n > self.max:
            self.close(1009, "too big")
            raise Closed("frame too big")
        if op >= 0x8 and (n > 125 or not fin):
            raise Closed("bad control frame")
        mask = self._read(4)
        data = bytearray(self._read(n))
        for i in range(n):
            data[i] ^= mask[i & 3]
        return bool(fin), op, bytes(data)

    def recv(self):
        """The next text message. Answers pings; raises Closed at the end."""
        parts, size = [], 0
        while True:
            fin, op, data = self._frame()
            if op == PING:
                self._send(PONG, data)
                continue
            if op == PONG:
                continue
            if op == CLOSE:
                self.close(1000, "")
                raise Closed("closed by the peer")
            if op == BINARY or (op == TEXT and parts) or (op == CONT and not parts) \
                    or op not in (TEXT, CONT):
                self.close(1003, "text only")
                raise Closed("unexpected frame")
            parts.append(data)
            size += len(data)
            if size > self.max:
                self.close(1009, "too big")
                raise Closed("message too big")
            if fin:
                try:
                    return b"".join(parts).decode()
                except UnicodeDecodeError:
                    self.close(1007, "not UTF-8")
                    raise Closed("not UTF-8") from None

    def _send(self, op, data):
        n = len(data)
        head = bytes([0x80 | op])
        if n < 126:
            head += bytes([n])
        elif n < 1 << 16:
            head += bytes([126]) + struct.pack("!H", n)
        else:
            head += bytes([127]) + struct.pack("!Q", n)
        with self._wlock:
            if self.closed and op != CLOSE:
                raise Closed("already closed")
            try:
                self.w.write(head + data)
                self.w.flush()
            except (OSError, ValueError) as e:
                self.closed = True
                raise Closed(str(e)) from None

    def send(self, text):
        self._send(TEXT, text.encode())

    def ping(self):
        self._send(PING, b"")

    def close(self, code=1000, reason=""):
        if self.closed:
            return
        try:
            self._send(CLOSE, struct.pack("!H", code) + reason.encode()[:120])
        except Closed:
            pass
        self.closed = True
        if self.kill:
            try:
                self.kill()
            except OSError:
                pass
