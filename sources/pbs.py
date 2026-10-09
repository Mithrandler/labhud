# Proxmox Backup Server. Often kept off and woken only for backups, so an error here is usually
# just "host down" — mark its card `on_demand = true` in config.toml.
#
#   LABHUD_PBS_URL            https://host:8007
#   LABHUD_PBS_TOKEN_ID       user@realm!tokenname (Audit role is enough)
#   LABHUD_PBS_TOKEN_SECRET

import time

from ._common import beyond_reading, env, percent, request, rights, source


@rights("pbs")
def _rights():
    header = {"Authorization": f"PBSAPIToken={env('PBS_TOKEN_ID')}:{env('PBS_TOKEN_SECRET')}"}
    perms = request(f"{env('PBS_URL').rstrip('/')}/api2/json/access/permissions", header, timeout=8).get("data")
    return [("pbs", beyond_reading(perms))]


@source("pbs", every=60, env=("PBS_URL", "PBS_TOKEN_ID", "PBS_TOKEN_SECRET"),
         title="Proxmox Backup Server", about="Datastores, last tasks, and whether backups are recent.",
         hints={"PBS_URL": "https://host:8007", "PBS_TOKEN_ID": "user@realm!tokenname, with the Audit role"})
def pbs():
    header = {"Authorization": f"PBSAPIToken={env('PBS_TOKEN_ID')}:{env('PBS_TOKEN_SECRET')}"}
    url = env("PBS_URL").rstrip("/")
    data = request(f"{url}/api2/json/status/datastore-usage", header, timeout=8).get("data") or []
    total = sum(d.get("total") or 0 for d in data)
    used = sum(d.get("used") or 0 for d in data)
    now = int(time.time())
    failed = 0
    try:
        tasks = request(f"{url}/api2/json/nodes/localhost/tasks?errors=1&since={now - 86400}&limit=100",
                        header, timeout=8).get("data") or []
        failed = len(tasks)
    except (OSError, ValueError):
        pass
    out = {"used_percent": percent(used, total), "used": used, "total": total, "failed": failed}
    out.update(_upkeep(url, header, now))
    out.update(_datastores(data, now))
    return out


def _datastores(data, now):
    """One row per datastore, with PBS's own estimate of when it is full."""
    rows, soonest = [], None
    for d in data:
        p = percent(d.get("used"), d.get("total"))
        full = d.get("estimated-full-date")
        days = int((full - now) // 86400) if isinstance(full, (int, float)) and full > now else None
        if days is not None:
            soonest = days if soonest is None else min(soonest, days)
        rows.append({"name": d.get("store", "?"), "bad": (p or 0) >= 90 or (days is not None and days < 30),
                     "value": f"{p:.0f}%" + (f" · full in {days}d" if days is not None and days < 365 else "")
                     if p is not None else "?"})
    return {"datastores": rows or [{"name": "no datastores", "value": "—"}], "full_days": soonest}


def _upkeep(url, header, now):
    """Verify jobs and garbage collection over the last 7 days: failures, and how long ago GC ran."""
    out = {"verify_failed": 0, "gc_age_days": None}
    try:
        tasks = request(f"{url}/api2/json/nodes/localhost/tasks?since={now - 7 * 86400}&limit=500",
                        header, timeout=10).get("data") or []
    except (OSError, ValueError):
        return out
    gc_last = None
    for t in tasks:
        kind = t.get("worker_type") or ""
        ok = (t.get("status") or "OK") == "OK" or str(t.get("status")).startswith("WARNINGS")
        if kind in ("verificationjob", "verify", "verify_snapshot", "verify_group") and t.get("endtime") and not ok:
            out["verify_failed"] += 1
        if kind == "garbage_collection" and t.get("endtime") and ok:
            gc_last = max(gc_last or 0, t["endtime"])
    if gc_last:
        out["gc_age_days"] = int((now - gc_last) // 86400)
    return out
