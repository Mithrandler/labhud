# CrowdSec: the IPs its own scenarios ban now (not the community blocklists), the scenarios that
# ban most, and above all whether one of your own addresses is banned: a ban on your home IP cuts
# your VPN and your sites at once.
#
#   LABHUD_CROWDSEC_URL     http://lapi-host:8080 (the Local API); several: "router=http://…,vps=http://…"
#   LABHUD_CROWDSEC_KEY     a bouncer key (`cscli bouncers add labhud`): it can only read decisions;
#                           several: "router=<key>,vps=<key>", the same names as the URLs
#   LABHUD_CROWDSEC_WATCH   optional: your own addresses or names, "home.example.com,203.0.113.7";
#                           a ban on one of them is a red row
#
# Data: crowdsec.{bans, scenarios, own_banned, by, rows}; `by` is the bans per instance; `rows` is a
# list for a card, a ban on your own address first: list = "crowdsec.rows". An instance that does not
# answer is a red row; the others are still read.

import collections
import ipaddress
import re
import socket

from ._common import env, labelled_urls, request, scrub, source

ORIGINS = "crowdsec,cscli"   # this LAPI's own scenarios and manual bans, not CAPI or lists
DURATION = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?")


def _left(d):
    """ "3h59m12.3s" -> "3h 59m" """
    m = DURATION.fullmatch(d or "")
    if not m or not any(m.groups()):
        return d or ""
    h, mi = int(m.group(1) or 0), int(m.group(2) or 0)
    return f"{h}h {mi}m" if h else f"{mi}m"


def _watched():
    """The addresses of LABHUD_CROWDSEC_WATCH, names resolved now (a dynamic home IP moves)."""
    out = set()
    for item in (x.strip() for x in env("CROWDSEC_WATCH").split(",")):
        if not item:
            continue
        try:
            out.add(str(ipaddress.ip_address(item)))
            continue
        except ValueError:
            pass
        try:
            out.update(a[4][0] for a in socket.getaddrinfo(item, None))
        except OSError:
            pass
    return out


def _hit(value, scope, mine):
    """Is one of `mine` inside this decision (an Ip or a Range)?"""
    try:
        net = ipaddress.ip_network(value, strict=False) if scope.lower() == "range" else ipaddress.ip_network(value)
    except ValueError:
        return False
    return any(ipaddress.ip_address(a) in net for a in mine if ipaddress.ip_address(a).version == net.version)


def _keys(urls):
    """The key of each instance: "router=<key>,vps=<key>" when the URLs are named, else the one key.
    A key may end in "=", so a part is named only by one of the URLs' own names."""
    raw = env("CROWDSEC_KEY")
    if list(urls) == [""]:
        return {"": raw.strip()}
    out = {}
    for part in (p.strip() for p in raw.split(",") if p.strip()):
        name, sep, key = part.partition("=")
        if sep and name.strip() in urls:
            out[name.strip()] = key.strip()
    return out


def _decisions(url, key):
    data = request(url.rstrip("/") + "/v1/decisions?origins=" + ORIGINS, {"X-Api-Key": key}, timeout=10)
    return [d for d in (data if isinstance(data, list) else []) if (d.get("type") or "ban") == "ban"]


@source("crowdsec", every=60, env=("CROWDSEC_URL", "CROWDSEC_KEY"),
         title="CrowdSec", about="What its own scenarios ban now, and a ban on your own address in red.",
         hints={"CROWDSEC_URL": "http://lapi-host:8080", "CROWDSEC_KEY": "cscli bouncers add labhud (it only reads decisions)",
                "CROWDSEC_WATCH": "optional: your own addresses or names, comma-separated"})
def crowdsec():
    urls = labelled_urls(env("CROWDSEC_URL"))
    keys = _keys(urls)
    bans, by, down = [], {}, []
    for name, url in urls.items():
        try:
            got = _decisions(url, keys.get(name, ""))
        except Exception as e:   # the others are still worth showing
            if len(urls) == 1:
                raise
            down.append((name, scrub(e)[:80] or type(e).__name__))
            continue
        by[name or "crowdsec"] = len(got)
        bans += [dict(d, _at=name) for d in got]
    mine = _watched()
    own = [d for d in bans if mine and _hit(str(d.get("value", "")), str(d.get("scope", "ip")), mine)]
    scenarios = collections.Counter(str(d.get("scenario") or "?").split("/")[-1] for d in bans)
    at = lambda d: f" on {d['_at']}" if d.get("_at") else ""  # noqa: E731
    rows = [{"name": f"YOUR {d.get('value')} is banned{at(d)}", "value": f"{str(d.get('scenario') or '').split('/')[-1]} · "
             f"{_left(d.get('duration'))} left", "bad": True} for d in own]
    rows += [{"name": f"{name}: no answer", "value": why, "bad": True} for name, why in down]
    rows += [{"name": s, "value": f"{n} banned"} for s, n in scenarios.most_common(5)]
    if not bans:
        rows.append({"name": "no bans", "value": "—"})
    return {"bans": len(bans), "scenarios": len(scenarios), "own_banned": len(own), "by": by, "rows": rows}
