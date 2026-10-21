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

A phone may leave a UnifiedPush endpoint (`set_push`, POST /api/v1/push): when the app's outbox
gets a message (notify.py) labhud POSTs a content-free wake-up there, and the app then reads the
message itself from /api/v1/notifications, over the home network or VPN. Only https endpoints.

A token only looks, until the phone signs in as the admin (`/api/v1/admin-login`, or pairing by
signing in): then the device keeps the admin's signature (admin.py `sign()`), and acts while that
signature is the admin's. A new admin (another password or authenticator) is another signature,
so every phone goes back to looking without anything to clean up; `admin-logout` drops it at once.
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
import util

DEVICES_KEY = "devices"
CODES_KEY = "pair_codes"
PAIR_MINUTES = 5
MAX_DEVICES = 32
MAX_CODES = 8
SEEN_EVERY = 300          # "last seen" is written at most this often per device
NAME = re.compile(r"[\w .,'()+-]{1,40}")
TOKEN = re.compile(r"([a-z0-9]{8})\.([A-Za-z0-9_-]{32,64})")
CODE = re.compile(r"[A-Za-z0-9_-]{16,64}")
PUSH = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*)?")
MAX_PUSH = 512

_lock = threading.Lock()
_seen = {}                # id -> last time "last seen" was written


def enabled():
    return store.enabled()


def clean_name(name):
    """A device's name as shown in lists: trimmed, at most 40 plain characters, never empty."""
    return util.clean_name(name, NAME, "phone")


# --- pairing codes ---

def new_code(name="", now=None):
    """A one-time pairing code, valid PAIR_MINUTES. None without LABHUD_DATA."""
    with _lock:
        return util.add_code(store, CODES_KEY, lambda code: (util.sha256_hex(code), {"name": clean_name(name) if name else ""}),
                             MAX_CODES, PAIR_MINUTES, now)


def pair(code, name, now=None, admin=None):
    """(token, device name) for a live code, which is used up; (None, None) otherwise. `admin`:
    the admin's signature, for a phone paired by signing in as the admin."""
    if not isinstance(code, str) or not CODE.fullmatch(code) or not store.enabled():
        return None, None
    now = now or time.time()
    with _lock:
        codes = store.load(CODES_KEY) or {}
        entry = codes.pop(util.sha256_hex(code), None)
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
        devices[did] = {"name": name, "hash": util.sha256_hex(secret), "paired": int(now), "seen": int(now)}
        if admin:
            devices[did]["admin"] = admin
        store.save(DEVICES_KEY, devices)
    return f"{did}.{secret}", name


def _unique(name, devices):
    return util.unique_name(name, (d["name"] for d in devices.values()))


# --- tokens ---

def check(token, now=None):
    """The device's name for a valid token, else None. Marks it seen (at most every SEEN_EVERY s)."""
    found = lookup(token, now)
    return found[1]["name"] if found else None


def lookup(token, now=None):
    """(id, record) of the device for a valid token, else None. Marks it seen."""
    m = TOKEN.fullmatch(token or "") if isinstance(token, str) else None
    if not m or not store.enabled():
        return None
    did, secret = m.groups()
    device = (store.load(DEVICES_KEY) or {}).get(did)
    if not device or not hmac.compare_digest(device.get("hash", ""), util.sha256_hex(secret)):
        return None
    now = now or time.time()
    if now - _seen.get(did, 0) >= SEEN_EVERY:
        _seen[did] = now
        with _lock:
            devices = store.load(DEVICES_KEY) or {}
            if did in devices:
                devices[did]["seen"] = int(now)
                store.save(DEVICES_KEY, devices)
    return did, device


def is_admin(device, sign):
    """True for a device signed in as the admin who is the admin now."""
    return bool(sign and device and hmac.compare_digest(str(device.get("admin") or ""), sign))


def set_admin(did, sign):
    """Signs the device in as the admin (sign = admin.sign()), or out (None). False if it is gone."""
    with _lock:
        devices = store.load(DEVICES_KEY) or {}
        if did not in devices:
            return False
        if sign:
            devices[did]["admin"] = sign
        else:
            devices[did].pop("admin", None)
        store.save(DEVICES_KEY, devices)
    return True


def set_push(did, endpoint):
    """The device's UnifiedPush endpoint, or "" to forget it. Raises ValueError for one that is not
    an https address; False if the device is gone."""
    endpoint = str(endpoint or "").strip()
    if endpoint and (len(endpoint) > MAX_PUSH or not PUSH.fullmatch(endpoint)):
        raise ValueError("the push endpoint must be an https:// address")
    with _lock:
        devices = store.load(DEVICES_KEY) or {}
        if did not in devices:
            return False
        if endpoint:
            devices[did]["push"] = endpoint
        else:
            devices[did].pop("push", None)
        store.save(DEVICES_KEY, devices)
    return True


def push_endpoints():
    """{endpoint: device id} of the phones that left one."""
    devices = store.load(DEVICES_KEY) or {} if store.enabled() else {}
    return {d["push"]: did for did, d in devices.items() if d.get("push")}


def drop_push(endpoint):
    """Forgets an endpoint its push server says is gone (404/410)."""
    with _lock:
        devices = store.load(DEVICES_KEY) or {}
        hit = [did for did, d in devices.items() if d.get("push") == endpoint]
        for did in hit:
            devices[did].pop("push", None)
        if hit:
            store.save(DEVICES_KEY, devices)


def listing(sign=None):
    """[{name, paired, seen, admin, push}], oldest first: for init.py devices, ⚙ and /status. No
    hashes. `admin` is true only for a phone signed in as the current admin (`sign`); `push` says
    it has a UnifiedPush endpoint (never the endpoint: it is a capability)."""
    devices = store.load(DEVICES_KEY) or {} if store.enabled() else {}
    return sorted(({"name": d["name"], "paired": d.get("paired", 0), "seen": d.get("seen", 0),
                    "admin": is_admin(d, sign), "push": bool(d.get("push"))}
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
