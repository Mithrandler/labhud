"""Editing config.toml from a browser, for a few minutes at a time.

labhud has no login, so the editor is opened the way the setup page is: `init.py edit` (run
where labhud's files are: `docker exec labhud python3 /app/init.py edit`) writes a one-time code's
hash and an end time into `.labhud-edit` next to config.toml, and prints the link with the code.
Only whoever can run that command can edit. Every call then needs the code, from a name in
LABHUD_HOSTS, with the page's own Origin, as JSON; ten wrong codes lock the editor for a minute.

A save is checked first (the same check as at start) and written in place, keeping the file's
inode, so a single mounted config.toml follows; the server then reloads it like any edit.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
import time

ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
DEFAULT_MINUTES = 15
MAX_FAILS = 10
LOCK_SECONDS = 60

_lock = threading.Lock()
_fails = {"n": 0, "until": 0}


def ticket_path(config_path):
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), ".labhud-edit")


def open_ticket(config_path, minutes=DEFAULT_MINUTES):
    """A new code, valid `minutes`; returns it (only its hash is kept)."""
    code = "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(2))
    path = ticket_path(config_path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"hash": hashlib.sha256(code.encode()).hexdigest(), "until": int(time.time() + minutes * 60)}, f)
    return code


def check(config_path, given, now=None):
    """True if `given` is the current code and it has not expired."""
    now = now or time.time()
    with _lock:
        if now < _fails["until"]:
            return False
        try:
            with open(ticket_path(config_path)) as f:
                t = json.load(f)
        except (OSError, ValueError):
            return False
        ok = (now < t.get("until", 0) and isinstance(given, str)
              and hmac.compare_digest(hashlib.sha256(given.strip().upper().encode()).hexdigest(), t.get("hash", "")))
        if ok:
            _fails["n"] = 0
        else:
            _fails["n"] += 1
            if _fails["n"] >= MAX_FAILS:
                _fails.update(n=0, until=now + LOCK_SECONDS)
        return ok


def save(config_path, text):
    """Writes `text` over config.toml in place (same inode); a copy of the old one goes to
    config.toml.bak when the folder allows it."""
    try:
        with open(config_path, encoding="utf-8") as f:
            old = f.read()
        with open(config_path + ".bak", "w", encoding="utf-8") as f:
            f.write(old)
    except OSError:
        pass
    with open(config_path, "r+", encoding="utf-8") as f:
        f.seek(0)
        f.write(text)
        f.truncate()
