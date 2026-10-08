# Jellyfin.
#
#   LABHUD_JELLYFIN_URL       http://host:8096
#   LABHUD_JELLYFIN_KEY       an API key (Dashboard -> API Keys)

from ._common import env, request, source


@source("jellyfin", every=300, env=("JELLYFIN_URL", "JELLYFIN_KEY"))
def jellyfin():
    # Jellyfin 12 dropped the /emby prefix and the key in the URL: the key goes in the Authorization header.
    header = {"Authorization": f'MediaBrowser Token="{env("JELLYFIN_KEY")}"'}
    base = env("JELLYFIN_URL").rstrip("/")
    counts = request(f"{base}/Items/Counts", header, timeout=12) or {}
    sessions = []
    try:
        for s in request(f"{base}/Sessions?activeWithinSeconds=900", header, timeout=12) or []:
            playing = s.get("NowPlayingItem")
            if playing:
                sessions.append({"user": s.get("UserName"), "title": playing.get("Name"),
                                 "client": s.get("Client")})
    except (OSError, ValueError):
        pass
    counts["sessions"] = sessions
    counts["streams"] = len(sessions)
    counts["now"] = [{"name": x["title"] or "?", "value": x["user"] or ""} for x in sessions] \
        or [{"name": "nobody watching", "value": "—"}]
    return counts
