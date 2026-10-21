# Healthchecks (healthchecks.io or self-hosted): the cron jobs and backups that ping it, and which
# of them are late.
#
#   LABHUD_HEALTHCHECKS_URL   https://healthchecks.io, or your own instance
#   LABHUD_HEALTHCHECKS_KEY   Project Settings -> API Access -> the READ-ONLY key is enough
#
# Data: healthchecks.{total, up, down, grace, paused, checks}; `checks` is a list for a card,
# late ones first: list = "healthchecks.checks".

from ._common import env, request, source

ORDER = {"down": 0, "grace": 1, "new": 2, "started": 3, "up": 4, "paused": 5}


@source("healthchecks", every=60, env=("HEALTHCHECKS_URL", "HEALTHCHECKS_KEY"),
         title="Healthchecks", about="Cron jobs and backups that ping Healthchecks, late ones first.",
         hints={"HEALTHCHECKS_URL": "https://healthchecks.io, or your own instance",
                "HEALTHCHECKS_KEY": "Project Settings > API Access: the read-only key is enough"})
def healthchecks():
    data = request(env("HEALTHCHECKS_URL").rstrip("/") + "/api/v3/checks/",
                   {"X-Api-Key": env("HEALTHCHECKS_KEY")}, timeout=10)
    checks = data.get("checks") or []
    out = {"total": len(checks)}
    for state in ("up", "down", "grace", "paused"):
        out[state] = sum(1 for c in checks if c.get("status") == state)
    rows = []
    for c in sorted(checks, key=lambda c: (ORDER.get(c.get("status"), 9), (c.get("name") or "").lower())):
        state = c.get("status") or "?"
        rows.append({"name": c.get("name") or c.get("slug") or "?",
                     "value": "late" if state == "grace" else state, "bad": state == "down"})
    out["checks"] = rows or [{"name": "no checks", "value": "—"}]
    return out
