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
    POST /api/servers/stacks {agent}                 read: the host's stacks (state, git source, webhook)
    POST /api/servers/stack-read {agent, name, version?}   admin: compose + .env (values masked) + saved versions
    POST /api/servers/stack-validate|stack-save {agent, name, compose, env}   admin: `compose config`, then (save) write
    POST /api/servers/stack-restore {agent, name, version}   admin: an older version becomes the present one
    POST /api/servers/stack-act {agent, name, verb}  act: redeploy | restart
    POST /api/servers/stack-down {agent, name, confirm}   admin: down (never removes volumes)
    POST /api/servers/stack-source {agent, name, repo, branch, path | clear}   admin: where the file comes from
    POST /api/servers/stack-hook {agent, name}       admin: a new webhook URL (shown once); stack-sync {agent, name, token?}: pull now
    POST /api/servers/guests {agent}                 read: the node's VMs and containers (LXC), with what each may do
    POST /api/servers/guest-config|guest-snapshots|guest-backups|guest-tasks {agent, id}   read: (keys that look like secrets masked)
    POST /api/servers/guest-act {agent, id, verb}    act: start | shutdown | reboot
    POST /api/servers/snap-create {agent, id, name}  act: a snapshot (no RAM state)
    POST /api/servers/snap-rollback {agent, id, name, confirm}   admin: the guest goes back; confirm = the guest's name
    POST /api/servers/snap-delete {agent, id, name, confirm}     admin: confirm = the snapshot's name
    POST /api/servers/host {agent}                   read: the host's system facts and its systemd units
    POST /api/servers/host-update {agent}            act: update the host's packages
    POST /api/servers/host-power {agent, verb, confirm}   admin: reboot or shutdown; confirm = the host's name
    POST /api/servers/unit {agent, name, verb}       act: start or restart a ticked unit
    POST /api/servers/unit-stop {agent, name, confirm}    admin: stop a ticked unit; confirm = its name
    POST /api/servers/activity {agent, offset}       admin: the audit log's entries about this host, newest first
    POST /api/servers/jobs {}                        read: the scheduler's jobs
    POST /api/servers/job-save {job}                 admin: makes a job (no id) or replaces one; the host must allow what the kind does
    POST /api/servers/job-delete {id, confirm}       admin: confirm = the job's name; the update job (the old window) stays
    POST /api/servers/job-run {id}                   admin: runs it now
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
import jobs
from api import audit
from routes import route

LEVELS = ("read", "act", "admin")
NEEDS = {
    "session": "admin", "hosts": "read", "containers": "read", "action": "act", "logs": "read",
    "inspect": "read", "stats": "read", "stream-open": "read", "stream-read": "read", "stream-close": "read",
    "stream-input": "admin", "stream-resize": "admin", "audit-verify": "admin",
    "images": "read", "volumes": "read", "networks": "read", "network": "read", "volume-ls": "read", "volume-read": "read",
    "stacks": "read", "stack-read": "admin", "stack-validate": "admin", "stack-save": "admin", "stack-restore": "admin",
    "stack-act": "act", "stack-down": "admin", "stack-source": "admin", "stack-hook": "admin", "stack-sync": "admin",
    "guests": "read", "guest-config": "read", "guest-snapshots": "read", "guest-backups": "read", "guest-tasks": "read",
    "guest-act": "act", "snap-create": "act", "snap-rollback": "admin", "snap-delete": "admin",
    "jobs": "read", "job-save": "admin", "job-delete": "admin", "job-run": "admin",
    "host": "read", "host-update": "act", "host-power": "admin", "unit": "act", "unit-stop": "admin", "activity": "admin",
    "pull": "act", "rm": "admin", "clean": "admin", "volume-access": "admin",
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


def _guarded(op, limit=16 * 1024):
    """Decorator: the gate, then the handler (which gets the parsed agent and body)."""
    def wrap(fn):
        def run(h, body):
            refused = _gate(h, op)
            if refused:
                return refused
            return fn(h, body)
        return route("POST", f"/api/servers/{op}", guard="open", limit=limit)(run)
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
            "pve": "pve" in a["caps"],
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


# --- images, volumes, networks (the host's Docker, "Unused images" ticked for the host) ---

HINT = "tick “Unused images” for this host in Settings > Hosts and agents"
KINDS = {"image": "rm-image", "volume": "rm-volume", "network": "rm-network"}
LIST_MAX = 300
READ_MAX = 64 << 10


def _inventory(body, fresh=False):
    aid, a = _agent(body)
    if not a:
        return None, None, None, (404, {"ok": False, "error": "no such host"})
    ok, error, inv = agents.inventory(aid, fresh=fresh)
    if not ok:
        return None, None, None, (502, {"ok": False, "error": HINT if error == "not allowed" else _s(error, 200) or "failed"})
    return aid, a, inv, None


def _ticked_volumes(a):
    return {t[len(agents.VOLUME_PREFIX):] for t in a.get("targets", {}).get("docker", []) if t.startswith(agents.VOLUME_PREFIX)}


@_guarded("images")
def images(h, body):
    aid, a, inv, err = _inventory(body, fresh=bool(body.get("fresh")))
    if err:
        return err
    st = agents.state_of(aid)
    rows = [dict(i, scan=_image_scan(st, i)) for i in inv["images"]]
    return 200, {"ok": True, "images": rows, "more": inv["more"].get("images", 0), "totals": inv["totals"]}


def _image_scan(st, image):
    """What the agent's `scan` (trivy/grype) found in the containers that run this image: the worst
    counts, the oldest scan time, and the containers that have any; None when none was scanned."""
    found = []
    for name in image.get("used_by") or []:
        c = (st.get("containers") or {}).get(name)
        sc = c.get("scan") if isinstance(c, dict) else None
        if isinstance(sc, dict) and type(sc.get("critical")) is int and type(sc.get("high")) is int:
            found.append((name, sc))
    if not found:
        return None
    count = lambda k: max(0, min(max(sc[k] for _, sc in found), 1000000))   # noqa: E731
    return {"critical": count("critical"), "high": count("high"), "tool": _s(found[0][1].get("tool"), 20),
            "t": min((sc["t"] for _, sc in found if type(sc.get("t")) is int), default=0),
            "affects": [n for n, sc in found if sc["critical"] or sc["high"]][:8]}


@_guarded("volumes")
def volumes(h, body):
    aid, a, inv, err = _inventory(body, fresh=bool(body.get("fresh")))
    if err:
        return err
    open_ = _ticked_volumes(a)
    return 200, {"ok": True, "volumes": [dict(v, browse=v["name"] in open_) for v in inv["volumes"]],
                 "more": inv["more"].get("volumes", 0), "totals": inv["totals"]}


@_guarded("networks")
def networks(h, body):
    aid, a, inv, err = _inventory(body, fresh=bool(body.get("fresh")))
    if err:
        return err
    return 200, {"ok": True, "networks": inv["networks"], "more": inv["more"].get("networks", 0)}


@_guarded("network")
def network(h, body):
    aid, a, inv, err = _inventory(body)
    if err:
        return err
    name = body.get("name")
    if not isinstance(name, str) or not any(n["name"] == name for n in inv["networks"]):
        return 404, {"ok": False, "error": "no such network on this host"}
    ok, error, text = agents.read(aid, "docker", "network-inspect", agents.PRUNE_TARGET, wait=30, arg=name)
    if not ok:
        return 502, {"ok": False, "error": _s(error, 200) or "failed"}
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError
        links = [{"name": _s(c.get("name"), 128), "ipv4": _s(c.get("ipv4"), 40)} for c in (raw.get("containers") or [])[:100] if isinstance(c, dict)]
    except (ValueError, TypeError, AttributeError):
        return 502, {"ok": False, "error": "the agent's answer was not a network"}
    return 200, {"ok": True, "network": {"name": _s(raw.get("name"), 128), "driver": _s(raw.get("driver"), 40), "scope": _s(raw.get("scope"), 20),
                                        "internal": raw.get("internal") is True, "containers": links}}


def _target_object(inv, kind, name):
    """The object `name` in the host's own current list (never what the client says it is), or None."""
    if kind == "image":
        return next((i for i in inv["images"] if name == i["id"] or name in i["tags"]), None)
    return next((x for x in inv["volumes" if kind == "volume" else "networks"] if x["name"] == name), None)


@_guarded("rm")
def rm(h, body):
    kind, name, confirm = body.get("kind"), body.get("name"), body.get("confirm")
    if kind not in KINDS or not isinstance(name, str) or not agents.arg_ok(KINDS[kind], name):
        return 400, {"ok": False, "error": "kind is image, volume or network, and name one of its objects"}
    aid, a, inv, err = _inventory(body, fresh=True)
    if err:
        return err
    obj = _target_object(inv, kind, name)
    if obj is None:
        return 404, {"ok": False, "error": "that is not in this host's list"}
    if confirm != name:
        return 400, {"ok": False, "error": "type the name to confirm"}
    if obj.get("builtin"):
        return 400, {"ok": False, "error": "a built-in network is never removed"}
    if obj.get("used"):
        return 409, {"ok": False, "error": "in use by " + (", ".join(obj.get("used_by") or []) or "a container") + ": remove that first"}
    ok, error = agents.command(aid, "docker", KINDS[kind], agents.PRUNE_TARGET, wait=60, arg=name)
    audit.record(h, f"servers/rm-{kind}", f"{a['name']}/{name}", ok)
    print(f"servers: rm {kind} {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    agents.inventory(aid, fresh=True)      # the next list is the host's, not a kept one
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("pull")
def pull(h, body):
    aid, a = _agent(body)
    image = body.get("image")
    if not a or not isinstance(image, str) or not agents.arg_ok("pull", image):
        return 400, {"ok": False, "error": "agent and an image such as nginx:1.27"}
    ok, error = agents.command(aid, "docker", "pull", agents.PRUNE_TARGET, wait=600, arg=image)
    audit.record(h, "servers/pull", f"{a['name']}/{image}", ok)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": HINT if error == "not allowed" else _s(error, 200) or "failed"})


@_guarded("clean")
def clean(h, body):
    aid, a = _agent(body)
    what, confirm = body.get("what"), body.get("confirm")
    if not a or what not in agents.CLEAN:
        return 400, {"ok": False, "error": "what is one of " + ", ".join(agents.CLEAN)}
    if confirm != what:
        return 400, {"ok": False, "error": "type " + what + " to confirm"}
    ok, error, text = agents.clean(aid, what)
    audit.record(h, f"servers/clean-{what}", a["name"], ok)
    agents.inventory(aid, fresh=True)
    return (200, {"ok": True, "text": _s(text, 400)}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("volume-access")
def volume_access(h, body):
    aid, a, inv, err = _inventory(body)
    if err:
        return err
    name, on = body.get("volume"), body.get("on") is True
    if not isinstance(name, str) or not any(v["name"] == name for v in inv["volumes"]):
        return 404, {"ok": False, "error": "no such volume on this host"}
    if not agents.set_volume_access(aid, name, on):
        return 400, {"ok": False, "error": "not changed"}
    audit.record(h, "servers/volume-access", f"{a['name']}/{name} {'on' if on else 'off'}", True)
    return 200, {"ok": True, "browse": on}


def _volume(body):
    aid, a = _agent(body)
    vol = body.get("volume")
    path = agents.clean_path(body.get("path") or "")
    if not a or not isinstance(vol, str) or not agents.NAME_ARG.fullmatch(vol) or path is None:
        return None, None, None, None, (400, {"ok": False, "error": "agent, volume and a path inside it"})
    if vol not in _ticked_volumes(a):
        return None, None, None, None, (403, {"ok": False, "error": "this volume is not opened for browsing", "needs_access": True})
    return aid, a, vol, path, None


def _secret_name(name):
    n = name.lower().rsplit("/", 1)[-1]
    dotenv = n == ".env" or n.endswith(".env") or n.startswith(".env.")
    secret = dotenv or any(w in n for w in ("key", "secret", "token", "passw", "credential", "id_rsa", "id_ed25519", "id_ecdsa")) \
        or n.endswith((".pem", ".p12", ".pfx")) or n.startswith("id_")
    return secret, dotenv


@_guarded("volume-ls")
def volume_ls(h, body):
    aid, a, vol, path, err = _volume(body)
    if err:
        return err
    ok, error, text = agents.read(aid, "docker", "volume-ls", agents.VOLUME_PREFIX + vol, wait=40, arg=path)
    if not ok:
        return 502, {"ok": False, "error": _s(error, 200) or "failed"}
    try:
        raw = json.loads(text)
        rows = [{"name": _s(e.get("name"), 255), "dir": e.get("dir") is True, "link": e.get("link") is True,
                 "size": int(_n(e.get("size"))), "mtime": int(_n(e.get("mtime"), 0, 4e9))}
                for e in (raw.get("entries") or [])[:LIST_MAX] if isinstance(e, dict) and "/" not in str(e.get("name"))]
        rows = [r for r in rows if r["name"]]
    except (ValueError, TypeError, AttributeError):
        return 502, {"ok": False, "error": "the agent's answer was not a listing"}
    audit.record(h, "servers/volume-ls", f"{a['name']}/{vol}{path}", True)
    return 200, {"ok": True, "path": path, "entries": rows, "truncated": raw.get("truncated") is True or len(raw.get("entries") or []) > LIST_MAX}


@_guarded("volume-read")
def volume_read(h, body):
    aid, a, vol, path, err = _volume(body)
    if err:
        return err
    if path == "/":
        return 400, {"ok": False, "error": "a file, not the root"}
    ok, error, text = agents.read(aid, "docker", "volume-read", agents.VOLUME_PREFIX + vol, wait=40, arg=path)
    if not ok:
        return 502, {"ok": False, "error": _s(error, 200) or "failed"}
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError
    except ValueError:
        return 502, {"ok": False, "error": "the agent's answer was not a file"}
    secret, dotenv = _secret_name(path)
    body_text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    masked = raw.get("masked") is True
    if secret and not dotenv:           # whatever the agent sent, nothing of such a file is shown
        body_text, masked = "", True
    elif dotenv:                        # names only, whatever the agent sent
        body_text = "\n".join(l.split("=", 1)[0] + "=••••" if "=" in l and not l.lstrip().startswith("#") else l for l in body_text.split("\n"))
        masked = True
    audit.record(h, "servers/volume-read", f"{a['name']}/{vol}{path}", True)
    return 200, {"ok": True, "file": {"path": path, "size": int(_n(raw.get("size"))), "text": _s_text(body_text), "binary": raw.get("binary") is True,
                                       "truncated": raw.get("truncated") is True, "masked": masked}}


def _s_text(v):
    """A file's text: kept as it is but for control characters other than newline and tab, and cut at the limit."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "�", v)[:READ_MAX] if isinstance(v, str) else ""


@_guarded("audit-verify")
def audit_verify(h, body):
    ok, checked, broken = audit.verify()
    return 200, {"ok": True, "intact": ok, "checked": checked, "broken": broken}


# --- Stacks: compose files kept by the agent, edited here (validate -> save -> redeploy, three steps) ---

STACK = agents.TARGET["stacks"]
STACK_TEXT = agents.STACK_FILE_MAX
MASK = "********"      # what the agent shows in place of a .env value; saved back as it is, it keeps the host's value


def _stack(body):
    """(aid, agent, name, error answer): the host and a stack name that is well formed."""
    aid, a = _agent(body)
    name = body.get("name")
    if not a or not isinstance(name, str) or not STACK.fullmatch(name):
        return None, None, None, (400, {"ok": False, "error": "agent and a stack name (lowercase letters, digits, - and _)"})
    if "stacks" not in a.get("caps", []):
        return None, None, None, (403, {"ok": False, "error": "Stacks is not ticked for this host"})
    if _kept(aid, name):
        agents.tick_stack(aid, name)
    return aid, agents.get(aid), name, None


def _kept(aid, name):
    return name in agents.stacks_of(aid)


def _failed(error, output=""):
    return 502, {"ok": False, "error": _s(error, 200) or "failed", "output": _s_text(output)[:2000]}


@_guarded("stacks")
def stacks(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    if "stacks" not in a.get("caps", []):
        return 200, {"ok": True, "enabled": False, "stacks": []}
    rows = []
    for name, x in sorted(agents.stacks_of(aid).items())[:200]:
        src = agents.stack_source(aid, name)
        rows.append({"name": name, "updated": int(_n(x.get("updated"), 0, 4e9)), "env": x.get("env") is True,
                     "services": int(_n(x.get("services"), 0, 1e4)), "running": int(_n(x.get("running"), 0, 1e4)),
                     "source": {k: _s(src.get(k), 200) for k in ("repo", "branch", "path")} if src else None,
                     "hook": name in (a.get("stack_hooks") or {})})
    return 200, {"ok": True, "enabled": True, "online": aid in agents._live, "stacks": rows}


@_guarded("stack-read")
def stack_read(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    if not _kept(aid, name):
        return 404, {"ok": False, "error": "no such stack on this host"}
    version = body.get("version")
    if version is None:
        ok, error, text = agents.read(aid, "stacks", "read", name, wait=30)
    elif isinstance(version, str) and agents.arg_ok("version", version):
        ok, error, text = agents.read(aid, "stacks", "version", name, wait=30, arg=version)
    else:
        return 400, {"ok": False, "error": "version is one of the saved versions"}
    if not ok:
        return _failed(error)
    try:
        d = json.loads(text)
    except ValueError:
        d = None
    if not isinstance(d, dict) or not isinstance(d.get("compose"), str):
        return 502, {"ok": False, "error": "the agent's answer was not understood"}
    env = d.get("env") if isinstance(d.get("env"), str) else ""
    vs = [v for v in (d.get("versions") or [])[:10] if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 4e9]
    audit.record(h, "servers/stack-read", f"{a['name']}/{name}" + (f"@{version}" if version else ""), True)
    return 200, {"ok": True, "compose": d["compose"][:STACK_TEXT], "env": env[:STACK_TEXT], "has_env": d.get("has_env") is True,
                 "versions": vs, "mask": MASK}


def _files(body):
    compose, env = body.get("compose"), body.get("env")
    if not isinstance(compose, str) or not compose.strip():
        return None, None, (400, {"ok": False, "error": "a compose file is needed"})
    if env is not None and not isinstance(env, str):
        return None, None, (400, {"ok": False, "error": "env is text"})
    return compose.replace("\r\n", "\n"), (env.replace("\r\n", "\n") if isinstance(env, str) else None), None


def _write(op, verb):
    def handler(h, body):
        aid, a, name, err = _stack(body)
        if err:
            return err
        compose, env, err = _files(body)
        if err:
            return err
        ok, error, out = agents.stack(aid, verb, name, compose, env)
        if verb == "save":   # only a write is an act worth the audit log; a check is not
            audit.record(h, "servers/stack-save", f"{a['name']}/{name}", ok)
            print(f"servers: stack {name} on {a['name']} saved by {h._who()}: {'ok' if ok else error}", flush=True)
        return (200, {"ok": True, "output": _s_text(out)[:2000]}) if ok else _failed(error, out)
    return handler


_guarded("stack-validate", limit=110 * 1024)(_write("stack-validate", "validate"))
_guarded("stack-save", limit=110 * 1024)(_write("stack-save", "save"))


@_guarded("stack-restore")
def stack_restore(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    version = body.get("version")
    if not isinstance(version, str) or not agents.arg_ok("version", version) or not _kept(aid, name):
        return 400, {"ok": False, "error": "a saved version of a stack on this host"}
    ok, error, out = agents.stack(aid, "restore", name, arg=version)
    audit.record(h, "servers/stack-restore", f"{a['name']}/{name}@{version}", ok)
    return (200, {"ok": True}) if ok else _failed(error, out)


@_guarded("stack-act")
def stack_act(h, body):
    aid, a, name, err = _stack(body)
    verb = body.get("verb")
    if err or verb not in ("redeploy", "restart"):
        return err or (400, {"ok": False, "error": "verb is redeploy or restart"})
    if not _kept(aid, name):
        return 404, {"ok": False, "error": "no such stack on this host"}
    ok, error, out = agents.stack(aid, verb, name)
    audit.record(h, f"servers/stack-{verb}", f"{a['name']}/{name}", ok)
    print(f"servers: stack {verb} {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True, "output": _s_text(out)[-2000:]}) if ok else _failed(error, out)


@_guarded("stack-down")
def stack_down(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    if not _kept(aid, name):
        return 404, {"ok": False, "error": "no such stack on this host"}
    if body.get("confirm") != name:
        return 400, {"ok": False, "error": "type the name to confirm"}
    ok, error, out = agents.stack(aid, "down", name)
    audit.record(h, "servers/stack-down", f"{a['name']}/{name}", ok)
    print(f"servers: stack down {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else _failed(error, out)


@_guarded("stack-source")
def stack_source(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    if not _kept(aid, name):
        return 404, {"ok": False, "error": "no such stack on this host"}
    if body.get("clear") is True:
        src = None
    else:
        src = {k: body.get(k) for k in ("repo", "branch", "path")}
        if not all(isinstance(v, str) for v in (src["repo"], src["path"])) or not isinstance(src["branch"] or "main", str):
            return 400, {"ok": False, "error": "repo, branch and path"}
    ok, error = agents.set_stack_source(aid, name, src)
    audit.record(h, "servers/stack-source", f"{a['name']}/{name}" + (" cleared" if src is None else ""), ok)
    return (200, {"ok": True}) if ok else (400, {"ok": False, "error": _s(error, 200)})


@_guarded("stack-hook")
def stack_hook(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    if body.get("clear") is True:
        hooks = dict(a.get("stack_hooks") or {})
        hooks.pop(name, None)
        agents._save(aid, stack_hooks=hooks)
        audit.record(h, "servers/stack-hook", f"{a['name']}/{name} removed", True)
        return 200, {"ok": True}
    token = agents.new_stack_hook(aid, name)
    if not token:
        return 400, {"ok": False, "error": "set a git source first"}
    audit.record(h, "servers/stack-hook", f"{a['name']}/{name} made", True)
    return 200, {"ok": True, "path": "/api/hook/" + token}


@_guarded("stack-sync")
def stack_sync(h, body):
    aid, a, name, err = _stack(body)
    if err:
        return err
    token = body.get("token")
    if token is not None and not isinstance(token, str):
        return 400, {"ok": False, "error": "token is text"}
    ok, error, out = agents.sync_stack(aid, name, token or None)
    audit.record(h, "servers/stack-sync", f"{a['name']}/{name}", ok)
    return (200, {"ok": True, "output": _s_text(out)[-2000:]}) if ok else _failed(error, out)


# --- VMs & LXC: the guests of a Proxmox node, read and acted on through the agent next to it ---

GUEST = agents.TARGET["pve"]
GUEST_ACTIONS = ("start", "shutdown", "reboot")
SNAP_WAIT = 330       # s: a snapshot may take its time (the agent gives it five minutes)
PVE_SECRET = re.compile(r"pass|secret|token|cicustom|sshkeys|(^|_)keys?$", re.I)
GUEST_TYPES = ("qemu", "lxc")


def _ticked_guests(a):
    return set(a.get("targets", {}).get("pve", [])) if "pve" in a.get("caps", []) else set()


def _guest(body):
    """(aid, agent, vmid, error answer): the host and a guest of it that the admin ticked."""
    aid, a = _agent(body)
    vmid = body.get("id")
    if isinstance(vmid, int) and not isinstance(vmid, bool):
        vmid = str(vmid)
    if not a or not isinstance(vmid, str) or not GUEST.fullmatch(vmid):
        return None, None, None, (400, {"ok": False, "error": "agent and the guest's number"})
    if vmid not in _ticked_guests(a):
        return None, None, None, (403, {"ok": False, "error": "that guest is not ticked for this host"})
    return aid, a, vmid, None


def _json(text, key):
    """The list `key` of the agent's JSON answer, or None."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d.get(key) if isinstance(d, dict) and isinstance(d.get(key), list) else None


def _pve_read(body, verb, key):
    """(agent, vmid, rows, error answer): one reading verb of the agent, its list rebuilt by the caller."""
    aid, a, vmid, err = _guest(body)
    if err:
        return None, None, None, err
    ok, error, text = agents.read(aid, "pve", verb, vmid, wait=40)
    if not ok:
        return None, None, None, (502, {"ok": False, "error": _s(error, 200) or "failed"})
    rows = _json(text, key)
    if rows is None:
        return None, None, None, (502, {"ok": False, "error": "the agent's answer was not understood"})
    return a, vmid, [r for r in rows[:200] if isinstance(r, dict)], None


@_guarded("guests")
def guests(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    if "pve" not in a.get("caps", []):
        return 200, {"ok": True, "enabled": False, "guests": []}
    st = agents.state_of(aid)
    ticked = _ticked_guests(a)
    rows = []
    for vmid, g in sorted((st.get("guests") or {}).items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)[:300]:
        if not isinstance(g, dict) or not GUEST.fullmatch(str(vmid)):
            continue
        rows.append({
            "id": str(vmid), "name": _s(g.get("name"), 80), "type": g.get("type") if g.get("type") in GUEST_TYPES else "",
            "running": g.get("running") is True, "template": g.get("template") is True,
            "cpu": _n(g.get("cpu"), 0, 100000), "mem": _n(g.get("mem"), 0, 1000),
            "mem_bytes": int(_n(g.get("mem_bytes"))), "mem_max": int(_n(g.get("mem_max"))), "disk": int(_n(g.get("disk"))),
            "uptime": int(_n(g.get("uptime"), 0, 4e9)),
            "tags": [_s(t, 30) for t in (g.get("tags") or [])[:8] if isinstance(t, str)],
            "ticked": str(vmid) in ticked, "can": list(GUEST_ACTIONS) if str(vmid) in ticked and not g.get("template") else [],
        })
    return 200, {"ok": True, "enabled": True, "online": aid in agents._live, "node": _s(st.get("node"), 60), "guests": rows}


@_guarded("guest-config")
def guest_config(h, body):
    a, vmid, rows, err = _pve_read(body, "config", "config")
    if err:
        return err
    out = [{"key": _s(r.get("key"), 60), "value": "********" if PVE_SECRET.search(str(r.get("key"))) else _s(r.get("value"), 300)}
           for r in rows if isinstance(r.get("key"), str)]
    return 200, {"ok": True, "config": out}


@_guarded("guest-snapshots")
def guest_snapshots(h, body):
    a, vmid, rows, err = _pve_read(body, "snap-list", "snapshots")
    if err:
        return err
    return 200, {"ok": True, "snapshots": _snapshots(rows)}


def _snapshots(rows):
    return [{"name": r["name"], "description": _s(r.get("description"), 200), "parent": _s(r.get("parent"), 40),
             "time": int(_n(r.get("time"), 0, 4e9)), "vmstate": r.get("vmstate") is True}
            for r in rows if isinstance(r.get("name"), str) and agents.SNAP_ARG.fullmatch(r["name"])]


@_guarded("guest-backups")
def guest_backups(h, body):
    a, vmid, rows, err = _pve_read(body, "backup-list", "backups")
    if err:
        return err
    return 200, {"ok": True, "backups": [
        {"storage": _s(r.get("storage"), 60), "volid": _s(r.get("volid"), 200), "size": int(_n(r.get("size"))),
         "time": int(_n(r.get("time"), 0, 4e9)), "format": _s(r.get("format"), 20), "notes": _s(r.get("notes"), 200),
         "protected": r.get("protected") is True} for r in rows[:100]]}


@_guarded("guest-tasks")
def guest_tasks(h, body):
    a, vmid, rows, err = _pve_read(body, "task-list", "tasks")
    if err:
        return err
    return 200, {"ok": True, "tasks": [
        {"type": _s(r.get("type"), 30), "status": _s(r.get("status"), 120), "user": _s(r.get("user"), 60),
         "start": int(_n(r.get("start"), 0, 4e9)), "end": int(_n(r.get("end"), 0, 4e9))} for r in rows[:30]]}


@_guarded("guest-act")
def guest_act(h, body):
    aid, a, vmid, err = _guest(body)
    verb = str(body.get("verb") or "")
    if err:
        return err
    if verb not in GUEST_ACTIONS:
        return 400, {"ok": False, "error": "a verb of start, shutdown or reboot"}
    ok, error = agents.command(aid, "pve", verb, vmid)
    audit.record(h, f"servers/guest-{verb}", f"{a['name']}/{vmid}", ok)
    print(f"servers: guest {verb} {a['name']}/{vmid} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


def _snap_name(body):
    name = body.get("name")
    return name if isinstance(name, str) and agents.SNAP_ARG.fullmatch(name) else None


@_guarded("snap-create")
def snap_create(h, body):
    aid, a, vmid, err = _guest(body)
    name = _snap_name(body)
    if err:
        return err
    if not name:
        return 400, {"ok": False, "error": "a name of letters, digits, - and _, starting with a letter (40 at most)"}
    ok, error = agents.command(aid, "pve", "snap-create", vmid, wait=SNAP_WAIT, arg=name)
    audit.record(h, "servers/snap-create", f"{a['name']}/{vmid}/{name}", ok)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


def _snap_destroy(h, body, verb):
    """Rollback and delete: the snapshot must be in the host's own list, and the admin types a name."""
    aid, a, vmid, err = _guest(body)
    name = _snap_name(body)
    if err:
        return err
    if not name:
        return 400, {"ok": False, "error": "the snapshot's name"}
    ok, error, text = agents.read(aid, "pve", "snap-list", vmid, wait=40)
    rows = _json(text, "snapshots") if ok else None
    if rows is None:
        return 502, {"ok": False, "error": _s(error, 200) or "the agent's answer was not understood"}
    if not any(r["name"] == name for r in _snapshots([r for r in rows[:200] if isinstance(r, dict)])):
        return 404, {"ok": False, "error": "that is not in this guest's list"}
    guest_name = _s(((agents.state_of(aid).get("guests") or {}).get(vmid) or {}).get("name"), 80)
    want = guest_name if verb == "snap-rollback" else name       # the rollback endangers the guest, the delete the snapshot
    if not want or body.get("confirm") != want:
        return 400, {"ok": False, "error": "type the name to confirm"}
    ok, error = agents.command(aid, "pve", verb, vmid, wait=SNAP_WAIT, arg=name)
    audit.record(h, f"servers/{verb}", f"{a['name']}/{vmid}/{name}", ok)
    print(f"servers: {verb} {a['name']}/{vmid}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("snap-rollback")
def snap_rollback(h, body):
    return _snap_destroy(h, body, "snap-rollback")


@_guarded("snap-delete")
def snap_delete(h, body):
    return _snap_destroy(h, body, "snap-delete")


# --- the host: system facts, systemd units, what was done to it ---

UNIT = agents.TARGET["systemd"]
POWER = ("reboot", "shutdown")


def _updates(u):
    """The agent's count of pending packages, rebuilt: None when it sent none."""
    if not isinstance(u, dict):
        return None
    names = [_s(n, 80) for n in (u.get("names") or [])[:12] if isinstance(n, str)] if isinstance(u.get("names"), list) else []
    return {"pending": int(_n(u.get("pending"), 0, 100000)), "security": int(_n(u.get("security"), 0, 100000)), "names": names}


@_guarded("host")
def host(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    st = agents.state_of(aid)
    ticked = set(a.get("targets", {}).get("systemd", [])) if "systemd" in a.get("caps", []) else set()
    units = []
    for name, u in sorted((st.get("units") or {}).items())[:200]:
        if isinstance(u, dict) and isinstance(name, str) and UNIT.fullmatch(name):
            units.append({"name": name, "active": _s(u.get("active"), 20), "sub": _s(u.get("sub"), 20),
                          "description": _s(u.get("description"), 160), "ticked": name in ticked})
    failed = [n for n in (st.get("failed_units") or [])[:200] if isinstance(n, str) and UNIT.fullmatch(n)]
    info = a.get("host") if isinstance(a.get("host"), dict) else {}
    sensors = st.get("sensors") if isinstance(st.get("sensors"), dict) else {}
    return 200, {"ok": True, "online": aid in agents._live, "version": _s(a.get("version"), 40),
                 "host": {k: _s(info.get(k), 80) for k in ("hostname", "os", "kernel", "arch")},
                 "uptime": int(_n(sensors.get("uptime"), 0, 1e10)),
                 "updates": _updates(st.get("updates")),
                 "can": {"update": agents.allowed(aid, "host", "update", "host"),
                         "power": agents.allowed(aid, "host", "reboot", "host")},
                 "units": units, "failed": failed}


@_guarded("host-update")
def host_update(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    ok, error = agents.command(aid, "host", "update", "host")
    audit.record(h, "servers/host-update", a["name"], ok)
    print(f"servers: host update {a['name']} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("host-power")
def host_power(h, body):
    aid, a = _agent(body)
    verb = str(body.get("verb") or "")
    if not a or verb not in POWER:
        return 400, {"ok": False, "error": "agent and a verb of reboot or shutdown"}
    if body.get("confirm") != a["name"]:
        return 400, {"ok": False, "error": "type the host's name to confirm"}
    ok, error = agents.command(aid, "host", verb, "host")
    audit.record(h, f"servers/host-{verb}", a["name"], ok)
    print(f"servers: host {verb} {a['name']} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


def _unit(body):
    name = body.get("name")
    return name if isinstance(name, str) and UNIT.fullmatch(name) else None


@_guarded("unit")
def unit(h, body):
    aid, a = _agent(body)
    name, verb = _unit(body), str(body.get("verb") or "")
    if not a or not name or verb not in ("start", "restart"):
        return 400, {"ok": False, "error": "agent, a unit and a verb of start or restart"}
    ok, error = agents.command(aid, "systemd", verb, name)
    audit.record(h, f"servers/unit-{verb}", f"{a['name']}/{name}", ok)
    print(f"servers: unit {verb} {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("unit-stop")
def unit_stop(h, body):
    aid, a = _agent(body)
    name = _unit(body)
    if not a or not name:
        return 400, {"ok": False, "error": "agent and a unit"}
    if body.get("confirm") != name:
        return 400, {"ok": False, "error": "type the unit's name to confirm"}
    ok, error = agents.command(aid, "systemd", "stop", name)
    audit.record(h, "servers/unit-stop", f"{a['name']}/{name}", ok)
    print(f"servers: unit stop {a['name']}/{name} by {h._who()}: {'ok' if ok else error}", flush=True)
    return (200, {"ok": True}) if ok else (502, {"ok": False, "error": _s(error, 200) or "failed"})


@_guarded("activity")
def activity(h, body):
    aid, a = _agent(body)
    if not a:
        return 404, {"ok": False, "error": "no such host"}
    offset = body.get("offset")
    rows, more = audit.query(action="servers/", target=a["name"], offset=offset if type(offset) is int else 0, limit=50)
    # the target is "<host>/<what>": a host whose name is a part of another's must not show that one's
    rows = [r for r in rows if r["target"] == a["name"] or r["target"].startswith(a["name"] + "/")]
    out = [{**{k: _s(r.get(k), 200) if k != "ok" else bool(r.get(k)) for k in ("ts", "who", "action", "target", "ok")}, "source": "server"} for r in rows]
    if not offset:   # the agent's own history (updates, crashes) joins the newest page, by time
        out += _agent_events(aid, a)
        out.sort(key=lambda r: r["ts"], reverse=True)
    return 200, {"ok": True, "more": more, "rows": out}


def _agent_events(aid, a):
    """The host's own history as rows like the audit's: an update, a crash, an out-of-memory kill, a loop, a hold."""
    rows = []
    for key, events in list((agents.history.get(aid) or {}).items())[:200]:
        if not isinstance(events, list) or not isinstance(key, str):
            continue
        for e in events[-agents.HISTORY_PER_TARGET:]:
            if isinstance(e, dict) and e.get("kind") in agents.HISTORY_KINDS and type(e.get("t")) is int:
                rows.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e["t"])), "who": "agent", "action": "agent/" + e["kind"],
                             "target": a["name"] + "/" + _s(key.split(":", 1)[-1], 80), "ok": e.get("ok") is True, "source": "agent"})
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:30]


# --- the scheduler: jobs that run on a host at a time of day ---

def _job_refusal(job):
    """Why the host does not allow what the job's kind does, else "" (the agent checks again when it runs)."""
    if job["kind"] == "update":
        if job["agent"] != jobs.ALL and not agents.by_name(job["agent"])[1]:
            return "no such host"
        return ""
    aid, a = agents.by_name(job["agent"])
    if not a:
        return "no such host"
    if job["kind"] == "prune" and not agents.allowed(aid, "docker", "prune", agents.PRUNE_TARGET):
        return HINT
    if job["kind"] == "host-update" and not agents.allowed(aid, "host", "update", "host"):
        return "tick Host for this agent in Settings > Hosts and agents"
    if job["kind"] == "backup":
        miss = [v for v in job["vmids"] if not agents.allowed(aid, "pve", "vzdump", v)]
        if miss:
            return "tick these guests for this host in Settings > Hosts and agents: " + ", ".join(miss[:8])
    return ""


@_guarded("jobs")
def job_list(h, body):
    return 200, {"ok": True, "kinds": list(jobs.KINDS), "jobs": jobs.load()}


@_guarded("job-save")
def job_save(h, body):
    raw = body.get("job")
    job = jobs.clean(raw)
    if not job:
        return 400, {"ok": False, "error": "a name, a kind (" + ", ".join(jobs.KINDS) + "), days 0-6 and a time HH:MM; update may name no host (all of them), the others need one; backup also needs guests, a storage and a mode (" + ", ".join(jobs.MODES) + ")"}
    why = _job_refusal(job)
    if why:
        return 400, {"ok": False, "error": why}
    saved = jobs.put(dict(raw, **{k: job[k] for k in ("name", "kind", "agent", "days", "at", "enabled", "vmids", "storage", "mode") if k in job}))
    audit.record(h, "servers/job-save", f"{job['agent']}/{job['name']}", bool(saved))
    if not saved:
        return 400, {"ok": False, "error": "not saved: an unknown job, a changed kind or too many jobs"}
    return 200, {"ok": True, "job": saved}


def _job(body):
    jid = body.get("id")
    return next((j for j in jobs.load() if j["id"] == jid), None) if isinstance(jid, str) else None


@_guarded("job-delete")
def job_delete(h, body):
    job = _job(body)
    if not job:
        return 404, {"ok": False, "error": "no such job"}
    if body.get("confirm") != job["name"]:
        return 400, {"ok": False, "error": "type the job's name to confirm"}
    if not jobs.delete(job["id"]):
        return 400, {"ok": False, "error": "the update job stays: turn it off instead"}
    audit.record(h, "servers/job-delete", job["name"], True)
    return 200, {"ok": True}


@_guarded("job-run")
def job_run(h, body):
    job = _job(body)
    if not job:
        return 404, {"ok": False, "error": "no such job"}
    ok, text = agents.run_job(job)
    jobs.record(job["id"], ok, text)
    audit.record(h, f"servers/job-run-{job['kind']}", job["agent"] if job["agent"] != jobs.ALL else job["name"], ok)
    print(f"servers: job {job['name']} run by {h._who()}: {'ok' if ok else text}", flush=True)
    now = next(j for j in jobs.load() if j["id"] == job["id"])
    return (200, {"ok": True, "text": _s(text, 300), "job": now}) if ok else (502, {"ok": False, "error": _s(text, 300) or "failed", "job": now})
