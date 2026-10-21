"""What the stores of phones, agents, tokens and sign-ins all need, once: a hash, a clean name,
a free name, a one-time code."""

import hashlib
import re
import secrets
import time


def sha256_hex(s):
    """The SHA-256 of a secret or a ticket, as it is kept on the disk (never the secret itself)."""
    return hashlib.sha256(str(s).encode()).hexdigest()


def clean_name(name, pattern, default="", dots=False):
    """A name as shown: spaces squeezed, at most 40 characters, and `default` when `pattern` (the
    plain characters it may hold) refuses it. `dots` turns "." into "-" first, for a name that is
    a step of a data path."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if dots:
        name = name.replace(".", "-")
    name = name[:40]
    return name if pattern.fullmatch(name) else default


def unique_name(name, taken):
    """`name`, or `name 2`, `name 3`… when `taken` (the names in use, compared without case) has it."""
    taken = {t.lower() for t in taken}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def add_code(st, key, entry_for, limit, minutes, now=None):
    """A one-time pairing code (24 characters) kept under `key` in the store `st` (the caller's own `store`), as `entry_for(code)`
    plus its end. The expired ones go, and the oldest when there are `limit`. The caller holds the
    lock of its own store; None without LABHUD_DATA."""
    if not st.enabled():
        return None
    now = now or time.time()
    code = secrets.token_urlsafe(18)
    codes = {h: c for h, c in (st.load(key) or {}).items() if c.get("until", 0) > now}
    while len(codes) >= limit:
        codes.pop(min(codes, key=lambda h: codes[h]["until"]))
    cid, entry = entry_for(code)
    codes[cid] = dict(entry, until=int(now + minutes * 60))
    st.save(key, codes)
    return code
