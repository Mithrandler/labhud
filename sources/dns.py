# DNS filters: Technitium, Pi-hole (v6) and AdGuard Home, each as its own source, all with the
# same numbers so a card works with any of them: queries, blocked, blocked_percent (last 24 h).
#
#   LABHUD_TECHNITIUM_URL     http://host:5380     LABHUD_TECHNITIUM_TOKEN   an API token (Administration >
#                                                  Sessions > Create Token: a login token expires)
#   LABHUD_PIHOLE_URL         http://host          LABHUD_PIHOLE_PASS        the app password (Settings >
#                                                  Web interface / API > App password)
#   LABHUD_ADGUARD_URL        http://host:3000     LABHUD_ADGUARD_USER / LABHUD_ADGUARD_PASS
#
# Data: <source>.{queries, blocked, blocked_percent, ...}; Technitium and AdGuard add top_blocked,
# a card list of the most blocked domains.

import json
import threading

from ._common import basic, env, percent, request, source

_pihole = {"sid": None}
_pihole_lock = threading.Lock()


@source("technitium", every=60, env=("TECHNITIUM_URL", "TECHNITIUM_TOKEN"),
        title="Technitium DNS", about="Queries and blocked share over the last day, and the most blocked domains.",
        hints={"TECHNITIUM_URL": "http://host:5380",
               "TECHNITIUM_TOKEN": "Administration > Sessions > Create Token (a login token expires silently)"})
def technitium():
    base = env("TECHNITIUM_URL").rstrip("/")
    data = request(f"{base}/api/dashboard/stats/get?token={env('TECHNITIUM_TOKEN')}&type=LastDay", timeout=10)
    if data.get("status") != "ok":
        raise RuntimeError(data.get("errorMessage") or f"Technitium answered {data.get('status')}")
    st = (data.get("response") or {}).get("stats") or {}
    queries, blocked = st.get("totalQueries") or 0, st.get("totalBlocked") or 0
    top = (data.get("response") or {}).get("topBlockedDomains") or []
    return {"queries": queries, "blocked": blocked, "blocked_percent": percent(blocked, queries),
            "clients": st.get("totalClients"), "cached": st.get("cachedEntries"),
            "top_blocked": [{"name": d.get("name", "?"), "value": str(d.get("hits", ""))} for d in top[:8]]
            or [{"name": "nothing blocked", "value": "—"}]}


def _pihole_get(base, path):
    with _pihole_lock:
        if not _pihole["sid"]:
            auth = request(f"{base}/api/auth", {"Content-Type": "application/json"},
                           data=json.dumps({"password": env("PIHOLE_PASS")}).encode(), timeout=10)
            sess = auth.get("session") or {}
            if not sess.get("valid"):
                raise RuntimeError("Pi-hole refused the password")
            _pihole["sid"] = sess.get("sid")
        sid = _pihole["sid"]
    try:
        return request(f"{base}{path}", {"X-FTL-SID": sid}, timeout=10)
    except OSError as e:
        if "401" in str(e):  # the session expired: log in again next time
            _pihole["sid"] = None
        raise


@source("pihole", every=60, env=("PIHOLE_URL", "PIHOLE_PASS"),
        title="Pi-hole", about="Queries and blocked share (Pi-hole v6 API).",
        hints={"PIHOLE_URL": "http://host", "PIHOLE_PASS": "an app password (Settings > Web interface / API)"})
def pihole():
    s = _pihole_get(env("PIHOLE_URL").rstrip("/"), "/api/stats/summary")
    q = s.get("queries") or {}
    return {"queries": q.get("total") or 0, "blocked": q.get("blocked") or 0,
            "blocked_percent": round(q.get("percent_blocked") or 0, 1),
            "clients": (s.get("clients") or {}).get("active"),
            "gravity": (s.get("gravity") or {}).get("domains_being_blocked")}


@source("adguard", every=60, env=("ADGUARD_URL", "ADGUARD_USER", "ADGUARD_PASS"),
        title="AdGuard Home", about="Queries, blocked share and the most blocked domains.",
        hints={"ADGUARD_URL": "http://host:3000"})
def adguard():
    base = env("ADGUARD_URL").rstrip("/")
    st = request(f"{base}/control/stats", {"Authorization": basic(env("ADGUARD_USER"), env("ADGUARD_PASS"))}, timeout=10)
    queries = st.get("num_dns_queries") or 0
    blocked = (st.get("num_blocked_filtering") or 0) + (st.get("num_replaced_safebrowsing") or 0)
    top = []
    for item in st.get("top_blocked_domains") or []:
        top += [{"name": k, "value": str(v)} for k, v in item.items()]
    return {"queries": queries, "blocked": blocked, "blocked_percent": percent(blocked, queries),
            "avg_ms": round((st.get("avg_processing_time") or 0) * 1000, 1),
            "top_blocked": top[:8] or [{"name": "nothing blocked", "value": "—"}]}
