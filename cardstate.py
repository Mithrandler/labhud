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

import cardtype

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_PERCENT = re.compile(r"\.(cpu|mem|used_percent|disk\.percent|gpu\d+_(util|mem))$")
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

# The marks for CPU, RAM and disk ([display] warn/crit) and a metric's own (its warn/crit):
# set from the topology by set_marks() whenever config.toml is read. app.js reads the same ones
# from /api/config (CFG.display, CFG.marks).
MARKS = {"default": [80, 92], "paths": {}}


def set_marks(topology):
    d = topology.get("display") or {}
    MARKS["default"] = [d.get("warn", 80), d.get("crit", 92)]
    MARKS["paths"] = dict(topology.get("marks") or {})


def _past(v, marks):
    return "crit" if v >= marks[1] else "warn" if v >= marks[0] else ""


def pct(fmt, v):
    """A percentage 0..100 for a bar, from a percent or used_of value, else None."""
    if fmt == "used_of":
        return round(100 * (v[0] or 0) / v[1], 1) if isinstance(v, list) and len(v) > 1 and _num(v[0]) and _num(v[1]) and v[1] else None
    if fmt in ("percent", "percent1") and _num(v):
        return round(max(0.0, min(100.0, float(v))), 1)
    return None


def marks_for(path, fmt):
    """[warn, crit] where this metric's bar turns orange and red, or None when high is not bad."""
    if path in MARKS["paths"]:
        return MARKS["paths"][path]
    if (fmt == "used_of" and _USED_OF.search(path)) or (fmt == "percent" and _PERCENT.search(path)):
        return MARKS["default"]
    return None


def raw_severity(path, fmt, v):
    own = MARKS["paths"].get(path)
    if fmt == "used_of" and isinstance(v, list) and len(v) > 1 and v[1] and (own or _USED_OF.search(path)):
        return _past(100 * (v[0] or 0) / v[1], own or MARKS["default"])
    if not _num(v):
        return ""
    if own:
        return _past(v, own)
    if fmt == "celsius":
        return "crit" if v >= 85 else "warn" if v >= 75 else ""
    if fmt == "percent" and _PERCENT.search(path):
        return _past(v, MARKS["default"])
    if re.search(r"\.latency\.loss$", path):
        return "crit" if v >= 5 else "warn" if v > 0 else ""
    if re.search(r"\.cert_min_days$", path):
        return "crit" if v < 7 else "warn" if v < 14 else ""
    if re.search(r"\.(cert_under_14|problems|stale)$", path):
        return "crit" if v > 0 else ""
    if re.search(r"\.security$", path):   # pending security updates: worth a look, not a problem
        return "warn" if v > 0 else ""
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
    if chk[0] == "agent":
        return agent_status(get(data, f"agents.{chk[1]}"), chk[2], chk[3])
    v = get(data, "status." + c["id"])
    if v == "scheduled":
        return "scheduled"
    if v is False and c.get("on_demand"):
        return "on-demand"
    return v if isinstance(v, bool) else None


# An agent's target: what counts as up (True) or down (False); anything else is in between (None).
AGENT_UP = {"docker": ("containers", "state", {"running"}, {"exited", "dead", "created"}),
            "systemd": ("units", "active", {"active"}, {"failed", "inactive"}),
            "ptero": ("servers", "state", {"running"}, {"offline"})}


def agent_status(a, cap, target):
    """A card that follows an agent: unknown (not down) while the agent is offline."""
    if not isinstance(a, dict) or not a.get("online"):
        return None
    if not cap:
        return True
    if cap == "pve":
        g = (a.get("guests") or {}).get(target)
        return bool(g.get("running")) if isinstance(g, dict) else None
    if cap not in AGENT_UP:
        return None
    part, key, up, down = AGENT_UP[cap]
    v = ((a.get(part) or {}).get(target) or {}).get(key)
    return True if v in up else False if v in down else None


def card_status(c, data):
    """True/False/"scheduled"/"on-demand"/"maintenance"/None."""
    st = raw_status(c, data)
    return "maintenance" if st is False and maintenance_until(c, data) else st


# An agent that was online and has been quiet this long counts as down in the uptime (the card
# itself still says unknown: the agent may only have lost its link).
AGENT_GONE = 300


def uptime_status(c, data, now=None):
    """card_status, stricter for the uptime: off on purpose only when the config says so
    (on_demand, wake, a schedule, maintenance), not because a guest has onboot off; and a host
    whose agent went quiet is down, not unknown."""
    st = card_status(c, data)
    chk = c.get("check") or [None]
    if st == "on-demand" and chk[0] == "proxmox" and not c.get("on_demand"):
        return False
    if st is None and chk[0] == "agent":
        a = get(data, f"agents.{chk[1]}")
        seen = a.get("seen") if isinstance(a, dict) and not a.get("online") else 0
        if _num(seen) and seen > 0 and (now or time.time()) - seen >= AGENT_GONE:
            if maintenance_until(c, data, now):
                return "maintenance"
            return "on-demand" if c.get("on_demand") or c.get("wake") else False
    return st


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


def list_row(d):
    """A list card's row: [name, value, bad], then `quiet` (grey on purpose) and the row's `icon`
    (its own, else the logo of its image or name) when there is one: [name, value, bad, quiet, icon]."""
    row = detail_row(d)
    icon = _js(d.get("icon")) if d.get("icon") else cardtype.icon_for(row[0], image=d.get("image"))
    if d.get("quiet") or icon:
        row.append(bool(d.get("quiet")))
    if icon:
        row.append(icon)
    return row


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


def metric_source(path):
    """The source a metric's numbers come from, whose `stale` and `_t` say whether they are live:
    one agent of the live agent (live.x) or of labhud's own (agents.x), else the whole source."""
    return ".".join(path.split(".")[:2]) if path.startswith(("live.", "agents.")) else path.split(".")[0]


def note(c, data):
    """(text, kind) under the card: why its numbers are not live, or that it is off on purpose.
    kind: stale | off | not_configured | error | "" (nothing to say). Like make_card in app.js."""
    st = card_status(c, data)
    if not c.get("metrics"):
        return (off_label(c, st, data), "off") if quiet_off(st) else ("", "")
    path0 = c["metrics"][0][0]
    source = metric_source(path0)
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
    source = metric_source(path0)
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
    m = re.fullmatch(r"agent-.+-(start|stop|restart|shutdown|reboot|update|wake)", name)
    if m:
        return AGENT_VERBS[m.group(1)]
    return name


AGENT_VERBS = {"start": "START", "stop": "STOP", "restart": "RESTART", "shutdown": "SHUT DOWN",
               "reboot": "REBOOT", "update": "UPDATE", "wake": "WAKE"}


def _container(c, data):
    """The container a card with `agent_target = "docker:<name>"` is, as its agent reports it."""
    cap, _, target = (c.get("agent_target") or "").partition(":")
    if cap != "docker" or not c.get("agent"):
        return None
    x = (get(data, f"agents.{c['agent']}.containers") or {}).get(target)
    return x if isinstance(x, dict) else {}


def useful_actions(c, data, enabled):
    every = [a for a in c.get("actions") or [] if enabled or a.startswith("agent-")]
    # a container: UPDATE only when its registry has a newer image, nothing while it is updated
    x = _container(c, data)
    if x is not None:
        every = [] if x.get("updating") is True else \
            [a for a in every if action_verb(a) != "update" or x.get("update") is True]
    starts = lambda a: (a.endswith("-start") or a.startswith(("wol-", "game-start-"))  # noqa: E731
                        or (a.startswith("agent-") and a.endswith("-wake")))
    stops = lambda a: bool(re.search(r"-(shutdown|reboot)$", a) or re.fullmatch(r"agent-.+-(stop|restart)", a)) \
        or a.startswith(("shutdown-", "game-stop-", "game-restart-"))  # noqa: E731
    if card_status(c, data) is True:
        keep = [a for a in every if not starts(a)]
    else:
        keep = [a for a in every if not stops(a)]
    return sorted(keep, key=action_rank)   # stable: the config's order within a rank


def action_verb(a):
    """The verb an action's name ends in ("" for the config's own); app.js `action_verb`."""
    m = (re.fullmatch(r"agent-.+-([a-z]+)", a) or re.fullmatch(r"vm-[A-Za-z0-9]+-\d+-(start|shutdown|reboot)", a)
         or re.match(r"game-(start|stop|restart)-", a))
    if m:
        return m.group(1)
    if a.startswith("wol-"):
        return "wake"
    if a.startswith("shutdown-"):
        return "shutdown"
    return ""


ACTION_RANK = {"start": 0, "wake": 0, "update": 1, "": 2, "restart": 3, "reboot": 3, "stop": 4, "shutdown": 4}


def action_rank(a):
    return ACTION_RANK.get(action_verb(a), 2)


def action_danger(a):
    """Red, after the line: what takes something down."""
    return action_rank(a) >= 3


def _action(name, c, actions):
    own = actions.get(name) or {}
    out = {"name": name, "label": action_label(name, actions),
           "confirm": own.get("confirm") or f"{action_label(name, actions)} {c.get('name', c['id'])}?",
           "danger": action_danger(name)}
    if own.get("choices"):
        out["choices"] = [{"label": ch["label"], "action": ch["action"],
                           "danger": action_danger(ch["action"])} for ch in own["choices"]]
    return out


def scan_summary(x):
    """A container's last image scan (agent with `scan`, trivy or grype): {critical, high, t}, else
    None. app.js `scan_summary`."""
    sc = x.get("scan") if isinstance(x, dict) else None
    if not isinstance(sc, dict) or type(sc.get("critical")) is not int or type(sc.get("high")) is not int:
        return None
    return {"critical": sc["critical"], "high": sc["high"], "t": sc["t"] if type(sc.get("t")) is int else 0}


def scan_text(s):
    """"2 critical · 5 high", "no critical or high", for a row and a panel. app.js `scan_text`."""
    if not s:
        return ""
    if not s["critical"] and not s["high"]:
        return "no critical or high CVEs"
    return " · ".join(p for p in ((f"{s['critical']} critical" if s["critical"] else ""),
                                  (f"{s['high']} high" if s["high"] else "")) if p)


def containers(c, data):
    """The containers of a card's own agent (a guest's panel), stopped ones first, each with its
    buttons: app.js `container_rows`. Each row is a card of its own for the buttons' rules."""
    every = get(data, f"agents.{c.get('agent')}.containers") if c.get("agent") else None
    if not isinstance(every, dict):
        return []
    btns = (get(data, f"agents.{c['agent']}.buttons") or {}).get("docker") or {}
    out = []
    for n in sorted(every, key=lambda n: (every[n].get("state") == "running", n.lower(), n)):
        x = every[n] if isinstance(every[n], dict) else {}
        up = x.get("state") == "running"
        one = {"id": f"{c['id']}:{n}", "name": n, "agent": c["agent"], "agent_target": "docker:" + n,
               "check": ["agent", c["agent"], "docker", n], "actions": list(btns.get(n) or [])}
        out.append({"name": n, "up": up, "status": (x.get("status") or "running") if up else "STOPPED",
                    "update": x.get("update") is True, "updating": x.get("updating") is True,
                    "card": one, "actions": useful_actions(one, data, True),
                    "project": x.get("project") or "", "usage": container_usage(x) if up else "",
                    "last": container_last(data, c["agent"], n), "scan": scan_summary(x),
                    "image": x.get("image") or "", "icon": cardtype.icon_for(n, image=x.get("image"))})
    return out


# --- the panel ---

def _section(out, title, rows):
    if rows:
        out.append({"title": title, "rows": [[_js(n) if not isinstance(n, str) else n,
                                              _js(v) if not isinstance(v, str) else v, bool(b[0]) if b else False]
                                             for n, v, *b in rows]})


HIST_TEXT = {"crash": "stopped by itself", "oom": "out of memory", "loop": "keeps stopping",
             "held": "update held: new critical CVEs"}


def history_text(h):
    """app.js history_text: "updated 1.2 → 1.3", "update failed", "stopped by itself"."""
    if h.get("kind") == "update":
        return (f"updated {h['from']} → {h['to']}" if h.get("from") and h.get("to") else "updated") if h.get("ok") else "update failed"
    return HIST_TEXT.get(h.get("kind"), str(h.get("kind") or ""))


def container_history(data, agent, name):
    rows = (get(data, f"agents.{agent}.history") or {}).get("docker:" + name)
    return [h for h in rows if isinstance(h, dict)] if isinstance(rows, list) else []


def container_usage(x):
    """app.js container_usage: "3.1% CPU · 245M", "" before the agent measured it."""
    if not _num(x.get("cpu")):
        return ""
    return f"{_nf1(x['cpu'])}% CPU · {bytes_(x.get('mem'))}" + (f" · {x['restarts']} restarts" if x.get("restarts") else "")


def container_last(data, agent, name, now=None):
    """app.js container_last: the newest entry of the last 7 days with its age, or ""."""
    hist = container_history(data, agent, name)
    if not hist:
        return ""
    age = (now or time.time()) - (hist[-1].get("t") or 0)
    return f"{history_text(hist[-1])} · {duration(max(60, round(age)))} ago" if age < 7 * 86400 else ""


def sections(c, data):
    """The panel's content as [{title, rows: [[name, value, bad]]}], like fill_panel in app.js
    (status, charts, links and actions are separate fields of the card)."""
    out = []
    x = _container(c, data)
    if x is not None:   # a container's own card: how it is, and what it went through
        name = c["agent_target"].partition(":")[2]
        _section(out, "container", [["image", x.get("image") or "—"],
                                    ["state", x.get("status") or x.get("state") or "—", x.get("state") not in (None, "running")]]
                 + ([["stack", x["project"]]] if x.get("project") else [])
                 + ([["CPU", f"{_nf1(x['cpu'])}%"], ["RAM", bytes_(x.get("mem"))]] if _num(x.get("cpu")) else [])
                 + ([["restarts", str(x["restarts"])]] if x.get("restarts") else [])
                 + ([["update", "updating…"]] if x.get("updating") else [["update", "available"]] if x.get("update") else [])
                 + ([["CVEs", scan_text(sc), sc["critical"] > 0]] if (sc := scan_summary(x)) else []))
        _section(out, "history", [[time.strftime("%d %b %H:%M", time.localtime(h.get("t") or 0)), history_text(h), not h.get("ok")]
                                  for h in reversed(container_history(data, c["agent"], name))])
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
        _section(out, "backup", backup_rows(get(data, f"backups.nodes.{node}")))
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
        if c.get("list") and c["list"] != "fleet.updates":   # that one has its own field, with the buttons
            rows = get(data, c["list"])
            _section(out, "details", [detail_row(d) for d in rows if isinstance(d, dict)] if isinstance(rows, list) else [])
    return out


# --- the whole state ---

STATUS = {True: "up", False: "down", None: "unknown"}


def machine(c, found=None):
    """cardtype.kind() for a card as the topology holds it: its check (ping, tcp, a Proxmox guest)
    is the config's `ping`, `tcp` or `proxmox` key again, which is what the guess reads."""
    raw = dict(c)
    check = c.get("check") or []
    if check and check[0] in ("ping", "tcp"):
        raw.setdefault(check[0], check[1])
    elif check and check[0] == "proxmox" and len(check) >= 3:
        raw.setdefault("proxmox", f"{check[1]}/{check[2]}")
    return cardtype.kind(raw, found)


def _metric(path, label, fmt, data, trends):
    """One number of a card; a percentage also has `pct` (0..100) for its bar, and `warn`/`crit`
    when high is bad (CPU, RAM, disk, or the metric's own marks)."""
    v = get(data, path)
    out = {"key": path, "label": label, "value": format_(v, fmt), "sev": severity(path, fmt, v),
           "trend": path in trends, "format": fmt}
    if (p := pct(fmt, v)) is not None:
        out["pct"] = p
    if (m := marks_for(path, fmt)) is not None:
        out["warn"], out["crit"] = m
    return out


def _gpus(c, data):
    """A host's graphics cards from its sensors (agent): [{util, mem, temp}], load and video memory in
    percent, the temperature in celsius; what a card does not know is left out. Empty without a GPU."""
    t = (get(data, c["sensors"]) if c.get("sensors") else None) or {}
    out, i = [], 0
    while isinstance(t, dict) and t.get(f"gpu{i}_util") is not None:
        g = {"util": t[f"gpu{i}_util"]}
        for k in ("mem", "temp"):
            if _num(t.get(f"gpu{i}_{k}")):
                g[k] = t[f"gpu{i}_{k}"]
        # the colour of each number as the wall draws it (the marks of [display], with hysteresis)
        for k, fmt in (("util", "percent"), ("mem", "percent"), ("temp", "celsius")):
            if k in g:
                g[f"{k}_sev"] = severity(f"{c['sensors']}.gpu{i}_{k}", fmt, g[k])
        out.append(g)
        i += 1
    return out


def _guest(c, data):
    """A running Proxmox guest's numbers for the app's bars (the card itself has no metrics):
    CPU, RAM and disk in percent (None when not known), the uptime in seconds, its node and id."""
    if not is_guest(c):
        return None
    g = get(data, f"proxmox.{c['check'][1]}.guests.{c['check'][2]}")
    if not isinstance(g, dict) or not g.get("running"):
        return None
    used, total = g.get("disk_used"), g.get("disk_total")
    disk = round(100 * used / total, 1) if _num(used) and _num(total) and total else None
    # RAM turns red only on memory pressure: Proxmox counts the page cache, so 95% is normal (the wall does the same)
    out = {"cpu": g.get("cpu"), "mem": g.get("mem"), "mem_sev": "crit" if bad_pressure(g) else "", "disk": disk,
           "node": c["check"][1], "vmid": str(c["check"][2])}
    if _num(g.get("uptime")):
        out["uptime"] = int(g["uptime"])
    return out


def backup_rows(guests, now=None):
    """[name, "ok 5h ago" | "FAILED 2d ago: why" | "never" | "excluded", bad] per guest of a node, from
    sources/proxmox.py's backups.nodes.<node>: the failed and the oldest first, the excluded last."""
    if not isinstance(guests, dict):
        return []
    now = time.time() if now is None else now
    rows = []
    for vmid, g in guests.items():
        if not isinstance(g, dict):
            continue
        name = f"{g.get('name') or vmid} ({vmid})"
        if g.get("excluded"):
            rows.append((2, 0, [name, "excluded", False]))
        elif not _num(g.get("ts")):
            rows.append((0, -1e18, [name, "never", True]))
        else:
            age = max(0, now - g["ts"])
            text = f"{int(age // 86400)}d ago" if age >= 86400 else f"{int(age // 3600)}h ago"
            bad = not g.get("ok") or age > 8 * 86400
            rows.append((0 if bad else 1, -age, [name, ("ok " if g.get("ok") else "FAILED ") + text + (f": {g['error']}" if g.get("error") else ""), bad]))
    return [r for _, _, r in sorted(rows, key=lambda x: x[:2])]


def _node(c):
    """The Proxmox node a card belongs to: a guest's own, or the one a host card shows ("host:pve" panel
    or its metrics under proxmox.pve.); "" for any other card. A guest's `node` links it to its host's."""
    if is_guest(c):
        return str(c["check"][1])
    if (c.get("panel") or "").startswith("host:"):
        return c["panel"][len("host:"):]
    for m in c.get("metrics") or []:
        parts = str(m[0]).split(".")
        if len(parts) >= 3 and parts[0] == "proxmox" and parts[2] != "guests":
            return parts[1]
    return ""


def _guest_counts(node, data):
    """{"vm": [running, total], "lxc": [running, total]} of a node, for a host card's "4/5 VM"."""
    out = {"vm": [0, 0], "lxc": [0, 0]}
    for g in (get(data, f"proxmox.{node}.guests") or {}).values():
        if isinstance(g, dict):
            k = out["lxc" if g.get("type") == "lxc" else "vm"]
            k[1] += 1
            k[0] += 1 if g.get("running") else 0
    return out


def card(c, data, topology, actions_on, page=None, group=None, full=False, found=None):
    st = card_status(c, data)
    text, kind = note(c, data)
    trends = set(topology.get("trends") or ())
    out = {
        "id": c["id"], "name": c.get("name", c["id"]), "page": page, "group": group,
        "kind": "guest" if is_guest(c) else "list" if c.get("list") else "card",
        "type": machine(c, found),   # what machine it is (cardtype.KINDS), "" for none: the app's Devices
        "status": STATUS.get(st, st) if not isinstance(st, str) else st,
        "bad": card_bad(c, data), "summary": summary(c, data), "frozen": _frozen(c, data),
        "note": text, "note_kind": kind,
        "metrics": [_metric(path, label, fmt, data, trends) for path, label, fmt in c.get("metrics") or []],
    }
    for k in ("icon", "size"):
        if c.get(k):
            out[k] = c[k]
    if (g := _guest(c, data)):
        out["guest"] = g
    if (g := _gpus(c, data)):
        out["gpus"] = g
    if (node := _node(c)):
        out["node"] = node
        if not is_guest(c) and isinstance(get(data, f"proxmox.{node}.guests"), dict):
            out["guest_counts"] = _guest_counts(node, data)
    if c.get("charts"):   # a host's agent sensors with a history (/api/v1/history), for its charts
        out["charts"] = [{"key": path, "label": label, "format": fmt} for path, label, fmt in c["charts"]
                         if path in trends and get(data, path) is not None]
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
        out["rows"] = [list_row(d) for d in rows if isinstance(d, dict)][:50] if isinstance(rows, list) else []
    if c.get("notes"):
        out["notes"] = (data.get("notes") or {}).get("text", "")
    for k in ("widget", "timezone", "frame", "frame_height"):
        if c.get(k):
            out[k] = c[k]
    if c.get("bookmarks"):
        out["bookmarks"] = [b for b in topology.get("bookmarks") or [] if c["bookmarks"] is True or b["group"] == c["bookmarks"]]
    if c.get("list") == "fleet.updates":   # the fleet's updates: each with its UPDATE, as the display's panel
        out["updates"] = [dict({k: r[k] for k in ("name", "host", "image", "updating")}, scan=r.get("scan"),
                               action=_action(r["action"], {"id": f"fleet:{r['host']}:{r['name']}", "name": r["name"]},
                                              topology.get("actions") or {}) if r.get("action") else None)
                          for r in (get(data, "fleet.updates") or []) if isinstance(r, dict)]
    if full:
        out["sections"] = sections(c, data)
        rows = containers(c, data)
        if rows:   # the guest's containers from its own agent; their logs: POST /api/setup/logs (admin)
            out["agent"] = c["agent"]
            out["containers"] = [dict({k: v for k, v in r.items() if k != "card"},
                                      actions=[_action(a, r["card"], topology.get("actions") or {}) for a in r["actions"]])
                                 for r in rows]
    return out


# --- game servers (a page with `games`): the display's game_card() in app.js ---

def _game_buttons(data, ident):
    """A game server's buttons from an agent that has it ticked (Pterodactyl), else None."""
    for a in (data.get("agents") or {}).values():
        b = ((a.get("buttons") or {}).get("ptero") or {}) if isinstance(a, dict) else {}
        if ident in b:
            return b[ident]
    return None


def game(sv, i, data, topology, actions_on=False, full=False):
    """{id, name, game, state, running, line, players, max, address, ram_mb, ram_max_mb, uptime,
    map, version, actions}: one tile of the games page; `full` adds who is playing."""
    running = sv.get("state") == "running"
    q = sv.get("query") if isinstance(sv.get("query"), dict) else None
    ident = sv.get("id") or ""
    players = q.get("players") if q else None
    if not running:
        line = "stopped"
    elif players is not None:
        line = f"{_js(players)}{'/' + _js(q['max']) if q.get('max') else ''} players"
    else:
        line = "running"
    name = sv.get("game") or sv.get("name") or "server"
    names = (_game_buttons(data, ident) or [f"game-{v}-{ident}" for v in ("start", "stop", "restart")]) if ident else []
    c = {"id": f"game{i}", "name": name, "check": ["known", running], "actions": names}
    out = {"id": ident, "name": sv.get("name") or name, "game": sv.get("game") or "", "state": sv.get("state") or "",
           "running": running, "title": sv.get("game") or sv.get("name") or "?", "line": line,
           "players": players, "max": q.get("max") if q else None, "address": sv.get("address") or "",
           "ram_mb": round(sv.get("ram_mb") or 0), "ram_max_mb": sv.get("ram_max_mb"),
           "uptime": round(sv.get("uptime_s") or 0), "map": q.get("map") if q else None,
           "version": q.get("version") if q else None,
           "actions": [_action(a, c, topology.get("actions") or {}) for a in useful_actions(c, data, actions_on)]}
    out["icon"] = cardtype.game_icon(sv.get("game") or "", sv.get("name") or "")
    if full:
        out["who"] = [n for n in (q.get("names") or []) if isinstance(n, str)] if q else []
    return out


def games(topology, data, actions_on=False, full=False):
    """{page id: [game, ...]} for every page with `games`."""
    out = {}
    for p in topology["pages"]:
        if p.get("games"):
            servers = get(data, p["games"])
            out[p["id"]] = [game(sv, i, data, topology, actions_on, full)
                            for i, sv in enumerate(servers if isinstance(servers, list) else [])
                            if isinstance(sv, dict)][:15]
    return out


def _internet(data):
    """The router's WAN rates in bytes per second, for the app's top bar; nothing when no router reports them."""
    o = data.get("opnsense") if isinstance(data.get("opnsense"), dict) else {}
    dn, up = o.get("wan_dn"), o.get("wan_up")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (dn, up)):
        return {}
    return {"internet": {"dn": dn, "up": up}}


def state(topology, data, actions_on=False, full=False, found=None, role=None):
    """Everything an app shows: pages with their cards, the problems first, the alerts. `found`:
    {agent name: what it found} (agents.listing()), for each card's `type`. `role` (viewer /
    operator / admin): only the pages [roles.<role>] lists; the alerts (alerting.py) do not depend
    on it, so a card a role does not see still raises its alert."""
    pages, cards, seen, hosts = [], [], set(), []
    titles = {}
    only = (topology.get("roles") or {}).get(role)
    for i, p in enumerate(topology["pages"]):
        if only is not None and p["id"] not in only:
            continue
        groups = []
        for g in p["groups"]:
            titles[g["id"]] = g["title"]
            if g.get("hidden"):
                continue   # left off every screen; its cards' alerts (alerting.py) go on
            ids = []
            every = topology["cards"].get(g["id"], [])
            for c in every:
                if c.get("hidden"):
                    continue
                ids.append(c["id"])
                if c["id"] not in seen:
                    seen.add(c["id"])
                    hosts.append(c)
                    cards.append(card(c, data, topology, actions_on, page=p["id"], group=g["id"], full=full, found=found))
            if ids or not every:   # a group whose cards are all hidden goes with them
                groups.append({"id": g["id"], "title": g["title"], "cards": ids})
        pages.append({"id": p["id"], "title": p["title"], "groups": groups,
                      **({"icon": p["icon"]} if p.get("icon") else {}), **({"games": True} if p.get("games") else {}),
                      **{k: p[k] for k in ("columns", "density") if p.get(k)}})
    every_game = games(topology, data, actions_on, full)
    for page in pages:
        if page.get("games"):
            page["servers"] = every_game[page["id"]]
    problems = [c["id"] for c in cards if c["bad"]]
    alerts_cfg = topology.get("alerts") or {}
    alerts = get(data, alerts_cfg["path"]) if alerts_cfg.get("path") else None
    alerts = [{"id": _js(a.get("id")), "text": _js(a.get("text"))} for a in alerts if isinstance(a, dict)] if isinstance(alerts, list) else []
    # and what the [[alert]] rules find now (alerting.py), with the card they are about
    rules = get(data, "alerting.active")
    alerts += [{"id": _js(a.get("id")), "text": _js(a.get("text")), "card": a.get("card"), "since": a.get("since")}
               for a in rules if isinstance(a, dict)] if isinstance(rules, list) else []
    disp = topology.get("display") or {}
    own = {(c.get("agent"), c["agent_target"][7:]) for cs in topology["cards"].values() for c in cs
           if str(c.get("agent_target") or "").startswith("docker:")}
    boxes = []   # the containers the hosts above list, for the app's palette (a container with a card of its own is that card)
    for host in hosts:
        if not host.get("agent") or host.get("agent_target") or host.get("game"):
            continue
        for r in containers(host, data):
            key = (host["agent"], r["name"])
            if key in own:
                continue
            own.add(key)
            boxes.append({"card": host["id"], "name": r["name"], "up": r["up"],
                          "actions": [_action(a, r["card"], topology.get("actions") or {}) for a in r["actions"]] if actions_on else []})
    return {"title": topology.get("title") or "LabHUD", "now": int(time.time()), "pages": pages,
            "cards": cards, "problems": problems, "alerts": alerts,
            "theme": {"base": disp.get("base", "oled"), "accent": disp.get("accent", "blue")},
            "containers": boxes,
            "bookmarks": [{"group": b["group"], "name": b["name"], "url": b["url"]} for b in topology.get("bookmarks") or []],
            "search": (topology.get("search") or {}).get("url") or "",
            **_internet(data),
            **({"role": role} if role else {}),
            **({"view": topology["role_views"][role]} if role in (topology.get("role_views") or {}) else {})}
