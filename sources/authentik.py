# authentik: failed logins of the last 24 hours (and from where), the users, and outposts that stopped
# checking in.
#
#   LABHUD_AUTHENTIK_URL     https://auth.example.com (or the server's own http://host:9000)
#   LABHUD_AUTHENTIK_TOKEN   an API token of a user that may only view events, users and outposts
#
# Data: authentik.{failed_24h, users, outposts, outposts_down, rows}; `rows` is a list for a card:
# list = "authentik.rows".

import calendar
import time

from ._common import env, request, source


def _get(path):
    return request(env("AUTHENTIK_URL").rstrip("/") + "/api/v3" + path,
                   {"Authorization": "Bearer " + env("AUTHENTIK_TOKEN"),
                    "Accept": "application/json"}, timeout=15)


def _ts(iso):
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))   # UTC, whatever the season
    except (ValueError, TypeError):
        return 0


@source("authentik", every=300, env=("AUTHENTIK_URL", "AUTHENTIK_TOKEN"),
         title="authentik",
         about="Failed logins of the last day, users, and outposts that stopped checking in.",
         hints={"AUTHENTIK_URL": "https://auth.example.com or http://host:9000",
                "AUTHENTIK_TOKEN": "a token of a user that may only view events, users and outposts"})
def authentik():
    now = time.time()
    ev = _get("/events/events/?action=login_failed&ordering=-created&page_size=100")
    recent = [e for e in (ev.get("results") or []) if now - _ts(e.get("created")) < 86400]
    by_user = {}
    for e in recent:
        user = ((e.get("context") or {}).get("username")) or \
               ((e.get("user") or {}).get("username")) or "?"
        ip = e.get("client_ip") or "?"
        key = f"{user} from {ip}"
        by_user[key] = by_user.get(key, 0) + 1
    users = ((_get("/core/users/?page_size=1&is_active=true").get("pagination")) or {}).get("count") or 0
    outposts = _get("/outposts/instances/?page_size=50").get("results") or []
    down = []
    for o in outposts:
        h = _get(f"/outposts/instances/{o['pk']}/health/")
        seen = max((_ts(x.get("last_seen")) for x in h), default=0) if isinstance(h, list) else 0
        if not seen or now - seen > 600:
            down.append(o.get("name") or str(o.get("pk")))
    rows = []
    for key, n in sorted(by_user.items(), key=lambda kv: -kv[1])[:6]:
        rows.append({"name": key, "value": f"{n} failed", "bad": n >= 5})
    for name in down:
        rows.append({"name": f"outpost {name}", "value": "not checking in", "bad": True})
    rows.append({"name": "active users", "value": str(users)})
    return {"failed_24h": len(recent), "users": users, "outposts": len(outposts),
            "outposts_down": len(down), "rows": rows}
