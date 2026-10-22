"""The audit log: who did what, when and from where.

One JSON line per entry in LABHUD_DATA/audit/<YYYY-MM-DD>.jsonl (a file per UTC day; files older
than KEEP_DAYS are deleted as the day turns). Every entry has the same fields:

    {"ts": "2026-10-18T13:01:36Z", "who": "admin", "action": "setup/save-text", "target": "",
     "ip": "192.168.1.20", "ua": "Firefox on Linux", "ok": true}

`who` is "admin" (a browser signed in), "phone <name>" (the app, or its Wall mode), "token <name>"
(api/tokens.py), "cli" (the labhud command beside the server) or "anonymous". Never a password,
a ticket, a token or a code: what is written is the path, a card's action name, a token's name.

Where it is heard, once each, so nothing else in labhud has to remember it:
- server._may_manage, the guard of everything ⚙ does (heard()): the path as `action`, the guard's
  answer as `ok`. Reads that ⚙ makes all the time (QUIET) are written only when refused;
- /action/<name>, the cards' buttons (record()), with the agent's answer;
- every admin sign-in, right or wrong (admin.LOGIN_HOOKS: on_login), however it came.

    GET /api/v1/audit?who=&action=&ok=1|0&from=2026-10-01&to=2026-10-18&offset=0&limit=100
        -> {rows: [...newest first], more: bool, offset, limit}
        behind the admin guard (a browser signed in, a phone signed in as the admin, an admin
        token, the labhud command).
"""

import json
import os
import re
import sys
import threading
import time
import urllib.parse

import admin
from routes import route

SUB = "audit"
KEEP_DAYS = 90                        # [audit] keep_days, 7 to 730, set by configure()
MAX_DAY_BYTES = 32 * 1024 * 1024      # a day that grew this big stops taking entries (a loop somewhere)
MAX_LIMIT = 500
DAY_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})\.jsonl")
WHEN = re.compile(r"\d{4}-\d{2}-\d{2}(T\d{2}(:\d{2}(:\d{2})?)?)?")
LIMITS = {"who": 60, "action": 80, "target": 120, "ip": 64, "ua": 120}

# ⚙'s reads, asked on every redraw: only a refusal is worth a line
QUIET = frozenset({
    "setup/status", "setup/catalog", "setup/layout", "setup/text", "setup/scan-status", "setup/logs",
    "setup/inspect", "setup/alerts", "setup/secrets", "agents/list", "tokens/list", "display/icons",
    "v1/sessions", "tokens/create", "tokens/revoke",   # these write their own line, with the token's name (api/tokens.py)
})

_lock = threading.Lock()
_pruned = {"day": ""}
_full = {"day": ""}


def configure(cfg):
    global KEEP_DAYS
    KEEP_DAYS = int((cfg or {}).get("keep_days", 90))


def folder():
    """LABHUD_DATA/audit, made if missing; "" without a usable LABHUD_DATA."""
    base = os.environ.get("LABHUD_DATA", "")
    if not base or not os.path.isdir(base):
        return ""
    path = os.path.join(base, SUB)
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError:
        return ""
    return path


def enabled():
    return bool(folder())


def _clip(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else ""))[:n]


def write(who, action, target="", ip="", ua="", ok=True, now=None):
    """Appends one entry. Never raises: the log must not stop what it records."""
    now = now or time.time()
    path = folder()
    if not path:
        return False
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "who": _clip(who, LIMITS["who"]),
             "action": _clip(action, LIMITS["action"]), "target": _clip(target, LIMITS["target"]),
             "ip": _clip(ip, LIMITS["ip"]), "ua": _clip(ua, LIMITS["ua"]), "ok": bool(ok)}
    line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
    name = os.path.join(path, day + ".jsonl")
    with _lock:
        try:
            if _pruned["day"] != day:
                _pruned["day"] = day
                prune(path, now)
            if os.path.exists(name) and os.path.getsize(name) >= MAX_DAY_BYTES:
                if _full["day"] != day:
                    _full["day"] = day
                    print(f"audit: {name} is {MAX_DAY_BYTES >> 20} MB, nothing more is written today", flush=True)
                return False
            fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            print(f"audit: not written: {e}", flush=True)
            return False
    return True


def prune(path, now=None):
    """Deletes the day files older than KEEP_DAYS. Returns the names deleted."""
    cutoff = time.strftime("%Y-%m-%d", time.gmtime((now or time.time()) - KEEP_DAYS * 86400))
    gone = []
    try:
        names = os.listdir(path)
    except OSError:
        return gone
    for n in names:
        m = DAY_FILE.fullmatch(n)
        if m and m.group(1) < cutoff:
            try:
                os.remove(os.path.join(path, n))
                gone.append(n)
            except OSError:
                pass
    return gone


# --- who, from where: read off the request ---

def client_ip(h):
    """The client's address. One place: when the server learns the real client behind a trusted
    proxy (h._client_ip), this follows it."""
    f = getattr(h, "_client_ip", None)
    if callable(f):
        try:
            return str(f())
        except Exception:
            pass
    try:
        return str(h.client_address[0])
    except Exception:
        return ""


def who(h):
    try:
        if h._cli():
            return "cli"
        tok = h._token()
        if tok:
            return "token " + tok[1].get("name", "?")
        name, _ = h._device()
        if name:
            return "phone " + name
        if h._admin_ok():
            return "admin"
    except Exception:
        pass
    return "anonymous"


def _ua(h):
    try:
        ua = h.headers.get("User-Agent") or ""
    except Exception:
        return ""
    try:   # "Firefox on Linux", as the list of sessions shows it (server.py, which may be __main__)
        label = sys.modules[type(h).__module__].browser_label(ua)
        return ua if label == "A browser" else label   # curl/8.5, python-requests: as they say
    except Exception:
        return ua


def record(h, action, target="", ok=True):
    return write(who(h), action, target, client_ip(h), _ua(h), ok)


def heard(h, ok):
    """From server._may_manage: once per request, the path it guards and its answer."""
    if getattr(h, "_audited_for", None) is h.headers:
        return
    h._audited_for = h.headers
    action = h.path.split("?", 1)[0]
    action = action[len("/api/"):] if action.startswith("/api/") else action.lstrip("/")
    if ok and action in QUIET:
        return
    record(h, action, "", ok)


def on_login(user, how, ok, where, what):
    write("admin" if ok else "anonymous", "sign-in", how, where, what, ok)


if on_login not in admin.LOGIN_HOOKS:
    admin.LOGIN_HOOKS.append(on_login)


# --- reading it back ---

def _truthy(v):
    v = str(v or "").lower()
    return True if v in ("1", "true", "yes", "ok") else False if v in ("0", "false", "no", "failed") else None


def query(who="", action="", ok=None, since="", until="", offset=0, limit=100):
    """The entries that match, newest first: (rows, more). `who`/`action`: a part of it, any case;
    `since`/`until`: a day or a moment (2026-10-18, 2026-10-18T13:05), both included."""
    path = folder()
    limit = max(1, min(int(limit or 100), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    if not path:
        return [], False
    who, action = str(who or "").lower(), str(action or "").lower()
    try:
        days = sorted((m.group(1) for m in map(DAY_FILE.fullmatch, os.listdir(path)) if m), reverse=True)
    except OSError:
        return [], False
    rows, skip = [], offset
    for day in days:
        if (since and day < since[:10]) or (until and day > until[:10]):
            continue
        try:
            with open(os.path.join(path, day + ".jsonl"), encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            ts = str(e.get("ts", ""))
            if (since and ts < since) or (until and ts[:len(until)] > until):
                continue
            if who and who not in str(e.get("who", "")).lower():
                continue
            if action and action not in str(e.get("action", "")).lower():
                continue
            if ok is not None and bool(e.get("ok")) != ok:
                continue
            if skip:
                skip -= 1
                continue
            if len(rows) == limit:
                return rows, True
            rows.append({k: e.get(k, "" if k != "ok" else False) for k in ("ts", "who", "action", "target", "ip", "ua", "ok")})
    return rows, False


def parse(qs):
    """query()'s arguments from a query string; ValueError for one that makes no sense."""
    q = {k: v[0] for k, v in urllib.parse.parse_qs(qs, keep_blank_values=False).items()}
    out = {"who": q.get("who", "")[:60], "action": q.get("action", "")[:80], "ok": _truthy(q.get("ok"))}
    for key, arg in (("from", "since"), ("to", "until")):
        v = q.get(key, "")
        if v and not WHEN.fullmatch(v):
            raise ValueError(f"{key} is a day or a moment: 2026-10-18 or 2026-10-18T13:05")
        out[arg] = v
    for key in ("offset", "limit"):
        v = q.get(key, "")
        if v and not v.isdigit():
            raise ValueError(f"{key} is a number")
        out[key] = int(v) if v else (0 if key == "offset" else 100)
    return out


@route("GET", "/api/v1/audit", guard="admin")
def audit_list(h, body):
    try:
        args = parse(h.path.partition("?")[2])
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    rows, more = query(**args)
    return 200, {"ok": True, "rows": rows, "more": more, "offset": args["offset"],
                 "limit": max(1, min(args["limit"], MAX_LIMIT)), "enabled": enabled(), "days": KEEP_DAYS, "keep_days": KEEP_DAYS}


@route("POST", "/api/audit/settings")
def audit_settings(h, body):
    """⚙ > System > API & audit: how many days the log keeps ([audit] keep_days, 7 to 730)."""
    import managed
    kd = body.get("keep_days")
    if isinstance(kd, bool) or not isinstance(kd, int) or not 7 <= kd <= 730:
        return 400, {"ok": False, "error": "keep_days is a whole number of days, 7 to 730"}
    try:
        raw = managed.read_config()
        raw["audit"] = {**(raw.get("audit") or {}), "keep_days": kd}
        managed.save_config(raw)
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}
    record(h, "audit-keep-days", str(kd))
    print(f"setup: audit keeps {kd} days, saved from {h._who()}", flush=True)
    return 200, {"ok": True, "keep_days": kd}
