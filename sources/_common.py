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
import json
import os
import re
import ssl
import time
import urllib.request
from dataclasses import dataclass

PREFIX = "LABHUD_"

# Homelab certificates are mostly self-signed, on LAN addresses.
_NO_VERIFY = ssl.create_default_context()
_NO_VERIFY.check_hostname = False
_NO_VERIFY.verify_mode = ssl.CERT_NONE


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
    with urllib.request.urlopen(req, timeout=timeout, context=_NO_VERIFY) as r:
        body = r.read()
    if raw:
        return body.decode("utf-8", "replace")
    return json.loads(body) if body else {}


def basic(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


# Any error text ends up in `unavailable`, so on the screen. No key and no password may pass
# through there, not even from a library exception.
_SECRETS = re.compile(r"(?i)(passwd|password|api[_-]?key|token|secret|_sid|\bkey)=[^&\s\"']+")
_SECRET_VARS = ("_KEY", "_SECRET", "_PASS", "_USER", "_TOKEN_ID")


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
