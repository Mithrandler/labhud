# Nextcloud: its version, storage, users active in the last 5 minutes and 24 hours, and whether an
# update is waiting.
#
#   LABHUD_NEXTCLOUD_URL     https://cloud.example.com
#   LABHUD_NEXTCLOUD_TOKEN   the serverinfo token (read-only):
#                            occ config:app:set serverinfo token --value <random>
#
# Data: nextcloud.{version, users, active_5m, active_24h, files, free, update, apps_update, rows};
# `rows` is a list for a card: list = "nextcloud.rows".

from ._common import env, fmt_bytes, request, source


@source("nextcloud", every=300, env=("NEXTCLOUD_URL", "NEXTCLOUD_TOKEN"),
         title="Nextcloud",
         about="Version, storage, active users and waiting updates (the serverinfo token, read-only).",
         hints={"NEXTCLOUD_URL": "https://cloud.example.com",
                "NEXTCLOUD_TOKEN": "occ config:app:set serverinfo token --value <random>"})
def nextcloud():
    data = request(env("NEXTCLOUD_URL").rstrip("/")
                   + "/ocs/v2.php/apps/serverinfo/api/v1/info?format=json&skipApps=false&skipUpdate=false",
                   {"NC-Token": env("NEXTCLOUD_TOKEN"), "OCS-APIRequest": "true"}, timeout=15)
    d = ((data.get("ocs") or {}).get("data")) or {}
    nc = d.get("nextcloud") or {}
    system = nc.get("system") or {}
    storage = nc.get("storage") or {}
    active = d.get("activeUsers") or {}
    version = str(system.get("version") or "")
    free = system.get("freespace")
    users = storage.get("num_users")
    files = storage.get("num_files")
    active_5m = active.get("last5minutes")
    active_24h = active.get("last24hours")
    upd = system.get("update") or {}
    update = str(upd.get("available_version") or "") if upd.get("available") is True else ""
    apps_update = (system.get("apps") or {}).get("num_updates_available") or 0
    rows = [{"name": "version", "value": version or "—"}]
    if update:
        rows.append({"name": "update", "value": update + " is out"})
    if apps_update > 0:
        rows.append({"name": "app updates", "value": str(apps_update)})
    rows.append({"name": "users",
                 "value": f"{active_5m or 0} now · {active_24h or 0} today · {users or 0} in all"})
    if files is not None:
        rows.append({"name": "files", "value": str(files)})
    if free is not None:
        rows.append({"name": "free space", "value": fmt_bytes(free)})
    return {"version": version, "users": users or 0, "active_5m": active_5m or 0,
            "active_24h": active_24h or 0, "files": files or 0, "free": free or 0,
            "update": update, "apps_update": apps_update, "rows": rows}
