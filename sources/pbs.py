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
    failed = 0
    try:
        since = int(time.time()) - 86400
        tasks = request(f"{url}/api2/json/nodes/localhost/tasks?errors=1&since={since}&limit=100",
                        header, timeout=8).get("data") or []
        failed = len(tasks)
    except (OSError, ValueError):
        pass
    return {"used_percent": percent(used, total), "used": used, "total": total, "failed": failed}
