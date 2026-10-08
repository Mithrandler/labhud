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

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
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

# Demo mode: made-up data for demo/config.toml, no network, no keys, no ping, no actions.
DEMO = os.environ.get("LABHUD_DEMO", "").lower() in ("1", "true", "yes")
if DEMO:
    import demo
    os.environ.setdefault("LABHUD_CONFIG", demo.CONFIG)
    ACTION_URL = ""

TOPOLOGY = config.load()

# Only the sources whose settings are present run; the others are reported once as not configured.
ACTIVE, INACTIVE = (demo.sources(), {}) if DEMO else sources.load(TOPOLOGY)
STATUS_EVERY = 30  # the ping/TCP checks, handled here: they need the topology
# name -> interval in seconds. The paces follow how fast the measured thing changes, not "as often as possible".
SOURCES = {name: src.every for name, src in ACTIVE.items()}
SOURCES["status"] = STATUS_EVERY

# The ping/TCP targets, taken once from the topology.
# Only ping/tcp: the state of Proxmox guests comes from the `proxmox` source, it is not measured twice.
# (Without this filter a ping to the node's name is tried and every VM shows down.)
TARGETS = {}
# Source -> the card whose status (ping) says whether its host is up: a card that has a ping/tcp
# check and shows that source's data. When the host comes back (false -> true), the source is polled
# again after 15 s and its backoff is reset. Without this, after a host starts in the morning its
# source keeps "Host is unreachable" from its last try for up to 10 minutes.
SOURCE_HOST = {}
for _cards in TOPOLOGY["cards"].values():
    for _c in _cards:
        if _c.get("check") and _c["check"][0] in ("ping", "tcp"):
            TARGETS[_c["id"]] = _c["check"]
            _paths = [m[0] for m in _c.get("metrics", [])] + [_c[k] for k in ("list", "torrents") if k in _c]
            for _src in {p.split(".")[0] for p in _paths} & set(ACTIVE):
                SOURCE_HOST.setdefault(_src, _c["id"])

_data = {}
_lock = threading.Lock()
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
            _, result = _collect(name)
            # Progressive backoff on a failed source. Without it, a source that refuses
            # authentication is polled at its normal interval forever — and qBittorrent, for
            # example, bans the IP after too many failed logins, so retrying sustains the very
            # fault it measures. The first retry at the normal interval, then doubled, up to
            # 10 minutes; on the first success it returns to its pace.
            interval = SOURCES[name]
            if "unavailable" in result:
                failures[name] = min(failures.get(name, 0) + 1, 6)
                next_run[name] = time.monotonic() + min(interval * (2 ** (failures[name] - 1)), 600)
            else:
                failures.pop(name, None)
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
                _data[name] = result
            if signatures.get(name) != sig:
                signatures[name] = sig
                _publish({name: result})
        finally:
            with running_lock:
                running.discard(name)

    while True:
        now = time.monotonic()
        for name, interval in SOURCES.items():
            if now < next_run[name]:
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
    for _name, _needs in INACTIVE.items():
        _data[_name] = {"not_configured": True, "needs": _needs, "_t": int(time.time())}
    print(("DEMO MODE, made-up data; " if DEMO else "")
          + f"sources: {', '.join(sorted(ACTIVE)) or 'none'}; not configured: {', '.join(sorted(INACTIVE)) or 'none'}",
          flush=True)
    print(f"answering to: {', '.join(sorted(ALLOWED_HOSTS))} (LABHUD_HOSTS)", flush=True)
    # labhud has no login: whoever opens the page sees the buttons. The agent's own checks
    # (Origin + source IP allowlist, see docs/actions.md) are the only thing between a tap and the action.
    print(f"actions: on, sent to {ACTION_URL} (the agent must check Origin and the source IP)" if ACTION_URL
          else "actions: off (set LABHUD_ACTION_URL to enable)", flush=True)
    threading.Thread(target=loop, daemon=True, name="collect").start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
