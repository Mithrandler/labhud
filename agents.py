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
    "docker": ("start", "stop", "restart", "update", "logs", "prune", "inspect",
               "inventory", "prune-dangling", "prune-networks", "prune-volumes"),
    "systemd": ("start", "stop", "restart"),
    "host": ("reboot", "shutdown", "update"),
    "wol": ("wake",),
    "pve": ("start", "shutdown", "reboot"),
    "ptero": ("start", "stop", "restart"),
    "stacks": ("deploy", "redeploy", "restart", "down", "show"),
}
# sensors and scan only report: scan (agent with trivy or grype) reads the ticked containers' images
CAPS = ("sensors",) + tuple(VERBS) + ("scan",)
# what an agent may say it understands beyond v1's first messages (hello.features)
FEATURES = ("registry-auth",)
# verbs that read instead of act: never a button or an action name, only read() (for the admin)
READS = {"docker": ("logs", "prune", "inspect", "inventory", "prune-dangling", "prune-networks", "prune-volumes"),
         "stacks": VERBS["stacks"]}   # compose answers with its output: only ⚙ > Containers > Stacks asks
# the host's Docker (its images, volumes and networks), ticked as this target, which is no container
# and takes none of a container's verbs, as the host verbs take no container (the agent checks the same)
PRUNE_TARGET = "images"
HOST_VERBS = ("inventory", "prune", "prune-dangling", "prune-networks", "prune-volumes")
# what cleans what: the name the admin sends -> the verb (api: /api/setup/prune {agent, what})
CLEAN = {"images": "prune", "dangling": "prune-dangling", "networks": "prune-networks", "volumes": "prune-volumes"}
HISTORY_KEY = "agent_history"
HISTORY_KINDS = ("update", "crash", "oom", "loop", "held")
# notify.py's event for each kind (⚙ > Notifications > Events); a failed update is still "update_done"
EVENT_OF = {"update": "update_done", "crash": "container_stopped", "oom": "container_stopped", "loop": "container_stopped"}
HISTORY_PER_TARGET = 10


def acts(cap):
    """The verbs of a capability that are buttons (everything but the reads)."""
    return tuple(v for v in VERBS[cap] if v not in READS.get(cap, ()))

TARGET = {
    "docker": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
    "systemd": re.compile(r"[A-Za-z0-9:_.@-]{1,250}\.(service|timer|socket|target|mount|path)"),
    "host": re.compile(r"host"),
    "wol": re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}"),
    "pve": re.compile(r"[1-9][0-9]{2,8}"),
    "ptero": re.compile(r"[0-9a-f]{8}"),
    "stacks": re.compile(r"[a-z0-9][a-z0-9_-]{0,63}"),
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
    w = store.load(WINDOW_KEY) if store.enabled() else None
    return w if isinstance(w, dict) and "days" in w and "at" in w else dict(WINDOW_DEFAULT)


def set_window(days, at):
    if not (isinstance(days, list) and days and all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days)
            and isinstance(at, str) and HHMM.fullmatch(at)):
        return False
    store.save(WINDOW_KEY, {"days": sorted(set(days)), "at": at})
    return True


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


def run_auto(wait=COMMAND_WAIT):
    """Updates, on every agent online, the auto containers whose registry has a newer image.
    Returns [(agent name, container, ok, error)]."""
    out = []
    for aid, a in _load().items():
        if aid not in _live:
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


def hook(token, now=None):
    """(ok, error, agent name, container) for a webhook call."""
    if not HOOK_TOKEN.fullmatch(token or ""):
        return False, "forbidden", "", ""
    h = util.sha256_hex(token)
    for aid, a in _load().items():
        for target, kept in a.get("hooks", {}).items():
            if hmac.compare_digest(kept, h):
                now = now or time.monotonic()
                if now - _hook_last.get((aid, target), -HOOK_EVERY) < HOOK_EVERY:
                    return False, "too soon: once in 30 s", a["name"], target
                _hook_last[(aid, target)] = now
                ok, error = command(aid, "docker", "update", target)
                return ok, error, a["name"], target
    return False, "forbidden", "", ""


def scheduler(stop, every=30):
    """One thread: opens the window once a day (its days), and says what it started."""
    last = None
    while not stop.wait(every):
        today = due(last=last)
        if not today:
            continue
        last = today
        started = run_auto()
        if started:
            good = [f"{n}/{c}" for n, c, ok, _ in started if ok]
            bad = [f"{n}/{c} ({e})" for n, c, ok, e in started if not ok]
            on_event(f"Scheduled updates: {len(good)} started" + (f", {len(bad)} refused: {', '.join(bad)}" if bad else "")
                     + (f" ({', '.join(good[:6])}{'…' if len(good) > 6 else ''})" if good else ""), bool(bad))


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
    all_targets = dict(a.get("targets", {}), **{cap: sorted(set(targets))})
    if not _save(aid, targets=all_targets):
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


def allowed(aid, cap, verb, target):
    a = get(aid)
    if cap == "docker" and (verb in HOST_VERBS) != (target == PRUNE_TARGET):
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
    return [n for n in _wings(a, "docker", names) if n != PRUNE_TARGET]


def listing():
    """For ⚙ > Devices > Agents: everything but the keys, with what each agent last reported."""
    out = []
    for aid, a in _load().items():
        st = _state.get(aid) or {}
        found = {"docker": _containers(a, sorted(st.get("containers") or {})) + [PRUNE_TARGET],
                 "systemd": sorted(st.get("units") or {}),
                 "pve": sorted(st.get("guests") or {}, key=lambda v: int(v) if v.isdigit() else 0),
                 "ptero": sorted(st.get("servers") or {}), "wol": sorted(st.get("neighbours") or {}),
                 "host": ["host"] if "host" in a.get("found", []) else [], "stacks": sorted(stacks_of(aid))}
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
                    "targets": {c: _wings(a, c, v) for c, v in a.get("targets", {}).items()},
                    "choices": {c: v for c, v in found.items() if c in a.get("found", [])},
                    "labels": labels})
    return sorted(out, key=lambda a: a["paired"])


def snapshot():
    """{name: {online, version, sensors, guests, ...}}: what cards read (`agents.<name>.…`)."""
    out = {}
    for aid, a in _load().items():
        st = {k: v for k, v in (_state.get(aid) or {}).items() if k != "type"}
        if st and aid not in _live:   # its last report, kept: shown dimmed, "not updated since"
            st["stale"] = "the agent is offline"
        out[a["name"]] = dict(st, id=aid, online=aid in _live, seen=int(_seen.get(aid) or a.get("seen", 0)), version=a.get("version", ""),
                              caps=a.get("caps", []), buttons=buttons(aid, a), history=history.get(aid, {}))
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

def command(aid, cap, verb, target, wait=COMMAND_WAIT, auto=False):
    """(ok, error): sends one command and waits for its result. `auto`: labhud's own scheduled
    update (it may be held back for new critical CVEs)."""
    if not allowed(aid, cap, verb, target):
        return False, "not allowed"
    return read(aid, cap, verb, target, wait, auto)[:2]


def read(aid, cap, verb, target, wait=COMMAND_WAIT, auto=False):
    """(ok, error, output): one command and its result's output (a read verb's text, else "")."""
    if not allowed(aid, cap, verb, target):
        return False, "not allowed", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    if cap == "docker" and verb == "update":
        extra, seal = update_extras(aid, target, auto)
        if extra or seal:
            return s.ask(dict({"type": "command", "cap": cap, "verb": verb, "target": target}, **extra), wait, seal)
    return s.command(cap, verb, target, wait)


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
    out = {
        "images": [{"id": s(x.get("id"), 64), "tags": [s(t) for t in (x.get("tags") or [])[:8] if isinstance(t, str)],
                    "size": _count(x.get("size")), "created": _count(x.get("created")), "used": _count(x.get("used")),
                    "dangling": x.get("dangling") is True} for x in rows("images")],
        "volumes": [{"name": s(x.get("name")), "driver": s(x.get("driver"), 40), "size": _count(x.get("size"), -1),
                     "used": _count(x.get("used")), "anonymous": x.get("anonymous") is True} for x in rows("volumes")],
        "networks": [{"name": s(x.get("name")), "driver": s(x.get("driver"), 40), "scope": s(x.get("scope"), 20),
                      "used": _count(x.get("used")), "builtin": x.get("builtin") is True} for x in rows("networks")],
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


def stack(aid, verb, name, compose=None, env=None):
    """(ok, error, output): one verb on a stack. A deploy carries the files, sealed: `compose` (text)
    and `env` (text; None keeps the .env the host has, "" removes it). Whatever the admin does here
    ticks the stack (the allowlist is the admin's own act); the agent checks it again."""
    a = get(aid)
    if not a or "stacks" not in a.get("caps", []):
        return False, "not allowed", ""
    if not isinstance(name, str) or not TARGET["stacks"].fullmatch(name):
        return False, "a stack's name is lowercase letters, digits, - and _", ""
    files = None
    if verb == "deploy":
        if not isinstance(compose, str) or not compose.strip():
            return False, "a compose file is needed", ""
        if len(compose.encode()) > STACK_FILE_MAX or (isinstance(env, str) and len(env.encode()) > STACK_FILE_MAX):
            return False, f"a file is at most {STACK_FILE_MAX >> 10} KiB", ""
        files = json.dumps({"compose": compose, **({"env": env} if isinstance(env, str) else {})}, separators=(",", ":"))
        if len(files.encode()) > P.MAX_SEALED_FILES:
            return False, "the files are too long together", ""
    elif verb not in VERBS["stacks"]:
        return False, "no such action", ""
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
    return s.command("stacks", verb, name, wait)


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
    a, b = parts(v), parts(own)
    if not a or a[1]:
        return False
    if not b:
        return True
    return a[0] > b[0] or (a[0] == b[0] and b[1])


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

    def command(self, cap, verb, target, wait):
        return self.ask({"type": "command", "cap": cap, "verb": verb, "target": target}, wait)

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
