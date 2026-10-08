# qBittorrent Web UI.
#
#   LABHUD_QBITTORRENT_URL    http://host:8080
#   LABHUD_QBITTORRENT_USER
#   LABHUD_QBITTORRENT_PASS
#
# The snapshot key is "qbt".

import urllib.error
import urllib.parse
import urllib.request

from ._common import env, request, source

_cookie = {"value": None}


def _url():
    return env("QBITTORRENT_URL").rstrip("/")


def _login():
    data = urllib.parse.urlencode({
        "username": env("QBITTORRENT_USER"), "password": env("QBITTORRENT_PASS"),
    }).encode()
    req = urllib.request.Request(f"{_url()}/api/v2/auth/login", data=data, headers={"Referer": _url()})
    with urllib.request.urlopen(req, timeout=10) as r:
        # qBittorrent 5.2 answers a successful login with 204 and an empty body, not "Ok."
        cookie = r.headers.get("Set-Cookie") or ""
    # The cookie name varies with the version: it used to be "SID", 5.2.3 calls it "QBT_SID_<port>".
    # Take the name=value pair before the first ";", whatever its name.
    pair = cookie.split(";", 1)[0].strip()
    if "=" not in pair:
        raise RuntimeError("qBittorrent login failed")
    _cookie["value"] = pair
    return pair


def _call(path):
    for attempt in (1, 2):
        cookie = _cookie["value"] or _login()
        try:
            return request(f"{_url()}{path}", {"Cookie": cookie, "Referer": _url()}, timeout=10)
        except urllib.error.HTTPError as e:
            if e.code != 403 or attempt == 2:
                raise
            _cookie["value"] = None
    return {}


@source("qbt", every=10, env=("QBITTORRENT_URL", "QBITTORRENT_USER", "QBITTORRENT_PASS"))
def qbittorrent():
    transfer = _call("/api/v2/transfer/info")
    items = _call("/api/v2/torrents/info?filter=downloading&sort=progress&reverse=true&limit=8")
    torrents = [{
        "name": t.get("name", "")[:60],
        "progress": round(100 * (t.get("progress") or 0), 1),
        "remaining": int((t.get("size") or 0) * (1 - (t.get("progress") or 0))),
        "eta": t.get("eta"),
        "speed": t.get("dlspeed"),
    } for t in (items if isinstance(items, list) else [])]
    return {
        "dl": transfer.get("dl_info_speed"), "ul": transfer.get("up_info_speed"),
        "active": len(torrents), "torrents": torrents,
    }
