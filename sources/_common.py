# What every source module shares: the registry, HTTP, environment access, formatting.
#
# Every fetch function returns a dict and does not raise upward for a missing field: the server
# catches exceptions anyway, but a source that copes with a missing field on its own gives a more
# useful screen than one that fails entirely. Everything is standard library — the container has no
# pip install.
#
# API keys are read from the environment (the .env file mounted by compose) and NEVER go to the
# browser: the phone only sees the result, through SSE.

import base64
import functools
import hashlib
import hmac
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

PREFIX = "LABHUD_"

# Homelab certificates are mostly self-signed, on LAN addresses. Unchecked, HTTPS is encrypted but
# anyone who can sit between labhud and a service (ARP spoofing, a forged DNS answer) can pose as
# it and receive its key. So, per host (see docs/security.md):
#   LABHUD_PINS="192.0.2.10:8006=<sha256 of the certificate>,nas.lan=<...>"   the exact certificate
#   LABHUD_VERIFY=on (+ LABHUD_CA=/path/ca.pem)   normal checking, against the system CAs or yours
#   neither: not checked (the default in 0.1.x), logged once per host and shown on /status.
_NO_VERIFY = ssl.create_default_context()
_NO_VERIFY.check_hostname = False
_NO_VERIFY.verify_mode = ssl.CERT_NONE


def parse_pins(value):
    """"host[:port]=AB:CD:...,other=abcd..." -> {"host[:port]": "abcd..."}. Colons and case in the
    fingerprint do not matter (openssl prints AB:CD:..., init.py prints plain hex)."""
    out = {}
    for part in (p.strip() for p in (value or "").split(",") if p.strip()):
        where, sep, fp = part.partition("=")
        fp = fp.replace(":", "").strip().lower()
        if not sep or not re.fullmatch(r"[0-9a-f]{64}", fp):
            raise SystemExit(f"LABHUD_PINS: {part!r} is not host[:port]=<sha256 fingerprint>")
        out[where.strip().lower()] = fp
    return out


PINS = parse_pins(os.environ.get(PREFIX + "PINS"))
VERIFY = os.environ.get(PREFIX + "VERIFY", "").lower() in ("1", "on", "true", "yes")
_CA = os.environ.get(PREFIX + "CA", "")
_VERIFIED = ssl.create_default_context(cafile=_CA or None) if VERIFY or _CA else None
# "host:port" -> "pinned" | "verified" | "unchecked", for /status; the first unchecked use is logged.
TLS_SEEN = {}
_tls_lock = threading.Lock()


class _PinnedConnection(http.client.HTTPSConnection):
    """Compares the certificate with the pinned fingerprint right after the handshake, before a
    single byte of the request (and so of the key) is sent."""

    def __init__(self, *args, pin, **kwargs):
        super().__init__(*args, **kwargs)
        self._pin = pin

    def connect(self):
        super().connect()
        got = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(got, self._pin):
            self.sock.close()
            raise ssl.SSLError(f"the certificate of {self.host}:{self.port} is not the pinned one "
                               f"(it has {got[:16]}...): changed, or someone in between")


class _PinnedHandler(urllib.request.HTTPSHandler):
    def __init__(self, pin):
        super().__init__(context=_NO_VERIFY)
        self._pin = pin

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedConnection, pin=self._pin), req, context=_NO_VERIFY)


def tls_mode(url):
    """("pinned", fingerprint) | ("verified", context) | ("unchecked", context) | (None, None) for http."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        return None, None
    host = (u.hostname or "").lower()
    where = f"{host}:{u.port or 443}"
    pin = PINS.get(where) or PINS.get(host)
    if pin:
        return "pinned", pin
    if _VERIFIED:
        return "verified", _VERIFIED
    return "unchecked", _NO_VERIFY


def _note_tls(url, mode):
    u = urllib.parse.urlsplit(url)
    where = f"{(u.hostname or '').lower()}:{u.port or 443}"
    with _tls_lock:
        if TLS_SEEN.get(where) == mode:
            return
        TLS_SEEN[where] = mode
    if mode == "unchecked":
        print(f"warning: the certificate of {where} is not checked. Pin it (LABHUD_PINS, see "
              f"`python3 init.py fingerprint https://{where}`) or set LABHUD_VERIFY=on.", flush=True)


def urlopen(req, timeout):
    """urllib's urlopen with the certificate policy of the request's host."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    mode, how = tls_mode(url)
    if mode:
        _note_tls(url, mode)
    if mode == "pinned":
        return urllib.request.build_opener(_PinnedHandler(how)).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=how)


# ---------------------------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------------------------

@dataclass
class Source:
    name: str             # the key in the snapshot, and the first part of every data path
    fetch: object         # fetch() -> dict, or fetch(section) when `section` is set
    every: int            # seconds between two polls
    env: tuple = ()       # one or more groups of variables (without LABHUD_); active if any group is complete
    section: str = None   # a config.toml table the source needs, passed to fetch()

    def missing(self, topology):
        """None if the source can run, otherwise the list of what is missing (names only, never values)."""
        if self.section and not topology.get(self.section):
            return [f"[{self.section}] in config.toml"]
        if not self.env:
            return None
        for group in self.env:
            lacking = [PREFIX + n for n in group if not env(n)]
            if not lacking:
                return None
        # report the first group: it is the main one (recent/calendar: Sonarr)
        return [PREFIX + n for n in self.env[0] if not env(n)]

    def run(self, topology):
        return self.fetch(topology[self.section]) if self.section else self.fetch()


REGISTRY = {}
# name -> a function that asks the service what the source's key may do: [(where, [privilege])]
# for every privilege beyond reading. Only for services whose keys can be limited (Proxmox VE,
# PBS); the others give their keys full rights anyway, see docs/security.md.
RIGHTS = {}
# Read-only privileges: what a monitoring key should be limited to. VM.Monitor (Proxmox 8) and
# VM.GuestAgent.Audit (Proxmox 9) are what the guest agent's disk usage needs.
READ_ONLY = re.compile(r"\.Audit$|^Sys\.Syslog$|^VM\.Monitor$")


def rights(name):
    """Registers the rights check of source `name`."""
    def wrap(fn):
        RIGHTS[name] = fn
        return fn
    return wrap


def beyond_reading(permissions):
    """The privileges in a Proxmox/PBS /access/permissions answer ({path: {priv: 0|1}}) that do
    more than read, sorted."""
    return sorted({p for privs in (permissions or {}).values() if isinstance(privs, dict)
                   for p in privs if not READ_ONLY.search(p)})


def source(name, every, env=(), any_of=(), section=None):
    """Registers a fetch function. `env` is the list of variables it needs, all of them;
    `any_of` is several such lists, of which one complete is enough."""
    def wrap(fn):
        groups = tuple(tuple(g) for g in any_of) if any_of else ((tuple(env),) if env else ())
        REGISTRY[name] = Source(name, fn, every, groups, section)
        return fn
    return wrap


def env(name, default=""):
    return os.environ.get(PREFIX + name, default)


def has(*names):
    return all(env(n) for n in names)


def env_name(label):
    """A node or label as it appears inside a variable name: "node1" -> "NODE1", "my-nas" -> "MY_NAS"."""
    return re.sub(r"[^A-Z0-9]", "_", label.upper())


def labelled_urls(value):
    """"a=http://x,b=http://y" -> {"a": "http://x", "b": "http://y"}; a single URL -> {"": url}."""
    out = {}
    for part in (p.strip() for p in value.split(",") if p.strip()):
        label, sep, url = part.partition("=")
        if sep and not label.startswith(("http:", "https:")) and "/" not in label:
            out[label.strip()] = url.strip()
        else:
            out[""] = part
    return out


# ---------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------

def request(url, headers=None, data=None, timeout=10, method=None, raw=False):
    """GET/POST JSON. Returns the decoded dict, or the raw text when raw=True."""
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout) as r:
        body = r.read()
    if raw:
        return body.decode("utf-8", "replace")
    return json.loads(body) if body else {}


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# Any error text ends up in `unavailable`, so on the screen. No key and no password may pass
# through there, not even from a library exception.
_SECRETS = re.compile(r"(?i)(passwd|password|api[_-]?key|token|secret|_sid|\bkey)=[^&\s\"']+")
_SECRET_VARS = ("_KEY", "_SECRET", "_PASS", "_USER", "_TOKEN_ID", "_AUTH")


def scrub(text):
    text = _SECRETS.sub(r"\1=<hidden>", str(text))
    for name, value in os.environ.items():
        if name.startswith(PREFIX) and name.endswith(_SECRET_VARS) and len(value) >= 8:
            text = text.replace(value, "<hidden>")
    return text


# ---------------------------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------------------------

def percent(part, whole):
    try:
        return round(100.0 * part / whole, 1) if whole else None
    except (TypeError, ZeroDivisionError):
        return None


def fmt_bytes(n):
    """Same format as bytes() in app.js: 14G, 1.5T, 612M."""
    v, i = float(n or 0), 0
    while v >= 1024 and i < 4:
        v /= 1024
        i += 1
    return (str(round(v)) if v >= 100 or i == 0 else f"{v:.1f}") + "BKMGT"[i]


def ago(iso):
    """\"2026-10-06T19:27:24Z\" -> \"1d ago\" / \"5h ago\" / \"12m ago\"."""
    try:
        t = time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except (ValueError, TypeError):
        return ""
    d = max(0, time.time() - t)
    return f"{int(d // 86400)}d ago" if d >= 86400 else f"{int(d // 3600)}h ago" if d >= 3600 else f"{int(d // 60)}m ago"


def in_window(window):
    """Are we inside the given hour window, as (start_hour, end_hour)? Crosses midnight too."""
    if not window:
        return False
    start, end = window
    hour = time.localtime().tm_hour
    return start <= hour < end if start < end else (hour >= start or hour < end)
