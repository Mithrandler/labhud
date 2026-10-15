"""Paired devices: the LabHUD app on a phone, with a token of its own.

labhud trusts its own page by the Host and Origin headers (see server.py). An app is not that
page, so it is paired once and then carries a token: `Authorization: Bearer <token>`, or the
`labhud_device` cookie for the wall page the app opens in a WebView. A valid token stands in for
the host allowlist, the Origin check and the lock's ticket: the app asks for the phone's
fingerprint or PIN itself before it shows anything.

Pairing: `init.py pair` or "Pair a phone" in the display's ⚙ makes a one-time code, valid for
PAIR_MINUTES, shown as a link and a QR code. The app sends the code to /api/v1/pair and gets its
token back, once. Both live in the store (LABHUD_DATA), so `init.py` (another process) and the
server see the same codes and devices; without LABHUD_DATA there is no pairing.

Only SHA-256 hashes of codes and tokens are kept: they are 128+ random bits, so a slow hash
(like the PIN's scrypt) would add nothing. A token is "<id>.<secret>": the id finds the device,
the secret is compared in constant time. `init.py unpair <name>` ends a device at once.
"""

import hashlib
import hmac
import re
import secrets
import ssl
import threading
import time
import urllib.parse

import store

DEVICES_KEY = "devices"
CODES_KEY = "pair_codes"
PAIR_MINUTES = 5
MAX_DEVICES = 32
MAX_CODES = 8
SEEN_EVERY = 300          # "last seen" is written at most this often per device
NAME = re.compile(r"[\w .,'()+-]{1,40}")
TOKEN = re.compile(r"([a-z0-9]{8})\.([A-Za-z0-9_-]{32,64})")
CODE = re.compile(r"[A-Za-z0-9_-]{16,64}")

_lock = threading.Lock()
_seen = {}                # id -> last time "last seen" was written


def _hash(s):
    return hashlib.sha256(s.encode()).hexdigest()


def enabled():
    return store.enabled()


def clean_name(name):
    """A device's name as shown in lists: trimmed, at most 40 plain characters, never empty."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:40]
    return name if NAME.fullmatch(name) else "phone"


# --- pairing codes ---

def new_code(name="", now=None):
    """A one-time pairing code, valid PAIR_MINUTES. None without LABHUD_DATA."""
    if not store.enabled():
        return None
    now = now or time.time()
    code = secrets.token_urlsafe(18)
    with _lock:
        codes = {h: c for h, c in (store.load(CODES_KEY) or {}).items() if c.get("until", 0) > now}
        while len(codes) >= MAX_CODES:
            codes.pop(min(codes, key=lambda h: codes[h]["until"]))
        codes[_hash(code)] = {"until": int(now + PAIR_MINUTES * 60), "name": clean_name(name) if name else ""}
        store.save(CODES_KEY, codes)
    return code


def pair(code, name, now=None):
    """(token, device name) for a live code, which is used up; (None, None) otherwise."""
    if not isinstance(code, str) or not CODE.fullmatch(code) or not store.enabled():
        return None, None
    now = now or time.time()
    with _lock:
        codes = store.load(CODES_KEY) or {}
        entry = codes.pop(_hash(code), None)
        codes = {h: c for h, c in codes.items() if c.get("until", 0) > now}
        store.save(CODES_KEY, codes)
        if not entry or entry.get("until", 0) <= now:
            return None, None
        devices = store.load(DEVICES_KEY) or {}
        if len(devices) >= MAX_DEVICES:
            return None, None
        name = _unique(entry.get("name") or clean_name(name), devices)
        did = secrets.token_hex(4)
        while did in devices:
            did = secrets.token_hex(4)
        secret = secrets.token_urlsafe(32)
        devices[did] = {"name": name, "hash": _hash(secret), "paired": int(now), "seen": int(now)}
        store.save(DEVICES_KEY, devices)
    return f"{did}.{secret}", name


def _unique(name, devices):
    taken = {d["name"].lower() for d in devices.values()}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


# --- tokens ---

def check(token, now=None):
    """The device's name for a valid token, else None. Marks it seen (at most every SEEN_EVERY s)."""
    m = TOKEN.fullmatch(token or "") if isinstance(token, str) else None
    if not m or not store.enabled():
        return None
    did, secret = m.groups()
    device = (store.load(DEVICES_KEY) or {}).get(did)
    if not device or not hmac.compare_digest(device.get("hash", ""), _hash(secret)):
        return None
    now = now or time.time()
    if now - _seen.get(did, 0) >= SEEN_EVERY:
        _seen[did] = now
        with _lock:
            devices = store.load(DEVICES_KEY) or {}
            if did in devices:
                devices[did]["seen"] = int(now)
                store.save(DEVICES_KEY, devices)
    return device["name"]


def listing():
    """[{name, paired, seen}], oldest first: for init.py devices and /status. No hashes."""
    devices = store.load(DEVICES_KEY) or {} if store.enabled() else {}
    return sorted(({"name": d["name"], "paired": d.get("paired", 0), "seen": d.get("seen", 0)}
                   for d in devices.values()), key=lambda d: d["paired"])


def unpair(name):
    """Removes the device with that name (case does not matter). True if there was one."""
    if not store.enabled():
        return False
    with _lock:
        devices = store.load(DEVICES_KEY) or {}
        gone = [did for did, d in devices.items() if d["name"].lower() == str(name).strip().lower()]
        for did in gone:
            del devices[did]
            _seen.pop(did, None)
        if gone:
            store.save(DEVICES_KEY, devices)
    return bool(gone)


# --- the pairing link ---

def tls_fingerprint(cert_path):
    """SHA-256 of labhud's own certificate (the first in LABHUD_TLS_CERT), for the app to pin."""
    if not cert_path:
        return ""
    end = "-----END CERTIFICATE-----"
    try:
        with open(cert_path, encoding="ascii") as f:
            pem = f.read()
        return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem[:pem.index(end) + len(end)])).hexdigest()
    except (OSError, ValueError):
        return ""


def pair_link(base, code, cert_path=""):
    """labhud://pair?u=<labhud's address>&c=<one-time code>[&fp=<its certificate's SHA-256>]."""
    q = {"u": base.rstrip("/"), "c": code}
    fp = tls_fingerprint(cert_path)
    if fp:
        q["fp"] = fp
    return "labhud://pair?" + urllib.parse.urlencode(q)
