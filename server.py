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
import json
import re
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import envfiles  # noqa: F401  (first: LABHUD_*_FILE -> LABHUD_*, before anything reads them)
import admin
import cardstate
import config
import devices
import editmode
import events
import lock
import mqtt
import notify
import qr
import sources
import screen
import store
import thresholds
from sources import push

# print() writes the text and the newline separately, so two threads logging at once glued their
# lines together ("kept across restarts in /datawarning: ..."). One lock around each whole line.
_print, _print_lock = builtins.print, threading.Lock()


def _one_line_print(*args, **kw):
    with _print_lock:
        _print(*args, **kw)


builtins.print = _one_line_print


class Server(ThreadingHTTPServer):
    # A browser or a phone that drops its connection mid-request (screen off, tab closed, Wi-Fi
    # switch) is normal: the default printed a full traceback for every one.
    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


PORT = int(os.environ.get("LABHUD_PORT", "8095"))
# HTTPS on labhud's own port (PEM files), for a display or agents on a network you do not trust.
# Without them, plain HTTP: put a reverse proxy in front for TLS, or keep the port on a trusted segment.
TLS_CERT = os.environ.get("LABHUD_TLS_CERT", "")
TLS_KEY = os.environ.get("LABHUD_TLS_KEY", "")
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
    notify.URL = ""  # made-up events are not news
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


def _auto_ids(topology):
    return sorted(c["id"] for cs in topology["cards"].values() for c in cs if c.get("auto"))


def _apply(topology):
    """Everything derived from config.toml, computed again when the file changes."""
    global TOPOLOGY, ACTIVE, INACTIVE, SOURCES, TARGETS, SOURCE_HOST, FILE_TOPOLOGY
    FILE_TOPOLOGY = topology
    topology = auto_guests(topology, (globals().get("_data") or {}).get("proxmox"), globals().get("TOPOLOGY"))
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
    LOCK.idle = topology.get("lock", {}).get("idle", 600)


# The display's lock (lock.py): a PIN, then a ticket that the requests changing something carry.
LOCK = lock.Lock()


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


def restore_history():
    """The sparklines and the uptime kept at the last stop (LABHUD_DATA), with the minutes it was
    down left empty."""
    for name, target, step, points in (("history", _history, HISTORY_STEP, HISTORY_POINTS),
                                       ("history_long", _long, LONG_STEP, LONG_POINTS)):
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
        gap = min(UPTIME_POINTS, max(0, int((time.time() - saved.get("t", 0)) // UPTIME_STEP)))
        ids = {c["id"] for c in _checked_cards()}
        for cid, values in (saved.get("series") or {}).items():
            if cid in ids and values:
                _uptime[cid] = collections.deque(values + [None] * gap, maxlen=UPTIME_POINTS)


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
            long_kept = _sample_long()
            uptime_kept = _sample_uptime(_data)
        if not DEMO:
            store.save("history", {"t": int(time.time()), "series": kept})
            if long_kept is not None:
                store.save("history_long", {"t": int(time.time()), "series": long_kept})
            if uptime_kept is not None:
                store.save("uptime", {"t": int(time.time()), "series": uptime_kept})
        time.sleep(HISTORY_STEP)


def _sample_long():
    """Adds the latest minute to the running 15 minutes; on the 15th, appends their average to the
    long series. Returns the long series when a point was added (to be saved), else None. Under _lock."""
    for path in TOPOLOGY["trends"]:
        _long_pending.setdefault(path, []).append(_history[path][-1] if _history.get(path) else None)
    if len(next(iter(_long_pending.values()), [])) * HISTORY_STEP < LONG_STEP:
        return None
    for path in TOPOLOGY["trends"]:
        values = [v for v in _long_pending.get(path, []) if v is not None]
        series = _long.setdefault(path, collections.deque(maxlen=LONG_POINTS))
        series.append(round(sum(values) / len(values), 1) if values else None)
    for path in set(_long) - set(TOPOLOGY["trends"]):
        del _long[path]
    _long_pending.clear()
    return {p: list(s) for p, s in _long.items()}


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
    st = cardstate.card_status(c, data)
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
    if len(next(iter(_uptime_pending.values()), [])) * HISTORY_STEP < UPTIME_STEP:
        return None
    ids = {c["id"] for c in cards}
    for cid in ids:
        _uptime.setdefault(cid, collections.deque(maxlen=UPTIME_POINTS)).append(_fold_uptime(_uptime_pending.get(cid, [])))
    for cid in set(_uptime) - ids:   # gone after a reload
        del _uptime[cid]
    _uptime_pending.clear()
    return {k: list(v) for k, v in _uptime.items()}


def uptime(span=86400):
    """{step, cards: {id: [..]}, percent: {id: 99.4}} over the last `span` seconds."""
    keep = max(1, span // UPTIME_STEP)
    with _lock:
        cards = {k: list(v)[-keep:] for k, v in _uptime.items()}
    percent = {}
    for k, v in cards.items():
        known = [x for x in v if x is not None and x >= 0]
        if known:
            percent[k] = round(100 * sum(known) / len(known), 2)
    return {"step": UPTIME_STEP, "cards": cards, "percent": percent}


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


def history(long=False):
    with _lock:
        if long:
            return {"step": LONG_STEP, "series": {p: list(s) for p, s in _long.items()}}
        return {"step": HISTORY_STEP, "series": {p: list(s) for p, s in _history.items()}}


def _action_allowed(name):
    """Only the actions the config offers: a card's `actions`, or a game server's buttons."""
    if not ACTION_NAME_RE.fullmatch(name):
        return False
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
             "/fonts/roboto-latin-700-normal.woff2": ("fonts/roboto-latin-700-normal.woff2", "font/woff2")}

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
        # ("status" = went down | "up" | "off", card id): a host's transition, kept with the event
        kind, card = ref[0] if ref else (None, None)
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
        notify.submit(TOPOLOGY["title"], text, bad, still_true, card)
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
            if not DEMO and "unavailable" not in result:
                past = WATCH.check([c for cs in TOPOLOGY["cards"].values() for c in cs],
                                   lambda p: thresholds.lookup(_data, p), name, maintenance_now())
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
            if name not in SOURCES and name not in INACTIVE and name not in ("events", "maintenance"):
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
               "/agent/labhud-agent.py": os.path.join(AGENTS, "labhud-agent.py")}


# ---------------------------------------------------------------------------------------------
# Apps (the LabHUD Android app): a token per paired device (devices.py), the cards' state worked
# out here (cardstate.py), under /api/v1. The contract is in docs/app-api.md.
# ---------------------------------------------------------------------------------------------

API_VERSION = 1


def app_state(full=False):
    with _lock:
        data = dict(_data)
    return cardstate.state(TOPOLOGY, data, actions_on=bool(ACTION_URL and ACTION_SECRET), full=full)


def app_info():
    return {"api": API_VERSION, "version": VERSION, "title": TOPOLOGY.get("title") or "LabHUD", "demo": DEMO,
            "actions": bool(ACTION_URL and ACTION_SECRET), "maintenance_buttons": TOPOLOGY["maintenance"]["buttons"],
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
            self._dev_for, self._dev = self.headers, (None, None)
            auth = self.headers.get("Authorization") or ""
            if auth.startswith("Bearer "):
                name = devices.check(auth[len("Bearer "):].strip())
                self._dev = (name, "bearer") if name else (None, None)
            else:
                for part in (self.headers.get("Cookie") or "").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == "labhud_device":
                        name = devices.check(v)
                        self._dev = (name, "cookie") if name else (None, None)
        return self._dev

    def _host_ok(self):
        """A name in LABHUD_HOSTS, or a paired device: its token is what DNS rebinding cannot have."""
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS or self._device()[0] is not None

    def _who(self):
        name, _ = self._device()
        return f"{self.client_address[0]} ({name})" if name else self.client_address[0]

    def do_GET(self):
        # The agent and its installer: public code, no data, fetched by machines that may know
        # labhud under any name (like /api/push), so before the LABHUD_HOSTS check.
        if self.path in AGENT_FILES:
            return self._agent_file(self.path)
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
                                   action_url="/action/" if ACTION_URL and ACTION_SECRET else ""))
        if path == "/api/snapshot":  # useful for debugging and for the check screenshots
            return self._json(snapshot())
        if path == "/api/status":
            return self._json(health())
        if path == "/api/history":   # ?long: 15-minute points over 7 days, for the panel's charts
            return self._json(history(long="long" in self.path.partition("?")[2]))
        if path == "/api/events":   # every kept event, newest first: the day's timeline
            return self._json({"events": EVENTS.all()})
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
        if self.path == "/api/v1/pair":
            return self._pair()
        if self.path == "/api/v1/pair-code":
            return self._pair_code()
        if self.path == "/api/v1/unpair":
            return self._unpair()
        if self.path == "/api/v1/login-pair":
            return self._login_pair()
        ad = re.fullmatch(r"/api/admin/(login|logout|edit)", self.path)
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
        m = re.fullmatch(r"/action/([a-z0-9-]{1,64})", self.path)
        if not (m and ACTION_URL and ACTION_SECRET and _action_allowed(m.group(1))
                and self._same_page() and self._unlocked()):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        code, answer = forward_action(m.group(1))
        print(f"action {m.group(1)} from {self._who()}: "
              f"{'ok' if answer.get('ok') else answer.get('error')}", flush=True)
        self._json(answer, code)

    def _same_page(self):
        """The request comes from labhud's own page, under a name in LABHUD_HOSTS, or from the
        app with its token (an app has no Origin; the WebView's page still needs its own)."""
        if self._device()[1] == "bearer":
            return True
        host = (self.headers.get("Host") or "").lower()
        origin = (self.headers.get("Origin") or "").lower()
        return self._host_ok() and origin in (f"http://{host}", f"https://{host}")

    def _unlocked(self):
        """No PIN set, or the request carries a live ticket (lock.py), or comes from the app with
        its token: the app asks for the phone's fingerprint or PIN itself when it opens. The wall
        page inside the app (the cookie) is a wall like any other: the display's PIN still holds."""
        return self._device()[1] == "bearer" or LOCK.allows(self.headers.get("X-Labhud-Unlock"))

    # --- apps (/api/v1) ---

    def _app_get(self, path):
        if path == "/api/v1/info":
            return self._json(app_info())
        if path == "/api/v1/state":
            return self._json(app_state())
        c = re.fullmatch(r"/api/v1/card/([A-Za-z0-9_.-]{1,64})", path)
        if c:
            card = next((x for x in app_state(full=True)["cards"] if x["id"] == c.group(1)), None)
            return self._json(card) if card else self._json({"error": "no such card"}, 404)
        if path == "/api/v1/history":
            return self._json(history(long="long" in self.path.partition("?")[2]))
        if path == "/api/v1/uptime":
            span = 7 * 86400 if "range=7d" in self.path.partition("?")[2] else 86400
            return self._json(uptime(span))
        if path == "/api/v1/events":
            return self._json({"events": EVENTS.all()})
        if path == "/api/v1/stream":
            return self._app_stream()
        if path == "/api/v1/devices":   # the display's ⚙ lists them; names and times, no secrets
            return self._json({"devices": devices.listing(), "pairing": devices.enabled()})
        return self._json({"error": "not found"}, 404)

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
        return ADMIN.valid(self.headers.get("X-Labhud-Admin"))

    def _may_manage(self):
        """Pairing and removing phones: from labhud's own page (also in the app's wall mode), never
        with the app's own token; signed in as the admin when there is one, else unlocked."""
        if not self._same_page() or self._device()[1] == "bearer":
            return False
        return self._admin_ok() if ADMIN.is_on() else self._unlocked()

    def _admin_step(self, step):
        """login {user, password, code} -> {ticket}; logout; edit -> a code for the config editor.
        From labhud's own page, JSON, small. One answer for every refusal."""
        body = self._small_json()
        if body is None or not self._same_page() or self._device()[1] == "bearer" or not ADMIN.is_on():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if step == "login":
            ticket, wait = ADMIN.login(body.get("user"), body.get("password"), body.get("code"))
            if not ticket:
                print(f"admin: wrong sign-in from {self.client_address[0]}", flush=True)
                return self._json({"ok": False, "error": "wrong user, password or code", "wait": wait}, 403)
            print(f"admin: signed in from {self.client_address[0]}", flush=True)
            return self._json({"ok": True, "ticket": ticket, "idle": ADMIN.idle, "user": ADMIN.user()})
        if step == "logout":
            ADMIN.close(self.headers.get("X-Labhud-Admin"))
            return self._json({"ok": True})
        if not self._admin_ok():
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not os.access(CONFIG_PATH, os.W_OK):
            return self._json({"ok": False, "error": f"{CONFIG_PATH} is not writable by labhud: mount it read-write"}, 409)
        try:
            code = editmode.open_ticket(CONFIG_PATH)
        except OSError as e:
            return self._json({"ok": False, "error": f"cannot open the editor: {e.strerror}"}, 409)
        print(f"admin: config editor opened from {self.client_address[0]}", flush=True)
        self._json({"ok": True, "code": code})

    def _login_pair(self):
        """{user, password, code, name} -> {token, name}: pairing from the app by signing in as the
        admin, without a QR code. Under any Host, like /api/v1/pair; slowed like every sign-in."""
        body = self._small_json()
        if body is None or not ADMIN.is_on() or not devices.enabled():
            return self._json({"ok": False, "error": "this LabHUD has no admin to sign in as"}, 403)
        right, wait = ADMIN.check(body.get("user"), body.get("password"), body.get("code"))
        if not right:
            print(f"admin: wrong sign-in to pair from {self.client_address[0]}", flush=True)
            return self._json({"ok": False, "error": "wrong user, password or code", "wait": wait}, 403)
        token, name = devices.pair(devices.new_code(), body.get("name"))
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

    def _app_stream(self):
        """SSE for apps: `event: state` with the whole of /api/v1/state, again whenever it changes
        (at most once a second). Counts against MAX_SUBSCRIBERS like a display."""
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
                st = app_state()
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
        if not (self._same_page() and 0 <= length <= 256):
            return self._json({"ok": False, "error": "forbidden"}, 403)
        ticket = self.headers.get("X-Labhud-Unlock")
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
        [maintenance] buttons = true; like actions, anyone at the display can press it."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            minutes = int(json.loads(self.rfile.read(length) if 0 < length <= 1024 else b"{}").get("minutes", -1))
        except (ValueError, AttributeError):
            minutes = -1
        if not (TOPOLOGY["maintenance"]["buttons"] and card in CARD_IDS and self._same_page()
                and 0 <= minutes <= 7 * 24 * 60 and self._unlocked()):
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
            if path.endswith(".sh"):
                with open(AGENT_FILES["/agent/labhud-agent.py"], "rb") as f:
                    body = body.replace(b"@AGENT_SHA256@", hashlib.sha256(f.read()).hexdigest().encode())
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
            return self._json({"error": "not found"}, 404)
        ext = os.path.splitext(target)[1]
        with open(target, "rb") as f:
            body = f.read()
        # Fonts never change; everything else must show right after an edit.
        cache = "public, max-age=31536000, immutable" if ext == ".woff2" else "no-cache"
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
                    message = q.get(timeout=20)
                except queue.Empty:
                    message = ": ping\n\n"  # keeps the connection open through any proxy
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


if __name__ == "__main__":
    _mark_inactive()
    _log([("LabHUD started", False)])
    print(("DEMO MODE, made-up data; " if DEMO else "")
          + f"sources: {', '.join(sorted(ACTIVE)) or 'none'}; not configured: {', '.join(sorted(INACTIVE)) or 'none'}",
          flush=True)
    print(f"answering to: {', '.join(sorted(ALLOWED_HOSTS))} (LABHUD_HOSTS)", flush=True)
    if envfiles.FROM_DOTENV:
        print(f"read from {envfiles.DOTENV}: {len(envfiles.FROM_DOTENV)} setting(s)", flush=True)
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
          else "notifications: off (set LABHUD_NOTIFY_URL to enable)", flush=True)
    if DEMO:
        seed_demo_history()
        seed_demo_uptime()
    threading.Thread(target=loop, daemon=True, name="collect").start()
    threading.Thread(target=watch_config, daemon=True, name="config").start()
    threading.Thread(target=watch_lock, daemon=True, name="lock").start()
    notify.start()
    if screen.enabled():
        print(f"screen: Fully Kiosk at {screen.URL}" + ("" if TOPOLOGY.get("night") else " (idle: no [night] in config.toml)"), flush=True)
    screen.start(lambda: None if DEMO else TOPOLOGY.get("night"))
    if mqtt.enabled():
        print(f"mqtt: {mqtt.problem() or 'on, ' + mqtt.PREFIX + '/…'}", flush=True)
        mqtt.announce(mqtt_cards(), TOPOLOGY["title"])
        mqtt.start()
    threading.Thread(target=check_keys, daemon=True, name="keys").start()
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
    httpd = Server(("0.0.0.0", PORT), Handler)
    if TLS_CERT:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(TLS_CERT, TLS_KEY or None)
        # The handshake happens in the request's own thread, so a client that never finishes it
        # does not block the others.
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    print(f"listening on :{PORT}, {'HTTPS' if TLS_CERT else 'HTTP'}"
          + (f"; pushed sources: {', '.join(sorted(push.KEYS))}" if push.KEYS else ""), flush=True)
    httpd.serve_forever()
