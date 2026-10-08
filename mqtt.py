"""MQTT: the cards' states and the history's events, for Home Assistant or anything else.

Off unless LABHUD_MQTT_URL is set:

    LABHUD_MQTT_URL        mqtt://broker:1883, or mqtts://broker:8883 (certificate checked like
                           the sources': LABHUD_PINS / LABHUD_VERIFY)
    LABHUD_MQTT_USER       optional
    LABHUD_MQTT_PASS       optional (or LABHUD_MQTT_PASS_FILE)
    LABHUD_MQTT_PREFIX     topic prefix, "labhud"
    LABHUD_MQTT_DISCOVERY  Home Assistant's discovery prefix, "homeassistant"; "off" for none

Topics:
    <prefix>/card/<card id>   up | down | maintenance | scheduled | on-demand   (retained)
    <prefix>/event            {"text", "bad", "t"}, one per line of the history
With discovery, every card with a status becomes a binary_sensor (device class "problem", on
when the card is down) in Home Assistant, grouped under one device.

A minimal MQTT 3.1.1 client, standard library only: each batch opens a connection, publishes
with QoS 0 and disconnects. Messages are queued and sent by one thread, so a broker that is
down never slows the display; while it is down, only the latest state per card is kept.
"""

import hashlib
import hmac
import json
import os
import queue
import re
import socket
import ssl
import struct
import threading
import time
import urllib.parse

URL = os.environ.get("LABHUD_MQTT_URL", "")
USER = os.environ.get("LABHUD_MQTT_USER", "")
PASS = os.environ.get("LABHUD_MQTT_PASS", "")
PREFIX = os.environ.get("LABHUD_MQTT_PREFIX", "labhud").strip("/")
DISCOVERY = os.environ.get("LABHUD_MQTT_DISCOVERY", "homeassistant").strip("/")

state = {"sent": 0, "last_error": None, "last_error_at": None}
_queue = queue.Queue(maxsize=500)
_states = {}            # card id -> last state queued
_pending = {}           # card id -> state not yet delivered (kept while the broker is down)
_lock = threading.Lock()


def enabled():
    return bool(URL)


def problem():
    u = urllib.parse.urlsplit(URL)
    if u.scheme not in ("mqtt", "mqtts") or not u.hostname:
        return "LABHUD_MQTT_URL must be mqtt://host:1883 or mqtts://host:8883"
    return None


# ---------------------------------------------------------------------------------------------
# The protocol (MQTT 3.1.1, QoS 0)
# ---------------------------------------------------------------------------------------------

def _length(n):
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def connect_packet(client_id, user="", password="", keepalive=30):
    flags = 0x02  # clean session
    payload = _str(client_id)
    if user:
        flags |= 0x80
        payload += _str(user)
        if password:
            flags |= 0x40
            payload += _str(password)
    var = _str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
    body = var + payload
    return bytes([0x10]) + _length(len(body)) + body


def publish_packet(topic, payload, retain=False):
    body = _str(topic) + payload
    return bytes([0x30 | (0x01 if retain else 0)]) + _length(len(body)) + body


def _recv(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("the broker closed the connection")
        data += chunk
    return data


CONNACK_ERRORS = {1: "protocol version refused", 2: "client id refused", 3: "broker unavailable",
                  4: "bad user name or password", 5: "not authorised"}


def _open():
    from sources import _common  # the certificate policy (pins, LABHUD_VERIFY) lives with the sources
    u = urllib.parse.urlsplit(URL)
    tls = u.scheme == "mqtts"
    port = u.port or (8883 if tls else 1883)
    sock = socket.create_connection((u.hostname, port), timeout=10)
    if tls:
        https = f"https://{u.hostname}:{port}"
        mode, how = _common.tls_mode(https)
        _common._note_tls(https, mode)
        ctx = _common._NO_VERIFY if mode == "pinned" else how
        sock = ctx.wrap_socket(sock, server_hostname=u.hostname)
        if mode == "pinned":
            got = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(got, how):
                sock.close()
                raise ssl.SSLError(f"the broker's certificate is not the pinned one ({got[:16]}...)")
    sock.sendall(connect_packet(f"labhud-{os.getpid()}", USER, PASS))
    head = _recv(sock, 4)
    if head[0] != 0x20 or head[3] != 0:
        sock.close()
        raise ConnectionError("connection refused: " + CONNACK_ERRORS.get(head[3], f"code {head[3]}"))
    return sock


def _send_batch(messages):
    sock = _open()
    try:
        for topic, payload, retain in messages:
            sock.sendall(publish_packet(topic, payload, retain))
        sock.sendall(b"\xe0\x00")  # DISCONNECT
    finally:
        sock.close()


# ---------------------------------------------------------------------------------------------
# What labhud publishes
# ---------------------------------------------------------------------------------------------

def _topic_id(card_id):
    return re.sub(r"[^A-Za-z0-9_-]", "_", card_id)


def discovery(cards, title):
    """Home Assistant discovery messages for every card with a status: [(topic, payload, retain)]."""
    if DISCOVERY.lower() in ("", "off", "false", "0"):
        return []
    out = []
    for card_id, name in cards.items():
        tid = _topic_id(card_id)
        config = {
            "name": name, "unique_id": f"labhud_{tid}", "object_id": f"labhud_{tid}",
            "state_topic": f"{PREFIX}/card/{card_id}", "device_class": "problem",
            "value_template": "{{ 'ON' if value == 'down' else 'OFF' }}",
            "device": {"identifiers": ["labhud"], "name": title, "manufacturer": "labhud"},
        }
        out.append((f"{DISCOVERY}/binary_sensor/labhud/{tid}/config", json.dumps(config).encode(), True))
    return out


def announce(cards, title):
    """At start and after a config reload: discovery for the cards that have a status."""
    if enabled():
        for msg in discovery(cards, title):
            _put(msg)


def update(states):
    """{card id: state}; only the changes are sent."""
    if not enabled():
        return
    with _lock:
        changed = {k: v for k, v in states.items() if _states.get(k) != v}
        _states.update(changed)
        _pending.update(changed)
    if changed:
        _put(None)  # wake the sender; the states themselves wait in _pending


def event(text, bad, t=None):
    if enabled():
        _put((f"{PREFIX}/event", json.dumps({"text": text, "bad": bool(bad), "t": int(t or time.time())}).encode(), False))


def _put(msg):
    try:
        _queue.put_nowait(msg)
    except queue.Full:  # the broker has been down a long time: events are dropped, states are not
        pass


def _sender():
    backoff = 5
    while True:
        batch = [_queue.get()]
        time.sleep(0.5)  # gather what arrives together (a reload, several cards at once)
        while True:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        with _lock:
            states = dict(_pending)
        messages = [m for m in batch if m] + [
            (f"{PREFIX}/card/{card}", value.encode(), True) for card, value in sorted(states.items())]
        if not messages:
            continue
        try:
            _send_batch(messages)
            with _lock:
                for card, value in states.items():
                    if _pending.get(card) == value:
                        del _pending[card]
            state["sent"] += len(messages)
            backoff = 5
        except Exception as e:
            state.update(last_error=str(e)[:160] or type(e).__name__, last_error_at=int(time.time()))
            print(f"mqtt: {state['last_error']}", flush=True)
            for m in batch:  # discovery and events are retried once the broker is back
                if m:
                    _put(m)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            _put(None)


def start():
    if enabled() and not problem():
        threading.Thread(target=_sender, daemon=True, name="mqtt").start()
