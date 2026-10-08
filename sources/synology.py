# Synology DSM 7. The session holds, so it is reused until it drops.
#
#   LABHUD_SYNOLOGY_URL       http://nas:5000
#   LABHUD_SYNOLOGY_USER      a DSM account allowed to read system utilisation and storage
#   LABHUD_SYNOLOGY_PASS

import urllib.parse

from ._common import env, percent, request, source

_sid = {"value": None}


def _url():
    return env("SYNOLOGY_URL").rstrip("/")


def _login():
    # POST, not GET: with GET the password lands in the query string, so in DSM's access logs
    # and in any error message that repeats the requested address.
    body = urllib.parse.urlencode({
        "api": "SYNO.API.Auth", "version": "3", "method": "login",
        "account": env("SYNOLOGY_USER"), "passwd": env("SYNOLOGY_PASS"),
        "session": "Core", "format": "sid",
    }).encode()
    data = request(f"{_url()}/webapi/auth.cgi", {"Content-Type": "application/x-www-form-urlencoded"},
                   data=body, timeout=10)
    sid = (data.get("data") or {}).get("sid")
    if not sid:
        raise RuntimeError("DSM login failed")
    _sid["value"] = sid
    return sid


def _call(api, method, version, extra=""):
    for attempt in (1, 2):
        sid = _sid["value"] or _login()
        url = f"{_url()}/webapi/entry.cgi?api={api}&version={version}&method={method}&_sid={sid}{extra}"
        data = request(url, timeout=10)
        if data.get("success"):
            return data.get("data") or {}
        _sid["value"] = None  # session expired: one more try, with a new one
        if attempt == 2:
            raise RuntimeError(f"DSM {api} returned an error")
    return {}


@source("synology", every=15, env=("SYNOLOGY_URL", "SYNOLOGY_USER", "SYNOLOGY_PASS"))
def synology():
    util = _call("SYNO.Core.System.Utilization", "get", "1")
    cpu = util.get("cpu") or {}
    mem = util.get("memory") or {}
    load = None
    try:
        load = round(float(cpu.get("user_load", 0)) + float(cpu.get("system_load", 0)), 1)
    except (TypeError, ValueError):
        pass
    storage = _call("SYNO.Storage.CGI.Storage", "load_info", "1")
    # DSM gives `used`, NOT `free`, in `size` (the keys are: total, used, total_device, *_inode).
    # Reading `free` always gives 0, and the card shows "FREE 0B" on a half-empty volume.
    used = total = 0
    for v in storage.get("volumes") or []:
        size = v.get("size") or {}
        total += int(size.get("total") or 0)
        used += int(size.get("used") or 0)
    return {
        "cpu": load,
        "mem": mem.get("real_usage"),
        "free": max(0, total - used), "used": used, "total": total,
        "used_of": [used, total],
        "used_percent": percent(used, total),
        "traffic": util.get("network") or [],
    }
