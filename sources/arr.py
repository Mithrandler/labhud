# The *arr family: Sonarr, Radarr, Prowlarr, Bazarr — plus two views built from Sonarr and Radarr
# together: "recent" (last imports + queue) and "calendar" (what comes next). Those two run when
# at least one of Sonarr and Radarr is set.
#
#   LABHUD_SONARR_URL    http://host:8989     LABHUD_SONARR_KEY
#   LABHUD_RADARR_URL    http://host:7878     LABHUD_RADARR_KEY
#   LABHUD_PROWLARR_URL  http://host:9696     LABHUD_PROWLARR_KEY
#   LABHUD_BAZARR_URL    http://host:6767     LABHUD_BAZARR_KEY

import time

from ._common import ago, env, has, request, source

SONARR = ("SONARR_URL", "SONARR_KEY")
RADARR = ("RADARR_URL", "RADARR_KEY")


def _client(app, version="v3"):
    url, key = env(f"{app}_URL").rstrip("/"), env(f"{app}_KEY")
    return lambda path: request(f"{url}/api/{version}{path}", {"X-Api-Key": key}, timeout=12)


@source("sonarr", every=300, env=SONARR,
         title="Sonarr", about="Series: queue, missing episodes.",
         hints={"SONARR_URL": "http://host:8989", "SONARR_KEY": "Settings > General > API Key"})
def sonarr():
    c = _client("SONARR")
    series = c("/series")
    # includeSeries=true, otherwise the record has only seriesId, no title to show in the panel.
    missing = c("/wanted/missing?pageSize=8&sortKey=airDateUtc&sortDirection=descending"
                "&includeSeries=true") or {}
    return {
        "series": len(series) if isinstance(series, list) else 0,
        "wanted": missing.get("totalRecords", 0),
        "queued": (c("/queue?pageSize=1") or {}).get("totalRecords", 0),
        "missing": [f"{(r.get('series') or {}).get('title', '?')} "
                    f"S{r.get('seasonNumber', 0):02d}E{r.get('episodeNumber', 0):02d}"
                    for r in (missing.get("records") or [])],
    }


@source("radarr", every=300, env=RADARR,
         title="Radarr", about="Movies: queue, missing films.",
         hints={"RADARR_URL": "http://host:7878", "RADARR_KEY": "Settings > General > API Key"})
def radarr():
    c = _client("RADARR")
    movies = c("/movie")
    missing = c("/wanted/missing?pageSize=8") or {}
    return {
        "movies": len(movies) if isinstance(movies, list) else 0,
        "wanted": missing.get("totalRecords", 0),
        "queued": (c("/queue?pageSize=1") or {}).get("totalRecords", 0),
        "missing": [r.get("title", "?") for r in (missing.get("records") or [])],
    }


@source("prowlarr", every=300, env=("PROWLARR_URL", "PROWLARR_KEY"),
         title="Prowlarr", about="Indexers and which of them fail.",
         hints={"PROWLARR_URL": "http://host:9696", "PROWLARR_KEY": "Settings > General > API Key"})
def prowlarr():
    c = _client("PROWLARR", "v1")
    st = c("/indexerstats") or {}
    indexers = st.get("indexers") or []
    return {
        "numberOfGrabs": sum(i.get("numberOfGrabs") or 0 for i in indexers),
        "numberOfQueries": sum(i.get("numberOfQueries") or 0 for i in indexers),
        "numberOfFailGrabs": sum(i.get("numberOfFailedGrabs") or 0 for i in indexers),
        "numberOfFailQueries": sum(i.get("numberOfFailedQueries") or 0 for i in indexers),
        "indexers": [{"name": i.get("indexerName", "?"), "grabs": i.get("numberOfGrabs") or 0,
                      "failed": i.get("numberOfFailedGrabs") or 0} for i in indexers],
    }


@source("bazarr", every=300, env=("BAZARR_URL", "BAZARR_KEY"),
         title="Bazarr", about="Missing subtitles.",
         hints={"BAZARR_URL": "http://host:6767", "BAZARR_KEY": "Settings > General > API Key"})
def bazarr():
    header = {"X-API-KEY": env("BAZARR_KEY")}
    base = env("BAZARR_URL").rstrip("/")
    ep = request(f"{base}/api/episodes/wanted?length=1", header, timeout=12) or {}
    mv = request(f"{base}/api/movies/wanted?length=1", header, timeout=12) or {}
    return {"missingEpisodes": ep.get("total", 0), "missingMovies": mv.get("total", 0)}


@source("recent", every=300, any_of=(SONARR, RADARR),
         title="Recently added", about="Last imports and the queue, from Sonarr and Radarr together.")
def recent():
    """The last 5 imported movies/series (Radarr + Sonarr history) and what is downloading now."""
    imported, queue = [], []
    if has(*SONARR):
        try:
            c = _client("SONARR")
            h = c("/history?pageSize=30&eventType=3&sortKey=date&sortDirection=descending"
                  "&includeSeries=true&includeEpisode=true") or {}
            for r in h.get("records") or []:
                ep = r.get("episode") or {}
                imported.append({"name": f"{(r.get('series') or {}).get('title', '?')} "
                                         f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}",
                                 "series": (r.get("series") or {}).get("title", "?"), "date": r.get("date") or ""})
            for r in (c("/queue?pageSize=5&includeSeries=true&includeEpisode=true") or {}).get("records") or []:
                ep = r.get("episode") or {}
                size = r.get("size") or 0
                queue.append({"name": f"{(r.get('series') or {}).get('title', '?')} "
                                      f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}",
                              "value": f"{round(100 * (1 - (r.get('sizeleft') or 0) / size))}%" if size else "—"})
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if has(*RADARR):
        try:
            c = _client("RADARR")
            h = c("/history?pageSize=30&eventType=3&sortKey=date&sortDirection=descending&includeMovie=true") or {}
            for r in h.get("records") or []:
                t = (r.get("movie") or {}).get("title", "?")
                imported.append({"name": t, "series": t, "date": r.get("date") or ""})
            for r in (c("/queue?pageSize=5&includeMovie=true") or {}).get("records") or []:
                size = r.get("size") or 0
                queue.append({"name": (r.get("movie") or {}).get("title", "?"),
                              "value": f"{round(100 * (1 - (r.get('sizeleft') or 0) / size))}%" if size else "—"})
        except (OSError, ValueError, KeyError, TypeError):
            pass
    imported.sort(key=lambda x: x["date"], reverse=True)
    seen, result = set(), []
    for x in imported:              # one row per title: the most recent episode/import
        if x["series"] in seen:
            continue
        seen.add(x["series"])
        result.append({"name": x["name"], "value": ago(x["date"])})
    return {"added": result[:5], "queue": queue[:4] or [{"name": "queue empty", "value": "—"}]}


@source("calendar", every=600, any_of=(SONARR, RADARR),
         title="Calendar", about="What comes next, from Sonarr and Radarr together.")
def calendar():
    """The next 5 movies/episodes, from the Radarr and Sonarr calendars, merged and sorted by date.

    Movies: the nearest future release date (cinema/digital/physical); the ones already on disk
    are skipped. Series: only each series' next episode, so weekly episodes of the same title do
    not fill the list.
    """
    today = time.strftime("%Y-%m-%d", time.gmtime())
    until = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 180 * 86400))
    items = []
    if has(*SONARR):
        try:
            c = _client("SONARR")
            seen = set()
            for e in sorted(c(f"/calendar?start={today}&end={until}&includeSeries=true") or [],
                            key=lambda e: e.get("airDate") or "~"):
                series = (e.get("series") or {}).get("title", "?")
                if series in seen or not e.get("airDate"):
                    continue
                seen.add(series)
                items.append({"name": f"{series} S{e.get('seasonNumber', 0):02d}E{e.get('episodeNumber', 0):02d}",
                              "date": e["airDate"]})
        except (OSError, ValueError, KeyError):
            pass
    if has(*RADARR):
        try:
            c = _client("RADARR")
            for f in c(f"/calendar?start={today}&end={until}") or []:
                if f.get("hasFile"):
                    continue
                dates = sorted(d[:10] for d in (f.get("inCinemas"), f.get("digitalRelease"), f.get("physicalRelease"))
                               if d and d[:10] >= today)
                if dates:
                    items.append({"name": f.get("title", "?"), "date": dates[0]})
        except (OSError, ValueError, KeyError):
            pass
    items.sort(key=lambda x: x["date"])
    return {"items": [{"name": i["name"], "value": i["date"][5:]} for i in items[:5]]}
