"""Servers: the zone for the hosts' Docker, in the app and at /servers (Dockhand-like). Every route
here is POST, JSON, and goes through one gate, `_gate`, in this order:

    HTTPS only (a plain request is 403)  ->  not blocked (429 after too many refusals)  ->  labhud's own
    page, or the app, or a token  ->  the level the route needs (NEEDS: read < act < admin)  ->  for act
    and admin, the address is in [admin] servers_networks (when set)  ->  an interactive session
    (/api/servers/session, 15 minutes without a call ends it)

    POST /api/servers/session {}                     admin: starts the session (the PIN/TOTP were just typed)
    POST /api/servers/hosts {}                       read: [{name, online, version, containers, cpu, mem, ...}]
    POST /api/servers/containers {agent}             read: the host's containers, with what each may do
    POST /api/servers/action {agent, name, verb}     act: start | stop | restart | update
    POST /api/servers/logs {agent, name}             read: the last lines
    POST /api/servers/inspect {agent, name}          read: how it is made (rebuilt field by field)
    POST /api/servers/stats {agent, name}            read: its numbers now
    POST /api/servers/stream-open {agent, kind, name, cols, rows}   read (logs) / admin + PIN (shell)
    POST /api/servers/stream-read|stream-close {id}                  read
    POST /api/servers/stream-input|stream-resize {id, ...}           admin (the shell only)
    POST /api/servers/audit-verify {}                admin: is the audit log's chain whole

Nothing the agent says reaches a screen as it came: the answers are rebuilt field by field, with
limits (the way agents.inventory does). The agent checks its allow list again for every verb.
"""

import hashlib
import json
import os
import re
import ssl
import threading
import time

import admin
import agents
from api import audit
from routes import route

LEVELS = ("read", "act", "admin")
NEEDS = {
    "session": "admin", "hosts": "read", "containers": "read", "action": "act", "logs": "read",
    "inspect": "read", "stats": "read", "stream-open": "read", "stream-read": "read", "stream-close": "read",
    "stream-input": "admin", "stream-resize": "admin", "audit-verify": "admin",
}
SESSION_IDLE = 900            # s without a call before the interactive session ends
FAILS = 8                     # refusals within WINDOW from one address before it is blocked
WINDOW = 60
FIRST_BLOCK = 60              # s; doubles with each block, up to MAX_BLOCK
MAX_BLOCK = 900
ACTIONS = ("start", "stop", "restart", "update")
NAME = agents.TARGET["docker"]
SECRETISH = ("pass", "token", "secret", "key", "auth", "credential", "cookie")

_now = time.time
_lock = threading.Lock()
_fails = {}       # ip -> [timestamps of refusals]
_blocked = {}     # ip -> (until, strikes)
_sessions = {}    # identity hash -> last call


def _plain_ok():
    """Plain HTTP is refused unless the operator asked for it by name (tests, a demo): LABHUD_SERVERS_HTTP=on."""
    return os.environ.get("LABHUD_SERVERS_HTTP", "").lower() in ("1", "on", "true", "yes")


def _https(h):
    """The request came over TLS, or from a trusted reverse proxy that says so."""
    if isinstance(getattr(h, "connection", None), ssl.SSLSocket):
        return True
    peer = admin.client_ip(h.client_address[0], h.headers.get("X-Forwarded-For"))
    return (h.headers.get("X-Forwarded-Proto") or "").lower() == "https" and peer != h.client_address[0]


def page_ok(h):
    """May this request be served the page of Servers (and its script and style): only over HTTPS."""
    return _https(h) or _plain_ok()


def _identity(h):
    """Who the session belongs to: a hash of the credentials the request carries (never kept as they are)."""
    raw = "|".join(h.headers.get(k) or "" for k in ("Authorization", "Cookie", "X-Labhud-Admin"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _blocked_for(ip, now):
    with _lock:
        until = _blocked.get(ip, (0, 0))[0]
    return max(0, int(until - now + 0.999))


def _refused(ip, now=None):
    """Counts a refusal; too many in a minute block the address for a while, longer each time."""
    now = now or _now()
    with _lock:
        recent = [t for t in _fails.get(ip, []) if now - t < WINDOW] + [now]
        _fails[ip] = recent
        if len(recent) >= FAILS:
            strikes = _blocked.get(ip, (0, 0))[1] + 1
            _blocked[ip] = (now + min(FIRST_BLOCK * 2 ** (strikes - 1), MAX_BLOCK), strikes)
            _fails[ip] = []
        if len(_fails) > 4096:
            _fails.clear()


def reset():
    """For the tests: forget refusals, blocks and sessions."""
    with _lock:
        _fails.clear()
        _blocked.clear()
        _sessions.clear()


def _gate(h, op, needed=None):
    """None when the request may go on, else (code, answer). Every refusal is written to the audit log."""
    needed = needed or NEEDS[op]
    ip = h._ip()
    now = _now()

    def deny(code, error, **more):
        if code == 403:   # a session that ended (401) is no failed attempt
            _refused(ip, now)
        audit.record(h, f"servers/{op}", "", False)
        return code, dict({"ok": False, "error": error}, **more)

    wait = _blocked_for(ip, now)
    if wait:
        audit.record(h, f"servers/{op}", "blocked", False)
        return 429, {"ok": False, "error": "too many refused calls: wait", "retry_after": wait}
    if not _https(h) and not _plain_ok():
        return deny(403, "Servers is only served over HTTPS")
    cli, tok = h._cli(), h._token()
    if not (cli or tok) and not h._same_page():
        return deny(403, "forbidden")
    have = {"read": lambda: h._token_may("read") or h._admin_any(), "act": h._operator,
            "admin": h._may_manage_now}[needed]()
    if not have:
        return deny(403, "forbidden")
    if needed != "read" and not cli and not admin.servers_network_ok(ip):
        return deny(403, "this address may not act on Servers")
    if not (cli or tok) and op != "session":
        key = _identity(h)
        with _lock:
            last = _sessions.get(key)
            if last is None or now - last > SESSION_IDLE:
                _sessions.pop(key, None)
                expired = True
            else:
                _sessions[key] = now
                expired = False
        if expired:
            return deny(401, "the Servers session ended: sign in with the PIN again", resume=True)
    return None


def _start_session(h):
    key = _identity(h)
    with _lock:
        _sessions[key] = _now()
        if len(_sessions) > 256:
            for k, t in list(_sessions.items()):
                if _now() - t > SESSION_IDLE:
                    del _sessions[k]


def _guarded(op):
    """Decorator: the gate, then the handler (which gets the parsed agent and body)."""
    def wrap(fn):
        def run(h, body):
            refused = _gate(h, op)
            if refused:
                return refused
            return fn(h, body)
        return route("POST", f"/api/servers/{op}", guard="open", limit=16 * 1024)(run)
    return wrap


# --- reading what the agent says, field by field ---

def _s(v, n=200):
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", v)[:n] if isinstance(v, str) else ""


def _n(v, low=0.0, high=1e15):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high else 0


def _agent(body):
    aid, a = agents.by_name(str(body.get("agent") or "")) if agents.enabled() else (None, None)
    return (aid, a) if a else (None, None)


def _container(body):
    name = body.get("name")
    return name if isinstance(name, str) and NAME.fullmatch(name) and name != agents.PRUNE_TARGET else None


def _can(a, name):
    ticked = set(a.get("targets", {}).get("docker", [])) if "docker" in a.get("caps", []) else set()
    return list(ACTIONS) if name in ticked else []


def _shell(a, name):
    return "terminal" in a.get("caps", []) and name in a.get("targets", {}).get("terminal", [])


@_guarded("session")
def session(h, body):
    _start_session(h)
    return 200, {"ok": True, "idle": SESSION_IDLE}


@_guarded("hosts")
def hosts(h, body):
    out = []
    for a in agents.listing():
        st = agents.state_of(a["id"])
        cs = [c for n, c in (st.get("containers") or {}).items() if isinstance(c, dict) and NAME.fullmatch(str(n))
              and n != agents.PRUNE_TARGET]
        sensors = st.get("sensors") if isinstance(st.get("sensors"), dict) else {}
        out.append({
            "name": a["name"], "online": bool(a["online"]), "version": _s(a["version"], 40),
            "update": bool(a.get("update")), "docker": "docker" in a["caps"] or "docker" in a["found"],
            "containers": {"running": sum(1 for c in cs if c.get("state") == "running"), "total": len(cs)},
            "cpu": _n(sensors.get("cpu"), 0, 10000), "mem": _n(sensors.get("mem"), 0, 100),
            "seen": _n(a.get("seen"), 0, 4e9),
        })
    return 200, {"ok": True, "hosts": out}


@_guarded("containers")
def containers(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    st = agents.state_of(aid)
    rows = []
    for name, c in sorted((st.get("containers") or {}).items())[:300]:
        if not isinstance(c, dict) or not NAME.fullmatch(str(name)) or name == agents.PRUNE_TARGET:
            continue
        rows.append({
            "name": name, "state": _s(c.get("state"), 20), "health": _s(c.get("health"), 20),
            "image": _s(c.get("image"), 200), "project": _s(c.get("project"), 80),
            "cpu": _n(c.get("cpu"), 0, 100000), "mem": int(_n(c.get("mem"))), "mem_pct": _n(c.get("mem_pct"), 0, 1000),
            "net_rx": int(_n(c.get("net_rx"))), "net_tx": int(_n(c.get("net_tx"))), "restarts": int(_n(c.get("restarts"), 0, 1e9)),
            "updating": c.get("updating") is True,
            "can": _can(a, name), "shell": _shell(a, name),
            "auto": name in agents.auto_targets(a), "hook": name in a.get("hooks", {}),
        })
    return 200, {"ok": True, "online": aid in agents._live, "containers": rows}


@_guarded("action")
def action(h, body):
    aid, a = _agent(body)
    name, verb = _container(body), str(body.get("verb") or "")
    if not a or not name or verb not in ACTIONS:
        return 400, {"ok": False, "error": "agent, name and a verb of start, stop, restart or update"}
    ok, error = agents.command(aid, "docker", verb, name)
    audit.record(h, f"servers/{verb}", f"{a['name']}/{name}", ok)
    print(f"servers: {verb} {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


def _read(body, verb):
    aid, a = _agent(body)
    name = _container(body)
    if not a or not name:
        return None, None, (400, {"ok": False, "error": "agent and name"})
    ok, error, text = agents.read(aid, "docker", verb, name, wait=30)
    if not ok:
        return None, None, (502, {"ok": False, "error": _s(error, 200) or "failed"})
    return a, text, None


@_guarded("logs")
def logs(h, body):
    a, text, err = _read(body, "logs")
    if err:
        return err
    return 200, {"ok": True, "text": text}


def _inspect_view(text):
    """The agent's inspect, rebuilt: lists and strings with limits; a label or variable whose name
    looks like a secret keeps its name and loses its value, whatever the agent sent."""
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError
    lst = lambda k, n, m: [_s(x, m) for x in (raw.get(k) or [])[:n] if isinstance(x, str)] if isinstance(raw.get(k), list) else []   # noqa: E731
    labels = {}
    for k, v in list((raw.get("labels") or {}).items())[:60] if isinstance(raw.get("labels"), dict) else []:
        k = _s(k, 120)
        labels[k] = "••••" if any(w in k.lower() for w in SECRETISH) else _s(v, 300)
    return {"image": _s(raw.get("image"), 200), "created": _s(raw.get("created"), 40), "started": _s(raw.get("started"), 40),
            "restart": _s(raw.get("restart"), 60), "restarts": int(_n(raw.get("restarts"), 0, 1e9)),
            "ports": lst("ports", 60, 80), "mounts": lst("mounts", 60, 300), "networks": lst("networks", 20, 80),
            "env": [x.split("=", 1)[0] for x in lst("env", 150, 200)], "labels": labels,
            "health": _s(raw.get("health"), 20), "command": _s(raw.get("command"), 400)}


@_guarded("inspect")
def inspect(h, body):
    a, text, err = _read(body, "inspect")
    if err:
        return err
    try:
        return 200, {"ok": True, "inspect": _inspect_view(text)}
    except (ValueError, TypeError):
        return 502, {"ok": False, "error": "the agent's answer was not an inspect"}


@_guarded("stats")
def stats(h, body):
    a, text, err = _read(body, "stats")
    if err:
        return err
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError
    except ValueError:
        return 502, {"ok": False, "error": "the agent's answer was not stats"}
    keys = ("cpu", "mem", "mem_limit", "mem_pct", "net_rx", "net_tx", "block_read", "block_write", "pids")
    return 200, {"ok": True, "stats": {k: _n(raw.get(k)) for k in keys}}


def _stream(op):
    def run(h, body):
        needed = NEEDS[op]
        if op == "stream-open" and body.get("kind") == "shell":
            needed = "admin"
        elif op in ("stream-read", "stream-close"):
            st = agents.stream_get(str(body.get("id") or ""))
            if st and st.kind == "shell":
                needed = "admin"
        refused = _gate(h, op, needed)
        if refused:
            return refused
        if op == "stream-open":
            body = dict(body, target=body.get("name"))
        h._live_stream(op, body, h._who())
        return None      # answered by _live_stream
    return route("POST", f"/api/servers/{op}", guard="open", limit=20 * 1024)(run)


for _op in ("stream-open", "stream-read", "stream-close", "stream-input", "stream-resize"):
    _stream(_op)


@_guarded("audit-verify")
def audit_verify(h, body):
    ok, checked, broken = audit.verify()
    return 200, {"ok": True, "intact": ok, "checked": checked, "broken": broken}
