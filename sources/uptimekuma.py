# Uptime Kuma: every monitor you already have there, so they are not defined twice.
#
#   LABHUD_UPTIMEKUMA_URL   http://host:3001
#   LABHUD_UPTIMEKUMA_KEY   Settings -> API Keys -> Add (it only reads /metrics)
#
# Data: uptimekuma.{total, up, down, pending, maintenance, monitors}; `monitors` is a list for a
# card, down first: list = "uptimekuma.monitors".

import re

from ._common import basic, env, request, source

# monitor_status{monitor_name="Gitea",monitor_type="http",...} 1
_LINE = re.compile(r'^(monitor_status|monitor_response_time)\{(.*)\}\s+([-0-9.eE+]+)\s*$')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
STATES = {0: "down", 1: "up", 2: "pending", 3: "maintenance"}


def parse(text):
    """{monitor name: {"status": n, "ms": n}} from Uptime Kuma's Prometheus text."""
    out = {}
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        labels = {k: v.replace('\\"', '"').replace("\\\\", "\\") for k, v in _LABEL.findall(m.group(2))}
        name = labels.get("monitor_name")
        if not name:
            continue
        key = "status" if m.group(1) == "monitor_status" else "ms"
        out.setdefault(name, {})[key] = float(m.group(3))
    return out


@source("uptimekuma", every=30, env=("UPTIMEKUMA_URL", "UPTIMEKUMA_KEY"))
def uptimekuma():
    text = request(env("UPTIMEKUMA_URL").rstrip("/") + "/metrics",
                   {"Authorization": basic("", env("UPTIMEKUMA_KEY"))}, timeout=10, raw=True)
    monitors = parse(text)
    out = {"total": len(monitors)}
    for state in STATES.values():
        out[state] = sum(1 for m in monitors.values() if STATES.get(int(m.get("status", -1))) == state)
    rows = []
    for name, m in monitors.items():
        state = STATES.get(int(m.get("status", -1)), "?")
        ms = m.get("ms")
        rows.append({"name": name, "bad": state == "down",
                     "value": f"{round(ms)} ms" if state == "up" and ms and ms > 0 else state})
    order = {"down": 0, "pending": 1, "maintenance": 2, "up": 3}
    rows.sort(key=lambda r: (order.get("up" if r["value"].endswith(" ms") else r["value"], 4), r["name"].lower()))
    out["monitors"] = rows or [{"name": "no monitors", "value": "—"}]
    return out
