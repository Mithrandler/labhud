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


_PUBLIC = ssl.create_default_context()


def tls_mode(url, public=False):
    """("pinned", fingerprint) | ("verified", context) | ("unchecked", context) | (None, None) for
    http. `public`: a service on the internet, with a real certificate: always checked."""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        return None, None
    host = (u.hostname or "").lower()
    where = f"{host}:{u.port or 443}"
    pin = PINS.get(where) or PINS.get(host)
    if pin:
        return "pinned", pin
    if _VERIFIED or public:
        return "verified", _VERIFIED or _PUBLIC
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


def urlopen(req, timeout, public=False):
    """urllib's urlopen with the certificate policy of the request's host."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    mode, how = tls_mode(url, public)
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
    backoff: bool = True  # polled less often while failing (not a pushed source: it is only read)
    title: str = ""       # the service's name, for the setup page
    about: str = ""       # one line: what the source shows and where its key comes from
    hints: dict = None    # variable (without LABHUD_) -> what to put there, for the setup page

    def fields(self):
        """[{"name", "label", "secret", "hint"}] for every variable the source can use, in order."""
        seen, out = set(), []
        for group in self.env:
            for n in group:
                if n not in seen:
                    seen.add(n)
                    out.append({"name": PREFIX + n, "label": field_label(n), "secret": is_secret(n),
                                "hint": (self.hints or {}).get(n, "")})
        return out

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


def source(name, every, env=(), any_of=(), section=None, backoff=True, title="", about="", hints=None):
    """Registers a fetch function. `env` is the list of variables it needs, all of them;
    `any_of` is several such lists, of which one complete is enough. `title`, `about` and `hints`
    ({variable: text}) describe it on the setup page."""
    def wrap(fn):
        groups = tuple(tuple(g) for g in any_of) if any_of else ((tuple(env),) if env else ())
        new = Source(name, fn, every, groups, section, backoff, title or name, about, hints)
        old = REGISTRY.get(name)
        # Two sources with one name (LABHUD_JSON_GAMES_URL and the built-in `games`): the one
        # that is set up wins, so a built-in added later never hides your own source.
        if old and old.fetch is not fn and old.missing({}) is None and not section:
            if new.missing({}) is not None:
                return fn
            print(f"warning: two sources are called {name!r}; the later one is used", flush=True)
        REGISTRY[name] = new
        return fn
    return wrap


def is_secret(name):
    """Is variable `name` (with or without LABHUD_) a key or a password, never shown back?"""
    return name.endswith(("_KEY", "_SECRET", "_PASS", "_TOKEN", "_AUTH"))


def field_label(name):
    """"HEALTHCHECKS_URL" -> "URL", "PBS_TOKEN_ID" -> "Token ID": the part after the service."""
    words = name.split("_")[1:] or [name]
    label = " ".join({"PASS": "password", "KEY": "API key"}.get(w, w.lower()) for w in words)
    label = label[0].upper() + label[1:]
    return re.sub(r"\bUrl\b|\burl\b", "URL", re.sub(r"\b[Ii]d\b", "ID", label))


# Values being tried on the setup page, for the current thread only: a source tested there reads
# them instead of the environment, and the running sources never see them.
_TRYING = threading.local()


def trying(values):
    """Context manager: env() in this thread reads `values` ({"LABHUD_X": "..."}) first."""
    import contextlib

    @contextlib.contextmanager
    def ctx():
        old = getattr(_TRYING, "values", None)
        _TRYING.values = dict(values)
        try:
            yield
        finally:
            _TRYING.values = old
    return ctx()


def env(name, default=""):
    overlay = getattr(_TRYING, "values", None)
    if overlay and overlay.get(PREFIX + name):
        return overlay[PREFIX + name]
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

def request(url, headers=None, data=None, timeout=10, method=None, raw=False, public=False):
    """GET/POST JSON. Returns the decoded dict, or the raw text when raw=True."""
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urlopen(req, timeout, public) as r:
        body = r.read()
    if raw:
        return body.decode("utf-8", "replace")
    return json.loads(body) if body else {}


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# Any error text ends up in `unavailable`, so on the screen. No key and no password may pass
# through there, not even from a library exception.
_SECRETS = re.compile(r"(?i)(passwd|password|api[_-]?key|token|secret|_sid|\bkey)=[^&\s\"']+")
_SECRET_VARS = ("_KEY", "_SECRET", "_PASS", "_USER", "_TOKEN_ID", "_AUTH", "_TOKEN")


def scrub(text):
    text = _SECRETS.sub(r"\1=<hidden>", str(text))
    trying_now = getattr(_TRYING, "values", None) or {}
    for name, value in list(os.environ.items()) + list(trying_now.items()):
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
