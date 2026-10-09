"""Setup mode: the first start, when there is no config.toml yet.

Instead of stopping on the missing file, labhud serves a setup page on its usual port: Proxmox
(address, token, which nodes and guests to show), the other sources (a form each, tried before
they are kept), the weather, the names the display will use. At the end it writes config.toml and
.env into the config file's folder, and starts again as the normal display, in the same process.

This is the only place where labhud receives keys over HTTP, so (docs/threat-model.md):
- every call needs the setup code, printed in the log at start (`docker logs labhud`): only who
  can read the log can set labhud up. Ten wrong codes lock the page for a minute;
- the Origin must match the Host (the browser's own page, not another site in the same browser),
  and the body must be JSON;
- nothing is sent back that was not typed in this session: no key from the environment;
- once config.toml exists, setup mode never starts again (LABHUD_SETUP=off disables it entirely).

What it serves is static/setup/ and the fonts, nothing else.
"""

import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import onboard
from sources._common import scrub

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static", "setup")
FONTS = os.path.join(HERE, "static", "fonts")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".woff2": "font/woff2"}
ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # nothing that reads as another (0/O, 1/I/L, U/V)
MAX_BODY = 64 * 1024
MAX_FAILS = 10
LOCK_SECONDS = 60
HOST_RE = re.compile(r"[A-Za-z0-9.-]{1,253}:\d{1,5}|\[[0-9A-Fa-f:.]{2,45}\]:\d{1,5}")


def enabled():
    return os.environ.get("LABHUD_SETUP", "on").lower() not in ("0", "off", "false", "no")


def new_code():
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(2))


class State:
    """What has been typed so far. Lives in memory only; a restart starts over with a new code."""

    def __init__(self, path, code=None):
        self.path = path
        self.folder = os.path.dirname(os.path.abspath(path))
        self.code = code or new_code()
        self.fails = 0
        self.locked_until = 0
        self.proxmox = None    # {"url", "token_id", "secret", "found", "pin"}
        self.sources = {}      # name -> {"LABHUD_X": value}, each tried successfully
        self.done = False
        self.lock = threading.Lock()

    def check_code(self, given):
        """True if `given` is the code. Counts failures and locks after MAX_FAILS."""
        with self.lock:
            now = time.time()
            if now < self.locked_until:
                return False
            ok = hmac.compare_digest((given or "").strip().upper().encode(), self.code.encode())
            if ok:
                self.fails = 0
            else:
                self.fails += 1
                if self.fails >= MAX_FAILS:
                    self.locked_until, self.fails = now + LOCK_SECONDS, 0
                    print(f"setup: {MAX_FAILS} wrong codes, locked for {LOCK_SECONDS} s", flush=True)
            return ok

    def writable(self):
        return os.access(self.folder, os.W_OK)

    # -- the steps ----------------------------------------------------------------------------

    def hello(self, host):
        return {"catalog": onboard.catalog(), "folder": self.folder, "writable": self.writable(),
                "host": host, "tried": sorted(self.sources),
                "proxmox": self._proxmox_view() if self.proxmox else None}

    def _proxmox_view(self):
        p = self.proxmox
        f = p["found"]
        return {"url": p["url"], "token_id": p["token_id"], "version": f["version"], "nodes": f["nodes"],
                "guests": {n: [[v, name, kind] for v, name, kind in g] for n, g in f["guests"].items()},
                "extra": f["extra"], "running": f.get("running", []), "fingerprint": p["pin"][1] if p["pin"] else None}

    def set_proxmox(self, body):
        url = str(body.get("url") or "").strip().rstrip("/")
        token_id, secret = str(body.get("token_id") or "").strip(), str(body.get("secret") or "").strip()
        if not url or not token_id or not secret:
            return {"ok": False, "error": "address, token ID and secret are all needed"}
        if not re.match(r"^https?://", url):
            url = "https://" + url
        if not re.search(r":\d+$", urllib.parse.urlsplit(url).netloc):
            url += ":8006"
        try:
            found = onboard.discover(url, token_id, secret)
        except Exception as e:
            return {"ok": False, "error": "could not read Proxmox: " + scrub(str(e).replace(secret, "<hidden>"))[:200]}
        pin = None
        if url.startswith("https://"):
            try:
                pin = onboard.fingerprint(url)
            except Exception:
                pin = None
        self.proxmox = {"url": url, "token_id": token_id, "secret": secret, "found": found, "pin": pin}
        return dict(ok=True, **self._proxmox_view())

    def forget_proxmox(self, _body):
        self.proxmox = None
        return {"ok": True}

    def weather(self, body):
        city = str(body.get("city") or "").strip()[:80]
        if not city:
            return {"ok": True, "weather": None}
        try:
            w = onboard.geocode(city)
        except Exception as e:
            return {"ok": False, "error": f"could not look it up: {scrub(e)[:120]}"}
        return {"ok": True, "weather": list(w) if w else None}

    def try_source(self, body):
        name = str(body.get("name") or "")
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        # a secret left empty on a second try keeps the one tried before
        kept = self.sources.get(name, {})
        values = {k: (str(v) if str(v).strip() else kept.get(k, "")) for k, v in values.items()}
        got = onboard.try_source(name, values)
        if got.get("ok"):
            fields = {f["name"] for e in onboard.catalog() if e["name"] == name for f in e["fields"]}
            self.sources[name] = {k: v.strip() for k, v in values.items() if k in fields and v.strip()}
        return got

    def suggest(self, body):
        """Known services on the hosts typed in (at most 32 names or addresses, no ranges)."""
        hosts = body.get("hosts") if isinstance(body.get("hosts"), list) else []
        try:
            found = onboard.suggest([str(h) for h in hosts])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        print(f"setup: looked for services on {len(hosts)} host(s), found {len(found)}", flush=True)
        return {"ok": True, "found": found}

    def forget_source(self, body):
        self.sources.pop(str(body.get("name") or ""), None)
        return {"ok": True}

    def build(self, plan):
        """(config text, env text, problems) for the plan the page sends."""
        problems = []
        title = str(plan.get("title") or "LABHUD").strip()[:40] or "LABHUD"
        hosts = sorted({h.strip() for h in plan.get("hosts") or [] if isinstance(h, str) and h.strip()})
        bad = [h for h in hosts if not HOST_RE.fullmatch(h)]
        if bad or not hosts:
            problems.append(f"names the display uses must be host:port, got {', '.join(bad) or 'none'}")
        weather = plan.get("weather")
        if weather is not None:
            try:
                lat, lon, tz, city = weather
                weather = (float(lat), float(lon), str(tz)[:64], str(city)[:80])
            except (TypeError, ValueError):
                problems.append("the weather location is not valid")
                weather = None
        found = include = None
        url = token_id = secret = pin = None
        if self.proxmox:
            p = self.proxmox
            found, url, token_id, secret = p["found"], p["url"], p["token_id"], p["secret"]
            pin = p["pin"] if plan.get("pin", True) else None
            include = set()
            for item in plan.get("include") or []:
                try:
                    include.add((str(item[0]), int(item[1])))
                except (TypeError, ValueError, IndexError):
                    pass
        services = [n for n in plan.get("services") or [] if n in self.sources]
        host = urllib.parse.urlsplit(url).hostname if url else ""
        text = onboard.build_config(found, host, weather, title, include, services)
        try:
            config.loads(text)
        except config.ConfigError as e:
            problems += e.problems
        env = onboard.build_env(found, url, token_id, secret, hosts, pin)
        values = {}
        for name in services or self.sources:
            values.update(self.sources.get(name, {}))
        env = onboard.merge_env(env, values)
        return text, env, problems

    def preview(self, plan):
        text, env, problems = self.build(plan)
        # the .env is shown by name only: its values are keys
        names = [line.split("=", 1)[0] for line in env.splitlines() if line.startswith("LABHUD_")]
        return {"ok": not problems, "problems": problems, "config": text, "env": names}

    def finish(self, plan):
        text, env, problems = self.build(plan)
        if problems:
            return {"ok": False, "problems": problems}
        if os.path.exists(self.path):
            return {"ok": False, "problems": [f"{self.path} exists now; nothing was written"]}
        if not self.writable():
            return {"ok": False, "problems": [f"cannot write to {self.folder}: mount it read-write"],
                    "config": text}
        env_path = os.path.join(self.folder, ".env")
        if os.path.exists(env_path):  # keep what is there: the new values go into it
            with open(env_path, encoding="utf-8") as f:
                env = onboard.merge_env(f.read(), {line.split("=", 1)[0]: line.split("=", 1)[1]
                                                   for line in env.splitlines() if line.startswith("LABHUD_")})
        _write(env_path, env, 0o600)
        _write(self.path, text, 0o644)
        self.done = True
        print(f"setup: wrote {self.path} and {env_path}; starting as the display", flush=True)
        return {"ok": True}


def _write(path, text, mode):
    tmp = f"{path}.tmp-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


STEPS = {"hello": None, "proxmox": "set_proxmox", "forget-proxmox": "forget_proxmox", "weather": "weather",
         "try": "try_source", "forget": "forget_source", "suggest": "suggest", "preview": "preview", "finish": "finish"}


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "labhud-setup"
        timeout = 60

        def log_message(self, *args):
            pass  # the steps log what matters; request lines would only repeat it

        def _send(self, code, ctype, body, cache="no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; form-action 'none'")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code=200):
            self._send(code, "application/json; charset=utf-8",
                       json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path.startswith("/fonts/"):
                base, name = FONTS, path[len("/fonts/"):]
            else:
                base, name = STATIC, ("index.html" if path in ("/", "/index.html") else path.lstrip("/"))
            target = os.path.normpath(os.path.join(base, name))
            if not target.startswith(base + os.sep) or not os.path.isfile(target):
                if not path.startswith(("/api/", "/fonts/")) and "." not in path.rsplit("/", 1)[-1]:
                    target = os.path.join(STATIC, "index.html")  # any page of the display: setup first
                else:
                    return self._json({"error": "not found"}, 404)
            with open(target, "rb") as f:
                body = f.read()
            ext = os.path.splitext(target)[1]
            self._send(200, TYPES.get(ext, "application/octet-stream"), body,
                       "public, max-age=31536000, immutable" if ext == ".woff2" else "no-store")

        def do_POST(self):
            m = re.fullmatch(r"/setup/api/([a-z-]{1,20})", self.path)
            host = (self.headers.get("Host") or "")[:300]
            origin = (self.headers.get("Origin") or "").lower()
            try:
                length = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                length = -1
            if not (m and m.group(1) in STEPS and 0 <= length <= MAX_BODY
                    and origin in (f"http://{host.lower()}", f"https://{host.lower()}")
                    and (self.headers.get("Content-Type") or "").startswith("application/json")):
                self.close_connection = True
                return self._json({"ok": False, "error": "forbidden"}, 403)
            body = self.rfile.read(length)
            if not state.check_code(self.headers.get("X-Labhud-Setup")):
                print(f"setup: wrong code from {self.client_address[0]}", flush=True)
                return self._json({"ok": False, "error": "wrong code (it is in labhud's log)"}, 403)
            if state.done:
                return self._json({"ok": True, "done": True})
            try:
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                return self._json({"ok": False, "error": "not JSON"}, 400)
            step = m.group(1)
            if step == "hello":
                return self._json(dict(ok=True, **state.hello(host)))
            try:
                answer = getattr(state, STEPS[step])(data)
            except Exception as e:  # a bug here must not show a key
                print(f"setup: {step} failed: {type(e).__name__}", flush=True)
                answer = {"ok": False, "error": f"{step} failed: {scrub(e)[:160]}"}
            self._json(answer)
            if step == "finish" and answer.get("ok"):
                threading.Thread(target=_restart, daemon=True).start()

    return Handler


def _restart():
    """The same process becomes the display: execv keeps the PID, so Docker sees no exit."""
    time.sleep(1.5)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable] + sys.argv)


def run(port, path, tls=(None, None)):
    """Serves the setup page until it has written the files, then restarts. Never returns."""
    state = State(path)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler_for(state))
    if tls[0]:
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls[0], tls[1] or None)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    scheme = "https" if tls[0] else "http"
    line = "=" * 64
    print(f"{line}\nSETUP MODE: there is no {path} yet.\n"
          f"Open {scheme}://<this machine>:{port}/#{state.code}\n"
          f"or {scheme}://<this machine>:{port}/ and enter the setup code:  {state.code}\n"
          f"Files will be written to {state.folder}"
          + ("" if state.writable() else " (NOT WRITABLE: mount it read-write, see docs/install.md)")
          + f"\n{line}", flush=True)
    httpd.serve_forever()
