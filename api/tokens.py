"""API tokens: for scripts and other tools that talk to labhud without a browser or the app.

A token is `lh_` + 32 random bytes (urlsafe base64), shown once when it is made; labhud keeps only
its SHA-256 (a 256-bit random secret needs no slow hash, as devices.py says for the phones). It is
sent as `Authorization: Bearer lh_…` and each has one scope, each including the one before it:

    read    the GETs that show state: /api/v1/state, /api/v1/card/<id>, history, uptime, events…
            (a token stands in for the LABHUD_HOSTS check, as a phone's does)
    act     + the cards' buttons (/action/<name>) and maintenance, without the screen's PIN
    admin   + everything ⚙ does (/api/setup/*, /api/agents/*, the routes of api/, this file's too)
            and GET /api/v1/audit

A name, an optional expiry, and when and from where it was last used. Kept in the store
(LABHUD_DATA, key "api_tokens"); without it there are no tokens.

    POST /api/tokens/list                    -> {tokens: [{id, name, scope, made, expires, last, ip, expired}]}
    POST /api/tokens/create {name, scope, days?}  -> {token: "lh_…", id, …}: the only time it is shown
    POST /api/tokens/revoke {id}             -> ends it at once

All three behind ⚙'s guard (the admin, unlocked; or an admin token, or the labhud command).
"""

import hmac
import re
import secrets
import threading
import time

import store
import util
from routes import route

KEY = "api_tokens"
PREFIX = "lh_"
SCOPES = ("read", "act", "admin")      # each includes the ones before it
MAX_TOKENS = 32
MAX_DAYS = 3650
USED_EVERY = 60                        # "last used" is written at most this often per token
NAME = re.compile(r"[\w .,'()+:/@-]{1,40}")
TOKEN = re.compile(r"lh_[A-Za-z0-9_-]{43}")
ID = re.compile(r"[a-f0-9]{8}")

_lock = threading.Lock()
_used = {}                             # id -> when "last used" was last written


def enabled():
    return store.enabled()


def clean_name(name):
    return util.clean_name(name, NAME)


def allows(scope, needed):
    """True if a token of `scope` may do what needs `needed` (read < act < admin)."""
    return scope in SCOPES and needed in SCOPES and SCOPES.index(scope) >= SCOPES.index(needed)


def _all():
    found = store.load(KEY) or {}
    return found if isinstance(found, dict) else {}


def make(name, scope, days=None, now=None):
    """(token, record) for a new token, or raises ValueError (a bad name, scope or expiry, too many,
    or no LABHUD_DATA). The token itself is in this answer only."""
    if not store.enabled():
        raise ValueError("API tokens need LABHUD_DATA (a folder labhud can write)")
    name = clean_name(name)
    if not name:
        raise ValueError("give the token a name: up to 40 letters, digits, spaces and .,'()+:/@-")
    if scope not in SCOPES:
        raise ValueError("scope is one of read, act, admin")
    if days in (None, "", 0):
        days = None
    elif isinstance(days, bool) or not isinstance(days, int) or not 0 < days <= MAX_DAYS:
        raise ValueError(f"expiry is a number of days, 1 to {MAX_DAYS}, or none")
    now = int(now or time.time())
    with _lock:
        tokens = _all()
        if len(tokens) >= MAX_TOKENS:
            raise ValueError(f"at most {MAX_TOKENS} tokens: revoke one first")
        if any(t.get("name", "").lower() == name.lower() for t in tokens.values()):
            raise ValueError(f"there is already a token named {name}")
        tid = secrets.token_hex(4)
        while tid in tokens:
            tid = secrets.token_hex(4)
        token = PREFIX + secrets.token_urlsafe(32)
        rec = {"name": name, "scope": scope, "hash": util.sha256_hex(token), "made": now,
               "expires": now + days * 86400 if days else 0, "last": 0, "ip": ""}
        tokens[tid] = rec
        store.save(KEY, tokens)
    return token, public(tid, rec, now)


def lookup(token, ip="", now=None):
    """(id, record) of the live token, else None. Every kept hash is compared, in constant time,
    so how long this takes says nothing about which (or whether one) matched. Marks it used."""
    if not isinstance(token, str) or not TOKEN.fullmatch(token) or not store.enabled():
        return None
    given = util.sha256_hex(token)
    now = now or time.time()
    found = None
    for tid, rec in _all().items():
        if hmac.compare_digest(str(rec.get("hash", "")), given) and found is None:
            found = (tid, rec)
    if not found:
        return None
    tid, rec = found
    if rec.get("expires") and rec["expires"] <= now:
        return None
    if now - _used.get(tid, 0) >= USED_EVERY:
        _used[tid] = now
        with _lock:
            tokens = _all()
            if tid in tokens:
                tokens[tid].update(last=int(now), ip=str(ip)[:64])
                store.save(KEY, tokens)
    return tid, rec


def public(tid, rec, now=None):
    """What lists show: never the hash."""
    now = now or time.time()
    return {"id": tid, "name": rec.get("name", ""), "scope": rec.get("scope", ""), "made": rec.get("made", 0),
            "expires": rec.get("expires", 0), "last": rec.get("last", 0), "ip": rec.get("ip", ""),
            "expired": bool(rec.get("expires") and rec["expires"] <= now)}


def listing(now=None):
    return sorted((public(tid, rec, now) for tid, rec in _all().items()), key=lambda t: t["made"])


def revoke(tid):
    """The revoked token's name, or None if there is none with that id."""
    if not isinstance(tid, str) or not ID.fullmatch(tid):
        return None
    with _lock:
        tokens = _all()
        rec = tokens.pop(tid, None)
        if rec is None:
            return None
        store.save(KEY, tokens)
    _used.pop(tid, None)
    return rec.get("name", "")


# --- routes (⚙ > System > API & audit; the app's Settings > API & audit) ---

def _audit(h, action, target, ok=True):
    try:
        from api import audit
        audit.record(h, action, target, ok)
    except Exception as e:   # the log never stops the change it records
        print(f"audit: not written: {e!r}", flush=True)


@route("POST", "/api/tokens/list")
def list_tokens(h, body):
    return 200, {"ok": True, "tokens": listing(), "enabled": enabled(), "scopes": list(SCOPES)}


@route("POST", "/api/tokens/create")
def create_token(h, body):
    try:
        token, rec = make(body.get("name"), body.get("scope"), body.get("days"))
    except ValueError as e:
        _audit(h, "token-create", clean_name(body.get("name")), False)
        return 400, {"ok": False, "error": str(e)}
    print(f"api: token {rec['name']} ({rec['scope']}) made from {h._who()}", flush=True)
    _audit(h, "token-create", f"{rec['name']} ({rec['scope']})")
    return 200, {"ok": True, "token": token, **rec}


@route("POST", "/api/tokens/revoke")
def revoke_token(h, body):
    name = revoke(body.get("id"))
    if name is None:
        _audit(h, "token-revoke", str(body.get("id") or "")[:16], False)
        return 404, {"ok": False, "error": "no such token"}
    print(f"api: token {name} revoked from {h._who()}", flush=True)
    _audit(h, "token-revoke", name)
    return 200, {"ok": True}
