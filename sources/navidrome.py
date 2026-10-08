# Navidrome, through its Subsonic API (user and password go in the query string, so keep it on
# the LAN, and use a dedicated account with no admin rights).
#
#   LABHUD_NAVIDROME_URL      http://host:4533
#   LABHUD_NAVIDROME_USER
#   LABHUD_NAVIDROME_PASS

import urllib.parse

from ._common import env, request, source


def _rest(path, **params):
    params.update({
        "u": env("NAVIDROME_USER"), "p": env("NAVIDROME_PASS"),
        "v": "1.16.1", "c": "labhud", "f": "json",
    })
    qs = urllib.parse.urlencode(params)
    body = request(f"{env('NAVIDROME_URL').rstrip('/')}/rest/{path}?{qs}", timeout=10) or {}
    answer = body.get("subsonic-response") or {}
    if answer.get("status") != "ok":
        raise RuntimeError((answer.get("error") or {}).get("message", "Navidrome error"))
    return answer


@source("navidrome", every=300, env=("NAVIDROME_URL", "NAVIDROME_USER", "NAVIDROME_PASS"))
def navidrome():
    playing = (_rest("getNowPlaying.view").get("nowPlaying") or {}).get("entry") or []
    sessions = [{"user": e.get("username"), "title": f"{e.get('artist', '?')} – {e.get('title', '?')}",
                 "state": e.get("state")} for e in playing]
    # The song count comes from Navidrome itself (getScanStatus: the files of the last scan).
    songs = int((_rest("getScanStatus.view").get("scanStatus") or {}).get("count") or 0)
    return {
        "listening": len(sessions),
        "users": len({s["user"] for s in sessions if s["user"]}),
        "songs": songs,
        "sessions": sessions,
        "now": [{"name": x["title"], "value": x["user"] or ""} for x in sessions]
               or [{"name": "nobody listening", "value": "—"}],
    }
