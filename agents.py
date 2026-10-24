"""Paired agents (labhud-agent, Go): one per host, dialling labhud on /api/agent/ws. The protocol
is in docs/agent-protocol.md, its cryptography in agentproto.py.

Pairing follows devices.py: ⚙ > Devices > Agents makes a one-time code, valid PAIR_MINUTES, of which only
code_id and pair_secret are kept (never the code). An agent is saved with its key only after its
first signed frame checks out. Everything lives in the store (LABHUD_DATA); without it there are
no agents.

What an agent may do is kept here: `caps` (ticked in ⚙) and `targets` per capability. A command
leaves only when the agent is connected and both allow it; the agent checks the same again.

The server plugs in through three hooks: on_change() when anything shown changed (online,
state, the list), on_event(text, bad, event=None) for the timeline (event: notify.py's, for
the kinds in EVENT_OF), and tls_fp, its certificate's SHA-256.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

import agentproto as P
import devices
import gitsource
import jobs
import store
import util
import websock

AGENTS_KEY = "agents"
CODES_KEY = "agent_codes"
PAIR_MINUTES = 5
MAX_AGENTS = 32
MAX_CODES = 8
FIRST_FRAME = 5            # seconds an agent has to say who it is
IDLE = 70                  # seconds without anything (pongs included) before a connection is dropped
PING_EVERY = 25
SEEN_EVERY = 300
COMMAND_WAIT = 45
EVENTS_PER_MINUTE = 10
MAX_OUTPUT = 48 << 10      # a read verb's text (the agent sends at most this much, the newest part)

VERBS = {
    "docker": ("start", "stop", "restart", "update", "logs", "prune", "inspect", "config", "reconfigure",
               "inventory", "prune-dangling", "prune-networks", "prune-volumes", "follow", "stats",
               "rm-image", "rm-network", "rm-volume", "pull", "network-inspect", "volume-ls", "volume-read"),
    "systemd": ("start", "stop", "restart"),
    "host": ("reboot", "shutdown", "update"),
    "wol": ("wake",),
    "pve": ("start", "shutdown", "reboot", "config", "snap-list", "backup-list", "task-list",
            "snap-create", "snap-rollback", "snap-delete", "vzdump"),
    "ptero": ("start", "stop", "restart"),
    "stacks": ("deploy", "redeploy", "restart", "down", "show", "read", "validate", "save", "version", "restore"),
    "terminal": ("shell",),
}
# sensors and scan only report: scan (agent with trivy or grype) reads the ticked containers' images
CAPS = ("sensors",) + tuple(VERBS) + ("scan",)
# what an agent may say it understands beyond v1's first messages (hello.features)
FEATURES = ("registry-auth",)
# verbs that read instead of act: never a button or an action name, only read() (for the admin)
READS = {"docker": ("logs", "prune", "inspect", "config", "reconfigure", "inventory", "prune-dangling", "prune-networks", "prune-volumes", "follow", "stats",
                    "rm-image", "rm-network", "rm-volume", "pull", "network-inspect", "volume-ls", "volume-read"),
         "pve": VERBS["pve"][3:],    # a guest's config, snapshots, backups and tasks answer with their output; start/shutdown/reboot are the buttons
         "stacks": VERBS["stacks"], "terminal": VERBS["terminal"]}   # compose answers with its output: only ⚙ > Containers > Stacks asks
# the host's Docker (its images, volumes and networks), ticked as this target, which is no container
# and takes none of a container's verbs, as the host verbs take no container (the agent checks the same)
PRUNE_TARGET = "images"
HOST_VERBS = ("inventory", "prune", "prune-dangling", "prune-networks", "prune-volumes",
              "rm-image", "rm-network", "rm-volume", "pull", "network-inspect")
# a volume ticked for browsing is the target "volume:<name>"; the two volume verbs are for those and nothing else
VOLUME_PREFIX = "volume:"
VOLUME_VERBS = ("volume-ls", "volume-read")
VZDUMP_ARG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}:(snapshot|suspend|stop)")   # storage:mode
SNAP_ARG = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,39}")     # Proxmox's own rule for a snapshot name
VERSION_ARG = re.compile(r"[0-9]{1,12}")
IMAGE_ARG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,199}")
NAME_ARG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
MAX_PATH = 400
# what cleans what: the name the admin sends -> the verb (api: /api/setup/prune {agent, what})
CLEAN = {"images": "prune", "dangling": "prune-dangling", "networks": "prune-networks", "volumes": "prune-volumes"}
HISTORY_KEY = "agent_history"
HISTORY_KINDS = ("update", "crash", "oom", "loop", "held")
# notify.py's event for each kind (⚙ > Notifications > Events); a failed update is still "update_done"
EVENT_OF = {"update": "update_done", "crash": "container_stopped", "oom": "container_stopped", "loop": "container_stopped"}
HISTORY_PER_TARGET = 10


def clean_path(p):
    """A path in a volume as the agent takes it ("/", "/a/b"), or None: no "..", no control characters, not too long."""
    if not isinstance(p, str) or len(p) > MAX_PATH or any(ord(c) < 32 or ord(c) == 127 for c in p):
        return None
    parts = p.split("/")
    if ".." in parts:
        return None
    return "/" + "/".join(x for x in parts if x not in ("", "."))


def arg_ok(verb, arg):
    """The argument has the shape its verb takes, and a verb that takes none gets none (the agent checks the same)."""
    if not isinstance(arg, str):
        return False
    if verb in ("rm-image", "pull"):
        return bool(IMAGE_ARG.fullmatch(arg))
    if verb in ("rm-network", "rm-volume", "network-inspect"):
        return bool(NAME_ARG.fullmatch(arg))
    if verb in ("snap-create", "snap-rollback", "snap-delete"):
        return bool(SNAP_ARG.fullmatch(arg))
    if verb == "vzdump":
        return bool(VZDUMP_ARG.fullmatch(arg))
    if verb in ("version", "restore"):
        return bool(VERSION_ARG.fullmatch(arg))
    if verb == "volume-ls":
        return clean_path(arg) is not None
    if verb == "volume-read":
        c = clean_path(arg)
        return c is not None and c != "/"
    return arg == ""


def acts(cap):
    """The verbs of a capability that are buttons (everything but the reads)."""
    return tuple(v for v in VERBS[cap] if v not in READS.get(cap, ()))

TARGET = {
    "docker": re.compile(r"(volume:)?[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
    "systemd": re.compile(r"[A-Za-z0-9:_.@-]{1,250}\.(service|timer|socket|target|mount|path)"),
    "host": re.compile(r"host"),
    "wol": re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}"),
    "pve": re.compile(r"[1-9][0-9]{2,8}"),
    "ptero": re.compile(r"[0-9a-f]{8}"),
    "stacks": re.compile(r"[a-z0-9][a-z0-9_-]{0,63}"),
    "terminal": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
}
MAX_TARGETS = 200

on_change = lambda: None             # noqa: E731
on_event = lambda text, bad, event=None: None    # noqa: E731
# api/registries.py: the login for an image's registry, (registry, user, token) or None
registry_auth = lambda image: None   # noqa: E731
# an update held back for its new critical CVEs (agent name, container, text): for notify.py
on_held = lambda name, target, text: None   # noqa: E731
registries = []   # [[registry]] of the config in use ({host, user}): server._apply sets it
tls_fp = ""

_lock = threading.Lock()
_live = {}       # agent id -> Session
_state = {}      # agent id -> last `state` message (in memory only), with "_t"
_seen = {}


def enabled():
    return store.enabled()


def _load():
    return store.load(AGENTS_KEY) or {} if store.enabled() else {}


# --- codes ---

def new_code(now=None):
    """A one-time pairing code (24 characters), valid PAIR_MINUTES. None without LABHUD_DATA."""
    with _lock:
        return util.add_code(store, CODES_KEY, lambda code: (P.code_id(code), {"secret": P.pair_secret(code)}), MAX_CODES, PAIR_MINUTES, now)


def _take_code(cid, now):
    """The pair_secret of a live code, which is used up whatever happens next; else None."""
    if not (isinstance(cid, str) and P.HEX32.fullmatch(cid)):
        return None
    with _lock:
        codes = store.load(CODES_KEY) or {}
        entry = codes.pop(cid, None)
        store.save(CODES_KEY, {h: c for h, c in codes.items() if c.get("until", 0) > now})
        if not entry or entry.get("until", 0) <= now or len(_load()) >= MAX_AGENTS:
            return None
    return entry["secret"]


# --- the list and what each agent may do ---

def _unique(name, agents, own=None):
    return util.unique_name(name, (a["name"] for aid, a in agents.items() if aid != own))


def clean_name(name):
    """An agent's name as shown: trimmed, at most 40 plain characters, never empty. No dots: the
    name is a step of a data path (`agents.<name>.sensors`), so "pve2.lan" becomes "pve2-lan"."""
    return util.clean_name(name, devices.NAME, "agent", dots=True)


def _save(aid, **fields):
    with _lock:
        agents = _load()
        if aid not in agents:
            return False
        agents[aid].update(fields)
        store.save(AGENTS_KEY, agents)
    return True


def get(aid):
    return _load().get(aid)


def by_name(name):
    """(id, record) of the agent with that name (case does not matter), else (None, None)."""
    for aid, a in _load().items():
        if a["name"].lower() == str(name).strip().lower():
            return aid, a
    return None, None


def allowance(aid):
    """{"caps": [...], "targets": {cap: [...]}, "auto": {"docker": [...]}}: what the agent may do,
    as sent to it, and which containers labhud updates on its own (the agent marks them: its
    LABHUD_AGENT_AUTO_LABELS)."""
    a = get(aid) or {}
    caps = [c for c in a.get("caps", []) if c in CAPS]
    auto = auto_targets(a)
    return {"caps": caps, "targets": {c: list(a.get("targets", {}).get(c, [])) for c in caps if c in VERBS},
            **({"auto": {"docker": auto}} if auto else {})}


# --- containers updated on a schedule (⚙ > Devices > Agents, "Update on their own") ---
#
# Each agent keeps `auto`: the ticked containers labhud updates by itself, in the window (every
# day or some days, at one time, the server's local time), when their registry has a newer image.
# Only containers ticked with UPDATE; one after the other, as UPDATE would.

WINDOW_KEY = "agents_update_window"
WINDOW_DEFAULT = {"days": [0, 1, 2, 3, 4, 5, 6], "at": "04:00"}   # Monday = 0
HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


def auto_targets(a):
    """The containers of one agent labhud updates on its own: still ticked, not the images' target."""
    ticked = set(a.get("targets", {}).get("docker", [])) if "docker" in a.get("caps", []) else set()
    return sorted(t for t in a.get("auto", []) if t in ticked and t != PRUNE_TARGET)


def set_auto(aid, targets):
    a = get(aid)
    if not a or not isinstance(targets, list) or len(targets) > MAX_TARGETS \
            or any(not isinstance(t, str) or not TARGET["docker"].fullmatch(t) or t == PRUNE_TARGET for t in targets):
        return False
    if not _save(aid, auto=sorted(set(targets))):
        return False
    _send_allow(aid)
    return True


def window():
    """The days and time of the update job (the scheduler's first job: jobs.py)."""
    return jobs.update_window() if store.enabled() else dict(WINDOW_DEFAULT)


def set_window(days, at):
    return jobs.set_update_window(days, at) if store.enabled() else False


def due(now=None, last=None):
    """The date (YYYY-MM-DD) when the window is open at `now` and not yet run that day, else None."""
    t = time.localtime(now or time.time())
    w = window()
    today = time.strftime("%Y-%m-%d", t)
    if t.tm_wday not in w["days"] or time.strftime("%H:%M", t) < w["at"] or last == today:
        return None
    # open for an hour: a labhud restarted at 04:20 still does it, one started at noon waits a day
    h, m = map(int, w["at"].split(":"))
    return today if (t.tm_hour * 60 + t.tm_min) - (h * 60 + m) < 60 else None


HOLD_KEY = "agents_hold_critical"


def hold_critical():
    """⚙ > Containers > Vulnerability scan: an update labhud starts on its own is held back when
    the new image has more critical CVEs than the running one (agents with scan ticked)."""
    return bool(store.load(HOLD_KEY)) if store.enabled() else False


def set_hold_critical(on):
    store.save(HOLD_KEY, bool(on))


def run_auto(wait=COMMAND_WAIT, only=None):
    """Updates, on every agent online (or just the one called `only`), the auto containers whose registry
    has a newer image. Returns [(agent name, container, ok, error)]."""
    out = []
    for aid, a in _load().items():
        if aid not in _live or (only and a["name"] != only):
            continue
        st = (_state.get(aid) or {}).get("containers") or {}
        for t in auto_targets(a):
            x = st.get(t) or {}
            if x.get("update") is True and not x.get("updating") and allowed(aid, "docker", "update", t):
                ok, error = command(aid, "docker", "update", t, wait, auto=True)
                out.append((a["name"], t, ok, error))
    return out


# --- webhooks: CI asks for a container's update (POST /api/hook/<token>) ---
#
# One secret URL per (agent, container), made in ⚙; labhud keeps only its SHA-256. It does exactly
# what UPDATE does on that container, while the container is ticked with UPDATE, at most once in
# HOOK_EVERY seconds. Nothing else can be asked through it.

HOOK_EVERY = 30
HOOK_TOKEN = re.compile(r"[A-Za-z0-9_-]{32,64}")
_hook_last = {}


def new_hook(aid, target):
    """A new secret for one ticked container (the old one, if any, stops working), or None."""
    a = get(aid)
    if not a or target == PRUNE_TARGET or target not in a.get("targets", {}).get("docker", []):
        return None
    token = secrets.token_urlsafe(32)
    if not _save(aid, hooks=dict(a.get("hooks", {}), **{target: util.sha256_hex(token)})):
        return None
    on_change()
    return token


def remove_hook(aid, target):
    a = get(aid)
    if not a or target not in a.get("hooks", {}):
        return False
    return bool(_save(aid, hooks={t: h for t, h in a["hooks"].items() if t != target}))


def _sync_from_hook(aid, name):
    ok, error, _ = sync_stack(aid, name)
    on_event(f"Stack {name}: synced from git by a webhook" + ("" if ok else f", failed: {error}"), not ok)


def hook(token, now=None):
    """(ok, error, agent name, container) for a webhook call."""
    if not HOOK_TOKEN.fullmatch(token or ""):
        return False, "forbidden", "", ""
    h = util.sha256_hex(token)
    for aid, a in _load().items():
        for target, kept in (a.get("stack_hooks") or {}).items():
            if hmac.compare_digest(kept, h):
                now = now or time.monotonic()
                if now - _hook_last.get((aid, "stack:" + target), -HOOK_EVERY) < HOOK_EVERY:
                    return False, "too soon: once in 30 s", a["name"], target
                _hook_last[(aid, "stack:" + target)] = now
                threading.Thread(target=_sync_from_hook, args=(aid, target), daemon=True).start()
                return True, "", a["name"], target
        for target, kept in a.get("hooks", {}).items():
            if hmac.compare_digest(kept, h):
                now = now or time.monotonic()
                if now - _hook_last.get((aid, target), -HOOK_EVERY) < HOOK_EVERY:
                    return False, "too soon: once in 30 s", a["name"], target
                _hook_last[(aid, target)] = now
                ok, error = command(aid, "docker", "update", target)
                return ok, error, a["name"], target
    return False, "forbidden", "", ""


def run_job(job):
    """(ok, text): one job of the scheduler, run now. What a kind may do is what the host allows."""
    kind, name = job["kind"], job["agent"]
    if kind == "update":
        started = run_auto(only=None if name == jobs.ALL else name)
        good = [f"{n}/{c}" for n, c, ok, _ in started if ok]
        bad = [f"{n}/{c} ({e})" for n, c, ok, e in started if not ok]
        text = f"{len(good)} started" + (f", {len(bad)} refused: {', '.join(bad)}" if bad else "") + (f" ({', '.join(good[:6])}{'…' if len(good) > 6 else ''})" if good else "")
        return not bad, text if started else "nothing to update"
    aid, a = by_name(name)
    if not a:
        return False, "no such host"
    if aid not in _live:
        return False, "the host is offline"
    if kind == "prune":
        ok, error, text = clean(aid, "images")
        return ok, text if ok else error
    if kind == "host-update":
        ok, error = command(aid, "host", "update", "host")
        return ok, "the update was started" if ok else error
    if kind == "backup":
        return start_backup(job, aid)
    return False, "unknown kind"


BACKUP_WAIT = 3 * 3600 + 60     # s: the agent gives one guest's backup three hours
_backing = set()                 # ids of the backup jobs running now


def start_backup(job, aid):
    """The guests are backed up one after the other in a thread of their own (it takes hours); the
    result is written into the job when it ends. (ok, text) of the start."""
    a = _load().get(aid) or {}
    ticked = set(a.get("targets", {}).get("pve", [])) if "pve" in a.get("caps", []) else set()
    ids = [v for v in job["vmids"] if v in ticked]
    if not ids:
        return False, "none of its guests is ticked for this host"
    with _lock:
        if job["id"] in _backing:
            return False, "it is still running"
        _backing.add(job["id"])
    threading.Thread(target=_backup, args=(job, aid, ids), daemon=True).start()
    return True, f"started: {len(ids)} guest(s) to {job['storage']}"


def _backup(job, aid, ids):
    from api import audit
    good, bad = [], []
    try:
        for v in ids:
            ok, error = command(aid, "pve", "vzdump", v, wait=BACKUP_WAIT, arg=f"{job['storage']}:{job['mode']}")
            (good if ok else bad).append(v if ok else f"{v} ({str(error)[:80]})")
    finally:
        with _lock:
            _backing.discard(job["id"])
    text = f"{len(good)} backed up" + (f", {len(bad)} failed: {', '.join(bad)}" if bad else "")
    jobs.record(job["id"], not bad, text)
    audit.write("scheduler", "servers/job-backup-done", job["agent"], ok=not bad)
    on_event(f"Scheduled {job['name']}: {text}", bool(bad))


def run_due(now=None):
    """Runs the jobs that are due, one after the other; says what each did. Returns the jobs it ran."""
    ran = []
    for job in jobs.load():
        if jobs.due(job, now):
            ok, text = run_job(job)
            jobs.record(job["id"], ok, text, now)
            from api import audit
            audit.write("scheduler", f"servers/job-{job['kind']}", job["agent"] if job["agent"] != jobs.ALL else "", ok=ok)
            if job["kind"] != "update" or text != "nothing to update":
                on_event(f"Scheduled {job['name']}: {text}", not ok)
            ran.append(job)
    return ran


def scheduler(stop, every=30):
    """One thread: runs the scheduler's jobs when they are due, and says what they did."""
    while not stop.wait(every):
        try:
            run_due()
        except Exception as e:   # a job must not end the thread
            print(f"agents: scheduler: {e}", flush=True)


def set_caps(aid, caps):
    """Ticks exactly these capabilities. False for an unknown agent or capability."""
    if not isinstance(caps, list) or any(c not in CAPS for c in caps):
        return False
    if not _save(aid, caps=sorted(set(caps))):
        return False
    _send_allow(aid)
    return True


def set_targets(aid, cap, targets):
    """The ticked targets of one capability (replacing the previous ones)."""
    if cap not in VERBS or not isinstance(targets, list) or len(targets) > MAX_TARGETS \
            or any(not isinstance(t, str) or not TARGET[cap].fullmatch(t) for t in targets):
        return False
    a = get(aid)
    if not a:
        return False
    if cap == "docker":   # the volumes ticked for browsing have their own switch (set_volume_access)
        targets = [t for t in targets if not t.startswith(VOLUME_PREFIX)] + \
                  [t for t in a.get("targets", {}).get("docker", []) if t.startswith(VOLUME_PREFIX)]
    all_targets = dict(a.get("targets", {}), **{cap: sorted(set(targets))})
    if not _save(aid, targets=all_targets):
        return False
    _send_allow(aid)
    return True


def set_volume_access(aid, volume, on):
    """Ticks (or unticks) one volume for browsing, read only. False for an unknown agent or a bad name."""
    a = get(aid)
    if not a or not isinstance(volume, str) or not NAME_ARG.fullmatch(volume) or len(a.get("targets", {}).get("docker", [])) >= MAX_TARGETS:
        return False
    key = VOLUME_PREFIX + volume
    now = list(a.get("targets", {}).get("docker", []))
    new = sorted(set(now) | {key}) if on else [t for t in now if t != key]
    if new != now and not _save(aid, targets=dict(a.get("targets", {}), docker=new)):
        return False
    _send_allow(aid)
    return True


def rename(aid, name):
    with _lock:
        agents = _load()
        if aid not in agents:
            return None
        agents[aid]["name"] = _unique(clean_name(name), agents, own=aid)
        store.save(AGENTS_KEY, agents)
        name = agents[aid]["name"]
    on_change()
    return name


def remove(aid):
    """Ends the agent at once: its key is deleted and its connection told and closed."""
    with _lock:
        agents = _load()
        a = agents.pop(aid, None)
        if a:
            store.save(AGENTS_KEY, agents)
        s = _live.pop(aid, None)
        _state.pop(aid, None)
        if history.pop(aid, None) is not None:
            store.save(HISTORY_KEY, history)
    if s:
        s.bye("removed")
    if a:
        on_change()
    return a


def _docker_kinds(verb, target):
    """The verb and the target are of one kind: a container, the host's Docker ("images"), or a volume."""
    is_volume = target.startswith(VOLUME_PREFIX)
    if (verb in VOLUME_VERBS) != is_volume:
        return False
    return is_volume or (verb in HOST_VERBS) == (target == PRUNE_TARGET)


def allowed(aid, cap, verb, target):
    a = get(aid)
    if cap == "docker" and not _docker_kinds(verb, target):
        return False
    if cap == "terminal" and target == PRUNE_TARGET:
        return False
    return bool(a and cap in VERBS and verb in VERBS[cap] and cap in a.get("caps", [])
                and target in a.get("targets", {}).get(cap, []))


def online(aid):
    return aid in _live


# A Pterodactyl node's Wings runs each game server as a container named by the server's UUID.
# On an agent that also has the game servers, those containers are the same servers twice, with
# names nobody can read: the Docker lists leave them out.
WINGS = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _wings(a, cap, names):
    if cap != "docker" or "ptero" not in a.get("found", []):
        return list(names)
    return [n for n in names if not WINGS.fullmatch(n)]


def _containers(a, names):
    """The ticked docker targets that are containers (not the images' target)."""
    return [n for n in _wings(a, "docker", names) if n != PRUNE_TARGET and not n.startswith(VOLUME_PREFIX)]


def listing():
    """For ⚙ > Devices > Agents: everything but the keys, with what each agent last reported."""
    out = []
    for aid, a in _load().items():
        st = _state.get(aid) or {}
        found = {"docker": _containers(a, sorted(st.get("containers") or {})) + [PRUNE_TARGET],
                 "systemd": sorted(st.get("units") or {}),
                 "pve": sorted(st.get("guests") or {}, key=lambda v: int(v) if v.isdigit() else 0),
                 "ptero": sorted(st.get("servers") or {}), "wol": sorted(st.get("neighbours") or {}),
                 "host": ["host"] if "host" in a.get("found", []) else [], "stacks": sorted(stacks_of(aid)),
                 "terminal": _containers(a, sorted(st.get("containers") or {}))}
        labels = {"docker": {PRUNE_TARGET: "Unused images"},
                  "pve": {k: (v or {}).get("name") for k, v in (st.get("guests") or {}).items()},
                  "ptero": {k: (v or {}).get("name") for k, v in (st.get("servers") or {}).items()},
                  "wol": {k: (v or {}).get("ip") for k, v in (st.get("neighbours") or {}).items()}}
        out.append({"id": aid, "name": a["name"], "online": aid in _live, "version": a.get("version", ""),
                    "install": a.get("install", ""), "update": update_for(a), "features": a.get("features", []),
                    "addr": getattr(_live.get(aid), "peer", "") or "",
                    "host": a.get("host", {}), "paired": a.get("paired", 0), "seen": a.get("seen", 0),
                    "found": a.get("found", []), "caps": a.get("caps", []),
                    "prune": "docker" in a.get("caps", []) and PRUNE_TARGET in a.get("targets", {}).get("docker", []),
                    "auto": auto_targets(a), "hooks": sorted(a.get("hooks", {})),
                    "stacks": stacks_of(aid) if "stacks" in a.get("found", []) else {},
                    "targets": {c: [t for t in _wings(a, c, v) if not t.startswith(VOLUME_PREFIX)] for c, v in a.get("targets", {}).items()},
                    "volumes": sorted(t[len(VOLUME_PREFIX):] for t in a.get("targets", {}).get("docker", []) if t.startswith(VOLUME_PREFIX)),
                    "choices": {c: v for c, v in found.items() if c in a.get("found", [])},
                    "labels": labels})
    return sorted(out, key=lambda a: a["paired"])


def state_of(aid):
    """A copy of the last `state` an agent sent (without the type and the clock), {} when none."""
    return {k: v for k, v in (_state.get(aid) or {}).items() if k not in ("type", "_t")}


def snapshot():
    """{name: {online, version, sensors, guests, ...}}: what cards read (`agents.<name>.…`)."""
    out = {}
    for aid, a in _load().items():
        st = {k: v for k, v in (_state.get(aid) or {}).items() if k != "type"}
        if st and aid not in _live:   # its last report, kept: shown dimmed, "not updated since"
            st["stale"] = "the agent is offline"
        out[a["name"]] = dict(st, id=aid, online=aid in _live, seen=int(_seen.get(aid) or a.get("seen", 0)), version=a.get("version", ""),
                              caps=a.get("caps", []), buttons=buttons(aid, a), history=history.get(aid, {}),
                              terminal=a.get("targets", {}).get("terminal", []) if "terminal" in a.get("caps", []) else [])
    return out


# --- what happened to each target: updates and crashes (events with a kind) ---

history = {}    # agent id -> {"docker:jellyfin": [{t, kind, ok, text, from, to}], newest last}


def load_history():
    history.clear()
    history.update(store.load(HISTORY_KEY) or {})


def _remember(aid, msg, now):
    cap, target, kind = msg.get("cap"), msg.get("target"), msg.get("kind")
    if cap not in TARGET or kind not in HISTORY_KINDS or not isinstance(target, str) \
            or not TARGET[cap].fullmatch(target):
        return
    entry = {"t": int(now), "kind": kind, "ok": msg.get("bad") is not True, "text": str(msg.get("text", ""))[:160]}
    for k in ("from", "to"):
        if isinstance(msg.get(k), str) and msg[k]:
            entry[k] = msg[k][:40]
    with _lock:
        rows = history.setdefault(aid, {}).setdefault(f"{cap}:{target}", [])
        rows.append(entry)
        del rows[:-HISTORY_PER_TARGET]
        store.save(HISTORY_KEY, history)
    on_change()   # the panels show it


# What a page draws next to one target without a card of its own: a container in a guest's
# panel, a game server's tile. Only ticked targets of ticked capabilities, so a button here is
# always one resolve() accepts.
LISTED = ("docker", "systemd", "ptero")


def buttons(aid, a):
    """{cap: {target: [action names]}} for the ticked targets the pages list themselves."""
    return {cap: {t: [action_name(aid, cap, t, v) for v in acts(cap)] for t in _containers_or(a, cap)}
            for cap in LISTED if cap in a.get("caps", [])}


def _containers_or(a, cap):
    names = a.get("targets", {}).get(cap, [])
    return _containers(a, names) if cap == "docker" else _wings(a, cap, names)


# --- actions: names, and the buttons a card gets ---
#
# agent-<agent id>-<cap>-<target ref>-<verb>. The verb comes last, so the display's rules for
# "-start" (hidden while up) and "-shutdown" (hidden while down) hold. A target that is not already
# a plain lowercase name (a MAC, "nginx.service", "Jellyfin_1") is referred to by a short hash;
# resolve() finds it among the agent's ticked targets, so a name can only ever mean one of those.

ACTION = re.compile(r"agent-([0-9a-f]{8})-(" + "|".join(VERBS) + r")-([a-z0-9-]+)-([a-z]+)")
PLAIN = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
VERB_TEXT = {"start": "START", "stop": "STOP", "restart": "RESTART", "shutdown": "SHUT DOWN",
             "reboot": "REBOOT", "update": "UPDATE", "wake": "WAKE"}


def tref(target):
    if PLAIN.fullmatch(target) and len(target) <= 24:
        return target
    return "x" + hashlib.sha256(target.encode()).hexdigest()[:12]


def action_name(aid, cap, target, verb):
    return f"agent-{aid}-{cap}-{tref(target)}-{verb}"


def resolve(name):
    """(agent id, cap, target, verb) for an action name that means one ticked target, else None."""
    m = ACTION.fullmatch(name or "")
    if not m:
        return None
    aid, cap, ref, verb = m.groups()
    a = get(aid)
    if not a or verb not in acts(cap) or cap not in a.get("caps", []):
        return None
    hits = [t for t in a.get("targets", {}).get(cap, []) if tref(t) == ref
            and not (cap == "docker" and t == PRUNE_TARGET)]
    return (aid, cap, hits[0], verb) if len(hits) == 1 else None


def card_actions(card):
    """The agents' buttons for one card of the config:
    - a Proxmox guest card (`proxmox = "node/vmid"`): from the agent on that node, if the guest is ticked;
    - `agent = "<name>"`: that host's reboot, shut down and update, if ticked (on a guest card, only
      update: Proxmox gives the rest);
    - `agent` + `agent_target = "<cap>:<target>"`: that container, unit, server, guest or MAC;
    - `wake = "<agent>:<mac>"`: WAKE from that agent, whichever agent the card has itself."""
    out = []
    chk = card.get("check") or []
    agents = _load()
    if chk and chk[0] == "proxmox":
        for aid, a in agents.items():
            if ("pve" in a.get("caps", []) and (_state.get(aid) or {}).get("node") == chk[1]
                    and str(chk[2]) in a.get("targets", {}).get("pve", [])):
                out += [action_name(aid, "pve", str(chk[2]), v) for v in acts("pve")]
                break
    if card.get("agent"):
        aid, a = by_name(card["agent"])
        cap, _, target = (card.get("agent_target") or "host:host").partition(":")
        if a and cap in VERBS and cap in a.get("caps", []) and target in a.get("targets", {}).get(cap, []):
            # a guest card has START / SHUT DOWN / REBOOT from Proxmox already: its own agent
            # adds only UPDATE, not a second REBOOT that means something else
            verbs = ("update",) if chk and chk[0] == "proxmox" and cap == "host" else acts(cap)
            out += [action_name(aid, cap, target, v) for v in verbs]
    if card.get("wake"):
        by, _, mac = card["wake"].partition(":")
        aid, a = by_name(by)
        if a and "wol" in a.get("caps", []) and mac in a.get("targets", {}).get("wol", []):
            name = action_name(aid, "wol", mac, "wake")
            if name not in out:
                out.insert(0, name)
    return out


def action_text(name):
    """"jellyfin · RESTART", "pve2 · UPDATE": an agent's action in words, for the timeline."""
    r = resolve(name)
    if not r:
        return None
    aid, cap, target, verb = r
    st = _state.get(aid) or {}
    part = {"pve": "guests", "ptero": "servers"}.get(cap)
    label = ((st.get(part) or {}).get(target) or {}).get("name") if part else None
    if cap == "host":
        label = (get(aid) or {}).get("name")
    return f"{label or target} · {VERB_TEXT[verb]}"


# --- commands ---

def command(aid, cap, verb, target, wait=COMMAND_WAIT, auto=False, arg=""):
    """(ok, error): sends one command and waits for its result. `auto`: labhud's own scheduled
    update (it may be held back for new critical CVEs). `arg`: the object a verb names (rm-image)."""
    if not allowed(aid, cap, verb, target) or not arg_ok(verb, arg):
        return False, "not allowed"
    return read(aid, cap, verb, target, wait, auto, arg)[:2]


def read(aid, cap, verb, target, wait=COMMAND_WAIT, auto=False, arg=""):
    """(ok, error, output): one command and its result's output (a read verb's text, else "")."""
    if not allowed(aid, cap, verb, target) or not arg_ok(verb, arg):
        return False, "not allowed", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    if cap == "docker" and verb == "update":
        extra, seal = update_extras(aid, target, auto)
        if extra or seal:
            return s.ask(dict({"type": "command", "cap": cap, "verb": verb, "target": target}, **extra), wait, seal)
    return s.command(cap, verb, target, wait, arg) if arg else s.command(cap, verb, target, wait)


INVENTORY_KEEP = 20    # seconds an inventory is reused (opening the screen twice does not ask the host twice)
_inventory = {}


def _count(v, low=0):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= low else low


def inventory(aid, fresh=False):
    """(ok, error, inventory): the host's images, volumes and networks, from the agent that has
    "images" ticked. The agent's text is parsed and rebuilt field by field: nothing else it says
    reaches the screens. Asked only when a screen opens, and kept for a few seconds."""
    hit = _inventory.get(aid)
    if hit and not fresh and time.time() - hit[0] < INVENTORY_KEEP:
        return True, "", hit[1]
    ok, error, text = read(aid, "docker", "inventory", PRUNE_TARGET, wait=60)
    if not ok:
        return False, error, None
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError
    except ValueError:
        return False, "the agent's answer was not an inventory", None
    s = lambda v, n=200: v[:n] if isinstance(v, str) else ""   # noqa: E731
    rows = lambda k: [x for x in raw.get(k) or [] if isinstance(x, dict)][:100]   # noqa: E731
    names = lambda x: [s(n, 128) for n in (x.get("used_by") or [])[:8] if isinstance(n, str)] if isinstance(x.get("used_by"), list) else []   # noqa: E731
    out = {
        "images": [{"id": s(x.get("id"), 64), "tags": [s(t) for t in (x.get("tags") or [])[:8] if isinstance(t, str)],
                    "size": _count(x.get("size")), "created": _count(x.get("created")), "used": _count(x.get("used")),
                    "dangling": x.get("dangling") is True, "used_by": names(x)} for x in rows("images")],
        "volumes": [{"name": s(x.get("name")), "driver": s(x.get("driver"), 40), "size": _count(x.get("size"), -1),
                     "used": _count(x.get("used")), "anonymous": x.get("anonymous") is True, "used_by": names(x)} for x in rows("volumes")],
        "networks": [{"name": s(x.get("name")), "driver": s(x.get("driver"), 40), "scope": s(x.get("scope"), 20),
                      "used": _count(x.get("used")), "builtin": x.get("builtin") is True, "used_by": names(x)} for x in rows("networks")],
        "more": {k: _count(v) for k, v in (raw.get("more") or {}).items()
                 if k in ("images", "volumes", "networks")} if isinstance(raw.get("more"), dict) else {},
    }
    out["totals"] = {"images": sum(i["size"] for i in out["images"]),
                     "unused_images": sum(i["size"] for i in out["images"] if not i["used"]),
                     "volumes": sum(max(v["size"], 0) for v in out["volumes"])}
    _inventory[aid] = (time.time(), out)
    return True, "", out


STACK_FILE_MAX = 48 << 10   # a compose file, and a .env


def stacks_of(aid):
    """{name: {updated, env, services, running}}: the stacks the agent keeps, with what runs of each
    (the containers whose compose project it is)."""
    st = _state.get(aid) or {}
    running = {}
    for x in (st.get("containers") or {}).values():
        if isinstance(x, dict) and x.get("project"):
            n = running.setdefault(x["project"], [0, 0])
            n[0] += 1
            n[1] += x.get("state") == "running"
    return {name: {"updated": x.get("updated", 0), "env": x.get("env") is True,
                   "services": running.get(name, [0, 0])[0], "running": running.get(name, [0, 0])[1]}
            for name, x in (st.get("stacks") or {}).items() if isinstance(x, dict) and TARGET["stacks"].fullmatch(name)}


STACK_FILE_VERBS = ("deploy", "validate", "save")     # these carry the files, sealed
STACK_ARG_VERBS = ("version", "restore")              # these name a saved version


def stack(aid, verb, name, compose=None, env=None, arg=""):
    """(ok, error, output): one verb on a stack. deploy, validate and save carry the files, sealed:
    `compose` (text) and `env` (text; None keeps the .env the host has, "" removes it; a line
    `KEY=********` keeps the host's value of KEY). version and restore name a saved version in `arg`.
    Whatever the admin does here ticks the stack (the allowlist is the admin's own act); the agent
    checks it again."""
    a = get(aid)
    if not a or "stacks" not in a.get("caps", []):
        return False, "not allowed", ""
    if not isinstance(name, str) or not TARGET["stacks"].fullmatch(name):
        return False, "a stack's name is lowercase letters, digits, - and _", ""
    if verb not in VERBS["stacks"]:
        return False, "no such action", ""
    if not arg_ok(verb, arg) or (verb in STACK_ARG_VERBS) != bool(arg):
        return False, "not allowed", ""
    files = None
    if verb in STACK_FILE_VERBS:
        if not isinstance(compose, str) or not compose.strip():
            return False, "a compose file is needed", ""
        if len(compose.encode()) > STACK_FILE_MAX or (isinstance(env, str) and len(env.encode()) > STACK_FILE_MAX):
            return False, f"a file is at most {STACK_FILE_MAX >> 10} KiB", ""
        files = json.dumps({"compose": compose, **({"env": env} if isinstance(env, str) else {})}, separators=(",", ":"))
        if len(files.encode()) > P.MAX_SEALED_FILES:
            return False, "the files are too long together", ""
    if name not in a.get("targets", {}).get("stacks", []):
        set_targets(aid, "stacks", sorted(set(a.get("targets", {}).get("stacks", [])) | {name}))
    if not allowed(aid, "stacks", verb, name):
        return False, "not allowed", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    wait = 330 if verb in ("deploy", "redeploy") else 120
    if files is not None:
        return s.ask({"type": "command", "cap": "stacks", "verb": verb, "target": name}, wait, files=files)
    return s.command("stacks", verb, name, wait, arg) if arg else s.command("stacks", verb, name, wait)


CHANGE_LISTS = ("env_unset", "ports", "volumes")


def reconfigure(aid, name, change):
    """(ok, error, output): a container's environment, ports, volumes or restart policy changed. The
    change (`env_set` {NAME: value}, `env_unset`, `ports`, `volumes`, `restart`) travels sealed, like a
    stack's files: a value is where a password lives. The agent checks every part again and makes the
    container again from its own settings (or, for the restart policy alone, changes it live)."""
    if not isinstance(name, str) or not allowed(aid, "docker", "reconfigure", name):
        return False, "not allowed", ""
    if not isinstance(change, dict) or not change or set(change) - {"env_set", "restart", *CHANGE_LISTS}:
        return False, "nothing to change", ""
    env = change.get("env_set", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        return False, "the variables are names and text", ""
    if any(k in change and not (isinstance(change[k], list) and all(isinstance(x, str) for x in change[k])) for k in CHANGE_LISTS):
        return False, "ports, volumes and removed variables are lists of text", ""
    if "restart" in change and not isinstance(change["restart"], str):
        return False, "a restart policy is text", ""
    files = json.dumps({"config": json.dumps(change, separators=(",", ":"))}, separators=(",", ":"))
    if len(files.encode()) > P.MAX_SEALED_FILES:
        return False, "the change is too long", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    return s.ask({"type": "command", "cap": "docker", "verb": "reconfigure", "target": name}, 330, files=files)


def tick_stack(aid, name):
    """A stack the host keeps joins the allowlist when the admin works on it from Servers (the cap is ticked)."""
    a = get(aid)
    if a and "stacks" in a.get("caps", []) and name not in a.get("targets", {}).get("stacks", []):
        set_targets(aid, "stacks", sorted(set(a.get("targets", {}).get("stacks", [])) | {name}))


# --- a stack's git source and its webhook (POST /api/hook/<token> pulls the file and redeploys) ---
#
# The source is {repo, branch, path}; a private repository's token is used for one request and
# never kept, so a webhook only syncs from a source that needs none. The webhook is a secret URL
# per (agent, stack), kept as a hash like a container's; it can only ask for that sync.

def stack_source(aid, name):
    a = get(aid) or {}
    src = (a.get("stack_sources") or {}).get(name)
    return dict(src) if isinstance(src, dict) else None


def set_stack_source(aid, name, src):
    """src = {repo, branch, path} (checked as gitsource checks them), or None to forget it and its webhook."""
    a = get(aid)
    if not a or not isinstance(name, str) or not TARGET["stacks"].fullmatch(name):
        return False, "no such stack"
    sources = dict(a.get("stack_sources") or {})
    hooks = dict(a.get("stack_hooks") or {})
    if src is None:
        sources.pop(name, None)
        hooks.pop(name, None)
    else:
        try:
            gitsource.raw_url(src.get("repo"), src.get("branch") or "main", src.get("path"))
        except ValueError as e:
            return False, str(e)
        sources[name] = {"repo": src["repo"], "branch": src.get("branch") or "main", "path": src["path"]}
    _save(aid, stack_sources=sources, stack_hooks=hooks)
    on_change()
    return True, ""


def new_stack_hook(aid, name):
    """A new secret for a stack that has a source (the old one stops working), or None."""
    a = get(aid)
    if not a or not stack_source(aid, name):
        return None
    token = secrets.token_urlsafe(32)
    _save(aid, stack_hooks=dict(a.get("stack_hooks") or {}, **{name: util.sha256_hex(token)}))
    on_change()
    return token


def sync_stack(aid, name, token=None):
    """(ok, error, output): fetch the stack's file from its source and deploy it (the .env stays)."""
    src = stack_source(aid, name)
    if not src:
        return False, "this stack has no git source", ""
    try:
        compose = gitsource.fetch(src["repo"], src["branch"], src["path"], token)
    except ValueError as e:
        return False, str(e), ""
    return stack(aid, "deploy", name, compose, None)


# ---------------------------------------------------------------------------------------------
# Live streams: a ticked container's log being followed, a shell in one (the terminal capability).
# The output is handed on once and forgotten: it is never logged, never written, and what the
# browser has not fetched is dropped past STREAM_BUFFER. The agent has its own limits (life, idle);
# these are labhud's, for an agent that does not keep them or a page that went away.
# ---------------------------------------------------------------------------------------------

STREAM_KINDS = {"logs": ("docker", "follow", 600), "shell": ("terminal", "shell", 900)}   # kind -> cap, verb, life (s)
STREAMS_PER_AGENT = 2
STREAM_BUFFER = 256 << 10
STREAM_IDLE_POLL = 60        # s: nobody fetching, the page is gone
STREAM_IDLE_INPUT = 180      # s: a shell nobody types in
STREAM_GRACE = 30            # s after the agent's own life, before labhud ends it
STREAM_INPUT_MAX = 16 << 10
_streams = {}                # id -> Stream
_streams_lock = threading.Lock()


class Stream:
    def __init__(self, sid, aid, kind, target, session, who):
        now = time.monotonic()
        self.id, self.aid, self.kind, self.target, self.session, self.who = sid, aid, kind, target, session, who
        self.opened, self.last_poll, self.last_input = now, now, now
        self.data, self.base = bytearray(), 0     # what is not fetched yet, and the offset of its first byte
        self.sent = 0                             # bytes that went through, for the audit line only
        self.end = None                           # why it ended, once it did
        self.cond = threading.Condition()

    def put(self, raw):
        with self.cond:
            self.sent += len(raw)
            self.data += raw
            if len(self.data) > STREAM_BUFFER:     # the page is too slow: the oldest goes
                drop = len(self.data) - STREAM_BUFFER
                del self.data[:drop]
                self.base += drop
            self.cond.notify_all()

    def finish(self, reason):
        with self.cond:
            if self.end is None:
                self.end = str(reason)[:200] or "ended"
            self.cond.notify_all()

    def read(self, after, wait):
        """(bytes from `after` on, the offset after them, why it ended or None): waits up to `wait`
        s for something. What the caller has (`after`) is forgotten."""
        deadline = time.monotonic() + wait
        with self.cond:
            self.last_poll = time.monotonic()
            while True:
                after = max(after, self.base)
                skip = after - self.base
                if skip:
                    del self.data[:skip]
                    self.base = after
                if self.data or self.end is not None:
                    out = bytes(self.data)
                    return out, self.base + len(out), self.end if not out else None
                left = deadline - time.monotonic()
                if left <= 0:
                    return b"", self.base, None
                self.cond.wait(left)


def stream_open(aid, kind, target, cols=80, rows=24, who=""):
    """(ok, error, id): opens a stream on a ticked target. The agent checks the allow list again."""
    cap, verb, _ = STREAM_KINDS.get(kind, (None, None, 0))
    a = get(aid)
    if not cap or not a:
        return False, "no such stream", ""
    if not isinstance(target, str) or not TARGET[cap].fullmatch(target) or not allowed(aid, cap, verb, target):
        return False, "not allowed", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    with _streams_lock:
        if sum(1 for x in _streams.values() if x.aid == aid and x.end is None) >= STREAMS_PER_AGENT:
            return False, "too many open on this agent", ""
        sid = secrets.token_hex(8)
        st = _streams[sid] = Stream(sid, aid, kind, target, s, who)
    s.streams[sid] = st
    msg = {"type": "open", "cmd": sid, "cap": cap, "verb": verb, "target": target}
    if kind == "shell":
        msg.update(cols=min(max(int(cols or 80), 20), 500), rows=min(max(int(rows or 24), 5), 200))
    try:
        s.send(msg)
    except websock.Closed:
        _forget_stream(st)
        return False, "the agent is offline", ""
    return True, "", sid


def _forget_stream(st):
    st.finish("closed")
    st.session.streams.pop(st.id, None)
    with _streams_lock:
        _streams.pop(st.id, None)


def stream_get(sid):
    return _streams.get(sid) if isinstance(sid, str) else None


def stream_send(st, msg):
    """input / resize / close to the agent; False when it is gone."""
    if st.end is not None and msg.get("type") != "close":
        return False
    try:
        st.session.send(dict(msg, cmd=st.id))
        return True
    except websock.Closed:
        st.finish("the agent went offline")
        return False


def stream_input(st, raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > STREAM_INPUT_MAX:
        return False
    st.last_input = time.monotonic()
    return stream_send(st, {"type": "input", "data": base64.b64encode(raw).decode()})


def stream_close(st):
    """Closes it and forgets it; (bytes that went through, seconds it lasted) for the audit line."""
    stream_send(st, {"type": "close"})
    result = (st.sent, int(time.monotonic() - st.opened))
    _forget_stream(st)
    return result


def reap_streams():
    """Called every few seconds: a stream whose life is up, whose page went away, or whose shell
    nobody types in is closed, and a finished one nobody fetches any more is forgotten."""
    now = time.monotonic()
    for st in list(_streams.values()):
        life = STREAM_KINDS[st.kind][2] + STREAM_GRACE
        if st.end is not None:
            if now - st.last_poll > STREAM_IDLE_POLL:
                _forget_stream(st)
        elif now - st.opened > life or now - st.last_poll > STREAM_IDLE_POLL or \
                (st.kind == "shell" and now - st.last_input > STREAM_IDLE_INPUT + STREAM_GRACE):
            stream_close(st)


def clean(aid, what="images"):
    """(ok, error, output): one cleaning of the host's Docker (CLEAN: images, dangling, networks,
    volumes); the inventory is asked again after it."""
    verb = CLEAN.get(what)
    if not verb:
        return False, "clean: " + ", ".join(CLEAN), ""
    result = read(aid, "docker", verb, PRUNE_TARGET, wait=60)
    _inventory.pop(aid, None)
    return result


def update_extras(aid, target, auto=False):
    """What a docker update carries besides its target: ({"hold_critical": true} or {}, the login to
    seal or None). The login only for an agent that understands it (hello.features), only for the
    registry of the image this container runs, and only in this command: labhud's registries."""
    a = get(aid) or {}
    extra, seal = {}, None
    if auto and hold_critical() and "scan" in a.get("caps", []) and "scan" in a.get("found", []):
        extra["hold_critical"] = True
    if "registry-auth" in a.get("features", []):
        x = ((_state.get(aid) or {}).get("containers") or {}).get(target)
        login = registry_auth(x.get("image", "") if isinstance(x, dict) else "")
        if login:
            registry, user, token = login
            seal = (registry, json.dumps({"username": user, "password": token}, separators=(",", ":")))
    return extra, seal


# --- updates (the agent replaces its own binary: labhud-agent's internal/selfupdate) ---
#
# labhud only names the release it pins (agents/agent-release.json) and that build's SHA-256; the
# agent downloads it from its own releases, checks it against the release's SHA256SUMS too, takes
# only a newer version, and never in a container (its image is updated instead).

AUTO_KEY = "agents_auto_update"
UPDATE_WAIT = 110   # the download takes seconds; this stays under the CLI's and a browser's patience


def newer(v, own):
    """Is release v later than own? A dev build (0.1.0-dev.x) is older than its release."""
    def parts(x):
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(-.*)?", x or "")
        return (tuple(int(n) for n in m.groups()[:3]), bool(m.group(4))) if m else None
    def order(t):   # the series wraps (0.1.19, 0.1.2, 0.1.21): a patch of one digit counts as tens, 0.1.2 is after 0.1.19
        return (t[0], t[1], t[2] * 10 if 0 < t[2] < 10 else t[2])
    a, b = parts(v), parts(own)
    if not a or a[1]:
        return False
    if not b:
        return True
    return order(a[0]) > order(b[0]) or (order(a[0]) == order(b[0]) and b[1])


def update_for(a, releases=""):
    """The version this agent can update itself to, or "": a service, older than the pinned release."""
    rel = release(releases)
    v = rel["@AGENT_VERSION@"]
    arch = (a.get("host") or {}).get("arch", "")
    if a.get("install") != "service" or arch not in ("amd64", "arm64") or not rel[f"@AGENT_SHA256_{arch.upper()}@"]:
        return ""
    return v if newer(v, a.get("version", "")) else ""


def update(aid, releases=""):
    """(ok, error): asks the agent to update itself to the pinned release; it then dials again."""
    a = get(aid)
    if not a:
        return False, "no such agent"
    v = update_for(a, releases)
    if not v:
        return False, ("a container is updated by its image" if a.get("install") == "container"
                       else "nothing newer to update to" if a.get("install") == "service"
                       else "this agent is too old to update itself: run its install line again")
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline"
    sha = release(releases)[f"@AGENT_SHA256_{a['host']['arch'].upper()}@"]
    ok, error, _ = s.ask({"type": "update", "version": v, "sha256": sha}, UPDATE_WAIT)
    on_event(f"Agent {a['name']}: {'updating to ' + v if ok else 'update to ' + v + ' failed: ' + error}", not ok)
    return ok, error


def auto_update():
    return bool(store.load(AUTO_KEY)) if store.enabled() else False


def set_auto_update(on):
    store.save(AUTO_KEY, bool(on))


def _send_allow(aid):
    s = _live.get(aid)
    if s:
        try:
            s.send(dict({"type": "allow"}, **allowance(aid)))
        except websock.Closed:
            pass
    on_change()


# --- one connection ---

class Refused(Exception):
    pass


class Session:
    def __init__(self, ws, peer, now=time.time):
        self.ws, self.peer, self.now = ws, peer, now
        self.session = secrets.token_hex(16)
        self.aid = self.key = None
        self.seq_in = self.seq_out = 0
        self.last_heard = time.monotonic()
        self._out = threading.Lock()
        self._waiting = {}     # cmd id -> [Event, (ok, error)]
        self.streams = {}      # stream id -> Stream (open on this connection)
        self._events = []

    def send(self, message):
        with self._out:
            self.seq_out += 1
            self.ws.send(P.make_frame(self.key, P.TO_AGENT, self.session, self.aid,
                                      self.seq_out, int(self.now()), message))

    def bye(self, reason):
        try:
            self.send({"type": "bye", "reason": reason})
        except websock.Closed:
            pass
        self.ws.close(1000, reason)

    def command(self, cap, verb, target, wait, arg=""):
        msg = {"type": "command", "cap": cap, "verb": verb, "target": target}
        if arg:
            msg["arg"] = arg
        return self.ask(msg, wait)

    def ask(self, message, wait, seal=None, files=None):
        """Sends a command or an update and waits for its result: (ok, error, output). `seal`:
        (registry, text) to add as the command's `auth`, sealed for this command of this
        connection (agentproto.seal): a frame is signed, not encrypted."""
        cmd = secrets.token_hex(8)
        slot = [threading.Event(), (False, "no answer from the agent", "")]
        self._waiting[cmd] = slot
        message = dict(message, cmd=cmd)
        if files is not None:   # a stack's files, sealed for this command like a login
            nonce = secrets.token_hex(16)
            message["files"] = {"nonce": nonce, "box": P.seal(self.key, self.session, cmd, nonce, files, P.MAX_SEALED_FILES)}
        if seal:
            nonce = secrets.token_hex(16)
            message["auth"] = {"registry": seal[0], "nonce": nonce,
                               "box": P.seal(self.key, self.session, cmd, nonce, seal[1])}
        try:
            self.send(message)
        except websock.Closed:
            self._waiting.pop(cmd, None)
            return False, "the agent is offline", ""
        slot[0].wait(wait)
        self._waiting.pop(cmd, None)
        return slot[1]

    def _recv(self):
        text = self.ws.recv()
        self.last_heard = time.monotonic()
        return text

    def _frame(self, text, key_for):
        aid, seq, msg = P.read_frame(text, key_for, P.TO_LABHUD, self.session, self.seq_in, int(self.now()))
        self.seq_in = seq
        return aid, msg

    def run(self, set_timeout=lambda s: None):
        """The whole connection. Returns the reason it ended, for the log."""
        try:
            self.ws.send(json.dumps({"type": "welcome", "v": 1, "session": self.session}))
            set_timeout(FIRST_FRAME)
            first = self._recv()
            pending = None
            try:
                msg = json.loads(first)
            except ValueError:
                msg = None
            if isinstance(msg, dict) and msg.get("type") == "pair":
                pending = self._pair(msg)
                first = self._recv()
                aid, hello = self._frame(first, {pending["id"]: pending["key"]}.get)
            else:
                aid, hello = self._frame(first, lambda a: (get(a) or {}).get("key"))
            if hello.get("type") != "hello":
                raise Refused("the first frame is not hello")
            self.aid = aid
            self.key = pending["key"] if pending else get(aid)["key"]
            self._hello(hello, pending)
            set_timeout(IDLE)
            while True:
                aid, msg = self._frame(self._recv(), {self.aid: self.key}.get)
                self._handle(msg)
        except websock.Closed as e:
            return str(e)
        except (Refused, ValueError) as e:
            try:
                self.ws.send(json.dumps({"type": "error", "error": str(e)}))
            except websock.Closed:
                pass
            self.ws.close(1008, "refused")
            return f"refused: {e}"
        except OSError as e:
            return f"connection lost ({e.__class__.__name__})"
        finally:
            self.ws.close()
            for slot in list(self._waiting.values()):
                slot[1] = (False, "the agent went offline", "")
                slot[0].set()
            for st in list(self.streams.values()):
                st.finish("the agent went offline")
            if self.aid:
                with _lock:
                    gone = _live.get(self.aid) is self
                    if gone:
                        del _live[self.aid]
                        _seen[self.aid] = self.now()   # when it went quiet: the uptime counts from here
                if gone:
                    on_change()

    def _pair(self, msg):
        an = msg.get("agent_nonce")
        if not (isinstance(an, str) and P.HEX16.fullmatch(an)):
            raise Refused("bad pairing request")
        secret = _take_code(msg.get("code_id"), self.now())
        if not secret:
            raise Refused("the code is wrong or expired: make a new one in ⚙ > Devices > Agents")
        existing = _load()
        aid = secrets.token_hex(4)
        while aid in existing:
            aid = secrets.token_hex(4)
        sn = secrets.token_hex(16)
        self.ws.send(json.dumps({"type": "challenge", "agent_id": aid, "server_nonce": sn, "tls_fp": tls_fp,
                                 "proof": P.server_proof(secret, self.session, aid, an, sn, tls_fp)}))
        return {"id": aid, "key": P.agent_key(secret, aid, an, sn), "name": msg.get("name")}

    def _hello(self, hello, pending):
        now = int(self.now())
        found = [c for c in hello.get("caps", []) if c in CAPS] if isinstance(hello.get("caps"), list) else []
        host = hello.get("host") if isinstance(hello.get("host"), dict) else {}
        host = {k: str(host.get(k, ""))[:80] for k in ("hostname", "os", "kernel", "arch") if host.get(k)}
        version = str(hello.get("version", ""))[:32]
        install = hello.get("install") if hello.get("install") in ("service", "container") else ""
        features = sorted({f for f in hello.get("features") or [] if f in FEATURES}) if isinstance(hello.get("features"), list) else []
        with _lock:
            agents = _load()
            if pending:
                if len(agents) >= MAX_AGENTS:
                    raise Refused("too many agents")
                name = _unique(clean_name(pending["name"] or host.get("hostname")), agents)
                agents[self.aid] = {"name": name, "key": self.key, "paired": now, "caps": [], "targets": {}}
            elif self.aid not in agents:
                raise Refused("removed")
            agents[self.aid].update(version=version, host=host, found=found, seen=now, install=install, features=features)
            store.save(AGENTS_KEY, agents)
            old = _live.get(self.aid)
            _live[self.aid] = self
            name = agents[self.aid]["name"]
        _seen[self.aid] = now
        if old:
            old.ws.close(1000, "replaced")
        if pending:
            print(f"agent paired: {name} from {self.peer}", flush=True)
            on_event(f"Agent paired: {name}", False)
        else:
            print(f"agent connected: {name} from {self.peer}", flush=True)
        self.send(dict({"type": "allow"}, **allowance(self.aid)))
        on_change()
        if auto_update() and update_for(agents[self.aid]):   # ⚙'s "Update agents automatically"
            threading.Thread(target=update, args=(self.aid,), daemon=True, name="agent-update").start()

    def _handle(self, msg):
        kind = msg["type"]
        now = self.now()
        if now - _seen.get(self.aid, 0) >= SEEN_EVERY:
            _seen[self.aid] = now
            _save(self.aid, seen=int(now))
        if kind == "state":
            _state[self.aid] = dict({k: v for k, v in msg.items() if k != "type"}, _t=int(now))
            on_change()
        elif kind == "result":
            slot = self._waiting.get(msg.get("cmd"))
            if slot:
                err = str(msg.get("error") or "failed")[:200]
                out = msg.get("output") if isinstance(msg.get("output"), str) else ""
                slot[1] = (True, "", out[-MAX_OUTPUT:]) if msg.get("ok") is True else (False, err, "")
                slot[0].set()
        elif kind == "chunk":
            st = self.streams.get(msg.get("cmd"))
            data = msg.get("data")
            if st and isinstance(data, str) and len(data) <= 16 << 10:
                try:
                    st.put(base64.b64decode(data, validate=True))
                except ValueError:
                    pass
        elif kind == "end":
            st = self.streams.get(msg.get("cmd"))
            if st:
                st.finish(msg.get("reason") if isinstance(msg.get("reason"), str) else "ended")
        elif kind == "event":
            self._events = [t for t in self._events if now - t < 60]
            if len(self._events) < EVENTS_PER_MINUTE and isinstance(msg.get("text"), str):
                self._events.append(now)
                _remember(self.aid, msg, now)
                name = (get(self.aid) or {}).get("name", self.aid)
                on_event(f"{name}: {msg['text'][:160]}", msg.get("bad") is True, EVENT_OF.get(msg.get("kind")))
                if msg.get("kind") == "held" and isinstance(msg.get("target"), str):
                    on_held(name, msg["target"][:128], msg["text"][:160])


def pinger(stop):
    """One thread for every connection: a ping each PING_EVERY s, which also finds dead ones."""
    while not stop.wait(PING_EVERY):
        reap_streams()
        for s in list(_live.values()):
            try:
                s.ws.ping()
            except websock.Closed:
                s.ws.close()


# ---------------------------------------------------------------------------------------------
# Installing: the release labhud pins (agents/agent-release.json) and the lines ⚙ shows
# ---------------------------------------------------------------------------------------------

RELEASE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "agent-release.json")
IMAGE = "ghcr.io/mithrandler/labhud-agent:latest"


def release(releases=""):
    """The placeholders /agent/install-agent.sh is served with: the version, where it downloads
    from (`releases`, LABHUD_AGENT_RELEASES, else the file's) and the SHA-256 of each build, which
    stay labhud's own whatever the address. Empty sums: nothing is released yet."""
    try:
        with open(RELEASE_FILE, encoding="utf-8") as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {}
    sums = r.get("sha256") or {}
    return {"@AGENT_VERSION@": str(r.get("version", "")), "@AGENT_RELEASES@": releases or r.get("releases", ""),
            "@AGENT_SHA256_AMD64@": sums.get("amd64", ""), "@AGENT_SHA256_ARM64@": sums.get("arm64", "")}


def install_script(releases=""):
    """/agent/install-agent.sh as labhud serves it: with this labhud's release placeholders filled."""
    with open(os.path.join(os.path.dirname(RELEASE_FILE), "install-agent.sh"), "rb") as f:
        body = f.read()
    for k, v in release(releases).items():
        body = body.replace(k.encode(), v.encode())
    return body


# sudo where there is one to need: root on a Proxmox node has no sudo at all
SUDO = '$([ "$(id -u)" = 0 ] || echo sudo)'


def install_lines(base, code, image="", releases="", own_cert=False):
    """What ⚙ > Devices > Agents > Add an agent shows: the commands that work with this labhud as it is.
    With a release pinned, one curl line downloads and checks the agent, and the image exists.
    Without one the installer cannot download anything, so the line installs a binary you built
    or copied, against the SHA-256 you got where you made it; a container only with `image` set.
    `own_cert`: labhud serves its own certificate, which curl does not trust, so the script is
    fetched with -k and its SHA-256, given in the line, is the check instead."""
    rel = release(releases)
    pinned = [a for a in ("amd64", "arm64") if rel[f"@AGENT_SHA256_{a.upper()}@"]]
    script = f"{base}/agent/install-agent.sh"
    out = {"version": rel["@AGENT_VERSION@"], "released": bool(pinned), "builds": pinned}
    args = f"--url {base} --code {code}" + ("" if pinned else " --binary ./labhud-agent --sha256 YOUR_SHA256")
    if own_cert:
        try:
            sha = hashlib.sha256(install_script(releases)).hexdigest()
        except OSError:
            sha = ""
        out["install"] = (f"curl -fsSLk {script} -o install-agent.sh && echo '{sha}  install-agent.sh' | sha256sum -c "
                          f"&& {SUDO} sh install-agent.sh {args}")
    elif pinned:
        out["install"] = f"curl -fsSL {script} | {SUDO} sh -s -- {args}"
    else:
        out["install"] = f"curl -fsSL {script} -o install-agent.sh && {SUDO} sh install-agent.sh {args}"
    image = image or (IMAGE if pinned else "")
    # host network: Wake-on-LAN and the ARP table; / read-only: the host's sensors and disk; the
    # sockets: Docker and systemd
    out["docker"] = (f"docker run -d --name labhud-agent --restart unless-stopped --network host "
                     f"-v /:/host:ro -v /var/run/docker.sock:/var/run/docker.sock "
                     f"-v /run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket "
                     f"-v labhud-agent:/etc/labhud-agent "
                     f"-e LABHUD_URL={base} -e LABHUD_AGENT_CODE={code} {image}") if image else ""
    return out
