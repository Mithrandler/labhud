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
# Actions do NOT go through here, on purpose. The agent that runs them (LABHUD_ACTION_URL) should
# accept POST only from the display's own IP, so that no container next to this server can shut a
# host down. The browser talks to that agent directly; this server only tells it where it is.

import collections
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import events
import notify
import sources

PORT = int(os.environ.get("LABHUD_PORT", "8095"))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# The names the display may be requested under ("host:port", as the browser sends them). The
# second layer, after a firewall that lets only the display reach the port: without it, any page
# opened in the display's browser could ask for a name that resolves to this server (DNS rebinding)
# and read the whole snapshot — which holds internal IPs, devices and security data.
ALLOWED_HOSTS = {h.strip().lower() for h in os.environ.get(
    "LABHUD_HOSTS", f"127.0.0.1:{PORT},localhost:{PORT}").split(",") if h.strip()}
# Where the browser sends actions (POST <url><action name>). Empty: action buttons are not shown.
ACTION_URL = os.environ.get("LABHUD_ACTION_URL", "")
# A client opening stream after stream would hold one thread each. The wall needs one.
MAX_SUBSCRIBERS = 8
# How long a configured source may keep failing before the top bar shows a mark: one failed poll
# is often a restart or a slow answer, and the card already says so on its own.
SOURCE_GRACE = int(os.environ.get("LABHUD_SOURCE_GRACE", "300"))
VERSION = os.environ.get("LABHUD_VERSION", "dev")
STARTED = int(time.time())

# Demo mode: made-up data for demo/config.toml, no network, no keys, no ping, no actions.
DEMO = os.environ.get("LABHUD_DEMO", "").lower() in ("1", "true", "yes")
if DEMO:
    import demo
    os.environ.setdefault("LABHUD_CONFIG", demo.CONFIG)
    ACTION_URL = ""
    notify.URL = ""  # made-up events are not news

STATUS_EVERY = 30  # the ping/TCP checks, handled here: they need the topology
CONFIG_PATH = config.config_path()
# The problems in config.toml since it was last edited; the server keeps the last good version.
CONFIG_ERRORS = []


def _apply(topology):
    """Everything derived from config.toml, computed again when the file changes."""
    global TOPOLOGY, ACTIVE, INACTIVE, SOURCES, TARGETS, SOURCE_HOST
    # Only the sources whose settings are present run; the others are reported as not configured.
    active, inactive = (demo.sources(), {}) if DEMO else sources.load(topology)
    # name -> interval in seconds. The paces follow how fast the measured thing changes, not "as often as possible".
    every = {name: src.every for name, src in active.items()}
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
    TOPOLOGY, ACTIVE, INACTIVE, SOURCES, TARGETS, SOURCE_HOST = (
        topology, active, inactive, every, targets, source_host)


_apply(config.load(CONFIG_PATH))

_data = {}
_lock = threading.Lock()
# name -> how the last polls of that source went, for /status (never the data itself)
_health = {}
# What changed, newest first: the `events` source (see events.py). The demo starts with a made-up past.
EVENTS = events.Log(demo.events() if DEMO else ())


# Sparklines: one value a minute for every number in TOPOLOGY["trends"], the last 6 hours, in
# memory (a restart starts empty). Sent on request (/api/history), not over the stream: the
# display asks once a minute, and the stream stays small.
HISTORY_STEP = 60
HISTORY_POINTS = 360
_history = {}


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
        time.sleep(HISTORY_STEP)


def history():
    with _lock:
        return {"step": HISTORY_STEP, "series": {p: list(s) for p, s in _history.items()}}


def _log(found):
    """Adds [(text, bad, ref?)] to the history, sends it to the displays and, if set up, to the
    notification webhook (see notify.py)."""
    if not found:
        return
    for text, bad, *ref in found:
        EVENTS.add(text, bad)
        still_true = None
        if ref and ref[0][0] == "status":
            card = ref[0][1]
            still_true = lambda card=card: _data.get("status", {}).get(card) is False  # noqa: E731
        notify.submit(TOPOLOGY["title"], text, bad, still_true)
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
            # fault it measures. The first retry at the normal interval, then doubled, up to
            # 10 minutes; on the first success it returns to its pace.
            interval = SOURCES.get(name, 60)
            if "unavailable" in result:
                failures[name] = min(failures.get(name, 0) + 1, 6)
                next_run[name] = time.monotonic() + min(interval * (2 ** (failures[name] - 1)), 600)
            else:
                failures.pop(name, None)
            _log(_note_health(name, result, took, failures.get(name, 0),
                              time.time() + next_run[name] - time.monotonic()))
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
            if signatures.get(name) != sig:
                signatures[name] = sig
                _publish({name: result})
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
            if name not in SOURCES and name not in INACTIVE and name != "events":
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
        try:
            st = os.stat(CONFIG_PATH)
            return st.st_mtime_ns, st.st_size, st.st_ino
        except OSError:
            return None
    seen = stamp()
    while True:
        time.sleep(3)
        now = stamp()
        if now == seen:
            continue
        seen = now
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
        _send("config", {"reload": True})


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
        "displays": displays, "max_displays": MAX_SUBSCRIBERS, "actions": bool(ACTION_URL),
        "grace": SOURCE_GRACE, "config_errors": CONFIG_ERRORS, "keys": KEY_CHECKS,
        "notify": dict(notify.state, on=notify.enabled(), format=notify.FORMAT, problem=notify.problem())
        if notify.enabled() else {"on": False},
        "problems": [n for n, h in result.items() if h.get("alarm")],
        "sources": result,
    }


# ---------------------------------------------------------------------------------------------
# The server: static files + the SSE stream + the topology.
# ---------------------------------------------------------------------------------------------

TYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".woff2": "font/woff2",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "wall"

    def _headers(self, code, ctype, length=None, cache=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
        self._headers(code, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def _host_ok(self):
        return (self.headers.get("Host") or "").lower() in ALLOWED_HOSTS

    def do_GET(self):
        if not self._host_ok():
            # Text, not JSON: this is what someone sees on their first try on another port or name.
            # Echoing the Host back is safe as text/plain.
            host = (self.headers.get("Host") or "")[:200]
            body = (f"421: labhud does not answer to the name {host!r}.\n"
                    f"Add it to LABHUD_HOSTS (now: {','.join(sorted(ALLOWED_HOSTS))}).\n").encode()
            self._headers(421, "text/plain; charset=utf-8", len(body))
            return self.wfile.write(body)
        path = self.path.split("?", 1)[0]
        if path == "/api/stream":
            return self._stream()
        if path == "/api/config":
            return self._json(dict(config.public(TOPOLOGY), action_url=ACTION_URL))
        if path == "/api/snapshot":  # useful for debugging and for the check screenshots
            return self._json(snapshot())
        if path == "/api/status":
            return self._json(health())
        if path == "/api/history":
            return self._json(history())
        if path == "/status":
            path = "/status.html"
        return self._static(path)

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
    _log([("labhud started", False)])
    print(("DEMO MODE, made-up data; " if DEMO else "")
          + f"sources: {', '.join(sorted(ACTIVE)) or 'none'}; not configured: {', '.join(sorted(INACTIVE)) or 'none'}",
          flush=True)
    print(f"answering to: {', '.join(sorted(ALLOWED_HOSTS))} (LABHUD_HOSTS)", flush=True)
    # labhud has no login: whoever opens the page sees the buttons. The agent's own checks
    # (Origin + source IP allowlist, see docs/actions.md) are the only thing between a tap and the action.
    print(f"actions: on, sent to {ACTION_URL} (the agent must check Origin and the source IP)" if ACTION_URL
          else "actions: off (set LABHUD_ACTION_URL to enable)", flush=True)
    print(f"notifications: {notify.problem() or 'on, ' + notify.FORMAT}" if notify.enabled()
          else "notifications: off (set LABHUD_NOTIFY_URL to enable)", flush=True)
    if DEMO:
        seed_demo_history()
    threading.Thread(target=loop, daemon=True, name="collect").start()
    threading.Thread(target=watch_config, daemon=True, name="config").start()
    notify.start()
    threading.Thread(target=check_keys, daemon=True, name="keys").start()
    threading.Thread(target=sample_history, daemon=True, name="history").start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
