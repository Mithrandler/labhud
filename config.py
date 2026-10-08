"""Load and validate config.toml: pages -> groups -> cards, the bottom strip and quiet hours.

Standard library only (tomllib, Python 3.11+). Every problem in the file is collected and
reported at once, with its location, instead of failing on the first one.

Check a file without starting the server:

    python3 config.py [path/to/config.toml]

The file is found through LABHUD_CONFIG, or config.toml next to this module.
"""

import ipaddress
import os
import re
import sys
import tomllib

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")

FORMATS = {
    "count",
    "percent",
    "percent1",  # one decimal
    "bytes",
    "used_of",   # [used, total] in bytes, shown as "1.2T / 5.3T"
    "rate",      # bytes per second
    "celsius",
}

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
PAGE_ID_RE = re.compile(r"^[a-z]+$")  # pages are addressed as #<id> in the URL
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")
CHECKS = ("ping", "tcp", "proxmox")

PAGE_KEYS = {"id", "title", "group", "games"}
GROUP_KEYS = {"id", "title", "card", "keep_together"}
CARD_KEYS = {"id", "name", "subtitle", *CHECKS, "large", "on_demand",
             "metrics", "list", "limit", "torrents", "panel", "sensors", "actions"}
METRIC_KEYS = {"key", "label", "format"}
STRIP_KEYS = {"code", "name", "card", "ip", "wan"}
QUIET_KEYS = {"from", "to", "sources"}
WEATHER_KEYS = {"latitude", "longitude", "timezone", "city"}
ACTION_KEYS = {"label", "confirm"}
ALERTS_KEYS = {"path", "page"}
TOP_KEYS = {"title", "page", "strip", "quiet_hours", "weather", "action", "alerts"}
DEFAULT_TITLE = "LABHUD"


class ConfigError(Exception):
    """Raised with every problem found, one per line."""

    def __init__(self, path, problems):
        self.path = path
        self.problems = problems
        super().__init__(f"{path}: {len(problems)} problem(s)\n" + "\n".join("  " + p for p in problems))


class _Checker:
    def __init__(self):
        self.problems = []

    def err(self, where, msg):
        self.problems.append(f"{where}: {msg}")

    def unknown(self, where, table, allowed):
        for k in table:
            if k not in allowed:
                hint = _closest(k, allowed)
                self.err(where, f"unknown key '{k}'" + (f" (did you mean '{hint}'?)" if hint else ""))

    def need(self, where, table, key, typ, what):
        v = table.get(key)
        if v is None:
            self.err(where, f"missing '{key}'")
        elif not isinstance(v, typ) or (isinstance(v, bool) and typ is not bool):
            self.err(where, f"'{key}' must be {what}")
            return None
        return v

    def opt(self, where, table, key, typ, what):
        if key not in table:
            return None
        return self.need(where, table, key, typ, what)

    def tables(self, where, table, key):
        v = table.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, dict) for x in v):
            self.err(where, f"'{key}' must be written as [[...{key}]] tables")
            return []
        return v


def _closest(word, options):
    best, score = None, 0
    for o in options:
        common = len(set(word) & set(o)) / max(len(set(word) | set(o)), 1)
        if common > score:
            best, score = o, common
    return best if score >= 0.6 else None


def _host(s):
    if not isinstance(s, str) or not s:
        return False
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return bool(HOST_RE.match(s)) and len(s) <= 253


def _check(ck, where, c):
    """The card's status check as a list: ["ping", host] | ["tcp", host, port] | ["proxmox", node, vmid]."""
    found = [k for k in CHECKS if k in c]
    if len(found) > 1:
        ck.err(where, f"use only one status check, found {', '.join(found)}")
        return None
    if not found:
        return None
    kind = found[0]
    v = c[kind]
    if kind == "ping":
        if _host(v):
            return ["ping", v]
        ck.err(where, f"ping must be an IP address or hostname, got {v!r}")
    elif kind == "tcp":
        host, _, port = v.rpartition(":") if isinstance(v, str) else ("", "", "")
        host = host.strip("[]")
        if _host(host) and port.isdigit() and 0 < int(port) < 65536:
            return ["tcp", host, int(port)]
        ck.err(where, f"tcp must be \"host:port\", got {v!r}")
    else:
        node, _, vmid = v.partition("/") if isinstance(v, str) else ("", "", "")
        if node and vmid.isdigit():
            return ["proxmox", node, int(vmid)]
        ck.err(where, f"proxmox must be \"node/vmid\", e.g. \"pve/101\", got {v!r}")
    return None


def _card(ck, where, c):
    ck.unknown(where, c, CARD_KEYS)
    cid = ck.need(where, c, "id", str, "a string")
    if cid is not None and not ID_RE.match(cid):
        ck.err(where, f"id '{cid}' may only contain letters, digits, '-' and '_'")
    out = {"id": cid, "name": ck.need(where, c, "name", str, "a string")}
    if (sub := ck.opt(where, c, "subtitle", str, "a string")) is not None:
        out["subtitle"] = sub
    if (check := _check(ck, where, c)) is not None:
        out["check"] = check
    if ck.opt(where, c, "large", bool, "true or false"):
        out["large"] = True
    if ck.opt(where, c, "on_demand", bool, "true or false"):
        out["on_demand"] = True
    if "metrics" in c:
        ms = c["metrics"]
        if not isinstance(ms, list) or not all(isinstance(m, dict) for m in ms):
            ck.err(where, "metrics must be a list of { key, label, format } tables")
        else:
            out["metrics"] = []
            for j, m in enumerate(ms):
                mw = f"{where}.metrics[{j}]"
                ck.unknown(mw, m, METRIC_KEYS)
                key = ck.need(mw, m, "key", str, "a dotted path into the data, e.g. \"proxmox.pve.cpu\"")
                label = ck.need(mw, m, "label", str, "a string")
                fmt = ck.need(mw, m, "format", str, "a string")
                if fmt is not None and fmt not in FORMATS:
                    ck.err(mw, f"format '{fmt}' is not one of: {', '.join(sorted(FORMATS))}")
                out["metrics"].append([key, label, fmt])
    for key, typ, what in (("list", str, "a dotted path into the data"),
                           ("limit", int, "a whole number"),
                           ("torrents", str, "a dotted path into the data"),
                           ("panel", str, "a string"),
                           ("sensors", str, "a dotted path into the data")):
        if (v := ck.opt(where, c, key, typ, what)) is not None:
            out[key] = v
    if isinstance(out.get("limit"), int):
        if not 1 <= out["limit"] <= 50:
            ck.err(where, "limit must be between 1 and 50")
        if "list" not in c:
            ck.err(where, "limit has no effect without list")
    if "actions" in c:
        a = c["actions"]
        if isinstance(a, list) and all(isinstance(x, str) and x for x in a):
            out["actions"] = a
        else:
            ck.err(where, "actions must be a list of action names")
    return out


def _hour(ck, where, table, key):
    v = ck.need(where, table, key, int, "an hour from 0 to 23")
    if v is not None and not 0 <= v <= 23:
        ck.err(where, f"'{key}' must be an hour from 0 to 23, got {v}")
        return None
    return v


def load(path=None):
    """Read and validate the file; raises ConfigError. Returns:

    title        the browser tab title
    pages        [{id, title, games?, groups: [{id, title}]}]
    cards        {group id: [card]}
    strip        {code: {name, card, ip?, wan?}}
    quiet_hours  {card id: (from, to)}
    skip_sources {source name: (from, to)}
    weather      {latitude, longitude, timezone?, city?} or None
    actions      {action name: {label?, confirm?}}
    alerts       {path, page?} or None
    """
    path = path or os.environ.get("LABHUD_CONFIG") or DEFAULT_PATH
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(path, ["file not found (copy config.example.toml to start)"]) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(path, [f"not valid TOML: {e}"]) from None

    ck = _Checker()
    ck.unknown("top level", raw, TOP_KEYS)
    title = ck.opt("top level", raw, "title", str, "a string")

    pages, cards = [], {}
    page_ids, group_ids, card_ids = set(), set(), set()
    raw_pages = ck.tables("top level", raw, "page")
    if not raw_pages:
        ck.err("top level", "at least one [[page]] is required")
    for i, p in enumerate(raw_pages):
        pw = f"page[{i}]"
        ck.unknown(pw, p, PAGE_KEYS)
        pid = ck.need(pw, p, "id", str, "a string")
        if pid is not None:
            pw = f"page '{pid}'"
            if not PAGE_ID_RE.match(pid):
                ck.err(pw, "id may only contain lowercase letters (it becomes #<id> in the URL)")
            if pid in page_ids:
                ck.err(pw, "duplicate page id")
            page_ids.add(pid)
        page = {"id": pid, "title": ck.need(pw, p, "title", str, "a string"), "groups": []}
        # A page with `games` has no fixed cards: its grid is the list of servers at that path.
        if (games := ck.opt(pw, p, "games", str, "a dotted path into the data")) is not None:
            page["games"] = games
        for j, g in enumerate(ck.tables(pw, p, "group")):
            gw = f"{pw} group[{j}]"
            ck.unknown(gw, g, GROUP_KEYS)
            gid = ck.need(gw, g, "id", str, "a string")
            if gid is not None:
                gw = f"{pw} group '{gid}'"
                if not ID_RE.match(gid):
                    ck.err(gw, "id may only contain letters, digits, '-' and '_'")
                if gid in group_ids:
                    ck.err(gw, "duplicate group id (group ids are unique across all pages)")
                group_ids.add(gid)
            group = {"id": gid, "title": ck.need(gw, g, "title", str, "a string")}
            # keep_together: no effect since 0.1.3, when no group splits across columns any more;
            # still accepted so older configs load unchanged
            ck.opt(gw, g, "keep_together", bool, "true or false")
            page["groups"].append(group)
            group_cards = cards.setdefault(gid, [])
            for k, c in enumerate(ck.tables(gw, g, "card")):
                cw = f"{gw} card[{k}]"
                if isinstance(c.get("id"), str):
                    cw = f"{gw} card '{c['id']}'"
                    if c["id"] in card_ids:
                        ck.err(cw, "duplicate card id (card ids are unique across all pages)")
                    card_ids.add(c["id"])
                group_cards.append(_card(ck, cw, c))
        pages.append(page)

    strip = {}
    for i, s in enumerate(ck.tables("top level", raw, "strip")):
        sw = f"strip[{i}]"
        ck.unknown(sw, s, STRIP_KEYS)
        code = ck.need(sw, s, "code", str, "a short label, e.g. \"NAS\"")
        if code is not None:
            sw = f"strip '{code}'"
            if code in strip:
                ck.err(sw, "duplicate code")
        node = {"name": ck.need(sw, s, "name", str, "a string")}
        card = ck.need(sw, s, "card", str, "the id of a card")
        if card is not None and card not in card_ids:
            ck.err(sw, f"card '{card}' does not exist")
        node["card"] = card
        if (ip := ck.opt(sw, s, "ip", str, "an IP address")) is not None:
            if not _host(ip):
                ck.err(sw, f"ip must be an IP address, got {ip!r}")
            node["ip"] = ip
        # wan: this node shows the router's WAN rates (opnsense.wan_dn / wan_up) instead of per-IP traffic
        if ck.opt(sw, s, "wan", bool, "true or false"):
            node["wan"] = True
        strip[code] = node

    quiet, skip = {}, {}
    qh = raw.get("quiet_hours", {})
    if not isinstance(qh, dict):
        ck.err("top level", "quiet_hours must be written as [quiet_hours.<card id>] tables")
        qh = {}
    for cid, q in qh.items():
        qw = f"quiet_hours.{cid}"
        if not isinstance(q, dict):
            ck.err(qw, "must be a table with from, to and optional sources")
            continue
        ck.unknown(qw, q, QUIET_KEYS)
        if cid not in card_ids:
            ck.err(qw, f"card '{cid}' does not exist")
        start, end = _hour(ck, qw, q, "from"), _hour(ck, qw, q, "to")
        if start is not None and start == end:
            ck.err(qw, "'from' and 'to' are the same hour")
        quiet[cid] = (start, end)
        srcs = q.get("sources", [])
        if not isinstance(srcs, list) or not all(isinstance(x, str) for x in srcs):
            ck.err(qw, "sources must be a list of source names")
            srcs = []
        for src in srcs:
            skip[src] = (start, end)

    weather = None
    if "weather" in raw:
        w = raw["weather"]
        if not isinstance(w, dict):
            ck.err("top level", "weather must be written as a [weather] table")
        else:
            ck.unknown("weather", w, WEATHER_KEYS)
            weather = {}
            for key, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
                v = w.get(key)
                if v is None:
                    ck.err("weather", f"missing '{key}'")
                elif isinstance(v, bool) or not isinstance(v, (int, float)) or not low <= v <= high:
                    ck.err("weather", f"'{key}' must be a number from {low} to {high}")
                else:
                    weather[key] = v
            for key in ("timezone", "city"):
                if (v := ck.opt("weather", w, key, str, "a string")) is not None:
                    weather[key] = v

    actions = {}
    act = raw.get("action", {})
    if not isinstance(act, dict):
        ck.err("top level", "action must be written as [action.<name>] tables")
        act = {}
    for name, a in act.items():
        aw = f"action.{name}"
        if not isinstance(a, dict):
            ck.err(aw, "must be a table with optional label and confirm")
            continue
        ck.unknown(aw, a, ACTION_KEYS)
        actions[name] = {}
        for key in ("label", "confirm"):
            if (v := ck.opt(aw, a, key, str, "a string")) is not None:
                actions[name][key] = v

    # The alert banner: a list of {id, text} at `path`; `page` gets a mark in the menu while any is open.
    alerts = None
    if "alerts" in raw:
        al = raw["alerts"]
        if not isinstance(al, dict):
            ck.err("top level", "alerts must be written as an [alerts] table")
        else:
            ck.unknown("alerts", al, ALERTS_KEYS)
            alerts = {"path": ck.need("alerts", al, "path", str, "a dotted path into the data")}
            if (pg := ck.opt("alerts", al, "page", str, "the id of a page")) is not None:
                if pg not in page_ids:
                    ck.err("alerts", f"page '{pg}' does not exist")
                alerts["page"] = pg

    if ck.problems:
        raise ConfigError(path, ck.problems)
    return {"title": title or DEFAULT_TITLE, "pages": pages, "actions": actions, "alerts": alerts, "cards": cards, "strip": strip, "quiet_hours": quiet, "skip_sources": skip,
            "weather": weather}


def public(topology):
    """What the browser gets from /api/config: the layout, without the server-side schedules
    and without the weather coordinates (only the city name is shown)."""
    out = {k: topology[k] for k in ("title", "pages", "cards", "strip", "actions", "alerts")}
    out["city"] = (topology.get("weather") or {}).get("city", "")
    return out


if __name__ == "__main__":
    try:
        t = load(sys.argv[1] if len(sys.argv) > 1 else None)
    except ConfigError as e:
        sys.exit(str(e))
    n = sum(len(v) for v in t["cards"].values())
    print(f"ok: {len(t['pages'])} pages, {len(t['cards'])} groups, {n} cards, {len(t['strip'])} strip nodes")
