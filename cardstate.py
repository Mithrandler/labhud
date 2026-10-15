"""Each card's state worked out on the server, for apps (/api/v1/state): the same answers the
display gives in static/app.js, so a phone never disagrees with the wall.

The browser keeps its own copy of this logic (it colours numbers as they arrive); the functions
here are named after their twins in app.js and tests/test_cardstate.py runs both on the demo data
to keep them equal. A change to card_status, card_bad, severity, format or summary in app.js
needs the same change here.

What the app gets per card: its status and whether it is a problem, a one-line summary, its
numbers already formatted and coloured, a short note ("not updated since 14:05", "off on
schedule"), the actions that make sense now, and the panel's sections as plain rows.
"""

import decimal
import math
import re
import threading
import time

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PERCENT = re.compile(r"\.(cpu|mem|used_percent|disk\.percent)$")
_USED_OF = re.compile(r"\.(mem_used_of|disk_used_of)$")
_SEV_RANK = {"": 0, "warn": 1, "crit": 2}
_last_sev = {}
_sev_lock = threading.Lock()


# --- JavaScript's way with numbers and text ---

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isnan(v))


def _round(v):
    """Math.round: halves go up."""
    return int(math.floor(v + 0.5))


def _js(v):
    """String(v) as JavaScript writes it."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        return str(int(v)) if v.is_integer() and abs(v) < 1e21 else repr(v)
    if isinstance(v, list):
        return ",".join("" if x is None else _js(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def _nf1(v):
    """Intl.NumberFormat with one decimal: rounds the shortest decimal form, halves away from zero."""
    d = decimal.Decimal(repr(float(v))).quantize(decimal.Decimal("0.1"), rounding=decimal.ROUND_HALF_UP)
    return f"{d:,}"


def get(data, path):
    """A dotted path into the snapshot, like get() in app.js."""
    v = data
    for part in path.split("."):
        if v is None:
            return None
        v = v.get(part) if isinstance(v, dict) else None
    return v


def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def when(t):
    """A row's time: "14:05" today, "7 Oct" before."""
    d = time.localtime(t)
    if time.strftime("%Y%m%d", d) == time.strftime("%Y%m%d"):
        return clock(t)
    return f"{d.tm_mday} {MONTHS[d.tm_mon - 1]}"


def duration(s):
    if not s:
        return "—"
    if s < 60:
        return f"{_js(s)}s"
    if s < 3600:
        return f"{_round(s / 60)}m"
    if s < 86400:
        return f"{_round(s / 3600)}h"
    return f"{_round(s / 86400)}d"


def bytes_(n):
    if n is None or not _num(n):
        return "—"
    units = "BKMGT"
    i, v = 0, float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return (str(_round(v)) if v >= 100 or i == 0 else _nf1(v)) + units[i]


def used_of(used, total, with_percent):
    if used is None or not total:
        return "—"
    t = f"{bytes_(used)} / {bytes_(total)}"
    return f"{t} · {_round(100 * used / total)}%" if with_percent else t


def rate(n):
    if n is None or not _num(n):
        return "—"
    if n < 1024:
        return "0"
    if n < 1024 * 1024:
        return f"{_round(n / 1024)}K"
    return _nf1(n / 1048576) + "M"


def format_(v, fmt):
    if v is None or v == "":
        return "—"
    if fmt == "percent":
        return (str(_round(v)) if _num(v) else _js(v)) + "%"
    if fmt == "percent1":
        return (_nf1(v) if _num(v) else _js(v)) + "%"
    if fmt == "bytes":
        return bytes_(v)
    if fmt == "used_of":
        return f"{bytes_(v[0])} / {bytes_(v[1])}" if isinstance(v, list) and len(v) > 1 and v[1] else "—"
    if fmt == "rate":
        return rate(v)
    if fmt == "celsius":
        return (str(_round(v)) if _num(v) else _js(v)) + "°"
    if fmt == "count":
        return f"{v:,}" if _num(v) and float(v).is_integer() else _js(v)
    return _js(v)


# --- colours ---

def raw_severity(path, fmt, v):
    if fmt == "used_of" and isinstance(v, list) and len(v) > 1 and v[1] and _USED_OF.search(path):
        p = 100 * (v[0] or 0) / v[1]
        return "crit" if p >= 92 else "warn" if p >= 80 else ""
    if not _num(v):
        return ""
    if fmt == "celsius":
        return "crit" if v >= 85 else "warn" if v >= 75 else ""
    if fmt == "percent" and _PERCENT.search(path):
        return "crit" if v >= 92 else "warn" if v >= 80 else ""
    if re.search(r"\.latency\.loss$", path):
        return "crit" if v >= 5 else "warn" if v > 0 else ""
    if re.search(r"\.cert_min_days$", path):
        return "crit" if v < 7 else "warn" if v < 14 else ""
    if re.search(r"\.(cert_under_14|problems|stale|security)$", path):
        return "crit" if v > 0 else ""
    return ""


def severity(path, fmt, v):
    """With hysteresis, like the display: back under a mark by a margin before the colour drops."""
    now = raw_severity(path, fmt, v)
    with _sev_lock:
        was = _last_sev.get(path, "")
        out = now
        if _SEV_RANK[now] < _SEV_RANK[was] and fmt in ("percent", "used_of", "celsius"):
            margin = 3 if fmt == "celsius" else 5
            if fmt == "used_of":
                bumped = [(v[0] or 0) + margin / 100 * v[1], v[1]] if isinstance(v, list) and len(v) > 1 and v[1] else v
            else:
                bumped = v + margin if _num(v) else v
            near = raw_severity(path, fmt, bumped)
            if _SEV_RANK[near] > _SEV_RANK[now]:
                out = was if _SEV_RANK[near] >= _SEV_RANK[was] else near
        _last_sev[path] = out
    return out


def bad_pressure(g):
    if not g:
        return False
    if (g.get("pressure_full") or 0) > 1:
        return True
    return (g.get("pressure") or 0) > 10


# --- status ---

def maintenance_until(c, data, now=None):
    until = get(data, "maintenance." + c["id"]) if c else 0
    return until if _num(until) and until > (now or time.time()) else 0


def quiet_off(st):
    return st in ("scheduled", "on-demand", "maintenance")


def off_label(c, st, data):
    if st == "scheduled":
        return "off on schedule"
    if st == "maintenance":
        return "maintenance until " + clock(maintenance_until(c, data))
    return "off · started on demand"


def raw_status(c, data):
    chk = c.get("check") if c else None
    if not chk:
        return None
    if chk[0] == "proxmox":
        g = get(data, f"proxmox.{chk[1]}.guests.{chk[2]}")
        if not g:
            return None
        if not g.get("running") and (g.get("onboot") is False or c.get("on_demand")):
            return "on-demand"
        return bool(g.get("running"))
    if chk[0] == "known":
        return chk[1]
    v = get(data, "status." + c["id"])
    if v == "scheduled":
        return "scheduled"
    if v is False and c.get("on_demand"):
        return "on-demand"
    return v if isinstance(v, bool) else None


def card_status(c, data):
    """True/False/"scheduled"/"on-demand"/"maintenance"/None."""
    st = raw_status(c, data)
    return "maintenance" if st is False and maintenance_until(c, data) else st


def card_bad(c, data):
    """True when red, False when not, None when nothing about the card is known yet."""
    if maintenance_until(c, data):
        return False
    st = card_status(c, data)
    if st is False:
        return True
    vals = [(path, fmt, get(data, path)) for path, _, fmt in c.get("metrics") or []]
    if any(severity(path, fmt, v) == "crit" for path, fmt, v in vals):
        return True
    if st is None and all(v is None for _, _, v in vals):
        return None
    return False


def is_guest(c):
    return (not c.get("large") and not c.get("list") and not c.get("torrents")
            and bool(c.get("check")) and c["check"][0] == "proxmox")


def detail_row(d):
    name = d.get("name") if d.get("name") is not None else d.get("text")
    value = d.get("value") if d.get("value") is not None else d.get("label")
    if value is None:
        value = when(d["t"]) if d.get("t") else ""
    return ["" if name is None else _js(name), _js(value), d.get("bad") is True]


def row_bad(d):
    return not d.get("quiet") and (d.get("bad") is True or (
        d.get("bad") is not False and bool(re.search(r"failed|\bdown\b|missing|expired", _js(d.get("value")) if d.get("value") is not None else "", re.I))))


def summary(c, data):
    """"CPU 12% · RAM 40%": the card's first numbers, or its first list row, in one line."""
    if is_guest(c):
        g = get(data, f"proxmox.{c['check'][1]}.guests.{c['check'][2]}")
        if not g or not g.get("running"):
            return ""
        return f"CPU {_round(g.get('cpu') or 0)}% · RAM {min(100, _round(g.get('mem') or 0))}%"
    if c.get("metrics"):
        return " · ".join(f"{label} {format_(get(data, path), fmt)}" for path, label, fmt in c["metrics"][:3])
    rows = get(data, c["list"]) if c.get("list") else None
    first = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    return " ".join(x for x in detail_row(first)[:2] if x) if first else ""


def note(c, data):
    """(text, kind) under the card: why its numbers are not live, or that it is off on purpose.
    kind: stale | off | not_configured | error | "" (nothing to say). Like make_card in app.js."""
    st = card_status(c, data)
    if not c.get("metrics"):
        return (off_label(c, st, data), "off") if quiet_off(st) else ("", "")
    path0 = c["metrics"][0][0]
    source = ".".join(path0.split(".")[:2]) if path0.startswith("live.") else path0.split(".")[0]
    root = source.split(".")[0]
    kept = get(data, source + ".stale") or (root != source and get(data, root + ".stale"))
    if kept and st is not False and not quiet_off(st):
        since = get(data, source + "._t") or get(data, root + "._t")
        return "not updated" + (f" since {clock(since)}" if since else ""), "stale"
    if st is False:
        return "", ""
    if quiet_off(st):
        return off_label(c, st, data), "off"
    if get(data, root + ".not_configured"):
        return "not configured", "not_configured"
    why = get(data, source + ".unavailable")
    return (_js(why), "error") if why else ("", "")


def _frozen(c, data):
    st = card_status(c, data)
    if not c.get("metrics"):
        return st is False or quiet_off(st)
    path0 = c["metrics"][0][0]
    source = ".".join(path0.split(".")[:2]) if path0.startswith("live.") else path0.split(".")[0]
    root = source.split(".")[0]
    kept = get(data, source + ".stale") or (root != source and get(data, root + ".stale"))
    return st is False or quiet_off(st) or bool(kept)


# --- actions ---

def action_label(name, actions):
    own = actions.get(name) or {}
    if own.get("label"):
        return own["label"]
    if name.startswith("wol-"):
        return "START"
    if name.startswith("shutdown-"):
        return "SHUT DOWN"
    m = re.fullmatch(r"vm-[A-Za-z0-9]+-\d+-(start|shutdown|reboot)", name)
    if m:
        return {"start": "START", "shutdown": "SHUT DOWN", "reboot": "REBOOT"}[m.group(1)]
    m = re.match(r"game-(start|stop|restart)-", name)
    if m:
        return m.group(1).upper()
    return name


def useful_actions(c, data, enabled):
    every = list(c.get("actions") or []) if enabled else []
    starts = lambda a: a.endswith("-start") or a.startswith(("wol-", "game-start-"))  # noqa: E731
    stops = lambda a: bool(re.search(r"-(shutdown|reboot)$", a)) or a.startswith(("shutdown-", "game-stop-", "game-restart-"))  # noqa: E731
    if card_status(c, data) is True:
        return [a for a in every if not starts(a)]
    return [a for a in every if not stops(a)]


def _action(name, c, actions):
    own = actions.get(name) or {}
    out = {"name": name, "label": action_label(name, actions),
           "confirm": own.get("confirm") or f"{action_label(name, actions)} {c.get('name', c['id'])}?",
           "danger": bool(re.search(r"shutdown|stop", name))}
    if own.get("choices"):
        out["choices"] = [{"label": ch["label"], "action": ch["action"],
                           "danger": bool(re.search(r"shutdown|stop", ch["action"]))} for ch in own["choices"]]
    return out


# --- the panel ---

def _section(out, title, rows):
    if rows:
        out.append({"title": title, "rows": [[_js(n) if not isinstance(n, str) else n,
                                              _js(v) if not isinstance(v, str) else v, bool(b[0]) if b else False]
                                             for n, v, *b in rows]})


def sections(c, data):
    """The panel's content as [{title, rows: [[name, value, bad]]}], like fill_panel in app.js
    (status, charts, links and actions are separate fields of the card)."""
    out = []
    lst = lambda path: [d for d in (get(data, path) or []) if isinstance(d, dict)] if isinstance(get(data, path), list) else []  # noqa: E731
    pair_rows = lambda path: [[d.get("name"), d.get("value")] for d in lst(path)]  # noqa: E731
    pct = lambda v: format_(v, "percent")  # noqa: E731
    chk = c.get("check") or []
    if chk and chk[0] == "proxmox":
        node, vmid = chk[1], chk[2]
        g = get(data, f"proxmox.{node}.guests.{vmid}") or {}
        pressure = g.get("pressure")
        _section(out, "resources", [
            ["CPU", pct(g.get("cpu"))],
            ["RAM", f"{pct(g.get('mem'))} · {bytes_(g.get('mem_bytes'))} / {bytes_(g.get('mem_max'))}"],
            ["memory pressure", "—" if pressure is None else
             (f"{_nf1(pressure)}% — REALLY short" if bad_pressure(g) else f"{_nf1(pressure or 0)}% — the % above includes cache"),
             bad_pressure(g)],
            ["disk", used_of(g.get("disk_used"), g.get("disk_total"), True) if g.get("disk_used") is not None else bytes_(g.get("disk"))],
            ["uptime", duration(g.get("uptime"))],
            ["type", "LXC" if g.get("type") == "lxc" else "VM"],
            ["node", f"{node} / {vmid}"],
        ])
        cont = get(data, f"live.containers.{vmid}")
        if isinstance(cont, list) and cont:
            running = sum(1 for x in cont if x.get("state") == "running")
            _section(out, f"docker containers ({running}/{len(cont)})",
                     [[x.get("name"), (x.get("status") or "running") if x.get("state") == "running" else "STOPPED",
                       x.get("state") != "running"] for x in cont])
        elif isinstance(cont, dict) and cont.get("unavailable"):
            _section(out, "docker containers", [["could not read", _js(cont["unavailable"])]])

    panel = c.get("panel") or ""
    if panel.startswith("host:"):
        node = panel[len("host:"):]
        p = get(data, "proxmox." + node) or {}
        _section(out, "host", [["CPU", pct(p.get("cpu"))], ["RAM", pct(p.get("mem"))],
                               ["RAM used", f"{bytes_(p.get('mem_used'))} / {bytes_(p.get('mem_total'))}"],
                               ["uptime", duration(p.get("uptime"))]])
        t = (get(data, c["sensors"]) if c.get("sensors") else None) or {}
        gpus, i = [], 0
        while t.get(f"gpu{i}_util") is not None:
            gpus.append([f"GPU{i}", f"{pct(t[f'gpu{i}_util'])} · {pct(t.get(f'gpu{i}_mem'))} mem · {format_(t.get(f'gpu{i}_temp'), 'celsius')}"])
            i += 1
        _section(out, "GPUs", gpus)
        _section(out, "storage", [[x.get("name"), used_of(x.get("used"), x.get("total"), True)] for x in p.get("storage") or []])
        _section(out, "guests", [[f"{vmid} {g.get('name') or ''}",
                                  (f"CPU {pct(g.get('cpu'))} · RAM {pct(g.get('mem'))}"
                                   + (f" · Disk {used_of(g.get('disk_used'), g.get('disk_total'), False)}" if g.get("disk_used") is not None else ""))
                                  if g.get("running") else "stopped", not g.get("running")]
                                 for vmid, g in (p.get("guests") or {}).items()])
    elif panel == "dns:details":
        _section(out, "top blocked domains", pair_rows("live.display.dns_blocked"))
        _section(out, "top clients", pair_rows("live.display.dns_clients"))
    elif panel == "security:log":
        _section(out, "latest events", [[j.get("text"), clock(j["ts"]) if j.get("ts") else ""] for j in lst("live.log")[:40]])
    elif panel == "security:suricata":
        s = get(data, "live.suricata") or {}
        _section(out, "signatures", [[x.get("name"), format_(x.get("n"), "count")] for x in (s.get("signatures") or [])[:15]])
    elif panel == "security:sources":
        _section(out, "top sources", pair_rows("live.display.sources"))
        _section(out, "countries", pair_rows("live.display.countries"))
    elif panel == "security:tripwire":
        _section(out, "tripwire · 30 days", pair_rows("live.display.tripwire"))
    elif panel == "security:logins":
        _section(out, "recent", [[f"{x.get('app') or '?'} · {x.get('user') or x.get('ip') or '—'}", clock(x["ts"]) if x.get("ts") else ""]
                                 for x in lst("live.logins.recent")[:25]])
    elif panel == "network:devices":
        _section(out, "active", [[d.get("name") or d.get("mac") or "?", d.get("ip") or ""] for d in lst("live.devices.list")[:40]])
    elif panel == "network:traffic":
        _section(out, "interfaces", [[n, f"↓{rate(r.get('dn'))} ↑{rate(r.get('up'))}"]
                                     for n, r in (get(data, "opnsense.interfaces") or {}).items() if isinstance(r, dict)])
    elif panel == "media:jellyfin":
        _section(out, "who is watching", [[f"{s.get('user')}: {s.get('title')}", s.get("client") or ""] for s in lst("jellyfin.sessions")])
        _section(out, "library", [["box sets", format_(get(data, "jellyfin.BoxSetCount") or 0, "count")],
                                  ["trailers", format_(get(data, "jellyfin.TrailerCount") or 0, "count")],
                                  ["items in total", format_(get(data, "jellyfin.ItemCount") or 0, "count")]])
    elif panel == "media:navidrome":
        _section(out, "who is listening", [[f"{s.get('user')}: {s.get('title')}", s.get("state") or ""] for s in lst("navidrome.sessions")])
    elif panel in ("media:sonarr", "media:radarr"):
        _section(out, "missing", [[t, ""] for t in get(data, panel[6:] + ".missing") or []])
    elif panel == "media:prowlarr":
        _section(out, "indexers", [[i.get("name"), f"{i.get('grabs')} grabs · {i['failed']} failed" if i.get("failed") else f"{i.get('grabs')} grabs",
                                    bool(i.get("failed"))] for i in lst("prowlarr.indexers")])
    elif panel == "media:torrents":
        _section(out, "downloading", [[t.get("name"), f"{_round(t.get('progress') or 0)}% · {bytes_(t.get('remaining'))} left"] for t in lst("qbt.torrents")])
    elif panel.startswith("list:"):
        rows = get(data, panel[len("list:"):]) or []
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        if any(isinstance(s.get("rows"), list) for s in rows):
            for s in rows:
                _section(out, _js(s.get("title") if s.get("title") is not None else ""),
                         [detail_row(d) for d in s.get("rows") or [] if isinstance(d, dict)])
        else:
            _section(out, "details", [detail_row(d) for d in rows])

    if not out:
        if c.get("metrics"):
            _section(out, "numbers", [[label, format_(get(data, path), fmt)] for path, label, fmt in c["metrics"]])
        if c.get("list"):
            rows = get(data, c["list"])
            _section(out, "details", [detail_row(d) for d in rows if isinstance(d, dict)] if isinstance(rows, list) else [])
    return out


# --- the whole state ---

STATUS = {True: "up", False: "down", None: "unknown"}


def card(c, data, topology, actions_on, page=None, group=None, full=False):
    st = card_status(c, data)
    text, kind = note(c, data)
    trends = set(topology.get("trends") or ())
    out = {
        "id": c["id"], "name": c.get("name", c["id"]), "page": page, "group": group,
        "kind": "guest" if is_guest(c) else "list" if c.get("list") else "card",
        "status": STATUS.get(st, st) if not isinstance(st, str) else st,
        "bad": card_bad(c, data), "summary": summary(c, data), "frozen": _frozen(c, data),
        "note": text, "note_kind": kind,
        "metrics": [{"key": path, "label": label, "value": format_(get(data, path), fmt),
                     "sev": severity(path, fmt, get(data, path)), "trend": path in trends, "format": fmt}
                    for path, label, fmt in c.get("metrics") or []],
    }
    if st is None and not c.get("check"):
        out["status"] = None
    for k in ("subtitle", "url"):
        if c.get(k):
            out[k] = c[k]
    if c.get("links"):
        out["links"] = c["links"]
    if c.get("age") and _num(get(data, c["age"])):
        out["as_of"] = get(data, c["age"])
    until = maintenance_until(c, data)
    if until:
        out["maintenance_until"] = until
    out["actions"] = [_action(a, c, topology.get("actions") or {}) for a in useful_actions(c, data, actions_on)]
    buttons = topology["maintenance_buttons"] if "maintenance_buttons" in topology else (topology.get("maintenance") or {}).get("buttons")
    out["maintenance_button"] = bool(buttons and c.get("check"))
    if c.get("list"):
        rows = get(data, c["list"])
        out["rows"] = [detail_row(d) + ([True] if d.get("quiet") else []) for d in rows if isinstance(d, dict)][:50] \
            if isinstance(rows, list) else []
    if full:
        out["sections"] = sections(c, data)
    return out


def state(topology, data, actions_on=False, full=False):
    """Everything an app shows: pages with their cards, the problems first, the alerts."""
    pages, cards, seen = [], [], set()
    titles = {}
    for i, p in enumerate(topology["pages"]):
        groups = []
        for g in p["groups"]:
            titles[g["id"]] = g["title"]
            ids = []
            for c in topology["cards"].get(g["id"], []):
                ids.append(c["id"])
                if c["id"] not in seen:
                    seen.add(c["id"])
                    cards.append(card(c, data, topology, actions_on, page=p["id"], group=g["id"], full=full))
            groups.append({"id": g["id"], "title": g["title"], "cards": ids})
        pages.append({"id": p["id"], "title": p["title"], "groups": groups, **({"games": True} if p.get("games") else {})})
    problems = [c["id"] for c in cards if c["bad"]]
    alerts_cfg = topology.get("alerts") or {}
    alerts = get(data, alerts_cfg["path"]) if alerts_cfg.get("path") else None
    alerts = [{"id": _js(a.get("id")), "text": _js(a.get("text"))} for a in alerts if isinstance(a, dict)] if isinstance(alerts, list) else []
    return {"title": topology.get("title") or "LabHUD", "now": int(time.time()), "pages": pages,
            "cards": cards, "problems": problems, "alerts": alerts}
