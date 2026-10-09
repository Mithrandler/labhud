# Seerr (Jellyseerr / Overseerr): request counters + the titles waiting for approval.
#
#   LABHUD_SEERR_URL          http://host:5055
#   LABHUD_SEERR_KEY          Settings -> General -> API Key

from ._common import env, request, source


@source("seerr", every=300, env=("SEERR_URL", "SEERR_KEY"),
         title="Jellyseerr / Overseerr", about="Request counters and titles waiting for approval.",
         hints={"SEERR_URL": "http://host:5055", "SEERR_KEY": "Settings > General > API Key"})
def seerr():
    header = {"X-Api-Key": env("SEERR_KEY")}
    base = env("SEERR_URL").rstrip("/") + "/api/v1"
    counts = request(f"{base}/request/count", header, timeout=10) or {}
    waiting = []
    for r in ((request(f"{base}/request?take=4&sort=added&filter=pending", header, timeout=10) or {})
              .get("results") or []):
        m = r.get("media") or {}
        kind = "tv" if m.get("mediaType") == "tv" else "movie"
        try:
            d = request(f"{base}/{kind}/{m.get('tmdbId')}", header, timeout=10) or {}
            title = d.get("title") or d.get("name") or "?"
        except (OSError, ValueError):
            title = "?"
        waiting.append({"name": title, "value": (r.get("requestedBy") or {}).get("displayName") or ""})
    counts["waiting"] = waiting or [{"name": "no pending requests", "value": "—"}]
    return counts
