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
import time
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
             "metrics", "list", "limit", "torrents", "panel", "sensors", "actions", "url"}
METRIC_KEYS = {"key", "label", "format"}
STRIP_KEYS = {"code", "name", "card", "ip", "wan"}
QUIET_KEYS = {"from", "to", "sources"}
WEATHER_KEYS = {"latitude", "longitude", "timezone", "city"}
ACTION_KEYS = {"label", "confirm", "choices"}
CHOICE_KEYS = {"label", "action"}
MAINTENANCE_KEYS = {"buttons", "window"}
WINDOW_KEYS = {"cards", "from", "until", "reason"}
ALERTS_KEYS = {"path", "page"}
NIGHT_KEYS = {"from", "to", "dim"}
TOP_KEYS = {"title", "page", "strip", "quiet_hours", "weather", "action", "alerts", "night", "sparklines",
            "maintenance"}
# The formats whose numbers get a sparkline (the last hours, behind the value)
TREND_FORMATS = {"percent", "percent1", "celsius", "rate", "used_of"}
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
    # url: the service's own page, offered as OPEN in the panel of the list view (?view=list)
    if (url := ck.opt(where, c, "url", str, "an http:// or https:// address")) is not None:
        if not re.match(r"^https?://", url):
            ck.err(where, f"url must start with http:// or https://, got {url!r}")
        out["url"] = url
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


ACTION_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def _when(ck, where, table, key, optional=False):
    """"2026-10-12 08:00" (labhud's local time, TZ) -> Unix seconds."""
    v = table.get(key)
    if v is None:
        if not optional:
            ck.err(where, f"missing '{key}' (a time like \"2026-10-12 08:00\")")
        return None
    try:
        return int(time.mktime(time.strptime(str(v).strip(), "%Y-%m-%d %H:%M")))
    except ValueError:
        ck.err(where, f"{key} must be a time like \"2026-10-12 08:00\", got {v!r}")
        return None


def config_path():
    """Where the config file is: LABHUD_CONFIG, or config.toml next to this module."""
    return os.environ.get("LABHUD_CONFIG") or DEFAULT_PATH


def load(path=None):
    """Read and validate the file; raises ConfigError. Returns:

    title        the browser tab title
    pages        [{id, title, games?, groups: [{id, title}]}]
    cards        {group id: [card]}
    strip        {code: {name, card, ip?, wan?}}
    quiet_hours  {card id: (from, to)}
    skip_sources {source name: (from, to)}
    weather      {latitude, longitude, timezone?, city?} or None
    actions      {action name: {label?, confirm?, choices?: [{label, action}]}}
    maintenance  {buttons: bool, windows: [{cards, from, until, reason?}]} (times as Unix seconds)
    alerts       {path, page?} or None
    night        {from, to, dim} or None: the screen dims in that window (the display's local time)
    trends       [data path]: the numbers kept for sparklines (empty with sparklines = false)
    """
    path = path or config_path()
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(path, ["file not found (copy config.example.toml to start)"]) from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(path, [f"not valid TOML: {e}"]) from None
    return _validate(raw, path)


def loads(text, path="config.toml"):
    """load() for a text not yet written anywhere (the setup page checks what it will write)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(path, [f"not valid TOML: {e}"]) from None
    return _validate(raw, path)


def _validate(raw, path):
    ck = _Checker()
    ck.unknown("top level", raw, TOP_KEYS)
    title = ck.opt("top level", raw, "title", str, "a string")
    sparklines = ck.opt("top level", raw, "sparklines", bool, "true or false")

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
        # A question before the action: each choice is an action of its own, sent as such. The
        # agent's protocol does not change, and only the choices written here can be sent.
        if "choices" in a:
            ch = a["choices"]
            if not (isinstance(ch, list) and ch and all(isinstance(x, dict) for x in ch)):
                ck.err(aw, 'choices must be a list of { label = "...", action = "<action name>" }')
                continue
            actions[name]["choices"] = []
            for j, x in enumerate(ch):
                cw = f"{aw}.choices[{j}]"
                ck.unknown(cw, x, CHOICE_KEYS)
                label = ck.need(cw, x, "label", str, "a string")
                target = ck.need(cw, x, "action", str, "an action name")
                if target is not None and not ACTION_NAME.fullmatch(target):
                    ck.err(cw, f"action '{target}' may only use a-z, 0-9 and '-'")
                actions[name]["choices"].append({"label": label, "action": target})

    # Maintenance: cards that are down on purpose for a while. Not red, no notification, and they
    # do not take the screen. Windows here; with `buttons`, also from a card's panel on the display.
    maintenance = {"buttons": False, "windows": []}
    if "maintenance" in raw:
        mt = raw["maintenance"]
        if not isinstance(mt, dict):
            ck.err("top level", "maintenance must be written as a [maintenance] table")
            mt = {}
        ck.unknown("maintenance", mt, MAINTENANCE_KEYS)
        maintenance["buttons"] = bool(ck.opt("maintenance", mt, "buttons", bool, "true or false"))
        wins = mt.get("window", [])
        if not isinstance(wins, list) or not all(isinstance(x, dict) for x in wins):
            ck.err("maintenance", "window must be written as [[maintenance.window]] tables")
            wins = []
        for j, x in enumerate(wins):
            ww = f"maintenance.window[{j}]"
            ck.unknown(ww, x, WINDOW_KEYS)
            cards_in = x.get("cards")
            if not (isinstance(cards_in, list) and cards_in and all(isinstance(c, str) for c in cards_in)):
                ck.err(ww, "cards must be a list of card ids")
                cards_in = []
            for cid in cards_in:
                if cid not in card_ids:
                    hint = _closest(cid, card_ids)
                    ck.err(ww, f"card '{cid}' does not exist" + (f" (did you mean '{hint}'?)" if hint else ""))
            win = {"cards": cards_in, "from": _when(ck, ww, x, "from", optional=True) or 0,
                   "until": _when(ck, ww, x, "until")}
            if (r := ck.opt(ww, x, "reason", str, "a string")) is not None:
                win["reason"] = r
            maintenance["windows"].append(win)

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

    # Night: the screen dims between two hours of the display's own clock; a touch or a new
    # problem lights it up again for a while.
    night = None
    if "night" in raw:
        nt = raw["night"]
        if not isinstance(nt, dict):
            ck.err("top level", "night must be written as a [night] table")
        else:
            ck.unknown("night", nt, NIGHT_KEYS)
            night = {"from": _hour(ck, "night", nt, "from"), "to": _hour(ck, "night", nt, "to"), "dim": 30}
            if night["from"] is not None and night["from"] == night["to"]:
                ck.err("night", "'from' and 'to' are the same hour")
            dim = ck.opt("night", nt, "dim", int, "a brightness from 0 to 100 (percent)")
            if dim is not None:
                if not 0 <= dim <= 100:
                    ck.err("night", f"dim must be from 0 to 100, got {dim}")
                night["dim"] = dim

    for name, a in actions.items():
        if a.get("choices") and not any(name in c.get("actions", ()) for cs in cards.values() for c in cs):
            ck.err(f"action.{name}", "has choices but no card offers it (add it to a card's actions)")
    if ck.problems:
        raise ConfigError(path, ck.problems)
    return {"title": title or DEFAULT_TITLE, "pages": pages, "actions": actions, "maintenance": maintenance, "alerts": alerts, "cards": cards, "strip": strip, "quiet_hours": quiet, "skip_sources": skip,
            "weather": weather, "night": night,
            "trends": [] if sparklines is False else sorted({
                m[0] for cs in cards.values() for c in cs for m in c.get("metrics", []) if m[2] in TREND_FORMATS})}


def public(topology):
    """What the browser gets from /api/config: the layout, without the server-side schedules
    and without the weather coordinates (only the city name is shown)."""
    out = {k: topology[k] for k in ("title", "pages", "cards", "strip", "actions", "alerts", "night", "trends")}
    out["maintenance_buttons"] = topology["maintenance"]["buttons"]
    out["city"] = (topology.get("weather") or {}).get("city", "")
    return out


if __name__ == "__main__":
    try:
        t = load(sys.argv[1] if len(sys.argv) > 1 else None)
    except ConfigError as e:
        sys.exit(str(e))
    n = sum(len(v) for v in t["cards"].values())
    print(f"ok: {len(t['pages'])} pages, {len(t['cards'])} groups, {n} cards, {len(t['strip'])} strip nodes")
