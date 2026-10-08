#!/usr/bin/env python3
"""An optional agent for a labhud host: sensor readings, and actions run from the display.

Standard library only. Run it on the machine whose sensors you want, or that should run the
actions (a Proxmox host, for example).

    GET  /                  {cpu_temp, gpu_count, gpu<N>_util, gpu<N>_mem, gpu<N>_temp, ...}
    POST /action/<name>     runs the command configured for <name>; answers {ok, action, error?}

Point labhud at it with LABHUD_JSON_<NAME>_URL (sensors; then `sensors = "<name>"` on a host
card) and LABHUD_ACTION_URL=http://<this host>:9189/action/ (actions).

Actions are OFF unless LABHUD_AGENT_ACTIONS and LABHUD_AGENT_ALLOW are set, plus one of the two
ways of proving where an action comes from:

    LABHUD_AGENT_ACTIONS   path to a TOML file:  [actions]  wol-nas = ["wakeonlan", "aa:bb:..."]
    LABHUD_AGENT_SECRET    signed actions (recommended): the same secret as LABHUD_ACTION_SECRET
                           on the labhud server, which forwards each action with an HMAC of it.
                           Then LABHUD_AGENT_ALLOW is the labhud server's IP.
    LABHUD_AGENT_ORIGIN    direct actions: the display's origin exactly as the browser sends it,
                           e.g. "http://192.0.2.5:8095". Then LABHUD_AGENT_ALLOW is the device the
                           display runs on (a wall tablet), NOT the labhud server.
    LABHUD_AGENT_ALLOW     comma-separated IPs allowed to POST.

With a secret, a signature older than 30 seconds or seen before is refused, so a copied request
cannot be replayed. Without one, both checks are needed: Origin stops other web pages from
posting through a visitor's browser, but anything that is not a browser can fake it; the IP
allowlist is what stops the rest.
Commands are argument lists, never passed through a shell; nothing from the request reaches
them except the action's name, which only selects one. See docs/actions.md.

Other settings: LABHUD_AGENT_PORT (9189), LABHUD_AGENT_TIMEOUT (seconds per command, 30).
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import glob
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib

PORT = int(os.environ.get("LABHUD_AGENT_PORT", "9189"))
TIMEOUT = int(os.environ.get("LABHUD_AGENT_TIMEOUT", "30"))
ORIGIN = os.environ.get("LABHUD_AGENT_ORIGIN", "")
ALLOW = {x.strip() for x in os.environ.get("LABHUD_AGENT_ALLOW", "").split(",") if x.strip()}
SECRET = os.environ.get("LABHUD_AGENT_SECRET", "").encode()
MAX_AGE = 30  # seconds a signature stays valid
ACTIONS_FILE = os.environ.get("LABHUD_AGENT_ACTIONS", "")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
CACHE_S = 5  # nvidia-smi takes ~100 ms; do not run it on every request


def load_actions():
    if not (ACTIONS_FILE and (SECRET or ORIGIN) and ALLOW):
        return {}
    with open(ACTIONS_FILE, "rb") as f:
        raw = tomllib.load(f).get("actions", {})
    actions = {}
    for name, argv in raw.items():
        if not NAME_RE.fullmatch(name):
            sys.exit(f"{ACTIONS_FILE}: action name {name!r} may only use a-z, 0-9 and '-'")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            sys.exit(f"{ACTIONS_FILE}: action {name!r} must be a list of strings, e.g. [\"systemctl\", \"start\", \"x\"]")
        actions[name] = argv
    return actions


ACTIONS = load_actions()
_running = {name: threading.Lock() for name in ACTIONS}  # one run of the same action at a time


# ---------------------------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------------------------

def _read(path):
    with open(path) as f:
        return f.read().strip()


def cpu_temp():
    """The package temperature (coretemp, k10temp or zenpower), else the hottest core."""
    for hw in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            if _read(os.path.join(hw, "name")) not in ("coretemp", "k10temp", "zenpower"):
                continue
        except OSError:
            continue
        values = []
        for label in glob.glob(os.path.join(hw, "temp*_label")):
            try:
                name = _read(label)
                value = int(_read(label.replace("_label", "_input"))) / 1000
            except (OSError, ValueError):
                continue
            if name.startswith("Package") or name == "Tctl":
                return round(value, 1)
            values.append(value)
        if values:
            return round(max(values), 1)
    return None


def gpus():
    if not shutil.which("nvidia-smi"):
        return []
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    ).stdout
    found = []
    for line in out.strip().splitlines():
        try:
            index, temp, util, used, total, power = [x.strip() for x in line.split(",")]
            found.append({"index": int(index), "temp": float(temp), "util": float(util),
                          "mem": round(float(used) * 100 / float(total), 1),
                          "power": float(power) if power not in ("[N/A]", "") else None})
        except (ValueError, ZeroDivisionError):
            continue
    return found


_cache = {"t": 0.0, "data": None}


def sensors():
    now = time.monotonic()
    if _cache["data"] is None or now - _cache["t"] > CACHE_S:
        data = {"cpu_temp": cpu_temp()}
        found = gpus()
        data["gpu_count"] = len(found)
        # flat keys, so a card metric can address one directly: "temp.pve.gpu0_temp"
        for g in found:
            n = g["index"]
            data.update({f"gpu{n}_util": g["util"], f"gpu{n}_mem": g["mem"],
                         f"gpu{n}_temp": g["temp"], f"gpu{n}_power": g["power"]})
        _cache.update(t=now, data=data)
    return _cache["data"]


# ---------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------

def run_action(name):
    """(http status, answer). The name is already known to be in ACTIONS."""
    lock = _running[name]
    if not lock.acquire(blocking=False):
        return 409, {"ok": False, "action": name, "error": "already running"}
    try:
        r = subprocess.run(ACTIONS[name], capture_output=True, text=True, timeout=TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 504, {"ok": False, "action": name, "error": f"no answer in {TIMEOUT}s"}
    except OSError as e:
        return 500, {"ok": False, "action": name, "error": e.strerror or str(e)}
    finally:
        lock.release()
    if r.returncode != 0:
        last = (r.stderr.strip() or r.stdout.strip() or f"exit code {r.returncode}").splitlines()[-1]
        return 500, {"ok": False, "action": name, "error": last[:200]}
    return 200, {"ok": True, "action": name}


_seen = {}  # signature -> when it was used, for MAX_AGE: each one works once
_seen_lock = threading.Lock()


def signed(name, headers, now=None):
    """Whether the request carries labhud's signature of this action, fresh and not seen before."""
    now = now or time.time()
    ts, nonce = headers.get("X-Labhud-Time", ""), headers.get("X-Labhud-Nonce", "")
    sig = headers.get("X-Labhud-Signature", "")
    if not (ts.isdigit() and abs(now - int(ts)) <= MAX_AGE and 8 <= len(nonce) <= 64):
        return False
    expected = hmac.new(SECRET, f"{ts}.{nonce}.{name}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    with _seen_lock:
        for old in [s for s, t in _seen.items() if now - t > MAX_AGE]:
            del _seen[old]
        if sig in _seen:
            return False
        _seen[sig] = now
    return True


def allowed(name, headers, ip):
    if name not in ACTIONS or ip not in ALLOW:
        return False
    return signed(name, headers) if SECRET else headers.get("Origin") == ORIGIN


class Handler(BaseHTTPRequestHandler):
    def _answer(self, code, data, cors=False):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cors and ORIGIN and not SECRET:
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/":
            return self._answer(404, {"error": "not found"})
        try:
            self._answer(200, sensors())
        except Exception as e:  # always JSON, so the card shows the error instead of hanging
            self._answer(500, {"error": str(e)})

    def do_POST(self):
        m = re.fullmatch(r"/action/([a-z0-9-]{1,64})", self.path)
        # One answer for every refusal: a caller that fails a check learns nothing about which one.
        if not m or not allowed(m.group(1), self.headers, self.client_address[0]):
            return self._answer(403, {"ok": False, "error": "forbidden"})
        name = m.group(1)
        code, data = run_action(name)
        print(f"action {name} from {self.client_address[0]}: {'ok' if data['ok'] else data['error']}", flush=True)
        self._answer(code, data, cors=True)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"labhud-agent on :{PORT}; actions: "
          + (", ".join(sorted(ACTIONS)) + (" (signed" if SECRET else f" (origin {ORIGIN}")
             + f", from {', '.join(sorted(ALLOW))})" if ACTIONS
             else "off (needs LABHUD_AGENT_ACTIONS, LABHUD_AGENT_ALLOW and LABHUD_AGENT_SECRET or _ORIGIN)"),
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
