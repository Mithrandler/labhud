"""Paired agents (labhud-agent, Go): one per host, dialling labhud on /api/agent/ws. The protocol
is in docs/agent-protocol.md, its cryptography in agentproto.py.

Pairing follows devices.py: ⚙ > Devices > Agents makes a one-time code, valid PAIR_MINUTES, of which only
code_id and pair_secret are kept (never the code). An agent is saved with its key only after its
first signed frame checks out. Everything lives in the store (LABHUD_DATA); without it there are
no agents.

What an agent may do is kept here: `caps` (ticked in ⚙) and `targets` per capability. A command
leaves only when the agent is connected and both allow it; the agent checks the same again.

The server plugs in through three hooks: on_change() when anything shown changed (online,
state, the list), on_event(text, bad) for the timeline, and tls_fp, its certificate's SHA-256.
"""

import hashlib
import json
import os
import re
import secrets
import threading
import time

import agentproto as P
import devices
import store
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
    "docker": ("start", "stop", "restart", "update", "logs"),
    "systemd": ("start", "stop", "restart"),
    "host": ("reboot", "shutdown", "update"),
    "wol": ("wake",),
    "pve": ("start", "shutdown", "reboot"),
    "ptero": ("start", "stop", "restart"),
}
CAPS = ("sensors",) + tuple(VERBS)
# verbs that read instead of act: never a button or an action name, only read() (for the admin)
READS = {"docker": ("logs",)}


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
}
MAX_TARGETS = 200

on_change = lambda: None             # noqa: E731
on_event = lambda text, bad: None    # noqa: E731
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
    if not store.enabled():
        return None
    now = now or time.time()
    code = secrets.token_urlsafe(18)
    with _lock:
        codes = {h: c for h, c in (store.load(CODES_KEY) or {}).items() if c.get("until", 0) > now}
        while len(codes) >= MAX_CODES:
            codes.pop(min(codes, key=lambda h: codes[h]["until"]))
        codes[P.code_id(code)] = {"until": int(now + PAIR_MINUTES * 60), "secret": P.pair_secret(code)}
        store.save(CODES_KEY, codes)
    return code


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
    taken = {a["name"].lower() for aid, a in agents.items() if aid != own}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def clean_name(name):
    """An agent's name as shown: trimmed, at most 40 plain characters, never empty. No dots: the
    name is a step of a data path (`agents.<name>.sensors`), so "pve2.lan" becomes "pve2-lan"."""
    name = re.sub(r"\s+", " ", str(name or "")).strip().replace(".", "-")[:40]
    return name if devices.NAME.fullmatch(name) else "agent"


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
    """{"caps": [...], "targets": {cap: [...]}}: what the agent may do, as sent to it."""
    a = get(aid) or {}
    caps = [c for c in a.get("caps", []) if c in CAPS]
    return {"caps": caps, "targets": {c: list(a.get("targets", {}).get(c, [])) for c in caps if c in VERBS}}


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
    if s:
        s.bye("removed")
    if a:
        on_change()
    return a


def allowed(aid, cap, verb, target):
    a = get(aid)
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


def listing():
    """For ⚙ > Devices > Agents: everything but the keys, with what each agent last reported."""
    out = []
    for aid, a in _load().items():
        st = _state.get(aid) or {}
        found = {"docker": _wings(a, "docker", sorted(st.get("containers") or {})), "systemd": sorted(st.get("units") or {}),
                 "pve": sorted(st.get("guests") or {}, key=lambda v: int(v) if v.isdigit() else 0),
                 "ptero": sorted(st.get("servers") or {}), "wol": sorted(st.get("neighbours") or {}),
                 "host": ["host"] if "host" in a.get("found", []) else []}
        labels = {"pve": {k: (v or {}).get("name") for k, v in (st.get("guests") or {}).items()},
                  "ptero": {k: (v or {}).get("name") for k, v in (st.get("servers") or {}).items()},
                  "wol": {k: (v or {}).get("ip") for k, v in (st.get("neighbours") or {}).items()}}
        out.append({"id": aid, "name": a["name"], "online": aid in _live, "version": a.get("version", ""),
                    "addr": getattr(_live.get(aid), "peer", "") or "",
                    "host": a.get("host", {}), "paired": a.get("paired", 0), "seen": a.get("seen", 0),
                    "found": a.get("found", []), "caps": a.get("caps", []),
                    "targets": {c: _wings(a, c, v) for c, v in a.get("targets", {}).items()},
                    "choices": {c: v for c, v in found.items() if c in a.get("found", [])},
                    "labels": labels})
    return sorted(out, key=lambda a: a["paired"])


def snapshot():
    """{name: {online, version, sensors, guests, ...}}: what cards read (`agents.<name>.…`)."""
    out = {}
    for aid, a in _load().items():
        st = {k: v for k, v in (_state.get(aid) or {}).items() if k != "type"}
        out[a["name"]] = dict(st, id=aid, online=aid in _live, version=a.get("version", ""),
                              caps=a.get("caps", []), buttons=buttons(aid, a))
    return out


# What a page draws next to one target without a card of its own: a container in a guest's
# panel, a game server's tile. Only ticked targets of ticked capabilities, so a button here is
# always one resolve() accepts.
LISTED = ("docker", "systemd", "ptero")


def buttons(aid, a):
    """{cap: {target: [action names]}} for the ticked targets the pages list themselves."""
    return {cap: {t: [action_name(aid, cap, t, v) for v in acts(cap)] for t in _wings(a, cap, a.get("targets", {}).get(cap, []))}
            for cap in LISTED if cap in a.get("caps", [])}


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
    hits = [t for t in a.get("targets", {}).get(cap, []) if tref(t) == ref]
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

def command(aid, cap, verb, target, wait=COMMAND_WAIT):
    """(ok, error): sends one command and waits for its result."""
    if not allowed(aid, cap, verb, target):
        return False, "not allowed"
    return read(aid, cap, verb, target, wait)[:2]


def read(aid, cap, verb, target, wait=COMMAND_WAIT):
    """(ok, error, output): one command and its result's output (a read verb's text, else "")."""
    if not allowed(aid, cap, verb, target):
        return False, "not allowed", ""
    s = _live.get(aid)
    if not s:
        return False, "the agent is offline", ""
    return s.command(cap, verb, target, wait)


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
        cmd = secrets.token_hex(8)
        slot = [threading.Event(), (False, "no answer from the agent", "")]
        self._waiting[cmd] = slot
        try:
            self.send({"type": "command", "cmd": cmd, "cap": cap, "verb": verb, "target": target})
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
        with _lock:
            agents = _load()
            if pending:
                if len(agents) >= MAX_AGENTS:
                    raise Refused("too many agents")
                name = _unique(clean_name(pending["name"] or host.get("hostname")), agents)
                agents[self.aid] = {"name": name, "key": self.key, "paired": now, "caps": [], "targets": {}}
            elif self.aid not in agents:
                raise Refused("removed")
            agents[self.aid].update(version=version, host=host, found=found, seen=now)
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
                name = (get(self.aid) or {}).get("name", self.aid)
                on_event(f"{name}: {msg['text'][:160]}", msg.get("bad") is True)


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
                          f"&& sudo sh install-agent.sh {args}")
    elif pinned:
        out["install"] = f"curl -fsSL {script} | sudo sh -s -- {args}"
    else:
        out["install"] = f"curl -fsSL {script} -o install-agent.sh && sudo sh install-agent.sh {args}"
    image = image or (IMAGE if pinned else "")
    # host network: Wake-on-LAN and the ARP table; / read-only: the host's sensors and disk; the
    # sockets: Docker and systemd
    out["docker"] = (f"docker run -d --name labhud-agent --restart unless-stopped --network host "
                     f"-v /:/host:ro -v /var/run/docker.sock:/var/run/docker.sock "
                     f"-v /run/dbus/system_bus_socket:/var/run/dbus/system_bus_socket "
                     f"-v labhud-agent:/etc/labhud-agent "
                     f"-e LABHUD_URL={base} -e LABHUD_AGENT_CODE={code} {image}") if image else ""
    return out
