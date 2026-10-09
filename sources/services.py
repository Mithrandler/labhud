# Smaller integrations, one source each: container updates (What's Up Docker), Traefik, Speedtest
# Tracker, Immich and Home Assistant entities.
#
#   LABHUD_WUD_URL          http://host:3000          What's Up Docker (no key; keep it private)
#   LABHUD_TRAEFIK_URL      http://host:8080          Traefik's API (api.insecure, or behind auth:
#   LABHUD_TRAEFIK_AUTH     optional Authorization header, e.g. "Basic dXNlcjpwYXNz")
#   LABHUD_SPEEDTEST_URL    http://host:8765          Speedtest Tracker
#   LABHUD_SPEEDTEST_TOKEN  an API token (Settings > API tokens), with read results
#   LABHUD_IMMICH_URL       http://host:2283          Immich
#   LABHUD_IMMICH_KEY       an API key of an admin (server statistics are admin-only)
#   LABHUD_HA_URL           http://host:8123          Home Assistant
#   LABHUD_HA_TOKEN         a long-lived access token (Profile > Security)
#   LABHUD_HA_ENTITIES      entity ids, comma separated: sensor.rack_temperature,binary_sensor.ups_on_battery
#
# Data:
#   updates.{available, total, containers}         containers with a newer image first
#   traefik.{routers, router_errors, router_warnings, services, service_errors, problems}
#   speedtest.{download, upload, ping, at}          download/upload in bytes/s (format "rate")
#   immich.{photos, videos, usage, users}           usage in bytes
#   homeassistant.<domain>.<object>.{state, value, unit}: value is the state as a number when it is one

from ._common import env, request, source


@source("updates", every=900, env=("WUD_URL",),
        title="What's Up Docker", about="Containers with a newer image available.",
        hints={"WUD_URL": "http://host:3000"})
def updates():
    items = request(env("WUD_URL").rstrip("/") + "/api/containers", timeout=15) or []
    rows = []
    for c in items if isinstance(items, list) else []:
        image = c.get("image") or {}
        tag = (image.get("tag") or {}).get("value", "")
        if c.get("updateAvailable"):
            new = (c.get("result") or {}).get("tag") or "newer digest"
            rows.append({"name": c.get("displayName") or c.get("name") or "?", "value": f"{tag} → {new}", "bad": False, "_n": 0})
        else:
            rows.append({"name": c.get("displayName") or c.get("name") or "?", "value": tag or "up to date", "_n": 1})
    rows.sort(key=lambda r: (r["_n"], r["name"].lower()))
    return {"available": sum(1 for r in rows if r["_n"] == 0), "total": len(rows),
            "containers": [{k: v for k, v in r.items() if k != "_n"} for r in rows] or [{"name": "no containers", "value": "—"}]}


@source("traefik", every=60, env=("TRAEFIK_URL",),
        title="Traefik", about="Routers and services, and which of them Traefik reports as broken.",
        hints={"TRAEFIK_URL": "http://host:8080 (the API: api.insecure, or set LABHUD_TRAEFIK_AUTH)"})
def traefik():
    base = env("TRAEFIK_URL").rstrip("/")
    headers = {"Authorization": env("TRAEFIK_AUTH")} if env("TRAEFIK_AUTH") else None
    ov = request(f"{base}/api/overview", headers, timeout=10)
    out = {"routers": 0, "router_errors": 0, "router_warnings": 0, "services": 0, "service_errors": 0}
    for proto in ("http", "tcp", "udp"):
        part = ov.get(proto) or {}
        r, s = part.get("routers") or {}, part.get("services") or {}
        out["routers"] += r.get("total") or 0
        out["router_errors"] += r.get("errors") or 0
        out["router_warnings"] += r.get("warnings") or 0
        out["services"] += s.get("total") or 0
        out["service_errors"] += s.get("errors") or 0
    problems = []
    if out["router_errors"] or out["router_warnings"]:
        for r in request(f"{base}/api/http/routers?per_page=500", headers, timeout=10) or []:
            if r.get("status") != "enabled" or r.get("error"):
                problems.append({"name": r.get("name", "?"), "value": "; ".join(r.get("error") or []) or r.get("status", "?"), "bad": True})
    out["problems"] = problems or [{"name": "no broken router", "value": "—"}]
    return out


@source("speedtest", every=900, env=("SPEEDTEST_URL", "SPEEDTEST_TOKEN"),
        title="Speedtest Tracker", about="The last speed test: download, upload and ping.",
        hints={"SPEEDTEST_URL": "http://host:8765", "SPEEDTEST_TOKEN": "Settings > API tokens (read results)"})
def speedtest():
    d = (request(env("SPEEDTEST_URL").rstrip("/") + "/api/v1/results/latest",
                 {"Authorization": f"Bearer {env('SPEEDTEST_TOKEN')}", "Accept": "application/json"}, timeout=10) or {}).get("data") or {}
    # download/upload come in bytes per second (the *_bits fields are bits)
    return {"download": d.get("download"), "upload": d.get("upload"), "ping": d.get("ping"),
            "at": d.get("created_at") or d.get("updated_at"), "healthy": d.get("healthy")}


@source("immich", every=600, env=("IMMICH_URL", "IMMICH_KEY"),
        title="Immich", about="Photos, videos and the space they take.",
        hints={"IMMICH_URL": "http://host:2283", "IMMICH_KEY": "Account Settings > API Keys (an admin's: statistics are admin-only)"})
def immich():
    st = request(env("IMMICH_URL").rstrip("/") + "/api/server/statistics", {"x-api-key": env("IMMICH_KEY")}, timeout=15)
    return {"photos": st.get("photos"), "videos": st.get("videos"), "usage": st.get("usage"),
            "users": len(st.get("usageByUser") or [])}


@source("homeassistant", every=30, env=("HA_URL", "HA_TOKEN", "HA_ENTITIES"),
        title="Home Assistant", about="Chosen entities' states (a room's temperature, a UPS sensor...).",
        hints={"HA_URL": "http://host:8123", "HA_TOKEN": "Profile > Security > Long-lived access tokens",
               "HA_ENTITIES": "sensor.rack_temperature,binary_sensor.door"})
def homeassistant():
    base = env("HA_URL").rstrip("/")
    header = {"Authorization": f"Bearer {env('HA_TOKEN')}"}
    out = {}
    for entity in (e.strip() for e in env("HA_ENTITIES").split(",") if e.strip()):
        domain, _, obj = entity.partition(".")
        try:
            st = request(f"{base}/api/states/{entity}", header, timeout=8)
        except (OSError, ValueError) as e:
            out.setdefault(domain, {})[obj] = {"state": "unavailable", "error": str(e)[:80]}
            continue
        state = st.get("state")
        try:
            value = float(state)
        except (TypeError, ValueError):
            value = {"on": 1, "off": 0}.get(state)
        out.setdefault(domain, {})[obj] = {"state": state, "value": value,
                                           "unit": (st.get("attributes") or {}).get("unit_of_measurement", "")}
    return out
