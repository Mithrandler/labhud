#!/usr/bin/env python3
# The wall display: aggregator + server.
#
# What it does, in short: polls every source at its own pace, keeps the last snapshot in memory
# and pushes it to the browser through a single SSE stream. The phone makes no API request and
# sees no key — that is where both the speed and the low power draw come from.
#
# Why SSE and not WebSocket: traffic goes one way, SSE reconnects by itself, works over plain
# HTTP and needs no library. Standard library only, like the rest of the fleet.
#
# Actions are never run here. This server checks that a press comes from its own page (or a paired
# app) and forwards it to the agent (LABHUD_ACTION_URL) with an HMAC signature (LABHUD_ACTION_SECRET)
# the agent checks, so nothing else, not even a container next to this server, can make one.

import builtins
import collections
import hashlib
import hmac
import iconcache
import json
import re
import os
import queue
import secrets
import socket
import threading
import time
import urllib.parse
import updates
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import envfiles  # noqa: F401  (first: LABHUD_*_FILE -> LABHUD_*, before anything reads them)
import managed
import admin
import agents
import cardtype
import routes
import alerting
import cardstate
import config
import devices
import editmode
import events
import lock
import mqtt
import layout
import onboard
import notify
import qr
import sources
import scan
import screen
import store
import thresholds
import websock
from sources import push
from api import audit, tokens

# print() writes the text and the newline separately, so two threads logging at once glued their
# lines together ("kept across restarts in /datawarning: ..."). One lock around each whole line.
_print, _print_lock = builtins.print, threading.Lock()


def _one_line_print(*args, **kw):
    with _print_lock:
        _print(*args, **kw)


builtins.print = _one_line_print


class Server(ThreadingHTTPServer):
    tls = None   # an ssl.SSLContext: HTTPS, and plain HTTP on the same port gets a redirect to it

    def finish_request(self, request, client_address):
        """In the request's own thread: a TLS handshake starts with 0x16. Anything else is plain
        HTTP from an old bookmark, answered with a redirect to https:// on the same port."""
        if self.tls:
            request.settimeout(Handler.timeout)
            try:
                first = request.recv(1, socket.MSG_PEEK)
            except OSError:
                return
            if first != b"\x16":
                return _redirect_to_https(request)
            try:
                request = self.tls.wrap_socket(request, server_side=True)
            except OSError:   # a scanner, a client that refused the certificate: nothing to tell
                return
            try:
                return super().finish_request(request, client_address)
            finally:
                request.close()   # the wrapped socket; the server closes only the one it accepted
        super().finish_request(request, client_address)

    # A browser or a phone that drops its connection mid-request (screen off, tab closed, Wi-Fi
    # switch) is normal: the default printed a full traceback for every one.
    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def _redirect_to_https(sock):
    """301 to the same Host and path over https; only to a name in LABHUD_HOSTS (no open redirect)."""
    try:
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            head += chunk
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        path = parts[1] if len(parts) >= 2 and parts[1].startswith("/") else "/"
        host = next((l.split(":", 1)[1].strip().lower() for l in lines[1:] if l.lower().startswith("host:")), "")
        if host in ALLOWED_HOSTS:
            body = b"labhud answers on https:// on this port.\n"
            sock.sendall(b"HTTP/1.1 301 Moved Permanently\r\nLocation: https://" + host.encode() + path.encode("latin-1")
                         + b"\r\nContent-Type: text/plain\r\nContent-Length: " + str(len(body)).encode()
                         + b"\r\nConnection: close\r\n\r\n" + body)
        else:
            body = b"labhud answers on https:// on this port.\n"
            sock.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)
    except OSError:
        pass


PORT = int(os.environ.get("LABHUD_PORT", "8095"))
# HTTPS on labhud's own port: your certificate (LABHUD_TLS_CERT/KEY), or else labhud's own, made at
# the first start (tlscert.py); plain HTTP only with LABHUD_HTTP=on. Decided when the server starts.
TLS_CERT = os.environ.get("LABHUD_TLS_CERT", "")
TLS_KEY = os.environ.get("LABHUD_TLS_KEY", "")
TLS_LINE = "HTTPS with LABHUD_TLS_CERT" if TLS_CERT else "plain HTTP"
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# The names the display may be requested under ("host:port", as the browser sends them). The
# second layer, after a firewall that lets only the display reach the port: without it, any page
# opened in the display's browser could ask for a name that resolves to this server (DNS rebinding)
# and read the whole snapshot — which holds internal IPs, devices and security data.
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get(
    "LABHUD_HOSTS", f"127.0.0.1:{PORT},localhost:{PORT}").split(",") if h.strip()}
# Where actions go (POST <url><action name>). Empty: action buttons are not shown.
ACTION_URL = os.environ.get("LABHUD_ACTION_URL", "")
# The secret shared with the agent: the browser posts to labhud (/action/<name>) and labhud forwards
# the action with an HMAC of it, which the agent checks. Without it there are no actions at all
# (no buttons), even with LABHUD_ACTION_URL set. See docs/actions.md.
ACTION_SECRET = os.environ.get("LABHUD_ACTION_SECRET", "").encode()
ACTION_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
GAME_ACTION_RE = re.compile(r"game-(start|stop|restart)-[a-z0-9-]+")
# A client opening stream after stream would hold one thread each. The wall needs one.
MAX_SUBSCRIBERS = 8
STREAM_PING = 20   # s: a comment line on a quiet stream, so no proxy cuts it
# How long a configured source may keep failing before the top bar shows a mark: one failed poll
# is often a restart or a slow answer, and the card already says so on its own.
SOURCE_GRACE = int(os.environ.get("LABHUD_SOURCE_GRACE", "300"))
# How long a failing source keeps showing its last good answer, marked "stale", before its cards
# switch to the error. One missed poll (a slow answer, a restart) then changes nothing on screen,
# and a VM that was down stays down instead of becoming "unknown" for one cycle. 0 turns it off.
SOURCE_STALE = int(os.environ.get("LABHUD_SOURCE_STALE", "60"))
VERSION = os.environ.get("LABHUD_VERSION", "dev")
STARTED = int(time.time())

# Demo mode: made-up data for demo/config.toml, no network, no keys, no ping, no actions.
DEMO = os.environ.get("LABHUD_DEMO", "").lower() in ("1", "true", "yes")
if DEMO:
    import demo
    os.environ.setdefault("LABHUD_CONFIG", demo.CONFIG)
    ACTION_URL = ""
    ACTION_SECRET = b""
    notify.URL, notify.MUTED = "", True  # made-up events are not news
    mqtt.URL = ""

STATUS_EVERY = 30  # the ping/TCP checks, handled here: they need the topology
CONFIG_PATH = config.config_path()
# The problems in config.toml since it was last edited; the server keeps the last good version.
CONFIG_ERRORS = []


def auto_guests(topology, px, previous=None):
    """`topology` with a card added to every `guests = "<node>"` group for each guest of that
    node (with `guests_tag`: only those tagged) that has no card anywhere. Recomputed from the
    file's topology on every change, so a deleted guest's card goes away by itself."""
    groups = [g for page in topology["pages"] for g in page["groups"] if g.get("guests")]
    if not groups:
        return topology
    have = {(c["check"][1], int(c["check"][2])) for cs in topology["cards"].values() for c in cs
            if (c.get("check") or [None])[0] == "proxmox"}
    ids = {c["id"] for cs in topology["cards"].values() for c in cs}
    cards = {gid: list(cs) for gid, cs in topology["cards"].items()}
    for g in groups:
        node, tag = g["guests"], g.get("guests_tag")
        answer = (px or {}).get(node) or {}
        if "guests" not in answer:  # the node did not answer: keep the cards it had
            for c in ((previous or {}).get("cards") or {}).get(g["id"], []):
                if c.get("auto") and c["id"] not in ids:
                    cards[g["id"]].append(c)
                    ids.add(c["id"])
            continue
        guests = answer["guests"]
        for vmid, info in sorted(guests.items(), key=lambda kv: int(kv[0])):
            if info.get("template") or (node, int(vmid)) in have or (tag and tag not in (info.get("tags") or [])):
                continue
            cid = re.sub(r"[^A-Za-z0-9_-]", "-", f"auto-{node}-{vmid}")
            if cid in ids:
                continue
            cards[g["id"]].append({"id": cid, "name": info.get("name") or str(vmid),
                                   "check": ["proxmox", node, int(vmid)], "auto": True})
            ids.add(cid)
            have.add((node, int(vmid)))
    return dict(topology, cards=cards)


DISCOVERED = {"id": "discovered", "title": "DISCOVERED"}


def label_cards(topology, every):
    """`topology` with a card for every container that has a `labhud.name` label (agents.<name>.
    containers.<c>.labels), as Homepage does: `labhud.page` / `labhud.group` (an id
    or a title; else the page's first group, or the DISCOVERED page), `labhud.url`,
    `labhud.description`, `labhud.icon`, `labhud.size`. Nothing is written to config.toml: the cards live as long as the labels.
    A container that already has a card of its own (agent + agent_target) is left alone."""
    found = []
    for agent, a in sorted((every or {}).items()):
        if agent.startswith("_") or not isinstance(a, dict):
            continue
        for c, x in sorted((a.get("containers") or {}).items()):
            labels = (x or {}).get("labels") if isinstance(x, dict) else None
            if isinstance(labels, dict) and str(labels.get("labhud.name") or "").strip():
                found.append((agent, c, labels))
    if not found:
        return topology
    have = {(c.get("agent"), c.get("agent_target")) for cs in topology["cards"].values() for c in cs}
    ids = {c["id"] for cs in topology["cards"].values() for c in cs}
    pages = [dict(p, groups=list(p["groups"])) for p in topology["pages"]]
    cards = {gid: list(cs) for gid, cs in topology["cards"].items()}

    def where(page, group):
        p = next((p for p in pages if page and page.lower() in (p["id"].lower(), str(p.get("title", "")).lower())), None)
        if p is None or p.get("games"):
            p = next((p for p in pages if p["id"] == DISCOVERED["id"]), None)
            if p is None:
                p = dict(DISCOVERED, groups=[{"id": "discovered", "title": "Docker"}])
                pages.append(p)
                cards["discovered"] = []
        g = next((g for g in p["groups"] if group and group.lower() in (g["id"].lower(), str(g.get("title", "")).lower())), None)
        if g is None and not p["groups"]:
            g = {"id": f"{p['id']}-docker", "title": "Docker"}
            p["groups"].append(g)
            cards.setdefault(g["id"], [])
        return (g or p["groups"][0])["id"]

    for agent, c, labels in found:
        if (agent, "docker:" + c) in have:
            continue
        cid = re.sub(r"[^A-Za-z0-9_-]", "-", f"docker-{agent}-{c}")[:64]
        if cid in ids:
            continue
        card = {"id": cid, "name": str(labels["labhud.name"])[:40], "agent": agent, "agent_target": "docker:" + c,
                "check": ["agent", agent, "docker", c], "labels": True}
        url = str(labels.get("labhud.url") or "")
        if re.fullmatch(r"https?://[^\s\"'<>]{1,190}", url):
            card["url"] = url
        if labels.get("labhud.description"):
            card["subtitle"] = str(labels["labhud.description"])[:80]
        if config.ICON_RE.match(str(labels.get("labhud.icon") or "")):
            card["icon"] = labels["labhud.icon"]
        if labels.get("labhud.size") in config.CARD_SIZES:
            card["size"] = labels["labhud.size"]
        cards.setdefault(where(labels.get("labhud.page"), labels.get("labhud.group")), []).append(card)
        ids.add(cid)
    return dict(topology, pages=pages, cards=cards)


def _label_ids(topology):
    return sorted((c["id"], c.get("url", ""), c["name"]) for cs in topology["cards"].values() for c in cs if c.get("labels"))


def derive(file_topology, previous=None):
    """What the display gets from config.toml: the guests' cards, the labelled containers' cards,
    the agents' buttons on all of them."""
    t = auto_guests(file_topology, (globals().get("_data") or {}).get("proxmox"), previous)
    t = label_cards(t, (globals().get("_data") or {}).get("agents"))
    return with_icons(with_agents(t))


def _icon_of(card, px, every):
    """What is known of the machine or container behind a card (a Proxmox guest's OS and tags, a
    container's image) and its name, as cardtype.icon_for reads it; "" when it says nothing."""
    check = card.get("check") or []
    os_type, tags, image = None, (), None
    if len(check) >= 3 and check[0] == "proxmox":
        g = (((px.get(check[1]) or {}).get("guests") or {}).get(str(check[2]))) or {}
        os_type, tags = g.get("os"), g.get("tags") or ()
    elif len(check) >= 4 and check[0] == "agent" and check[2] == "docker":
        image = (((every.get(check[1]) or {}).get("containers") or {}).get(check[3]) or {}).get("image")
    elif check and check[0] in ("proxmox", "agent") and not card.get("agent_target"):
        return ""                      # a node or a host: its source's icon (cardtype.icon) is the right one
    return cardtype.icon_for(card.get("name", ""), os=os_type, image=image, tags=tags)


def with_icons(topology):
    """Every card with an `icon` (its own, its source's or its kind's: cardtype.icon), so the
    display and the app draw the same one without guessing twice."""
    try:
        found = {a["name"]: a.get("found", []) for a in agents.listing()} if agents.enabled() else {}
    except Exception:   # the agents' file not readable yet: the kinds are guessed without them
        found = {}
    cards = {}
    px = (globals().get("_data") or {}).get("proxmox") or {}
    every = (globals().get("_data") or {}).get("agents") or {}
    for gid, cs in topology["cards"].items():
        cards[gid] = []
        for c in cs:
            name = c.get("icon") or _icon_of(c, px, every) or cardtype.icon(c, found)
            cards[gid].append(dict(c, icon=name) if name and not c.get("icon") else c)
    return dict(topology, cards=cards)


def with_agents(topology):
    """`topology` with the agents' buttons on the cards they control (agents.card_actions). An
    agent's buttons for a Proxmox guest replace that guest's own vm-<node>-<vmid>-* ones."""
    cards = {}
    for gid, cs in topology["cards"].items():
        cards[gid] = []
        for c in cs:
            extra = agents.card_actions(c)
            if extra:
                own = [a for a in c.get("actions", ())
                       if not (any(x.endswith("-start") and "-pve-" in x for x in extra)
                               and re.fullmatch(r"vm-[A-Za-z0-9]+-\d+-(start|shutdown|reboot)", a))]
                c = dict(c, actions=own + extra)
            cards[gid].append(c)
    return dict(topology, cards=cards)


def _agent_buttons(topology):
    return sorted((c["id"], a) for cs in topology["cards"].values() for c in cs
                  for a in c.get("actions", ()) if a.startswith("agent-"))


def _auto_ids(topology):
    return sorted(c["id"] for cs in topology["cards"].values() for c in cs if c.get("auto"))


def _apply(topology):
    """Everything derived from config.toml, computed again when the file changes."""
    global TOPOLOGY, ACTIVE, INACTIVE, SOURCES, TARGETS, SOURCE_HOST, FILE_TOPOLOGY
    FILE_TOPOLOGY = topology
    cardstate.set_marks(topology)
    topology = derive(topology, globals().get("TOPOLOGY"))
    # Only the sources whose settings are present run; the others are reported as not configured.
    active, inactive = (demo.sources(), {}) if DEMO else sources.load(topology)
    # name -> interval in seconds. The paces follow how fast the measured thing changes, not "as
    # often as possible"; LABHUD_<SOURCE>_EVERY changes one (never below 2 s).
    every = {name: max(2, int(os.environ.get(f"LABHUD_{sources.env_name(name)}_EVERY") or src.every))
             for name, src in active.items()}
    every["status"] = STATUS_EVERY
    # The ping/TCP targets.
    # Only ping/tcp: the state of Proxmox guests comes from the `proxmox` source, it is not measured twice.
    # (Without this filter a ping to the node's name is tried and every VM shows down.)
    targets = {}
    # Source -> the card whose status (ping) says whether its host is up: a card that has a ping/tcp
    # check and shows that source's data. When the host comes back (false -> true), the source is polled
    # again after 15 s and its backoff is reset. Without this, after a host starts in the morning its
    # source keeps "Host is unreachable" from its last try for up to 10 minutes.
    source_host = {}
    for cards in topology["cards"].values():
        for c in cards:
            if c.get("check") and c["check"][0] in ("ping", "tcp"):
                targets[c["id"]] = c["check"]
                paths = [m[0] for m in c.get("metrics", [])] + [c[k] for k in ("list", "torrents") if k in c]
                for src in {p.split(".")[0] for p in paths} & set(active):
                    source_host.setdefault(src, c["id"])
    global CARD_IDS, CARD_NAMES
    CARD_NAMES = {c["id"]: c.get("name", c["id"]) for cards in topology["cards"].values() for c in cards}
    CARD_IDS = set(CARD_NAMES)
    TOPOLOGY, ACTIVE, INACTIVE, SOURCES, TARGETS, SOURCE_HOST = (
        topology, active, inactive, every, targets, source_host)
    LOCK.idle = topology.get("lock", {}).get("idle", lock.IDLE)
    admin.configure(topology.get("admin"))   # session length, trusted proxies, admin networks, OIDC
    notify.configure(topology.get("notify") or {}, topology.get("title"))
    updates.configure(topology.get("update"))
    audit.configure(topology.get("audit"))
    agents.registries = topology.get("registries") or []   # the logins api/registries.py hands out


# The display's lock (lock.py): a PIN, then a ticket that the requests changing something carry.
LOCK = lock.Lock()


if __name__ == "__main__":
    import tlscert
    TLS_CERT, TLS_KEY, TLS_LINE = tlscert.choose(os.environ, store.DIR if store.enabled() else "",
                                                 sorted(ALLOWED_HOSTS), demo=DEMO)
    print(TLS_LINE, flush=True)

# First start, no config.toml yet: the setup page instead (setupmode.py), which writes the files and
# starts this process again as the display.
if __name__ == "__main__" and not DEMO and not os.path.exists(CONFIG_PATH):
    import setupmode
    if setupmode.enabled():
        setupmode.run(PORT, CONFIG_PATH, (TLS_CERT, TLS_KEY))

_apply(config.load(CONFIG_PATH))

_data = {}
_lock = threading.Lock()
# name -> how the last polls of that source went, for /status (never the data itself)
_health = {}
# What changed, newest first: the `events` source (see events.py). The demo starts with a made-up past.
EVENTS = events.Log(demo.events() if DEMO else store.load("events", []))
LOCK.on_block = lambda text: (EVENTS.add(text, True), print(text, flush=True))
# The admin (admin.py): one account with a password and TOTP, for pairing phones and the editor.
ADMIN = admin.Admin()
ADMIN.on_block = LOCK.on_block
# Maintenance set from the display: {card id: until (Unix seconds)}. Windows from config.toml are
# added to it in maintenance_now(); both are kept out of the red, the notifications and the focus.
MAINT = {} if DEMO else {k: v for k, v in store.load("maintenance", {}).items() if v > time.time()}
_maint_lock = threading.Lock()
# Numbers past their critical mark for a while: events and notifications (thresholds.py)
WATCH = thresholds.Watch()
# The [[alert]] rules on hosts (alerting.py): their state, and the lock for the two callers
RULES = alerting.Rules()
_rules_lock = threading.Lock()


# Sparklines: one value a minute for every number in TOPOLOGY["trends"], the last 6 hours, in
# memory (a restart starts empty). Sent on request (/api/history), not over the stream: the
# display asks once a minute, and the stream stays small.
HISTORY_STEP = 60
HISTORY_POINTS = 360
_history = {}
# The long view, for the charts in a card's panel (24 hours, 7 days): the average of every 15
# minutes, 7 days of them. Kept like the short one (LABHUD_DATA), saved once per new point.
LONG_STEP = 900
LONG_POINTS = 7 * 86400 // LONG_STEP
_long = {}
_long_pending = {}   # path -> the minute values since the last long point
# The year: the average of every 2 hours (8 long points), a year of them, kept the same way.
YEAR_STEP = 7200
YEAR_POINTS = 365 * 86400 // YEAR_STEP
_year = {}
_year_pending = {}   # path -> the long points since the last year point


def _value(path, data=None):
    """The number at a data path; [used, total] becomes a percentage."""
    data = _data if data is None else data
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    if isinstance(data, list) and len(data) == 2 and all(isinstance(x, (int, float)) for x in data):
        return round(100 * data[0] / data[1], 1) if data[1] else None
    if isinstance(data, bool) or not isinstance(data, (int, float)):
        return None
    return round(data, 1)


def seed_demo_history():
    """Six hours of made-up past, so the demo shows its sparklines at once. Before the collector
    starts: demo.history() moves the demo's clock back while it runs."""
    paths = TOPOLOGY["trends"]
    rows = demo.history(HISTORY_POINTS, HISTORY_STEP, lambda d: [_value(p, d) for p in paths])
    for i, path in enumerate(paths):
        _history[path] = collections.deque((r[i] for r in rows), maxlen=HISTORY_POINTS)
    rows = demo.history(LONG_POINTS, LONG_STEP, lambda d: [_value(p, d) for p in paths])
    for i, path in enumerate(paths):
        _long[path] = collections.deque((r[i] for r in rows), maxlen=LONG_POINTS)
    rows = demo.history(YEAR_POINTS // 4, YEAR_STEP, lambda d: [_value(p, d) for p in paths])   # 3 months
    for i, path in enumerate(paths):
        _year[path] = collections.deque((r[i] for r in rows), maxlen=YEAR_POINTS)


def restore_history():
    """The sparklines and the uptime kept at the last stop (LABHUD_DATA), with the minutes it was
    down left empty."""
    for name, target, step, points in (("history", _history, HISTORY_STEP, HISTORY_POINTS),
                                       ("history_long", _long, LONG_STEP, LONG_POINTS),
                                       ("history_year", _year, YEAR_STEP, YEAR_POINTS)):
        saved = store.load(name)
        if not saved:
            continue
        gap = min(points, max(0, int((time.time() - saved.get("t", 0)) // step)))
        for path in TOPOLOGY["trends"]:
            values = saved.get("series", {}).get(path)
            if values:
                target[path] = collections.deque(values + [None] * gap, maxlen=points)
    saved = store.load("uptime")
    if saved:
        _restore_uptime(saved)


def _restore_uptime(saved):
    """The minutes since the last save are unknown (labhud was off): appended to the running 15
    minutes that were saved with it, folded like _sample_uptime. A save from before the running
    minutes were kept has none: its time was that of the last point."""
    per = UPTIME_STEP // HISTORY_STEP
    missed = min(UPTIME_POINTS * per, max(0, int((time.time() - saved.get("t", 0)) // HISTORY_STEP)))
    pending = saved.get("pending") or {}
    run = max((len(v) for v in pending.values()), default=0)
    for cid in {c["id"] for c in _checked_cards()}:
        series = collections.deque(saved.get("series", {}).get(cid) or [], maxlen=UPTIME_POINTS)
        minutes = (list(pending.get(cid) or []) + [None] * run)[:run] + [None] * missed
        while len(minutes) >= per:
            series.append(_fold_uptime(minutes[:per]))
            minutes = minutes[per:]
        if series:
            _uptime[cid] = series
        _uptime_pending[cid] = minutes


def sample_history():
    time.sleep(15)  # the first answers of the sources
    while True:
        with _lock:
            for path in TOPOLOGY["trends"]:
                series = _history.get(path)
                if series is None:
                    series = _history[path] = collections.deque(maxlen=HISTORY_POINTS)
                series.append(_value(path))
            for path in set(_history) - set(TOPOLOGY["trends"]):  # gone after a reload
                del _history[path]
            kept = {p: list(s) for p, s in _history.items()}
            long_kept, year_kept = _sample_long()
            _sample_uptime(_data)
            uptime_kept = _uptime_saved()
        if not DEMO:
            store.save("history", {"t": int(time.time()), "series": kept})
            if long_kept is not None:
                store.save("history_long", {"t": int(time.time()), "series": long_kept})
            if year_kept is not None:
                store.save("history_year", {"t": int(time.time()), "series": year_kept})
            store.save("uptime", uptime_kept)   # every minute: the running 15 survive a restart
        check_rules()   # "offline for 5 min" needs the clock, not only an agent's report
        time.sleep(HISTORY_STEP)


def _sample_long():
    """Adds the latest minute to the running 15 minutes; on the 15th, appends their average to the
    long series, and every 8th long point to the year's the same way. Returns (long, year): each
    series when a point was added to it (to be saved), else None. Under _lock."""
    for path in TOPOLOGY["trends"]:
        _long_pending.setdefault(path, []).append(_history[path][-1] if _history.get(path) else None)
    if len(next(iter(_long_pending.values()), [])) * HISTORY_STEP < LONG_STEP:
        return None, None
    for path in TOPOLOGY["trends"]:
        values = [v for v in _long_pending.get(path, []) if v is not None]
        series = _long.setdefault(path, collections.deque(maxlen=LONG_POINTS))
        series.append(round(sum(values) / len(values), 1) if values else None)
    for path in set(_long) - set(TOPOLOGY["trends"]):
        del _long[path]
    _long_pending.clear()
    return {p: list(s) for p, s in _long.items()}, _sample_year()


def _sample_year():
    """The year's series when the latest long point completed 2 hours, else None. Under _lock."""
    for path in TOPOLOGY["trends"]:
        _year_pending.setdefault(path, []).append(_long[path][-1] if _long.get(path) else None)
    if len(next(iter(_year_pending.values()), [])) * LONG_STEP < YEAR_STEP:
        return None
    for path in TOPOLOGY["trends"]:
        values = [v for v in _year_pending.get(path, []) if v is not None]
        _year.setdefault(path, collections.deque(maxlen=YEAR_POINTS)).append(
            round(sum(values) / len(values), 1) if values else None)
    for path in set(_year) - set(TOPOLOGY["trends"]):
        del _year[path]
    _year_pending.clear()
    return {p: list(s) for p, s in _year.items()}


# Uptime: for every card with a check, each minute up / down / off on purpose, folded every 15
# minutes into one number like the long sparklines: the share of known minutes it was up (0..1),
# -1 when it was off on purpose the whole time (schedule, on demand, maintenance), None when
# nothing was known. 7 days of them, kept like history_long. Sent on request (/api/v1/uptime).
UPTIME_STEP = LONG_STEP
UPTIME_POINTS = LONG_POINTS
_uptime = {}
_uptime_pending = {}   # card id -> [1 up, 0 down, 2 off on purpose, None unknown] since the last point


def _checked_cards():
    return [c for cs in TOPOLOGY["cards"].values() for c in cs if c.get("check")]


def _minute_status(c, data):
    st = cardstate.uptime_status(c, data)
    if cardstate.quiet_off(st):
        return 2
    return {True: 1, False: 0}.get(st) if isinstance(st, bool) else None


def _fold_uptime(minutes):
    known = [m for m in minutes if m in (0, 1)]
    if known:
        return round(sum(known) / len(known), 3)
    return -1 if any(m == 2 for m in minutes) else None


def _sample_uptime(data):
    """Under _lock, once a minute. Returns the series when a 15-minute point was added, else None."""
    cards = _checked_cards()
    for c in cards:
        _uptime_pending.setdefault(c["id"], []).append(_minute_status(c, data))
    if max(map(len, _uptime_pending.values()), default=0) * HISTORY_STEP < UPTIME_STEP:
        return None
    ids = {c["id"] for c in cards}
    for cid in ids:
        _uptime.setdefault(cid, collections.deque(maxlen=UPTIME_POINTS)).append(_fold_uptime(_uptime_pending.get(cid, [])))
    for cid in set(_uptime) - ids:   # gone after a reload
        del _uptime[cid]
    _uptime_pending.clear()
    return {k: list(v) for k, v in _uptime.items()}


def _uptime_saved():
    """What LABHUD_DATA keeps: the points, and the minutes of the point still running. Under _lock."""
    return {"t": int(time.time()), "series": {k: list(v) for k, v in _uptime.items()},
            "pending": {k: list(v) for k, v in _uptime_pending.items()}}


def uptime(span=86400):
    """{step, cards: {id: [..]}, percent: {id: 99.4}, coverage: {id: 87.5}} over the last `span`
    seconds. The percent is of what is known; coverage is how much of the span is known at all
    (labhud off, an agent never heard from): 100% up over 2 known hours of 24 is 8.3% covered."""
    keep = max(1, span // UPTIME_STEP)
    with _lock:
        cards = {k: list(v)[-keep:] for k, v in _uptime.items()}
    percent, coverage = {}, {}
    for k, v in cards.items():
        known = [x for x in v if x is not None and x >= 0]
        if known:
            percent[k] = round(100 * sum(known) / len(known), 2)
        coverage[k] = round(100 * sum(x is not None for x in v) / keep, 1)
    return {"step": UPTIME_STEP, "cards": cards, "percent": percent, "coverage": coverage}


def seed_demo_uptime():
    """A week of made-up past for the demo: mostly up, a few short outages, one host off at night."""
    import random
    rnd = random.Random(7)
    for i, c in enumerate(_checked_cards()):
        series = []
        for n in range(UPTIME_POINTS):
            hour = (n * UPTIME_STEP // 3600) % 24
            if i % 7 == 3 and hour < 7:
                series.append(-1)
            elif rnd.random() < 0.002:
                series.append(round(rnd.choice((0.0, 0.4, 0.8)), 3))
            else:
                series.append(1.0)
        _uptime[c["id"]] = collections.deque(series, maxlen=UPTIME_POINTS)


def history(long=False, year=False, keys=None):
    """The kept series. `keys` (a set of paths; ?keys=a&keys=b) limits them to those a screen draws:
    a card's, not the whole lab's."""
    with _lock:
        step, src = (YEAR_STEP, _year) if year else (LONG_STEP, _long) if long else (HISTORY_STEP, _history)
        return {"step": step, "series": {p: list(s) for p, s in src.items() if not keys or p in keys}}


def history_args(query):
    """history()'s arguments from a query string: ?long, ?year, ?keys=… (repeated)."""
    q = urllib.parse.parse_qs(query, keep_blank_values=True)
    return {"long": "long" in q, "year": "year" in q, "keys": set(q.get("keys", [])[:200])}


SPARK_POINTS = 30


def sparks(points=SPARK_POINTS):
    """The last `points` minutes of every number with a trend, for the cards' sparklines in one
    request (/api/v1/sparks): the app draws them without asking /api/v1/history card by card.
    A series with nothing known in that time is left out."""
    with _lock:
        series = {p: list(s)[-points:] for p, s in _history.items()}
    return {"step": HISTORY_STEP, "series": {p: v for p, v in series.items() if any(x is not None for x in v)}}


def actions_on():
    """The config's own buttons (vm-…, wol-…, game-…) need the action agent to go to. An agent's
    buttons (agent-…) do not: they are shown whenever a card has them."""
    return bool(ACTION_URL and ACTION_SECRET)


def _action_allowed(name):
    """Only the actions the config offers: a card's `actions`, or a game server's buttons. An
    agent's action: only a ticked target of a ticked capability (agents.resolve), card or not."""
    if not ACTION_NAME_RE.fullmatch(name):
        return False
    if name.startswith("agent-"):
        return agents.resolve(name) is not None
    if GAME_ACTION_RE.fullmatch(name):
        return any(p.get("games") for p in TOPOLOGY["pages"])
    offered = {a for cards in TOPOLOGY["cards"].values() for c in cards for a in c.get("actions", ())}
    if name in offered:
        return True
    # a choice of an offered action's question ([action.<name>] choices)
    return any(ch["action"] == name for a in offered
               for ch in TOPOLOGY["actions"].get(a, {}).get("choices", ()))


ACTION_RESULTS = {"accepted": "sent", "done": "done", "failed": "no change after 5 min", "refused": "refused"}


def action_text(name):
    """An action in words, for the timeline: "Valheim · START", "web01 · SHUT DOWN"."""
    with _lock:
        data = dict(_data)
    if name.startswith("agent-"):
        return agents.action_text(name) or name
    m = re.fullmatch(r"game-(start|stop|restart)-(.+)", name)
    if m:
        servers = []
        for p in TOPOLOGY["pages"]:
            if p.get("games"):
                node = data
                for part in p["games"].split("."):
                    node = node.get(part) if isinstance(node, dict) else None
                servers += node if isinstance(node, list) else []
        game = next((sv.get("game") or sv.get("name") for sv in servers if str(sv.get("id")) == m.group(2)), m.group(2))
        return f"{game} · {m.group(1).upper()}"
    m = re.fullmatch(r"vm-([A-Za-z0-9]+)-(\d+)-(start|shutdown|reboot)", name)
    if m:
        guest = ((data.get("proxmox") or {}).get(m.group(1)) or {}).get("guests", {}).get(m.group(2)) or {}
        verb = {"start": "START", "shutdown": "SHUT DOWN", "reboot": "REBOOT"}[m.group(3)]
        return f"{guest.get('name') or m.group(2)} · {verb}"
    for cards in TOPOLOGY["cards"].values():
        for c in cards:
            if name in c.get("actions", ()):
                label = (TOPOLOGY["actions"].get(name) or {}).get("label") or name
                return f"{c.get('name', c['id'])} · {label}"
    return name


def maintenance_now(now=None):
    """{card id: until} for every card in maintenance right now, from config.toml and the display."""
    now = now or time.time()
    out = {}
    for w in TOPOLOGY["maintenance"]["windows"]:
        if w["from"] <= now < w["until"]:
            for card in w["cards"]:
                out[card] = max(out.get(card, 0), w["until"])
    with _maint_lock:
        for card, until in MAINT.items():
            if until > now and card in CARD_IDS:
                out[card] = max(out.get(card, 0), int(until))
    return out


def set_maintenance(card, minutes):
    """From the display: `minutes` from now, or 0 to end it (only what the display set)."""
    with _maint_lock:
        if minutes:
            MAINT[card] = int(time.time()) + minutes * 60
        else:
            MAINT.pop(card, None)
        kept = dict(MAINT)
    store.save("maintenance", kept)
    publish_maintenance()


def publish_maintenance():
    now = maintenance_now()
    with _lock:
        before = {k: v for k, v in (_data.get("maintenance") or {}).items() if k != "_t"}
        if before == now and "maintenance" in _data:
            return
        _data["maintenance"] = dict(now, _t=int(time.time()))
        result = _data["maintenance"]
    for card in sorted(set(now) - set(before)):
        EVENTS.add(f"{CARD_NAMES.get(card, card)}: maintenance until {time.strftime('%H:%M', time.localtime(now[card]))}")
    for card in sorted(set(before) - set(now)):
        EVENTS.add(f"{CARD_NAMES.get(card, card)}: maintenance over")
    _publish({"maintenance": result})
    mqtt.update(card_states())
    _log([])


def card_states():
    """{card id: up | down | maintenance | scheduled | on-demand} for every card with a status that
    is known, worked out like the display does (card_status in app.js). For MQTT."""
    maint = maintenance_now()
    with _lock:
        status = _data.get("status") or {}
        px = _data.get("proxmox") or {}
        out = {}
        for cards in TOPOLOGY["cards"].values():
            for c in cards:
                chk = c.get("check")
                if not chk:
                    continue
                if chk[0] == "proxmox":
                    g = ((px.get(chk[1]) or {}).get("guests") or {}).get(str(chk[2]))
                    v = bool(g.get("running")) if isinstance(g, dict) else None
                else:
                    v = status.get(c["id"])
                if v == "scheduled":
                    out[c["id"]] = "scheduled"
                elif v is True:
                    out[c["id"]] = "up"
                elif v is False:
                    out[c["id"]] = ("maintenance" if c["id"] in maint
                                    else "on-demand" if c.get("on_demand") else "down")
    return out


def public_status():
    """What the public page shows: per chosen group, each card's name and state. No address, no
    number, no action: this may face the internet (LABHUD_PUBLIC_PORT)."""
    pub = TOPOLOGY.get("public")
    if not pub:
        return None
    states = card_states()
    titles = {g["id"]: g["title"] for p in TOPOLOGY["pages"] for g in p["groups"]}
    groups = []
    for gid in pub["groups"]:
        items = [{"name": c.get("name", c["id"]), "state": states[c["id"]]}
                 for c in TOPOLOGY["cards"].get(gid, []) if c["id"] in states]
        groups.append({"title": titles.get(gid, gid), "items": items})
    every = [i["state"] for g in groups for i in g["items"]]
    return {"title": pub["title"], "now": int(time.time()), "groups": groups,
            "all_up": all(x in ("up", "scheduled", "on-demand", "maintenance") for x in every)}


class PublicHandler(BaseHTTPRequestHandler):
    """LABHUD_PUBLIC_PORT: the public status page, and nothing else of labhud's."""
    protocol_version = "HTTP/1.1"
    server_version = "labhud"
    timeout = 30
    FILES = {"/": ("public.html", "text/html; charset=utf-8"), "/public.js": ("public.js", "text/javascript; charset=utf-8"),
             "/fonts/roboto-latin-400-normal.woff2": ("fonts/roboto-latin-400-normal.woff2", "font/woff2"),
             "/fonts/roboto-latin-700-normal.woff2": ("fonts/roboto-latin-700-normal.woff2", "font/woff2"),
             "/brand/mark.svg": ("brand/mark.svg", "image/svg+xml")}

    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body, cache="no-cache"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/public":
            data = public_status()
            body = json.dumps(data or {"error": "no [public] in config.toml"}, ensure_ascii=False).encode()
            return self._send(200 if data else 404, "application/json; charset=utf-8", body)
        if path in self.FILES:
            name, ctype = self.FILES[path]
            with open(os.path.join(STATIC, name), "rb") as f:
                return self._send(200, ctype, f.read(), "public, max-age=86400" if name.startswith("fonts/") else "no-cache")
        self._send(404, "text/plain; charset=utf-8", b"not found\n")


def start_public(port):
    httpd = Server(("0.0.0.0", port), PublicHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True, name="public").start()
    print(f"public status page on :{port}" + ("" if TOPOLOGY.get("public") else " (empty: no [public] in config.toml)"), flush=True)


def mqtt_cards():
    return {c["id"]: c.get("name", c["id"]) for cards in TOPOLOGY["cards"].values() for c in cards if c.get("check")}


def watch_maintenance():
    """Windows start and end on their own: checked every 10 s."""
    while True:
        publish_maintenance()
        time.sleep(10)


def sign(name, now=None):
    """The headers that prove an action comes from this labhud: the time, a random nonce (two
    presses in the same second are two actions) and an HMAC-SHA256 of "<time>.<nonce>.<name>"
    with the shared secret. The agent refuses an old time or a signature it has seen."""
    ts, nonce = str(int(now or time.time())), os.urandom(12).hex()
    sig = hmac.new(ACTION_SECRET, f"{ts}.{nonce}.{name}".encode(), hashlib.sha256).hexdigest()
    return {"X-Labhud-Time": ts, "X-Labhud-Nonce": nonce, "X-Labhud-Signature": sig}


def forward_action(name):
    """(status, answer) from the agent."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(ACTION_URL + name, data=b"", method="POST", headers=sign(name))
    try:
        with sources.urlopen(req, 45) as r:
            code, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read()
    except OSError as e:
        return 502, {"ok": False, "error": f"the action agent does not answer ({sources.scrub(e)[:80]})"}
    try:
        answer = json.loads(body or b"{}")
    except ValueError:
        answer = {"ok": 200 <= code < 300}
    return code, answer if isinstance(answer, dict) else {"ok": 200 <= code < 300}


def _log(found):
    """Adds [(text, bad, ref?)] to the history, sends it to the displays and, if set up, to the
    notification webhook (see notify.py). A host in maintenance going down is history, not news.
    An empty list only sends the history as it is (after maintenance_now added to it)."""
    maint = maintenance_now() if found else {}
    for text, bad, *ref in found:
        still_true = None
        # ("status" = went down | "up" | "off" | "rule", card id[, notify's event]): a host's transition,
        # kept with the event; then, for a rule (alerting.py), whether it is urgent (sent in the quiet hours)
        kind, card, *event = ref[0] if ref and ref[0] else (None, None)
        event = event[0] if event else {"status": "host_down", "up": "host_down", "off": "host_down"}.get(kind)
        urgent = ref[1] if len(ref) > 1 else True
        state = {"status": "down", "up": "up", "off": "off"}.get(kind)
        if kind == "status":
            if card in maint:
                EVENTS.add(text + " (maintenance)", False, card=card, state="maintenance")
                mqtt.event(text + " (maintenance)", False)
                continue
            still_true = lambda card=card: (_data.get("status", {}).get(card) is False  # noqa: E731
                                            and card not in maintenance_now())
        EVENTS.add(text, bad, card=card, state=state)
        mqtt.event(text, bad)
        if bad:
            screen.wake()
        notify.submit(TOPOLOGY["title"], text, bad, still_true, card, urgent=urgent, event=event)
    if not DEMO:
        store.save("events", EVENTS.all())
    result = dict(EVENTS.snapshot(), _t=int(time.time()))
    with _lock:
        _data["events"] = result
    _publish({"events": result})
_subscribers = []
_subscribers_lock = threading.Lock()


def _collect(name):
    # A source that depends on a host that is off on schedule is not polled in its window: it
    # would hang until the timeout and report an "outage" every night.
    if sources.in_window(TOPOLOGY["skip_sources"].get(name)):
        return name, {"scheduled": True, "_t": int(time.time())}
    try:
        if DEMO and name == "agents":   # the made-up agent, and any real one paired with the demo
            return name, dict(ACTIVE[name].run(TOPOLOGY), **agents.snapshot(), _t=int(time.time()))
        if name == "status":
            result = (demo.status(TARGETS) if DEMO
                      else sources.host_status(TARGETS, TOPOLOGY["quiet_hours"]))
        else:
            result = ACTIVE[name].run(TOPOLOGY)
        if not isinstance(result, dict):
            result = {"value": result}
    except Exception as e:  # one failed source does not stop the others
        result = {"unavailable": sources.scrub(e)[:160] or type(e).__name__}
    result["_t"] = int(time.time())
    return name, result


def _keep_stale(name, result):
    """A failed poll within SOURCE_STALE of the last good one — or within two of the source's own
    intervals, for a slow one like the weather — gives the last good answer, with `stale` = the
    error. Past that, or with nothing good yet, the failure itself."""
    if "unavailable" not in result or not SOURCE_STALE:
        return result
    window = max(SOURCE_STALE, 2 * SOURCES.get(name, 0))
    with _lock:
        before = _data.get(name)
        last_ok = _health.get(name, {}).get("last_ok")
    if not isinstance(before, dict) or "unavailable" in before or before.get("scheduled") \
            or not last_ok or time.time() - last_ok > window:
        return result
    return dict(before, stale=result["unavailable"])


def _publish(delta):
    """Sends only the sources that changed. A slow subscriber is dropped, it does not block the loop."""
    if not delta:
        return
    message = f"event: delta\ndata: {json.dumps(delta, ensure_ascii=False, separators=(',', ':'))}\n\n"
    with _subscribers_lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


def loop():
    """One thread per source, so a slow one (PBS off, DSM waking up) does not delay the rest."""
    next_run = {name: 0.0 for name in SOURCES}
    signatures = {}
    failures = {}
    previous_status = {}
    running = set()
    running_lock = threading.Lock()

    def run(name):
        try:
            started = time.monotonic()
            _, result = _collect(name)
            took = round(time.monotonic() - started, 2)
            # Progressive backoff on a failed source. Without it, a source that refuses
            # authentication is polled at its normal interval forever — and qBittorrent, for
            # example, bans the IP after too many failed logins, so retrying sustains the very
            # fault it measures. The first retry at the normal interval but no later than a minute
            # (a slow source such as the weather, every 15 minutes, would otherwise stay empty for
            # 10 after one passing 503), then doubled, up to 10 minutes; on the first success it
            # returns to its pace.
            interval = SOURCES.get(name, 60)
            if "unavailable" in result and getattr(ACTIVE.get(name), "backoff", True):
                failures[name] = min(failures.get(name, 0) + 1, 6)
                next_run[name] = time.monotonic() + min(min(interval, 60) * (2 ** (failures[name] - 1)), 600)
            else:
                failures.pop(name, None)
            _log(_note_health(name, result, took, failures.get(name, 0),
                              time.time() + next_run[name] - time.monotonic()))
            result = _keep_stale(name, result)
            if name == "status":
                for source, card in SOURCE_HOST.items():
                    was, now = previous_status.get(card), result.get(card)
                    if was in (False, "scheduled") and now is True and source in failures:
                        failures.pop(source, None)
                        next_run[source] = time.monotonic() + 15
                previous_status.update({k: v for k, v in result.items() if k != "_t"})
            # The signature ignores `_t`: otherwise every source would look changed on every cycle
            # and the whole snapshot would go out over SSE for nothing, dozens of times a minute.
            without_time = {k: v for k, v in result.items() if k != "_t"}
            sig = json.dumps(without_time, sort_keys=True, ensure_ascii=False, default=str)
            with _lock:
                before = _data.get(name)
                _data[name] = result
            _log(events.changes(name, before, result, TOPOLOGY))
            if name == "agents":
                check_rules()
            if not DEMO and "unavailable" not in result:
                every_card = [c for cs in TOPOLOGY["cards"].values() for c in cs]
                past = WATCH.check(every_card, lambda p: thresholds.lookup(_data, p), name,
                                   set(maintenance_now()) | alerting.covered(every_card, TOPOLOGY["alert_rules"]))
                if past:
                    _log(past)
            if signatures.get(name) != sig:
                signatures[name] = sig
                _publish({name: result})
                if name == "proxmox" and _auto_ids(auto_guests(FILE_TOPOLOGY, result, TOPOLOGY)) != _auto_ids(TOPOLOGY):
                    _apply(FILE_TOPOLOGY)  # a guest came or went in a `guests = "<node>"` group
                    print(f"guest cards now: {', '.join(_auto_ids(TOPOLOGY)) or 'none'}", flush=True)
                    _send("config", {"reload": True})
                if name in ("status", "proxmox"):
                    mqtt.update(card_states())
        finally:
            with running_lock:
                running.discard(name)

    while True:
        now = time.monotonic()
        for name, interval in list(SOURCES.items()):
            if now < next_run.setdefault(name, 0.0):
                continue
            with running_lock:
                if name in running:
                    continue  # the previous cycle is still running; do not start a second one
                running.add(name)
            # the normal pace; if the source fails, `run` pushes the next moment further out
            next_run[name] = now + interval
            threading.Thread(target=run, args=(name,), daemon=True).start()
        time.sleep(1)


def snapshot():
    with _lock:
        return dict(_data)


def browser_label(ua):
    """"Chrome on Android", "Firefox on Linux": how a session is told apart in ⚙. Never trusted."""
    ua = ua or ""
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("Brave", "Brave"),
                                           ("Chrome/", "Chrome"), ("Safari/", "Safari")) if key in ua), "A browser")
    system = next((name for key, name in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
                                          ("Windows", "Windows"), ("Mac OS", "macOS"), ("Linux", "Linux")) if key in ua), "")
    return f"{browser} on {system}" if system else browser


def check_rules():
    """The [[alert]] rules (alerting.py) against the agents' numbers: events and notifications, and
    `alerting.active` (what is wrong now, for the bell). On every agent report and once a minute."""
    if "TOPOLOGY" not in globals():
        return
    with _rules_lock:
        with _lock:
            data = dict(_data)
        before = RULES.active()
        cards = [c for cs in TOPOLOGY["cards"].values() for c in cs]
        found = RULES.check(cards, TOPOLOGY["alert_rules"], data.get("agents") or {},
                            set(maintenance_now()), lambda c: cardstate.raw_status(c, data))
        now = RULES.active()
    if found:
        _log(found)
    if now != before or "alerting" not in data:
        result = {"active": now, "_t": int(time.time())}
        with _lock:
            _data["alerting"] = result
        _publish({"alerting": result})


NOTES_MAX = 4000


def fleet_updates(every):
    """`fleet.updates`: every container, on every agent, whose registry has a newer image, as a
    card's list (`list = "fleet.updates"`), host by host; `action` is its UPDATE button when it is
    ticked. `fleet.update_count` for a metric."""
    rows = []
    for name, a in sorted(every.items()):
        if name.startswith("_") or not isinstance(a, dict):
            continue
        btns = (a.get("buttons") or {}).get("docker") or {}
        for c, x in sorted((a.get("containers") or {}).items()):
            if isinstance(x, dict) and (x.get("update") is True or x.get("updating") is True):
                act = next((b for b in btns.get(c, []) if b.endswith("-update")), None)
                rows.append({"name": c, "value": "updating…" if x.get("updating") else name, "host": name,
                             "image": x.get("image", ""), "action": act, "updating": x.get("updating") is True,
                             "scan": cardstate.scan_summary(x)})
    return {"updates": rows, "update_count": len(rows), "_t": int(time.time())}


def publish_agents():
    """The agents' state (agents.py) as one more entry of the snapshot: `agents.<name>.…`. When
    what they may do changed the cards' buttons (a tick in ⚙, a guest moved), the displays reload."""
    # the demo's made-up agent (demo.agents, also polled as a source) beside any real one
    result = dict(demo.agents() if DEMO else {}, **agents.snapshot(), _t=int(time.time()))
    fleet = fleet_updates(result)
    with _lock:
        _data["agents"] = result
        _data["fleet"] = fleet
    _publish({"agents": result, "fleet": fleet})
    fresh = [] if DEMO else notify.new_updates(fleet["updates"])   # "update available"
    if fresh:
        _log([(notify.updates_text(fresh), False, (None, None, "update_available"))])
    check_rules()
    if "TOPOLOGY" in globals():
        new = derive(FILE_TOPOLOGY, TOPOLOGY)
        if _agent_buttons(new) != _agent_buttons(TOPOLOGY) or _label_ids(new) != _label_ids(TOPOLOGY):
            _apply(FILE_TOPOLOGY)
            print(f"agent buttons now on: {', '.join(sorted({c for c, _ in _agent_buttons(TOPOLOGY)})) or 'no card'}; "
                  f"cards from labels: {len(_label_ids(TOPOLOGY))}", flush=True)
            _send("config", {"reload": True})


agents.on_change = publish_agents
agents.on_event = lambda text, bad, event=None: _log([(text, bad, (None, None, event))])
# At most this many agent connections at once, paired or still saying who they are.
_agent_slots = threading.BoundedSemaphore(agents.MAX_AGENTS + 8)


# What each limitable key may do beyond reading (sources.check_rights), checked at start and after
# a reload. A monitoring key that can also stop a VM is the most common avoidable risk.
KEY_CHECKS = {}


def check_keys():
    global KEY_CHECKS
    if DEMO:
        return
    KEY_CHECKS = sources.check_rights(ACTIVE)
    for name, r in sorted(KEY_CHECKS.items()):
        for where, privs in r.get("extra", []):
            print(f"warning: the {name} key ({where}) can do more than read: {', '.join(privs)}."
                  f" A read-only role is enough (docs/security.md).", flush=True)


def _mark_inactive():
    """The snapshot's entries for sources that do not run: "not configured", or gone after a reload."""
    with _lock:
        for name in list(_data):
            if name not in SOURCES and name not in INACTIVE and name not in ("events", "maintenance", "agents"):
                del _data[name]
                _health.pop(name, None)
        for name, needs in INACTIVE.items():
            _data[name] = {"not_configured": True, "needs": needs, "_t": int(time.time())}


def _send(event, payload):
    """One SSE event to every open stream (see _publish for the data itself)."""
    message = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    with _subscribers_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(message)
            except queue.Full:
                _subscribers.remove(q)


def watch_config():
    """Reads config.toml again when it changes. A good file replaces the running one and every
    display reloads; a broken one is kept out, and its problems go to the displays as a banner
    until the file is fixed. No restart either way."""
    global CONFIG_ERRORS
    def stamp():
        # config.toml and every file it includes: an edit to any of them reloads
        out = []
        for path in [CONFIG_PATH] + config.included(CONFIG_PATH):
            try:
                st = os.stat(path)
                out.append((path, st.st_mtime_ns, st.st_size, st.st_ino))
            except OSError:
                out.append((path, None))
        return tuple(out)
    seen = stamp()
    while True:
        time.sleep(3)
        now = stamp()
        if now == seen:
            continue
        seen = now
        # a pushing agent added by `init.py agent`: its key went into .env just before the config
        if envfiles.load_dotenv(envfiles.DOTENV):
            for name in push.reload():
                print(f"pushed source {name}: key read from {envfiles.DOTENV}", flush=True)
        try:
            topology = config.load(CONFIG_PATH)
        except config.ConfigError as e:
            CONFIG_ERRORS = e.problems
            print(f"config.toml not applied, {len(e.problems)} problem(s):\n  " + "\n  ".join(e.problems), flush=True)
            _send("config", {"errors": CONFIG_ERRORS})
            _log([(f"config.toml not applied ({len(e.problems)} problem(s))", True)])
            continue
        _apply(topology)
        _mark_inactive()
        CONFIG_ERRORS = []
        print(f"config.toml reloaded; sources: {', '.join(sorted(ACTIVE)) or 'none'}", flush=True)
        _log([("config.toml reloaded", False)])
        threading.Thread(target=check_keys, daemon=True).start()
        mqtt.announce(mqtt_cards(), TOPOLOGY["title"])
        _send("config", {"reload": True})


def watch_lock():
    """A PIN set or removed outside the page (init.py pin): open screens learn it in a few seconds."""
    while True:
        time.sleep(3)
        if LOCK.outside_change():
            print(f"lock: PIN {'set' if LOCK.is_on() else 'removed'} outside the display", flush=True)
            _send("lock", LOCK.public())


def _note_health(name, result, took, failures, next_at):
    """Records how the poll went; returns the events it makes: a source that has kept failing
    past SOURCE_GRACE (with its host up), and the same source answering again."""
    now = int(time.time())
    found = []
    with _lock:
        h = _health.setdefault(name, {})
        h.update(last_run=now, took=took, failures=failures, next_at=int(next_at))
        if result.get("scheduled"):
            h["state"] = "scheduled"
        elif "unavailable" in result:
            h.update(state="failing", error=result["unavailable"], error_at=now)
            h.setdefault("failing_since", now)
            card = SOURCE_HOST.get(name)
            host_down = card and _data.get("status", {}).get(card) in (False, "scheduled")
            if now - h["failing_since"] >= SOURCE_GRACE and not host_down and not h.get("announced"):
                h["announced"] = True
                found.append((f"{name} not answering: {h['error']}", True))
        else:
            h.update(state="ok", last_ok=now)
            h.pop("failing_since", None)
            if h.pop("announced", None):
                found.append((f"{name} answering again", False))
    return found


def checklist(result):
    """What is set up well and what is left, for the top of /status: [{"item", "ok", "how"}], where
    ok is True, False, or None for "not needed here". From states only, never values."""
    out = []

    def add(item, ok, how):
        out.append({"item": item, "ok": ok, "how": how})

    configured = [n for n in result if result[n]["state"] != "not_configured"]
    add("At least one source set up", bool(configured),
        f"{len(configured)} set up" if configured else "the setup page, or .env.example")
    failing = [n for n in configured if result[n]["state"] == "failing"]
    add("Every source set up answers", not failing, ", ".join(failing) or "all answer")
    limited = {n: k for n, k in KEY_CHECKS.items() if "extra" in k}
    risky = [n for n, k in limited.items() if k["extra"]]
    add("Proxmox/PBS keys can only read", not risky if limited else None,
        "too much: " + ", ".join(risky) if risky else "checked" if limited else "no Proxmox or PBS source")
    unchecked = sorted(w for w, m in sources.TLS_SEEN.items() if m == "unchecked")
    add("Certificates of the services checked", not unchecked if sources.TLS_SEEN else None,
        "LABHUD_PINS for " + ", ".join(unchecked) if unchecked else "pinned or verified" if sources.TLS_SEEN else "no HTTPS source yet")
    default_hosts = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
    add("HTTPS on labhud's port", bool(TLS_CERT) or (None if "LABHUD_HTTP" in TLS_LINE or "demo" in TLS_LINE else False),
        TLS_LINE)
    add("LABHUD_HOSTS names the display", bool(ALLOWED_HOSTS - default_hosts),
        "only localhost: the display gets 421" if not ALLOWED_HOSTS - default_hosts else "set")
    if ACTION_URL:
        add("Actions signed", bool(ACTION_SECRET), "set" if ACTION_SECRET else "actions are off until LABHUD_ACTION_SECRET is set (docs/actions.md)")
    add("Keys kept in files, not in the environment", bool(envfiles.LOADED) or None,
        "LABHUD_*_FILE: " + ", ".join(envfiles.LOADED) if envfiles.LOADED else "optional: LABHUD_*_FILE (docs/security.md)")
    add("History kept across restarts", store.enabled(),
        "in " + str(store.DIR) if store.enabled() else (store.PROBLEM or "LABHUD_DATA + a mounted folder"))
    pushed = sorted(push.KEYS)
    if pushed:
        silent = [n for n in pushed if result.get(n, {}).get("state") != "ok"]
        add("Every pushing agent has reported", not silent, "waiting for " + ", ".join(silent) if silent else f"{len(pushed)} agent(s)")
    return out


def adoption():
    """Proxmox guests with no card, and cards whose guest is gone, for /status. Only nodes that
    answered count: a node that is down says nothing about its guests."""
    with _lock:
        px = dict(_data.get("proxmox") or {})
    have = {}
    for cs in TOPOLOGY["cards"].values():
        for c in cs:
            if (c.get("check") or [None])[0] == "proxmox":
                have[(c["check"][1], int(c["check"][2]))] = c["id"]
    new, gone = [], []
    for node, answer in sorted(px.items()):
        guests = answer.get("guests") if isinstance(answer, dict) else None
        if guests is None:
            continue
        for vmid, info in sorted(guests.items(), key=lambda kv: int(kv[0])):
            if not info.get("template") and (node, int(vmid)) not in have:
                new.append({"node": node, "vmid": int(vmid), "name": info.get("name") or vmid,
                            "type": info.get("type"), "tags": info.get("tags") or []})
        gone += [{"card": cid, "node": n, "vmid": v} for (n, v), cid in sorted(have.items())
                 if n == node and str(v) not in guests]
    return {"new": new, "gone": gone}


def where_shown(name):
    """For a source that stopped answering: what it is and where it shows, so the display can say
    "NAS (Synology DSM) · GENERAL › Storage › NAS · host NAS is up" instead of a bare "synology"."""
    src = ACTIVE.get(name)
    pages = {g["id"]: p["title"] for p in TOPOLOGY["pages"] for g in p["groups"]}
    groups = {g["id"]: g["title"] for p in TOPOLOGY["pages"] for g in p["groups"]}
    shown = []
    for gid, cards in TOPOLOGY["cards"].items():
        for c in cards:
            paths = [m[0] for m in c.get("metrics", [])] + [c[k] for k in ("list", "torrents", "sensors", "panel") if c.get(k)]
            roots = {p.split(":", 1)[-1].split(".")[0] for p in paths}
            subs = {".".join(p.split(":", 1)[-1].split(".")[:2]) for p in paths}
            if name in roots or name in subs:
                shown.append(f"{pages.get(gid, '?')} › {groups.get(gid, gid)} › {c.get('name', c['id'])}")
    host = SOURCE_HOST.get(name)
    with _lock:
        state = _data.get("status", {}).get(host) if host else None
    return {"title": getattr(src, "title", name) or name, "shown": shown,
            "host": CARD_NAMES.get(host, host) if host else None,
            "host_state": {True: "up", False: "down", "scheduled": "off on schedule"}.get(state, "unknown") if host else None,
            "every": SOURCES.get(name)}


def health():
    """What /status shows: how each source is doing, never its data or its settings' values."""
    now = int(time.time())
    with _lock:
        hosts = _data.get("status", {})
        result = {}
        for name in sorted(set(SOURCES) | set(INACTIVE)):
            if name in INACTIVE:
                result[name] = {"state": "not_configured", "needs": INACTIVE[name]}
                continue
            h = dict(_health.get(name, {"state": "waiting"}))
            h["every"] = SOURCES[name]
            card = SOURCE_HOST.get(name)
            if card:
                h["host"] = card
                # The card already shows its host as down: the source's error is the consequence.
                if h["state"] == "failing" and hosts.get(card) in (False, "scheduled"):
                    h["state"] = "host_down"
            h["alarm"] = h["state"] == "failing" and now - h["failing_since"] >= SOURCE_GRACE
            result[name] = h
    with _subscribers_lock:
        displays = len(_subscribers)
    return {
        "version": VERSION, "demo": DEMO, "now": now, "started": STARTED,
        "displays": displays, "max_displays": MAX_SUBSCRIBERS, "actions": "signed" if ACTION_URL and ACTION_SECRET else False,
        "grace": SOURCE_GRACE, "config_errors": CONFIG_ERRORS, "keys": KEY_CHECKS,
        "tls": dict(sources.TLS_SEEN), "secret_files": envfiles.LOADED,
        "store": {"on": store.enabled(), "problem": store.PROBLEM} if store.DIR else {"on": False},
        "mqtt": dict(mqtt.state, on=True, problem=mqtt.problem()) if mqtt.enabled() else {"on": False},
        "maintenance": maintenance_now(),
        "devices": devices.listing(),
        "admin": ADMIN.public(),
        "notify": dict(notify.state, on=notify.enabled(), format=notify.FORMAT, problem=notify.problem())
        if notify.enabled() else {"on": False},
        "problems": [n for n, h in result.items() if h.get("alarm")],
        "where": {n: where_shown(n) for n, h in result.items() if h.get("alarm")},
        "checklist": [] if DEMO else checklist(result),
        "adoption": adoption(),
        "sources": result,
    }


# ---------------------------------------------------------------------------------------------
# The server: static files + the SSE stream + the topology.
# ---------------------------------------------------------------------------------------------

def _csp():
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            f"font-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'; object-src 'none'")


CSP = _csp()

TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".woff2": "font/woff2",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".webmanifest": "application/manifest+json", ".webm": "video/webm", ".mp4": "video/mp4",
    ".txt": "text/plain; charset=utf-8",
}


AGENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents")
AGENT_FILES = {"/agent/install.sh": os.path.join(AGENTS, "install.sh"),
               "/agent/labhud-agent.py": os.path.join(AGENTS, "labhud-agent.py"),
               "/agent/install-agent.sh": os.path.join(AGENTS, "install-agent.sh")}
# Where install-agent.sh downloads labhud-agent from (a mirror, or a forge of your own); the
# checksums stay labhud's own (agents/agent-release.json), whatever the address.
AGENT_RELEASES = os.environ.get("LABHUD_AGENT_RELEASES", "")
# The image `docker run` uses in ⚙ > Devices > Agents. Without a release pinned it is shown only when set here.
AGENT_IMAGE = os.environ.get("LABHUD_AGENT_IMAGE", "")


# ---------------------------------------------------------------------------------------------
# Apps (the LabHUD Android app): a token per paired device (devices.py), the cards' state worked
# out here (cardstate.py), under /api/v1. The contract is in docs/app-api.md.
# ---------------------------------------------------------------------------------------------

API_VERSION = 1


# The `labhud` command (cli.py) runs next to this process (docker exec) and calls the same routes
# as ⚙, as the admin: from loopback only, with a key that is new at every start and kept in
# LABHUD_DATA/cli.json (mode 600), which only this server's own user can read.
CLI_KEY = ""
CLI_FILE = "cli.json"
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def write_cli_key():
    global CLI_KEY
    if not store.enabled():
        return
    key = secrets.token_hex(32)
    host = next((h.strip() for h in os.environ.get("LABHUD_HOSTS", "").split(",")
                 if h.strip() and not h.strip().startswith(("localhost", "127."))), "")
    path = os.path.join(store.DIR, CLI_FILE)
    try:
        fd = os.open(path + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"url": f"{'https' if TLS_CERT else 'http'}://127.0.0.1:{PORT}", "key": key, "host": host}, f)
        os.chmod(path + ".new", 0o600)
        os.replace(path + ".new", path)
        CLI_KEY = key
    except OSError as e:
        print(f"warning: the labhud command will not work: {path}: {e}", flush=True)


def app_state(full=False, role=None):
    with _lock:
        data = dict(_data)
    found = {a["name"]: a.get("found", []) for a in agents.listing()} if agents.enabled() else {}
    return cardstate.state(TOPOLOGY, data, actions_on=actions_on(), full=full, found=found, role=role)


def app_info():
    return {"api": API_VERSION, "version": VERSION, "title": TOPOLOGY.get("title") or "LabHUD", "demo": DEMO,
            "actions": actions_on() or bool(agents.listing()), "maintenance_buttons": TOPOLOGY["maintenance"]["buttons"],
            "pairing": devices.enabled(), "admin": ADMIN.is_on(), "trends": TOPOLOGY.get("trends") or [], "uptime": True}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wall"
    # A client that stops halfway (a body shorter than announced, a TLS handshake never finished)
    # gives up its thread after this long. The SSE stream writes at least every 20 s, so it stays.
    timeout = 60

    def _headers(self, code, ctype, length=None, cache=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # Only this server's own scripts, styles, fonts and media; no frames around it; the
        # browser talks to this server alone (actions included: it forwards them, signed).
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if length is not None:
            self.send_header("Content-Length", str(length))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self._headers(code, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _device(self):
        """(name, how) of the paired device this request comes from, or (None, None). "bearer":
        the app itself; "cookie": the wall page the app opens in a WebView (devices.py)."""
        # One handler serves every request of a kept-alive connection: the answer is per request.
        if getattr(self, "_dev_for", None) is not self.headers:
            self._dev_for, self._dev, self._dev_rec = self.headers, (None, None), None
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                found = devices.lookup(auth[len("Bearer "):].strip())
                if found:
                    self._dev, self._dev_rec = (found[1]["name"], "bearer"), found
            else:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "labhud_device":
                        found = devices.lookup(v)
                        if found:
                            self._dev, self._dev_rec = (found[1]["name"], "cookie"), found
        return self._dev

    def _device_admin(self):
        """The app with its token, signed in as the admin who is the admin now (devices.py), from
        one of [admin] admin_networks."""
        return (self._device()[1] == "bearer" and devices.is_admin(self._dev_rec[1], ADMIN.sign())
                and self._admin_net())

    def _ip(self):
        """The client's address: X-Forwarded-For only from [admin] trusted_proxies (admin.client_ip)."""
        return admin.client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"))

    def _admin_net(self):
        """[admin] admin_networks: the admin signs in and acts only from these (empty = anywhere)."""
        return admin.network_ok(self._ip())

    def _host_ok(self):
        """A name in LABHUD_HOSTS, or a paired device: its token is what DNS rebinding cannot have."""
        return ((self.headers.get("Host") or "").lower() in ALLOWED_HOSTS or self._device()[0] is not None or self._cli()
                or self._token_may("read"))

    # --- API tokens (api/tokens.py) ---

    def _token(self):
        """(id, record) of the API token this request carries (Authorization: Bearer lh_…), or None."""
        if getattr(self, "_tok_for", None) is not self.headers:
            self._tok_for, self._tok = self.headers, None
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer " + tokens.PREFIX):
                self._tok = tokens.lookup(auth[len("Bearer "):].strip(), audit.client_ip(self))
        return self._tok

    def _role(self):
        """viewer / operator / admin: what [roles.<role>] decides this request sees. An API token's
        scope; the labhud command and a phone signed in as the admin are admin; any other paired
        phone only looks (viewer)."""
        if self._cli():
            return "admin"
        tok = self._token()
        if tok:
            return {v: k for k, v in tokens.ROLES.items()}.get(tok[1].get("scope"), "viewer")
        return "admin" if self._device_admin() else "viewer"

    def _token_may(self, needed):
        """An API token whose scope covers `needed` (read < act < admin)."""
        tok = self._token()
        return bool(tok) and tokens.allows(tok[1].get("scope"), needed)

    def _admin_any(self):
        """The admin by any way in, for reading (routes' GET guard "admin"): a browser's ticket, a
        phone signed in as the admin, an admin API token, the labhud command. No PIN: it only reads."""
        return (self._cli() or self._token_may("admin") or self._device_admin()
                or (ADMIN.is_on() and self._admin_ok()))

    def _cli(self):
        """The `labhud` command beside this server (write_cli_key): loopback and the current key."""
        given = self.headers.get("X-Labhud-Cli") or ""
        return bool(CLI_KEY and given and self.client_address[0] in LOOPBACK and hmac.compare_digest(given, CLI_KEY))

    def _who(self):
        if self._cli():
            return "the labhud command"
        if self._token():
            return f"{self.client_address[0]} (token {self._token()[1].get('name', '?')})"
        name, _ = self._device()
        return f"{self.client_address[0]} ({name})" if name else self.client_address[0]

    def do_GET(self):
        # The agent and its installer: public code, no data, fetched by machines that may know
        # labhud under any name (like /api/push), so before the LABHUD_HOSTS check.
        if self.path in AGENT_FILES:
            return self._agent_file(self.path)
        if self.path == "/api/agent/ws":   # an agent's own signature is the check (agents.py)
            return self._agent_ws()
        if not self._host_ok():
            # Text, not JSON: this is what someone sees on their first try on another port or name.
            # Echoing the Host back is safe as text/plain.
            host = (self.headers.get("Host") or "")[:200]
            body = (f"421: labhud does not answer to the name {host!r}.\n"
                    f"Add it to LABHUD_HOSTS (now: {','.join(sorted(ALLOWED_HOSTS))}).\n").encode()
            self._headers(421, "text/plain; charset=utf-8", len(body))
            return self.wfile.write(body)
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/v1/"):
            return self._app_get(path)
        if path == "/api/stream":
            return self._stream()
        if path == "/api/config":
            return self._json(dict(config.public(TOPOLOGY), lock=LOCK.public(), admin=ADMIN.public(),
                                   action_url="/action/" if actions_on() else ""))
        if path == "/api/snapshot":  # useful for debugging and for the check screenshots
            return self._json(snapshot())
        if path == "/api/status":
            return self._json(health())
        if path == "/api/history":   # ?long: 15-minute points over 7 days, ?year: 2-hour ones over a year
            q = self.path.partition("?")[2]
            return self._json(history(**history_args(q)))
        if path == "/api/uptime":    # ?range=7d: a card's uptime in its panel, as /api/v1/uptime gives the app
            return self._json(uptime(7 * 86400 if "range=7d" in self.path.partition("?")[2] else 86400))
        if path == "/api/events":   # every kept event, newest first: the day's timeline
            return self._json({"events": EVENTS.all()})
        if routes.find("GET", path):
            return self._module_route("GET", path)
        if path == "/status":
            path = "/status.html"
        if path == "/edit":
            path = "/edit.html"
        return self._static(path)

    def do_POST(self):
        """Signed actions (LABHUD_ACTION_SECRET): checks that the request comes from labhud's own
        page, then forwards it to the agent with a signature. One answer for every refusal."""
        p = re.fullmatch(r"/api/push/([a-z0-9_]{1,64})", self.path)
        if p:
            return self._push(p.group(1))
        hk = re.fullmatch(r"/api/hook/([A-Za-z0-9_-]{1,64})", self.path)
        if hk:
            return self._hook(hk.group(1))
        if self.path == "/api/v1/pair":
            return self._pair()
        if self.path == "/api/v1/pair-code":
            return self._pair_code()
        if self.path == "/api/v1/unpair":
            return self._unpair()
        if self.path == "/api/v1/login-pair":
            return self._login_pair()
        if self.path in ("/api/v1/admin-login", "/api/v1/admin-logout", "/api/v1/unlock"):
            return self._app_admin(self.path.rsplit("/", 1)[1])
        if self.path == "/api/v1/push":
            return self._app_push()
        ag = re.fullmatch(r"/api/agents/(list|code|caps|targets|remove|update|auto|autoupdate|window|hook|unhook)", self.path)
        if ag:
            return self._agents_admin(ag.group(1))
        st = re.fullmatch(r"/api/setup/(status|take-over|text|save-text|secrets|catalog|try|layout|layout-save|organize|source-save|source-forget|scan|scan-status|alerts|alerts-save|notify-test|logs|inspect|prune|notes)", self.path)
        if st:
            return self._setup(st.group(1))
        ad = re.fullmatch(r"/api/admin/(login|logout|check|sessions|revoke|logout-all)", self.path)
        if ad:
            return self._admin_step(ad.group(1))
        ed = re.fullmatch(r"/edit/api/(load|check|save)", self.path)
        if ed:
            return self._edit(ed.group(1))
        lk = re.fullmatch(r"/api/lock/(unlock|touch|lock|pin)", self.path)
        if lk:
            return self._lock_step(lk.group(1))
        mt = re.fullmatch(r"/api/maintenance/([A-Za-z0-9_.-]{1,64})", self.path)
        if mt:
            return self._maintenance(mt.group(1))
        al = re.fullmatch(r"/api/action-log/([a-z0-9-]{1,64})", self.path)
        if al:
            return self._action_log(al.group(1))
        if routes.find("POST", self.path):
            return self._module_route("POST", self.path)
        m = re.fullmatch(r"/action/([a-z0-9-]{1,64})", self.path)
        if not (m and _action_allowed(m.group(1)) and self._same_page() and self._operator()
                and (m.group(1).startswith("agent-") or (ACTION_URL and ACTION_SECRET))):
            if m:
                audit.record(self, "action", m.group(1), False)
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if m.group(1).startswith("agent-"):
            aid, cap, target, verb = agents.resolve(m.group(1))
            ok, error = agents.command(aid, cap, verb, target)
            code, answer = (200, {"ok": True}) if ok else (502, {"ok": False, "error": error})
        else:
            code, answer = forward_action(m.group(1))
        print(f"action {m.group(1)} from {self._who()}: "
              f"{'ok' if answer.get('ok') else answer.get('error')}", flush=True)
        audit.record(self, "action", m.group(1), bool(answer.get("ok")))
        self._json(answer, code)

    def _module_route(self, method, path):
        """A route from a module in api/ (routes.py), behind its guard."""
        fn, guard, limit = routes.find(method, path)
        body = {}
        if guard in ("manage", "open"):
            body = self._small_json(limit)
            if body is None or (guard == "manage" and not self._may_manage()):
                return self._json({"ok": False, "error": "forbidden"}, 403)
        elif guard == "admin" and not self._admin_any():
            audit.record(self, path.removeprefix("/api/"), "", False)
            return self._json({"ok": False, "error": "forbidden"}, 403)
        try:
            code, answer = fn(self, body)
        except Exception as e:   # a module's bug: logged here, never a stack trace to the page
            print(f"route {method} {path} failed: {e!r}", flush=True)
            return self._json({"ok": False, "error": "failed"}, 500)
        if method == "GET" and code == 303:   # routes.redirect(): OIDC's callback
            self.send_response(303)
            self.send_header("Location", answer["location"])
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._json(answer, code)

    def _same_page(self):
        """The request comes from labhud's own page, under a name in LABHUD_HOSTS, or from the
        app with its token (an app has no Origin; the WebView's page still needs its own)."""
        if self._device()[1] == "bearer" or self._cli() or self._token_may("act"):
            return True
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        return self._host_ok() and origin in (f"http://{host}", f"https://{host}")

    def _unlocked(self):
        """No PIN set, or the request carries a live ticket (lock.py): a display gets one with the
        PIN, the app with /api/v1/unlock. The same PIN for both, and the same 2 minutes."""
        return LOCK.allows(self.headers.get("X-Labhud-Unlock"))

    # --- apps (/api/v1) ---

    def _app_get(self, path):
        if path == "/api/v1/info":
            return self._json(dict(app_info(), **self._app_rights()))
        if path == "/api/v1/state":
            return self._json(dict(app_state(role=self._role()), **self._app_rights()))
        c = re.fullmatch(r"/api/v1/card/([A-Za-z0-9_.-]{1,64})", path)
        if c:
            card = next((x for x in app_state(full=True, role=self._role())["cards"] if x["id"] == c.group(1)), None)
            return self._json(card) if card else self._json({"error": "no such card"}, 404)
        g = re.fullmatch(r"/api/v1/game/([A-Za-z0-9_-]{1,64})", path)
        if g:
            with _lock:
                data = dict(_data)
            found = [x for xs in cardstate.games(TOPOLOGY, data, actions_on(), full=True).values()
                     for x in xs if x["id"] == g.group(1)]
            return self._json(found[0]) if found else self._json({"error": "no such game server"}, 404)
        if path == "/api/v1/history":
            q = self.path.partition("?")[2]
            return self._json(history(**history_args(q)))
        if path == "/api/v1/sparks":
            return self._json(sparks())
        if path == "/api/v1/uptime":
            span = 7 * 86400 if "range=7d" in self.path.partition("?")[2] else 86400
            return self._json(uptime(span))
        if path == "/api/v1/events":
            return self._json({"events": EVENTS.all()})
        if path == "/api/v1/notifications":   # ?after=<id>: the outbox (notify.py), for the app to show
            m = re.search(r"(?:^|&)after=(\d{1,16})", self.path.partition("?")[2])
            nt = TOPOLOGY.get("notify") or {}
            return self._json({"via": notify.effective_via(), "quiet": list(nt["quiet"]) if nt.get("quiet") else None,
                               "notifications": notify.outbox(int(m.group(1)) if m else 0)})
        if path == "/api/v1/stream":
            return self._app_stream()
        if path == "/api/v1/devices":   # ⚙ lists them; names and times, no secrets, but not for anyone
            if not (self._cli() or self._device()[1] == "bearer" or (ADMIN.is_on() and self._admin_ok())):
                return self._json({"error": "forbidden"}, 403)
            return self._json({"devices": devices.listing(ADMIN.sign()), "pairing": devices.enabled()})
        if routes.find("GET", path):   # /api/v1/audit (api/audit.py)
            return self._module_route("GET", path)
        return self._json({"error": "not found"}, 404)

    def _app_rights(self):
        """What this app may do: device_admin (signed in as the admin), pin (a PIN is set, so it
        needs /api/v1/unlock before a button), unlocked (its ticket is live)."""
        return {"device_admin": self._device_admin(), "pin": LOCK.is_on(),
                "unlocked": self._device_admin() and self._unlocked()}

    def _app_admin(self, step):
        """The app with its token: admin-login {user, password, code} signs this phone in as the
        admin; admin-logout signs it out; unlock {pin} -> {ticket} for a phone signed in, when the
        display has a PIN (the ticket goes in X-Labhud-Unlock, like a display's). Slowed like the
        display's own sign-in and PIN: the same counters."""
        body = self._small_json()
        if body is None or self._device()[1] != "bearer":
            return self._json({"ok": False, "error": "forbidden"}, 403)
        did, name = self._dev_rec[0], self._dev_rec[1]["name"]
        if step == "admin-logout":
            devices.set_admin(did, None)
            LOCK.close(self.headers.get("X-Labhud-Unlock"))
            print(f"admin: phone {name} signed out from {self.client_address[0]}", flush=True)
            return self._json({"ok": True, **self._app_rights_after(False)})
        if step == "admin-login":
            if not self._admin_net():
                return self._json({"ok": False, "error": "forbidden"}, 403)
            if not ADMIN.is_on():
                return self._json({"ok": False, "error": "this LabHUD has no admin to sign in as"}, 403)
            right, wait = ADMIN.check(body.get("user"), body.get("password"), body.get("code"))
            admin.told(body.get("user"), "phone", right, self.client_address[0], name)
            if not right:
                print(f"admin: wrong sign-in from phone {name} at {self.client_address[0]}", flush=True)
                return self._json({"ok": False, "error": "wrong user, password or code", "wait": wait}, 403)
            devices.set_admin(did, ADMIN.sign())
            print(f"admin: phone {name} signed in from {self.client_address[0]}", flush=True)
            _log([(f"Admin signed in on the phone {name}", False)])
            return self._json({"ok": True, **self._app_rights_after(True)})
        if not self._device_admin():
            return self._json({"ok": False, "error": "sign in as the admin first"}, 403)
        if not LOCK.is_on():
            return self._json({"ok": True, "ticket": None, "idle": LOCK.idle})
        ticket, wait = LOCK.unlock(body.get("pin"))
        if not ticket:
            print(f"lock: wrong PIN from phone {name} at {self.client_address[0]}", flush=True)
            return self._json({"ok": False, "error": "wrong PIN", "wait": wait}, 403)
        return self._json({"ok": True, "ticket": ticket, "idle": LOCK.idle})

    def _app_push(self):
        """{endpoint}: this phone's UnifiedPush endpoint ("" forgets it), woken with no news in it
        when a notification is due (notify.wake_phones). Any paired phone: it only learns that
        there is something to read, which it may read anyway."""
        body = self._small_json()
        if body is None or self._device()[1] != "bearer" or not isinstance(body.get("endpoint", ""), str):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        try:
            if not devices.set_push(self._dev_rec[0], body.get("endpoint", "")):
                return self._json({"ok": False, "error": "forbidden"}, 403)
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        print(f"push: phone {self._dev_rec[1]['name']} " + ("left an endpoint" if body.get("endpoint") else "forgot its endpoint"),
              flush=True)
        return self._json({"ok": True})

    @staticmethod
    def _app_rights_after(admin):
        return {"device_admin": admin, "pin": LOCK.is_on(), "unlocked": admin and not LOCK.is_on()}

    def _small_json(self, limit=1024):
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            return None
        if not (0 <= length <= limit) or not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def _pair(self):
        """{code, name} -> {token, name}: the one-time code from init.py pair or the display's ⚙.
        Under any Host, like /api/push: the phone may know labhud by its VPN address, and the code
        is the check. One answer for every refusal."""
        body = self._small_json()
        token, name = devices.pair(body.get("code"), body.get("name")) if body else (None, None)
        if not token:
            print(f"pairing refused from {self.client_address[0]}", flush=True)
            return self._json({"ok": False, "error": "the code is wrong or expired: make a new one"}, 403)
        print(f"device paired: {name} from {self.client_address[0]}", flush=True)
        _log([(f"Phone paired: {name}", False)])
        self._json({"ok": True, "token": token, "name": name, "info": app_info()})

    def _pair_code(self):
        """The display's "Pair a phone" (⚙): from labhud's own page, unlocked. Answers the link and
        its QR code as SVG; the link uses the address the display itself opened."""
        body = self._small_json()
        if body is None or not self._may_manage():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not devices.enabled():
            return self._json({"ok": False, "error": "pairing needs LABHUD_DATA (a folder labhud can write)"}, 409)
        code = devices.new_code(body.get("name") or "")
        host = (self.headers.get("Host") or "").lower()
        link = devices.pair_link(f"{'https' if TLS_CERT else 'http'}://{host}", code, TLS_CERT)
        print(f"pairing code made on the display from {self.client_address[0]}", flush=True)
        self._json({"ok": True, "link": link, "svg": qr.svg(link), "minutes": devices.PAIR_MINUTES})

    def _admin_ok(self):
        return self._admin_net() and (ADMIN.valid(self.headers.get("X-Labhud-Admin")) or self._wall_admin())

    def _wall_admin(self):
        """The wall page in the app's Wall mode (the labhud_device cookie) of a phone signed in as
        the admin: the admin too, without a second sign-in, but only from labhud's own page, and
        the PIN still holds (_unlocked), so the wall goes back to only showing after 2 minutes."""
        return (self._device()[1] == "cookie" and ADMIN.is_on() and self._same_page()
                and devices.is_admin(self._dev_rec[1], ADMIN.sign()))

    def _operator(self):
        """The cards' buttons and maintenance: a display or a phone signed in as the admin, and
        unlocked when there is a PIN. Without an admin, nobody: every screen only shows. And an API
        token of scope act or admin: the PIN guards a screen, a script has none."""
        if self._token_may("act"):
            return True
        if self._device()[1] == "bearer":
            return self._device_admin() and self._unlocked()
        return ADMIN.is_on() and self._admin_ok() and self._unlocked()

    def _may_manage(self):
        """_may_manage_now(), heard once per request by the audit log (api/audit.py)."""
        ok = self._may_manage_now()
        audit.heard(self, ok)
        return ok

    def _may_manage_now(self):
        """⚙ on the server's side (phones, agents, the PIN): from labhud's own page signed in as
        the admin and unlocked (a screen left signed in goes back to only showing), or a phone
        signed in as the admin and unlocked (a stolen phone still needs the PIN, and the admin
        signs it out from any screen). And the `labhud` command (_cli), and an admin API token."""
        if self._cli() or self._token_may("admin"):
            return True
        if self._device()[1] == "bearer":
            return self._device_admin() and self._unlocked()
        return self._same_page() and ADMIN.is_on() and self._admin_ok() and self._unlocked()

    def _admin_step(self, step):
        """login {user, password, code} -> {ticket}; logout; check -> {user, id}; sessions;
        revoke {id}; logout-all. From labhud's own page, JSON, small. One answer for every refusal."""
        body = self._small_json()
        if (body is None or not self._same_page() or self._device()[1] == "bearer" or not ADMIN.is_on()
                or not self._admin_net()):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if step == "login":
            ticket, wait = ADMIN.login(body.get("user"), body.get("password"), body.get("code"),
                                       self._ip(), browser_label(self.headers.get("User-Agent")))
            if not ticket:
                print(f"admin: wrong sign-in from {self.client_address[0]}", flush=True)
                return self._json({"ok": False, "error": "wrong user, password or code", "wait": wait}, 403)
            print(f"admin: signed in from {self.client_address[0]}", flush=True)
            _log([(f"Admin signed in from {self.client_address[0]}", False)])
            return self._json({"ok": True, "ticket": ticket, "idle": ADMIN.idle, "user": ADMIN.user()})
        if step == "logout":
            ADMIN.close(self.headers.get("X-Labhud-Admin"))
            if self._wall_admin():   # Wall mode is the admin because its phone is: sign the phone out
                devices.set_admin(self._dev_rec[0], None)
                print(f"admin: phone {self._dev[0]} signed out from its Wall mode", flush=True)
            return self._json({"ok": True})
        current = ADMIN.session_id(self.headers.get("X-Labhud-Admin")) or (
            f"phone:{self._dev[0]}" if self._wall_admin() else None)
        if not current:
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if step == "check":
            return self._json({"ok": True, "user": ADMIN.user(), "id": current, "idle": ADMIN.idle})
        if step == "sessions":
            return self._json({"ok": True, "sessions": ADMIN.sessions(), "current": current, "idle": ADMIN.idle,
                               "phones": [d for d in devices.listing(ADMIN.sign()) if d["admin"]]})
        if step == "revoke":
            ok = ADMIN.revoke(body.get("id"))
            print(f"admin: a session signed out from {self.client_address[0]}", flush=True)
            return self._json({"ok": ok}, 200 if ok else 404)
        if step == "logout-all":
            ADMIN.close_all()
            print(f"admin: every session signed out from {self.client_address[0]}", flush=True)
            _log([("Admin signed out everywhere", False)])
            return self._json({"ok": True})

    def _login_pair(self):
        """{user, password, code, name} -> {token, name}: pairing from the app by signing in as the
        admin, without a QR code. Under any Host, like /api/v1/pair; slowed like every sign-in."""
        body = self._small_json()
        if body is None or not self._admin_net():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not ADMIN.is_on() or not devices.enabled():
            return self._json({"ok": False, "error": "this LabHUD has no admin to sign in as"}, 403)
        right, wait = ADMIN.check(body.get("user"), body.get("password"), body.get("code"))
        admin.told(body.get("user"), "pair", right, self.client_address[0], self.headers.get("User-Agent", ""))
        if not right:
            print(f"admin: wrong sign-in to pair from {self.client_address[0]}", flush=True)
            return self._json({"ok": False, "error": "wrong user, password or code", "wait": wait}, 403)
        token, name = devices.pair(devices.new_code(), body.get("name"), admin=ADMIN.sign())
        if not token:
            return self._json({"ok": False, "error": "too many paired phones"}, 409)
        print(f"device paired by the admin: {name} from {self.client_address[0]}", flush=True)
        _log([(f"Phone paired: {name} (admin sign-in)", False)])
        self._json({"ok": True, "token": token, "name": name, "info": app_info()})

    def _unpair(self):
        """{name}: the display's ⚙ removes a paired phone, whose token stops working at once. From
        labhud's own page (also in the app's wall mode), unlocked; never with the app's own token
        (a stolen phone cannot lock the others out)."""
        body = self._small_json()
        name = body.get("name") if body else None
        if not isinstance(name, str) or not self._may_manage():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not devices.unpair(name):
            return self._json({"ok": False, "error": "no such device"}, 404)
        print(f"device unpaired: {name} from the display at {self.client_address[0]}", flush=True)
        _log([(f"Phone unpaired: {devices.clean_name(name)}", False)])
        self._json({"ok": True})

    def _agent_ws(self):
        """An agent's WebSocket (docs/agent-protocol.md). A browser always sends Origin with a
        WebSocket and an agent never does: refusing it keeps every web page off this door."""
        key = websock.handshake_ok(self.headers)
        if self.headers.get("Origin") or not key:
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not agents.enabled():
            return self._json({"ok": False, "error": "agents need LABHUD_DATA (a folder labhud can write)"}, 409)
        if not _agent_slots.acquire(blocking=False):
            return self._json({"ok": False, "error": "too many agent connections"}, 503)
        try:
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", websock.accept_key(key))
            self.end_headers()
            self.wfile.flush()
            sock = self.connection
            conn = websock.Conn(self.rfile, self.wfile, kill=lambda: sock.shutdown(socket.SHUT_RDWR))
            why = agents.Session(conn, self.client_address[0]).run(set_timeout=sock.settimeout)
            print(f"agent connection from {self.client_address[0]} ended: {why}", flush=True)
        finally:
            _agent_slots.release()
            self.close_connection = True

    def _agents_admin(self, op):
        """⚙ > Devices > Agents: the list, a pairing code, what each agent may do, removing one. From labhud's
        own page, as the admin when there is one (like pairing a phone). One answer for every refusal."""
        body = self._small_json(64 * 1024)
        if body is None or not self._may_manage():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if DEMO and op == "list":   # the demo's made-up agents first, then any real one paired with it
            made_up = [dict(r, update=agents.update_for(r, AGENT_RELEASES)) for r in demo.agent_rows()]
            return self._json({"ok": True, "agents": made_up + (agents.listing() if agents.enabled() else []),
                               "caps": list(agents.CAPS), "verbs": {c: list(v) for c, v in agents.VERBS.items()},
                               "release": agents.release(AGENT_RELEASES)["@AGENT_VERSION@"],
                               "auto_update": agents.auto_update() if agents.enabled() else False,
                               "window": agents.window()})
        if DEMO and op not in ("list", "code"):
            if op == "update":
                body = dict(body, version=agents.release(AGENT_RELEASES)["@AGENT_VERSION@"])
            done = demo.agent_change(body.get("id"), op, body, agents.CAPS, agents.VERBS)
            if done is not None:
                return self._json({"ok": True} if done else {"ok": False, "error": "not a valid choice in the demo"}, 200 if done else 400)
        if not agents.enabled():
            return self._json({"ok": False, "error": "agents need LABHUD_DATA (a folder labhud can write)"}, 409)
        if op == "list":
            return self._json({"ok": True, "agents": agents.listing(), "caps": list(agents.CAPS),
                               "verbs": {c: list(v) for c, v in agents.VERBS.items()},
                               "release": agents.release(AGENT_RELEASES)["@AGENT_VERSION@"], "auto_update": agents.auto_update(),
                               "window": agents.window()})
        if op == "window":   # when the containers ticked "on their own" are updated
            if not agents.set_window(body.get("days"), body.get("at")):
                return self._json({"ok": False, "error": "days: 0 (Monday) to 6; at: HH:MM"}, 400)
            print(f"agents: update window {agents.window()} from {self.client_address[0]}", flush=True)
            return self._json({"ok": True, "window": agents.window()})
        if op == "auto":   # ⚙'s "Update agents automatically": an older agent updates itself when it dials in
            agents.set_auto_update(body.get("on") is True)
            print(f"agents: automatic updates {'on' if body.get('on') is True else 'off'} from {self.client_address[0]}", flush=True)
            return self._json({"ok": True, "auto_update": agents.auto_update()})
        if op == "code":
            code = agents.new_code()
            host = (self.headers.get("Host") or "").lower()
            base = f"{'https' if TLS_CERT else 'http'}://{host}"
            print(f"agent pairing code made from {self.client_address[0]}", flush=True)
            return self._json({"ok": True, "code": code, "minutes": agents.PAIR_MINUTES, "url": base,
                               **agents.install_lines(base, code, AGENT_IMAGE, AGENT_RELEASES,
                                                       own_cert=bool(TLS_CERT) and not os.environ.get("LABHUD_TLS_CERT"))})
        aid = body.get("id")
        if not isinstance(aid, str) or not agents.get(aid):
            return self._json({"ok": False, "error": "no such agent"}, 404)
        name = agents.get(aid)["name"]
        if op == "caps":
            ok = agents.set_caps(aid, body.get("caps"))
            what = f"capabilities {body.get('caps')}"
        elif op == "targets":
            ok = agents.set_targets(aid, body.get("cap"), body.get("targets"))
            what = f"{body.get('cap')} targets {body.get('targets')}"
        elif op in ("hook", "unhook"):   # a container's webhook URL: shown once, kept as a hash
            target = str(body.get("target") or "")
            if op == "unhook":
                ok, what = agents.remove_hook(aid, target), f"webhook of {target} removed"
            else:
                token = agents.new_hook(aid, target)
                if not token:
                    return self._json({"ok": False, "error": "tick the container under Docker first"}, 400)
                host = (self.headers.get("Host") or "").lower()
                print(f"agent {name}: webhook for {target} made from {self.client_address[0]}", flush=True)
                return self._json({"ok": True, "url": f"{'https' if TLS_CERT else 'http'}://{host}/api/hook/{token}"})
        elif op == "autoupdate":
            ok = agents.set_auto(aid, body.get("targets"))
            what = f"containers updated on their own {body.get('targets')}"
        elif op == "update":
            ok, error = agents.update(aid, AGENT_RELEASES)
            print(f"agent {name}: update from {self.client_address[0]}: {'ok' if ok else error}", flush=True)
            return self._json({"ok": ok, **({} if ok else {"error": error})}, 200 if ok else 409)
        else:
            agents.remove(aid)
            print(f"agent removed: {name} from {self.client_address[0]}", flush=True)
            _log([(f"Agent removed: {name}", False)])
            return self._json({"ok": True})
        if not ok:
            return self._json({"ok": False, "error": "not a valid choice"}, 400)
        print(f"agent {name}: {what} from {self.client_address[0]}", flush=True)
        self._json({"ok": True})

    def _setup(self, op):
        """⚙ > Setup and ⚙ > Advanced (managed.py): the config labhud keeps in LABHUD_DATA and the
        keys typed into the page. Only for whoever may manage; values of keys never go out."""
        body = self._small_json(256 * 1024)
        if body is None or not self._may_manage():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        who = self._who()
        try:
            if op == "status":
                return self._json({"ok": True, **managed.status(), "path": CONFIG_PATH})
            if op == "catalog":
                return self._json({"ok": True, "sources": onboard.catalog()})
            if op == "try":
                values = body.get("values") if isinstance(body.get("values"), dict) else {}
                name = str(body.get("name") or "")
                item = next((c for c in onboard.catalog() if c["name"] == name), None)
                # what is left empty in the form is tried with what is set (a key the page never sees)
                kept = {f["name"]: os.environ.get(f["name"], "") for f in (item or {}).get("fields", [])}
                return self._json(onboard.try_source(name, {**kept, **{k: v for k, v in values.items() if str(v).strip()}}))
            if op == "take-over":
                path = managed.take_over()
                print(f"setup: config taken over into {path} from {who}; restarting", flush=True)
                _log([("Config taken over by labhud", False)])
                managed.restart()
                return self._json({"ok": True, "path": path, "restart": True})
            if op in ("layout", "organize"):
                found = {a["name"]: a.get("found", []) for a in agents.listing()} if agents.enabled() else {}
                raw = managed.read_config()
                if op == "layout":
                    return self._json({"ok": True, **layout.view(raw, found)})
                return self._json({"ok": True, **layout.organize(raw, str(body.get("page") or ""), found)})
            if op in ("source-save", "source-forget"):
                return self._source(op, body, who)
            if op in ("alerts", "alerts-save", "notify-test"):
                return self._alerts(op, body, who)
            if op in ("logs", "inspect"):   # a ticked container's last lines, or how it is made (no secrets)
                target = str(body.get("target") or "")
                if DEMO and str(body.get("agent")) == "docker" and target in demo.DEMO_CONTAINERS:
                    return self._json({"ok": True, "output": demo.logs(target) if op == "logs" else demo.inspect(target)})
                aid, a = agents.by_name(str(body.get("agent") or "")) if agents.enabled() else (None, None)
                if not a:
                    return self._json({"ok": False, "error": "no such agent"}, 404)
                ok, error, output = agents.read(aid, "docker", op, target)
                print(f"setup: {op} of {target} on {a['name']} from {who}: {'ok' if ok else error}", flush=True)
                return self._json({"ok": True, "output": output} if ok else {"ok": False, "error": error}, 200 if ok else 502)
            if op == "notes":   # the notes card's text (notes = true), written in its panel or in the app
                text = body.get("text")
                if not isinstance(text, str) or len(text) > NOTES_MAX:
                    return self._json({"ok": False, "error": f"text, at most {NOTES_MAX} characters"}, 400)
                notes = {"text": text.replace("\r\n", "\n"), "t": int(time.time()), "by": who[:40]}
                store.save("notes", notes)
                with _lock:
                    _data["notes"] = notes
                _publish({"notes": notes})
                print(f"setup: notes saved by {who} ({len(text)} characters)", flush=True)
                return self._json({"ok": True, "notes": notes})
            if op == "prune":   # a host's unused images, by the agent that has "images" ticked
                if DEMO and str(body.get("agent")) == "docker":
                    return self._json({"ok": True, "output": "freed 2.4 GB (31 image layers)"})
                aid, a = agents.by_name(str(body.get("agent") or "")) if agents.enabled() else (None, None)
                if not a:
                    return self._json({"ok": False, "error": "no such agent"}, 404)
                ok, error, output = agents.read(aid, "docker", "prune", agents.PRUNE_TARGET, wait=60)
                print(f"setup: unused images on {a['name']} cleaned by {who}: {output if ok else error}", flush=True)
                _log([(f"{a['name']}: unused images cleaned, {output}" if ok else
                       f"{a['name']}: cleaning unused images failed: {error}", not ok)])
                return self._json({"ok": True, "output": output} if ok else {"ok": False, "error": error}, 200 if ok else 502)
            if op == "scan-status":
                return self._json({"ok": True, **scan.status(), "default": scan.default_cidr(os.environ.get("LABHUD_HOSTS", ""))})
            if op == "scan":
                extra = [a["addr"] for a in agents.listing() if a.get("addr")] if agents.enabled() else []
                configured = [c["name"] for c in onboard.catalog() if c["configured"]]
                st = scan.start(str(body.get("cidr") or scan.default_cidr(os.environ.get("LABHUD_HOSTS", ""))), extra, configured)
                print(f"setup: scan of {st['cidr']} ({st['total']} addresses) from {who}", flush=True)
                return self._json({"ok": True, **st})
            if op == "layout-save":
                managed.save_config(layout.apply(managed.read_config(), body.get("pages")))
                print(f"setup: pages laid out from {who}", flush=True)
                return self._json({"ok": True})
            if op == "text":
                with open(CONFIG_PATH, encoding="utf-8") as f:
                    text = f.read()
                return self._json({"ok": True, "text": text, "path": CONFIG_PATH, "managed": managed.active(),
                                   "writable": managed.active() or os.access(CONFIG_PATH, os.W_OK)})
            if op == "save-text":
                text = body.get("text")
                if not isinstance(text, str):
                    return self._json({"ok": False, "error": "no text"}, 400)
                try:
                    config.loads(text, CONFIG_PATH)
                except config.ConfigError as e:
                    return self._json({"ok": False, "problems": e.problems})
                if body.get("check"):
                    return self._json({"ok": True, "problems": []})
                if managed.active():
                    managed.save_text(text)
                elif os.access(CONFIG_PATH, os.W_OK):
                    editmode.save(CONFIG_PATH, text)
                else:
                    return self._json({"ok": False, "problems": [
                        f"{CONFIG_PATH} is mounted read-only: Take over first, then labhud keeps it"]})
                print(f"setup: config.toml saved from the editor, from {who}", flush=True)
                return self._json({"ok": True, "problems": []})
            values = body.get("values")
            if not isinstance(values, dict) or not values:
                return self._json({"ok": False, "error": "no values"}, 400)
            changed = managed.set_secrets(values)
            if changed:
                print(f"setup: keys {', '.join(changed)} set from {who}; restarting", flush=True)
                managed.restart()
            return self._json({"ok": True, "changed": changed, "restart": bool(changed)})
        except ValueError as e:
            return self._json({"ok": False, "error": str(e)[:600]}, 409)

    def _alerts(self, op, body, who):
        """⚙ > Alerts. alerts: the rules, [notify], the hosts a rule can name, the webhook's state.
        alerts-save {rules, notify}: into the kept config (checked like any save; applied without a
        restart). notify-test {url?, token?}: one message now. Raises ValueError for the 409."""
        if op == "notify-test":
            url, token = str(body.get("url") or "").strip(), str(body.get("token") or "").strip()
            if url and not re.match(r"https?://", url):
                raise ValueError("the address must start with http:// or https://")
            auth = (token if token.lower().startswith(("bearer ", "basic ")) else f"Bearer {token}") if token else None
            error = notify.test(url or None, auth, "ntfy" if url else None)
            print(f"setup: test notification from {who}: {error or 'sent'}", flush=True)
            return self._json({"ok": error is None, "error": error})
        if op == "alerts-save":
            rules, nt = body.get("rules"), body.get("notify")
            if not isinstance(rules, list) or not isinstance(nt, dict):
                raise ValueError("rules and notify are needed")
            keep = ("on", "what", "above", "minutes")
            raw = managed.read_config()
            raw["alert"] = [{k: r[k] for k in keep if k in r and not (k == "above" and r.get("what") in alerting.NO_MARK)}
                            for r in rules if isinstance(r, dict)]
            quiet = nt.get("quiet")
            # [notify.email] and the rest stay; a new `via` moves [notify.events]' ntfy/app ticks with it
            kept = raw.get("notify") if isinstance(raw.get("notify"), dict) else {}
            raw["notify"] = dict(kept, via=str(nt.get("via") or "both"))
            if isinstance(kept.get("events"), dict) and raw["notify"]["via"] != kept.get("via", "both"):
                raw["notify"]["events"] = notify.apply_via(kept["events"], raw["notify"]["via"])
            a, b = quiet if isinstance(quiet, list) and len(quiet) == 2 else (0, 0)
            raw["notify"].update(quiet_from=a, quiet_to=b)
            managed.save_config(raw)
            print(f"setup: alert rules saved from {who} ({len(raw['alert'])})", flush=True)
            return self._json({"ok": True})
        cards = [c for cs in TOPOLOGY["cards"].values() for c in cs]
        nt = TOPOLOGY.get("notify") or {}
        return self._json({
            "ok": True, "managed": managed.active(), "rules": TOPOLOGY["alert_rules"],
            "hosts": [{"id": c["id"], "name": c.get("name", c["id"]), "agent": c["agent"]} for c in alerting.hosts(cards)],
            "what": alerting.WHAT, "urgent": sorted(alerting.URGENT), "no_mark": sorted(alerting.NO_MARK),
            "notify": {"via": nt.get("via", "both"), "quiet": list(nt["quiet"]) if nt.get("quiet") else None},
            "ntfy": {"set": notify.enabled(), "host": notify.where(), "format": notify.FORMAT,
                     "problem": notify.problem(), "sent": notify.state["sent"],
                     "last_error": notify.state["last_error"]},
            "active": RULES.active()})

    def _source(self, op, body, who):
        """source-save {name, values, page}: tried first, then the keys go to secrets.env and a
        card to the page, and labhud restarts to read them. source-forget {name}: the keys and the
        card go. Raises ValueError for the caller's 409."""
        name = str(body.get("name") or "")
        item = next((c for c in onboard.catalog() if c["name"] == name), None)
        if item is None or not item["fields"]:
            raise ValueError(f"no source {name!r} to set up here")
        fields = [f["name"] for f in item["fields"]]
        raw = managed.read_config()
        if op == "source-forget":
            managed.save_config(onboard.remove_source_card(raw, name))
            changed = managed.set_secrets({f: "" for f in fields})
            print(f"setup: source {name} forgotten from {who}", flush=True)
            if changed:
                managed.restart()
            return self._json({"ok": True, "restart": bool(changed)})
        given = body.get("values") if isinstance(body.get("values"), dict) else {}
        values = {k: str(v).strip() for k, v in given.items() if k in fields and str(v).strip()}
        # a secret left empty in the form keeps the one already set (the page never sees it)
        tried = onboard.try_source(name, {**{f: os.environ.get(f, "") for f in fields}, **values})
        if not tried.get("ok"):
            return self._json({"ok": False, "error": "the test failed: " + str(tried.get("error"))}, 409)
        raw, card = onboard.add_source_card(raw, name, str(body.get("page") or ""))
        managed.save_config(raw)
        changed = managed.set_secrets(values)
        print(f"setup: source {name} saved from {who}" + (f"; card {card}" if card else ""), flush=True)
        if changed:
            managed.restart()
        return self._json({"ok": True, "card": card, "restart": bool(changed)})

    def _app_stream(self):
        """SSE for apps: `event: state` with the whole of /api/v1/state, again whenever it changes
        (at most once a second). Counts against MAX_SUBSCRIBERS like a display."""
        role = self._role()
        q = queue.Queue(maxsize=64)
        with _subscribers_lock:
            if len(_subscribers) >= MAX_SUBSCRIBERS:
                return self._json({"error": "too many open streams"}, 503)
            _subscribers.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = None
        try:
            while True:
                st = app_state(role=role)
                sign = json.dumps({k: v for k, v in st.items() if k != "now"}, sort_keys=True)
                if sign != last:
                    last = sign
                    body = json.dumps(st, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: state\ndata: {body}\n\n".encode())
                    self.wfile.flush()
                try:
                    q.get(timeout=20)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                time.sleep(1)   # several sources often answer together: one state for all of them
                while True:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def _lock_step(self, step):
        """The lock: unlock {pin}, touch, lock, pin {old, new}. From labhud's own page, JSON, small."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if 0 < length <= 256 else {}
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            length, body = -1, {}
        if not (self._same_page() and 0 <= length <= 256) or (step == "pin" and not self._may_manage()):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        ticket = self.headers.get("X-Labhud-Unlock")
        if step == "unlock" and not (ADMIN.is_on() and self._admin_ok()):
            # a screen that is not signed in only shows: there is nothing for the PIN to open
            return self._json({"ok": False, "error": "sign in as the admin first"}, 403)
        if step == "unlock":
            new, wait = LOCK.unlock(body.get("pin"))
            if not new:
                print(f"lock: wrong PIN from {self.client_address[0]}", flush=True)
                return self._json({"ok": False, "error": "wrong PIN", "wait": wait}, 403)
            return self._json({"ok": True, "ticket": new, "idle": LOCK.idle})
        if step == "touch":
            ok = not LOCK.is_on() or LOCK.valid(ticket)
            return self._json({"ok": ok}, 200 if ok else 403)
        if step == "lock":
            LOCK.close(ticket)
            return self._json({"ok": True})
        ok, new, error, wait = LOCK.set_pin(body.get("old"), body.get("new"))
        if not ok:
            return self._json({"ok": False, "error": error, "wait": wait}, 403)
        what = "set" if new else "removed"
        print(f"lock: PIN {what} from {self.client_address[0]}", flush=True)
        EVENTS.add(f"Display PIN {what}")
        _send("lock", LOCK.public())
        return self._json({"ok": True, "ticket": new, "lock": LOCK.public()})

    def _edit(self, step):
        """The config editor (editmode.py): a code from `init.py edit`, the page's own Origin,
        JSON only. One answer for every refusal."""
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if not (self._same_page() and 0 <= length <= 512 * 1024
                and (self.headers.get("Content-Type") or "").startswith("application/json")):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        body = self.rfile.read(length)
        if not editmode.check(CONFIG_PATH, self.headers.get("X-Labhud-Edit")):
            print(f"config editor: refused from {self.client_address[0]}", flush=True)
            return self._json({"ok": False, "error": "the code is wrong or expired: run init.py edit again"}, 403)
        if step == "load":
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return self._json({"ok": True, "text": f.read(), "path": CONFIG_PATH,
                                   "writable": os.access(CONFIG_PATH, os.W_OK)})
        try:
            text = json.loads(body).get("text")
            if not isinstance(text, str):
                raise ValueError
        except (ValueError, AttributeError):
            return self._json({"ok": False, "error": "no text"}, 400)
        try:
            config.loads(text, CONFIG_PATH)
            problems = []
        except config.ConfigError as e:
            problems = e.problems
        if step == "check" or problems:
            return self._json({"ok": not problems, "problems": problems})
        try:
            editmode.save(CONFIG_PATH, text)
        except OSError as e:
            return self._json({"ok": False, "problems": [f"not saved: {e.strerror}"]})
        print(f"config editor: config.toml saved from {self.client_address[0]}", flush=True)
        self._json({"ok": True, "problems": []})

    def _maintenance(self, card):
        """A card's MAINTENANCE button: {"minutes": n} (0 ends it). Off unless
        [maintenance] buttons = true; like actions, a display signed in as the admin, or the app."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            minutes = int(json.loads(self.rfile.read(length) if 0 < length <= 1024 else b"{}").get("minutes", -1))
        except (ValueError, AttributeError):
            minutes = -1
        if not (TOPOLOGY["maintenance"]["buttons"] and card in CARD_IDS and self._same_page()
                and 0 <= minutes <= 7 * 24 * 60 and self._operator()):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        set_maintenance(card, minutes)
        print(f"maintenance {card}: {f'{minutes} min' if minutes else 'ended'} from {self._who()}", flush=True)
        self._json({"ok": True, "until": maintenance_now().get(card)})

    def _action_log(self, name):
        """The display reports how an action it sent went ({"result": accepted|done|failed|refused,
        "took": seconds}), for the day's timeline. Only an action the config offers, from labhud's
        own page; the text is written here from the action's name, never taken from the request."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) if 0 < length <= 512 else b"{}")
            result, took = body.get("result"), int(body.get("took") or 0)
        except (ValueError, AttributeError, TypeError):
            result, took = None, 0
        # No lock ticket here: "done" may come minutes after the action, once the screen locked again;
        # the action itself needed one, and the text is written here, never taken from the request.
        if not (result in ACTION_RESULTS and _action_allowed(name) and self._same_page()):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        text = f"{action_text(name)}: {ACTION_RESULTS[result]}"
        if result == "done" and 0 < took < 86400:
            text += f" in {took // 60}m {took % 60:02d}s" if took >= 60 else f" in {took}s"
        _log([(text, result in ("failed", "refused"))])
        self._json({"ok": True})

    def _hook(self, token):
        """A webhook from CI (agents.hook): the token is the whole check, so not behind LABHUD_HOSTS
        nor the admin. It updates one container, as its UPDATE button would."""
        ok, error, name, target = agents.hook(token) if agents.enabled() else (False, "forbidden", "", "")
        if not target:
            print(f"webhook from {self.client_address[0]} refused", flush=True)
            return self._json({"ok": False, "error": "forbidden"}, 403)
        print(f"webhook from {self.client_address[0]}: {name}/{target} update: {'started' if ok else error}", flush=True)
        _log([(f"{target} on {name}: update asked by a webhook" + ("" if ok else f", refused: {error}"), not ok)])
        return self._json({"ok": ok, **({} if ok else {"error": error})}, 202 if ok else 409)

    def _push(self, name):
        """An agent's data (sources/push.py). Not behind LABHUD_HOSTS: the signature is the check,
        and an agent may reach labhud under any name. One answer for every refusal."""
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if not 0 <= length <= push.MAX_BODY or name not in ACTIVE:
            self.close_connection = True
            return self._json({"ok": False, "error": "forbidden"}, 403)
        body = self.rfile.read(length)
        why = push.verify(name, self.headers, body)
        if not why:
            try:
                push.receive(name, body)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)[:100]}, 400)
            return self._json({"ok": True})
        print(f"push to {name} from {self.client_address[0]} refused: {why}", flush=True)
        self._json({"ok": False, "error": "forbidden"}, 403)

    def _agent_file(self, path):
        try:
            with open(AGENT_FILES[path], "rb") as f:
                body = f.read()
            if path == "/agent/install.sh":
                with open(AGENT_FILES["/agent/labhud-agent.py"], "rb") as f:
                    body = body.replace(b"@AGENT_SHA256@", hashlib.sha256(f.read()).hexdigest().encode())
            elif path == "/agent/install-agent.sh":
                body = agents.install_script(AGENT_RELEASES)
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._headers(200, "text/plain; charset=utf-8", len(body), "no-cache")
        self.wfile.write(body)

    def _static(self, path):
        if path == "/":
            path = "/index.html"
        target = os.path.normpath(os.path.join(STATIC, path.lstrip("/")))
        # `startswith(STATIC)` alone would accept a sibling called "static-other": compare on the
        # separator, not on a string prefix.
        if not (target == STATIC or target.startswith(STATIC + os.sep)) or not os.path.isfile(target):
            icon = re.fullmatch(r"/icons/svc/([a-z0-9-]+)\.(svg|png)", path)
            found = iconcache.get(icon[1], icon[2], (TOPOLOGY.get("display") or {}).get("icons_online", True)) if icon else None
            if found:
                self._headers(200, found[1], len(found[0]), "public, max-age=86400")
                return self.wfile.write(found[0])
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(target)[1]
        with open(target, "rb") as f:
            body = f.read()
        # Fonts never change; everything else must show right after an edit.
        cache = ("public, max-age=31536000, immutable" if ext == ".woff2"
                 else "public, max-age=86400" if path.startswith("/icons/svc/") else "no-cache")
        self._headers(200, TYPES.get(ext, "application/octet-stream"), len(body), cache)
        self.wfile.write(body)

    def _stream(self):
        q = queue.Queue(maxsize=64)
        with _subscribers_lock:
            if len(_subscribers) >= MAX_SUBSCRIBERS:
                return self._json({"error": "too many open streams"}, 503)
            _subscribers.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # Traefik/NPM would otherwise cut the stream when nothing flows; and no buffering anyway.
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            full = json.dumps(snapshot(), ensure_ascii=False, separators=(",", ":"))
            self.wfile.write(f"event: full\ndata: {full}\n\n".encode())
            if CONFIG_ERRORS:
                self.wfile.write(f"event: config\ndata: {json.dumps({'errors': CONFIG_ERRORS})}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=STREAM_PING)
                except queue.Empty:
                    message = ": ping\n\n"  # keeps the connection open through any proxy
                with _subscribers_lock:
                    dropped = q not in _subscribers
                if dropped:   # too slow, so _publish let it go: closing makes the page come back for a full one
                    self.close_connection = True
                    break
                self.wfile.write(message.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _subscribers_lock:
                if q in _subscribers:
                    _subscribers.remove(q)

    def log_message(self, *args):
        pass


# the routes of the modules in api/ (routes.py): imported once, before the first request
ROUTE_MODULES = routes.load()

if __name__ == "__main__":
    _mark_inactive()
    _log([("LabHUD started", False)])
    print(("DEMO MODE, made-up data; " if DEMO else "")
          + f"sources: {', '.join(sorted(ACTIVE)) or 'none'}; not configured: {', '.join(sorted(INACTIVE)) or 'none'}",
          flush=True)
    print(f"answering to: {', '.join(sorted(ALLOWED_HOSTS))} (LABHUD_HOSTS)", flush=True)
    if envfiles.FROM_SECRETS:
        print(f"read from {envfiles.secrets_path()}: {len(envfiles.FROM_SECRETS)} key(s) set in ⚙ > Setup", flush=True)
    if envfiles.FROM_DOTENV:
        print(f"read from {envfiles.given_dotenv()}: {len(envfiles.FROM_DOTENV)} setting(s)", flush=True)
    if envfiles.LOADED:
        print(f"read from files: {', '.join(envfiles.LOADED)}", flush=True)
    if not DEMO:
        rest = "checked (LABHUD_VERIFY)" if sources._common._VERIFIED else "NOT checked (see LABHUD_PINS)"
        print(f"certificates: {len(sources.PINS)} pinned, the others {rest}" if sources.PINS
              else f"certificates: {rest}", flush=True)
    print(("actions: on, signed, forwarded by labhud to " + ACTION_URL if ACTION_URL and ACTION_SECRET
           else "actions: OFF: LABHUD_ACTION_URL is set but LABHUD_ACTION_SECRET is not (docs/actions.md)"
           if ACTION_URL else "actions: off (set LABHUD_ACTION_URL and LABHUD_ACTION_SECRET to enable)"), flush=True)
    print(f"notifications: {notify.problem() or 'on, ' + notify.FORMAT}" if notify.enabled()
          else "notifications: no ntfy (⚙ > Alerts, or `labhud notify set <url>`); the app can still read them", flush=True)
    if DEMO:
        seed_demo_history()
        seed_demo_uptime()
    threading.Thread(target=loop, daemon=True, name="collect").start()
    threading.Thread(target=watch_config, daemon=True, name="config").start()
    threading.Thread(target=watch_lock, daemon=True, name="lock").start()
    notify.start()
    import api.backup   # the daily backup, and its failures as news
    api.backup.start(lambda text, bad: _log([(text, bad, (None, None, "backup_failed" if bad else None))]))
    if screen.enabled():
        print(f"screen: Fully Kiosk at {screen.URL}" + ("" if TOPOLOGY.get("night") else " (idle: no [night] in config.toml)"), flush=True)
    screen.start(lambda: None if DEMO else TOPOLOGY.get("night"))
    if mqtt.enabled():
        print(f"mqtt: {mqtt.problem() or 'on, ' + mqtt.PREFIX + '/…'}", flush=True)
        mqtt.announce(mqtt_cards(), TOPOLOGY["title"])
        mqtt.start()
    threading.Thread(target=check_keys, daemon=True, name="keys").start()
    if not DEMO:
        threading.Thread(target=updates.run, args=(VERSION, store, lambda text: _log([(text, False, (None, None, "update_available"))])),
                         daemon=True, name="updates").start()
    agents.tls_fp = devices.tls_fingerprint(TLS_CERT)
    agents.load_history()
    _data["notes"] = store.load("notes") or ({"text": "Synology sleeps 00:00–08:00.\nBackups at 02:00.", "t": int(time.time())}
                                            if DEMO else {"text": "", "t": 0})
    threading.Thread(target=agents.pinger, args=(threading.Event(),), daemon=True, name="agents").start()
    threading.Thread(target=agents.scheduler, args=(threading.Event(),), daemon=True, name="agent-updates").start()
    publish_agents()
    # Maintenance as kept at the last stop is not news: in the snapshot before the watcher starts.
    _data["maintenance"] = dict(maintenance_now(), _t=int(time.time()))
    if not DEMO:
        restore_history()
        if store.DIR:
            print(f"kept across restarts in {store.DIR}" if store.enabled()
                  else f"LABHUD_DATA: {store.PROBLEM}", flush=True)
    threading.Thread(target=sample_history, daemon=True, name="history").start()
    threading.Thread(target=watch_maintenance, daemon=True, name="maintenance").start()
    if os.environ.get("LABHUD_PUBLIC_PORT"):
        start_public(int(os.environ["LABHUD_PUBLIC_PORT"]))
    write_cli_key()
    httpd = Server(("0.0.0.0", PORT), Handler)
    if TLS_CERT:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY or None)
        # Each connection is wrapped in its own thread (Server.finish_request), so a client that
        # never finishes the handshake does not block the others.
        httpd.tls = ctx
    print(f"listening on :{PORT}, {'HTTPS' if TLS_CERT else 'HTTP'}"
          + (f"; pushed sources: {', '.join(sorted(push.KEYS))}" if push.KEYS else ""), flush=True)
    httpd.serve_forever()
