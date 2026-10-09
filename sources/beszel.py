# Beszel: every system its agents report, with CPU, RAM and disk, without a labhud agent on each.
#
#   LABHUD_BESZEL_URL    http://host:8090
#   LABHUD_BESZEL_USER   a Beszel user (an e-mail address); a read-only user is enough
#   LABHUD_BESZEL_PASS
#
# Data: beszel.{total, up, down, systems}; `systems` is a card list, down first
# (list = "beszel.systems"), and each system as beszel.<name>.{up, cpu, mem, disk} for metrics
# (the name lowercase, anything but letters and digits as "_").

import json
import re
import threading
import time

from ._common import env, request, source

_token = {"value": None, "at": 0}
_lock = threading.Lock()


def _auth(base):
    with _lock:
        if _token["value"] and time.time() - _token["at"] < 3600:
            return _token["value"]
        r = request(f"{base}/api/collections/users/auth-with-password", {"Content-Type": "application/json"},
                    data=json.dumps({"identity": env("BESZEL_USER"), "password": env("BESZEL_PASS")}).encode(), timeout=10)
        if not r.get("token"):
            raise RuntimeError("Beszel refused the login")
        _token.update(value=r["token"], at=time.time())
        return r["token"]


def key(name):
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "system"


def summarize(items):
    out = {"total": 0, "up": 0, "down": 0}
    rows = []
    for s in items:
        info = s.get("info") or {}
        up = s.get("status") == "up"
        paused = s.get("status") == "paused"
        out["total"] += 1
        out["up" if up else "down"] += 0 if paused else 1
        out[key(s.get("name"))] = {"up": up, "cpu": info.get("cpu"), "mem": info.get("mp"), "disk": info.get("dp")}
        if up:
            value = " · ".join(f"{label} {round(info[k])}%" for label, k in (("CPU", "cpu"), ("RAM", "mp"), ("disk", "dp"))
                               if isinstance(info.get(k), (int, float)))
            bad = any((info.get(k) or 0) >= 92 for k in ("cpu", "mp", "dp"))
        else:
            value, bad = (s.get("status") or "?"), not paused
        rows.append({"name": s.get("name") or "?", "value": value, "bad": bad})
    rows.sort(key=lambda r: (not r["bad"], r["name"].lower()))
    out["systems"] = rows or [{"name": "no systems", "value": "—"}]
    return out


@source("beszel", every=30, env=("BESZEL_URL", "BESZEL_USER", "BESZEL_PASS"),
        title="Beszel", about="Every system Beszel's agents report: up or down, CPU, RAM and disk.",
        hints={"BESZEL_URL": "http://host:8090", "BESZEL_USER": "a Beszel user's e-mail (read-only is enough)"})
def beszel():
    base = env("BESZEL_URL").rstrip("/")
    try:
        data = request(f"{base}/api/collections/systems/records?perPage=500&sort=name",
                       {"Authorization": _auth(base)}, timeout=10)
    except OSError as e:
        if "401" in str(e) or "403" in str(e):
            _token["value"] = None
        raise
    return summarize(data.get("items") or [])
