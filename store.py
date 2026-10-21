"""What survives a restart, when LABHUD_DATA names a writable folder: the event history, the
sparklines and maintenance set from the display. One small SQLite file, `labhud.db`, holding a
few JSON values by name. Without LABHUD_DATA everything stays in memory, as before.

The container's root filesystem stays read-only: mount a volume at that folder (owned by uid
10001), see compose.example.yaml.
"""

import json
import os
import sqlite3
import threading

DIR = os.environ.get("LABHUD_DATA", "")
_lock = threading.Lock()
_db = None
PROBLEM = None   # why the store could not open, for /status and the log


def _open():
    global _db, PROBLEM
    if _db is not None or not DIR or PROBLEM:
        return _db
    try:
        _db = sqlite3.connect(os.path.join(DIR, "labhud.db"), check_same_thread=False)
        _db.execute("PRAGMA journal_mode=WAL")
        _db.execute("CREATE TABLE IF NOT EXISTS kv (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        _db.commit()
    except sqlite3.Error as e:
        PROBLEM = f"{DIR}: {e} (is it writable by uid {os.getuid()}?)"
        print(f"warning: LABHUD_DATA not usable, nothing will be kept: {PROBLEM}", flush=True)
        _db = None
    return _db


def enabled():
    return _open() is not None


def load(name, default=None):
    with _lock:
        db = _open()
        if db is None:
            return default
        row = db.execute("SELECT value FROM kv WHERE name = ?", (name,)).fetchone()
    try:
        return json.loads(row[0]) if row else default
    except ValueError:
        return default


def save(name, value):
    with _lock:
        db = _open()
        if db is None:
            return
        try:
            db.execute("INSERT INTO kv (name, value) VALUES (?, ?) "
                       "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                       (name, json.dumps(value, separators=(",", ":"))))
            db.commit()
        except sqlite3.Error as e:  # a full disk must not stop the display
            print(f"warning: could not save {name}: {e}", flush=True)
