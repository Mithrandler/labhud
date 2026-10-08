# OPNsense.
#
#   LABHUD_OPNSENSE_URL       https://router
#   LABHUD_OPNSENSE_KEY       API key of a user with exactly these two privileges:
#   LABHUD_OPNSENSE_SECRET
#     page-diagnostics-system-activity -> /api/diagnostics/activity/getActivity  (CPU, RAM)
#     page-status-trafficgraph         -> /api/diagnostics/traffic/*            (traffic)
# Any other endpoint returns 403; do not add others without granting the privilege too.

import time

from ._common import basic, env, percent, request, source


def _activity(url, header):
    """`getActivity` returns top's output; CPU and RAM are read from its header lines."""
    data = request(f"{url}/api/diagnostics/activity/getActivity", header, timeout=8)
    cpu = mem = None
    for line in data.get("headers") or []:
        text = line.replace("&nbsp;", " ")
        if "idle" in text and cpu is None:
            # "CPU:  0.4% user,  0.0% nice,  1.2% system,  0.2% interrupt, 98.2% idle"
            for part in text.split(","):
                if "idle" in part:
                    try:
                        cpu = round(100.0 - float(part.strip().split("%")[0]), 1)
                    except ValueError:
                        pass
        if text.startswith("Mem:") and mem is None:
            # "Mem: 694M Active, 10G Inact, 3801M Wired, 2058K Buf, 1083M Free"
            #
            # `Inact` is NOT used memory: on FreeBSD it is pages that can be reclaimed at once, and
            # it is usually the biggest number on the line. Counted as used, the router would show
            # ~93% forever and the card would say nothing. Used = Active + Wired + Laundry + Buf.
            units = {"K": 1 / 1024, "M": 1, "G": 1024}
            available_labels = ("free", "inact", "cache")
            used = free = 0.0
            for part in text[4:].split(","):
                p = part.strip().split()
                if len(p) != 2:
                    continue
                val, label = p
                try:
                    n = float(val[:-1]) * units.get(val[-1].upper(), 1)
                except ValueError:
                    continue
                if label.lower().startswith(available_labels):
                    free += n
                else:
                    used += n
            mem = percent(used, used + free)
    return cpu, mem


_previous_traffic = {}


@source("opnsense", every=5, env=("OPNSENSE_URL", "OPNSENSE_KEY", "OPNSENSE_SECRET"))
def opnsense():
    url = env("OPNSENSE_URL").rstrip("/")
    header = {"Authorization": basic(env("OPNSENSE_KEY"), env("OPNSENSE_SECRET"))}
    out = {}
    try:
        out["cpu"], out["mem"] = _activity(url, header)
    except (OSError, ValueError):
        out["cpu"] = out["mem"] = None

    # Per-interface counters: the rate is computed here, from the difference to the previous read.
    interfaces = request(f"{url}/api/diagnostics/traffic/interface", header, timeout=8)
    now = time.monotonic()
    rates = {}
    for name, data in (interfaces.get("interfaces") or {}).items():
        bytes_in = float(data.get("bytes received") or data.get("bytes_received") or 0)
        bytes_out = float(data.get("bytes transmitted") or data.get("bytes_transmitted") or 0)
        old = _previous_traffic.get(name)
        if old and now > old[0]:
            dt = now - old[0]
            rates[name] = {"dn": max(0.0, (bytes_in - old[1]) / dt), "up": max(0.0, (bytes_out - old[2]) / dt)}
        _previous_traffic[name] = (now, bytes_in, bytes_out)
    out["interfaces"] = rates
    wan = rates.get("wan") or {}
    out["wan_dn"], out["wan_up"] = wan.get("dn"), wan.get("up")

    # Per-IP traffic on the LAN (iftop on the router), for the bottom strip.
    try:
        out["top_lan"] = request(f"{url}/api/diagnostics/traffic/top/lan", header, timeout=10)
    except (OSError, ValueError):
        out["top_lan"] = {}
    return out
