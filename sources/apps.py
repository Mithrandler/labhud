# Self-hosted apps with an API key: Portainer, Paperless-ngx, Tautulli, Mealie.
#
#   LABHUD_PORTAINER_URL / _KEY    http://host:9000, an access token (My account > Access tokens)
#   LABHUD_PAPERLESS_URL / _TOKEN  http://host:8000, a token (My Profile > API token)
#   LABHUD_TAUTULLI_URL / _KEY     http://host:8181, the API key (Settings > Web Interface)
#   LABHUD_MEALIE_URL / _TOKEN     http://host:9925, an API token of an admin (User Profile > API Tokens)
#
# Data:
#   portainer.{environments, down, running, stopped, unhealthy, rows}   rows: one line per environment
#   paperless.{documents, inbox, characters}
#   tautulli.{streams, transcoding, bandwidth, now}                      bandwidth in bytes/s; now: who watches what
#   mealie.{recipes, users, categories, uncategorized}

from ._common import env, request, source


@source("portainer", every=120, env=("PORTAINER_URL", "PORTAINER_KEY"),
        title="Portainer", about="Environments and the containers on them: running, stopped, unhealthy.",
        hints={"PORTAINER_URL": "http://host:9000", "PORTAINER_KEY": "My account > Access tokens"})
def portainer():
    items = request(env("PORTAINER_URL").rstrip("/") + "/api/endpoints", {"X-API-Key": env("PORTAINER_KEY")}, timeout=15)
    running = stopped = unhealthy = down = 0
    rows = []
    for e in items if isinstance(items, list) else []:
        snap = (e.get("Snapshots") or [{}])[0] or {}
        up = e.get("Status") == 1
        r, s, u = (int(snap.get(k) or 0) for k in ("RunningContainerCount", "StoppedContainerCount", "UnhealthyContainerCount"))
        if up:
            running, stopped, unhealthy = running + r, stopped + s, unhealthy + u
        else:
            down += 1
        rows.append({"name": str(e.get("Name") or "?"), "value": f"{r} up, {s} stopped" if up else "down",
                     "bad": (not up) or u > 0})
    rows.sort(key=lambda r: (not r["bad"], r["name"].lower()))
    return {"environments": len(rows), "down": down, "running": running, "stopped": stopped, "unhealthy": unhealthy,
            "rows": rows or [{"name": "no environments", "value": "—"}]}


@source("paperless", every=600, env=("PAPERLESS_URL", "PAPERLESS_TOKEN"),
        title="Paperless-ngx", about="Documents, and how many wait in the inbox.",
        hints={"PAPERLESS_URL": "http://host:8000", "PAPERLESS_TOKEN": "My Profile > API token"})
def paperless():
    st = request(env("PAPERLESS_URL").rstrip("/") + "/api/statistics/",
                 {"Authorization": "Token " + env("PAPERLESS_TOKEN"), "Accept": "application/json"}, timeout=15)
    return {"documents": st.get("documents_total"), "inbox": st.get("documents_inbox"),
            "characters": st.get("character_count")}


@source("tautulli", every=30, env=("TAUTULLI_URL", "TAUTULLI_KEY"),
        title="Tautulli", about="Who is watching Plex now, and how much bandwidth it takes.",
        hints={"TAUTULLI_URL": "http://host:8181", "TAUTULLI_KEY": "Settings > Web Interface > API key"})
def tautulli():
    data = (request(env("TAUTULLI_URL").rstrip("/") + "/api/v2?cmd=get_activity&apikey=" + env("TAUTULLI_KEY"),
                    timeout=15).get("response") or {})
    if data.get("result") not in (None, "success"):
        raise RuntimeError(str(data.get("message") or "Tautulli refused the key")[:100])
    act = data.get("data") or {}
    sessions = act.get("sessions") or []
    now = [{"name": str(s.get("friendly_name") or "?"),
            "value": str(s.get("full_title") or "")[:50] + (" · paused" if s.get("state") == "paused" else "")} for s in sessions]
    return {"streams": int(act.get("stream_count") or 0), "transcoding": int(act.get("stream_count_transcode") or 0),
            "bandwidth": int(act.get("total_bandwidth") or 0) * 125,   # kbit/s -> bytes/s
            "now": now or [{"name": "nobody watching", "value": "—"}]}


@source("mealie", every=900, env=("MEALIE_URL", "MEALIE_TOKEN"),
        title="Mealie", about="Recipes, users and what is still uncategorized.",
        hints={"MEALIE_URL": "http://host:9925", "MEALIE_TOKEN": "User Profile > API Tokens (an admin's)"})
def mealie():
    st = request(env("MEALIE_URL").rstrip("/") + "/api/admin/about/statistics",
                 {"Authorization": "Bearer " + env("MEALIE_TOKEN")}, timeout=15)
    return {"recipes": st.get("totalRecipes"), "users": st.get("totalUsers"), "categories": st.get("totalCategories"),
            "uncategorized": st.get("uncategorizedRecipes")}
