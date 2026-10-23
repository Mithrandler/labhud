"""Load and validate config.toml: pages -> groups -> cards, the bottom strip and quiet hours.

Standard library only (tomllib, Python 3.11+). Every problem in the file is collected and
reported at once, with its location, instead of failing on the first one.

Check a file without starting the server:

    python3 config.py [path/to/config.toml]

The file is found through LABHUD_CONFIG, or config.toml next to this module; once labhud has
taken it over (managed.py, ⚙ > Setup) it is LABHUD_DATA/config.toml.
"""

import ipaddress
import os
import re
import sys
import time
import tomllib
import zoneinfo

import alerting

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
CHECKS = ("ping", "tcp", "proxmox", "up")

PAGE_KEYS = {"id", "title", "group", "games", "icon", "columns", "density"}
GROUP_KEYS = {"id", "title", "card", "keep_together", "guests", "guests_tag", "hidden"}
CARD_KEYS = {"id", "name", "subtitle", *CHECKS, "large", "on_demand",
             "metrics", "list", "limit", "torrents", "panel", "sensors", "actions", "url", "age", "links",
             "agent", "agent_target", "wake", "kind", "bookmarks", "notes", "icon", "size", "hidden",
             "widget", "timezone", "frame", "frame_height"}
CARD_KINDS = ("proxmox", "nas", "docker", "vm", "pc")   # cardtype.KINDS: for ⚙ > Pages > Organize by type
# agent_target = "<capability>:<target>": what a card controls through its agent (agents.py)
AGENT_TARGET = {"docker": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
                "systemd": r"[A-Za-z0-9:_.@-]{1,250}\.(service|timer|socket|target|mount|path)",
                "wol": r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", "pve": r"[1-9][0-9]{2,8}", "ptero": r"[0-9a-f]{8}"}
METRIC_KEYS = {"key", "label", "format", "warn", "crit"}
# A card's size in the grid: s 1x1, m 2x1, l 2x2, xl 4x2 cells (large = true is l)
CARD_SIZES = ("s", "m", "l", "xl")
PAGE_DENSITIES = ("compact", "normal")   # a page's spacing, for the apps and the browser
PAGE_COLUMNS = (1, 6)                    # a page's columns, from to (both included)
# An icon's name: a file in static/icons/svc/ (dashboard-icons names: "proxmox", "home-assistant")
ICON_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
DISPLAY_KEYS = {"warn", "crit", "icons_online", "base", "accent"}
# The board's theme (⚙ > Dashboard): the colours behind the cards and the accent. A screen may pick its own (⚙ > Look)
THEME_BASES = ("oled", "grey")
THEME_ACCENTS = ("blue", "green", "orange", "red", "violet", "cyan", "pink", "yellow")
ACCENT_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# What a role (an API token's scope, a paired phone) sees: [roles.<role>] pages = ["id", ...]
ROLE_NAMES = ("viewer", "operator", "admin")
ROLE_KEYS = {"pages", "view"}
# How the app opens for a role (its default, the phone may still change it): the glance tiles only,
# the glance with the action buttons, or everything.
ROLE_VIEWS = ("glance", "actions", "full")
# The marks where a resource (CPU, RAM, disk: a percentage) turns orange, then red
DEFAULT_MARKS = (80, 92)
STRIP_KEYS = {"code", "name", "card", "ip", "wan"}
QUIET_KEYS = {"from", "to", "sources"}
WEATHER_KEYS = {"latitude", "longitude", "timezone", "city"}
ACTION_KEYS = {"label", "confirm", "choices"}
CHOICE_KEYS = {"label", "action"}
MAINTENANCE_KEYS = {"buttons", "window"}
WINDOW_KEYS = {"cards", "from", "until", "reason"}
ALERTS_KEYS = {"path", "page"}
NIGHT_KEYS = {"from", "to", "dim"}
CALM_KEYS = {"after"}
LOCK_KEYS = {"idle"}
PUBLIC_KEYS = {"title", "groups"}
TOP_KEYS = {"title", "page", "strip", "quiet_hours", "weather", "action", "alerts", "night", "calm", "public", "include", "sparklines",
            "maintenance", "lock", "alert", "notify", "search", "bookmark", "display", "admin", "registry", "update", "audit", "roles"}
UPDATE_KEYS = {"check"}
AUDIT_KEYS = {"keep_days"}
SEARCH_KEYS = {"url"}
REGISTRY_KEYS = {"host", "user"}
REGISTRY_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9.-]{0,58}[a-z0-9])?(:[0-9]{1,5})?")
DOCKER_HUB_NAMES = ("docker.io", "index.docker.io", "registry-1.docker.io", "hub.docker.com")
BOOKMARK_KEYS = {"group", "name", "url"}
NOTIFY_KEYS = {"via", "quiet_from", "quiet_to", "email", "gotify", "events"}   # the last three: notify.check()
ADMIN_KEYS = {"session_hours", "trusted_proxies", "admin_networks", "servers_networks", "oidc"}
OIDC_KEYS = {"issuer", "client_id", "allow", "allow_groups", "redirect_uri", "scopes"}
NOTIFY_VIA = ("ntfy", "app", "both")
# The formats whose numbers get a sparkline (the last hours, behind the value)
TREND_FORMATS = {"percent", "percent1", "celsius", "rate", "used_of"}
# A host card's charts (agent = "<name>"): the agent's sensors, kept like the cards' numbers
AGENT_CHARTS = [("cpu", "CPU", "percent"), ("mem", "RAM", "percent"), ("disk", "Disk", "percent"),
                ("cpu_temp", "CPU temp", "celsius"), ("gpu0_temp", "GPU temp", "celsius"),
                ("net_rx", "Network in", "rate"), ("net_tx", "Network out", "rate")]
DEFAULT_TITLE = "LabHUD"


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
    elif kind == "up":
        if isinstance(v, str) and re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", v):
            return ["data", v]   # a true/false the sources already give: the card is up while it is true
        ck.err(where, f"up must be a data path such as \"live.wireguard.up\", got {v!r}")
    else:
        node, _, vmid = v.partition("/") if isinstance(v, str) else ("", "", "")
        if node and vmid.isdigit():
            return ["proxmox", node, int(vmid)]
        ck.err(where, f"proxmox must be \"node/vmid\", e.g. \"pve/101\", got {v!r}")
    return None


WIDGETS = ("clock", "calendar")
FRAME_RE = re.compile(r"^https?://[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?(/[^\s\"'<>]*)?$")


def frame_origin(url):
    """"https://host:port" of a frame's address (what the page's frame-src lists), or None."""
    m = re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else None


def _widget(ck, where, c, out):
    """A small card the browser draws alone: widget = "clock" (timezone = "Europe/Bucharest", the
    screen's own when missing) or "calendar" (the month); frame = "https://…" a page shown inside the
    card (frame_height = 120 to 800 pixels). Only the frame's own origin is let through by the CSP."""
    w = ck.opt(where, c, "widget", str, "clock or calendar")
    if w is not None:
        if w not in WIDGETS:
            ck.err(where, f"widget must be one of {', '.join(WIDGETS)}, got {w!r}")
        else:
            out["widget"] = w
    tz = ck.opt(where, c, "timezone", str, "a timezone name such as \"Europe/Bucharest\"")
    if tz is not None:
        try:
            zoneinfo.ZoneInfo(tz)
            out["timezone"] = tz
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
            ck.err(where, f"timezone {tz!r} is not a timezone name")
    fr = ck.opt(where, c, "frame", str, "an http:// or https:// address")
    if fr is not None:
        if FRAME_RE.match(fr):
            out["frame"] = fr
        else:
            ck.err(where, "frame must be an http:// or https:// address")
    fh = c.get("frame_height")
    if fh is not None:
        if isinstance(fh, int) and not isinstance(fh, bool) and 120 <= fh <= 800:
            out["frame_height"] = fh
        else:
            ck.err(where, "frame_height must be a whole number from 120 to 800")
    if "widget" in out and "frame" in out:
        ck.err(where, "a card is a widget or a frame, not both")


def _icon(ck, where, t):
    icon = ck.opt(where, t, "icon", str, "a string")
    if icon is not None and not ICON_RE.match(icon):
        ck.err(where, f"icon must be a name like \"proxmox\" (lowercase letters, digits, '-'), got {icon!r}")
        return None
    return icon


def _marks(ck, where, t):
    """[warn, crit] from a table's warn/crit (numbers, warn below crit), else None."""
    w = t.get("warn")
    c = t.get("crit")
    if w is None and c is None:
        return None
    ok = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if not ok(w) or not ok(c) or not w < c:
        ck.err(where, f"warn and crit must both be numbers, warn below crit, got warn={w!r} crit={c!r}")
        return None
    return [w, c]


def _card(ck, where, c):
    ck.unknown(where, c, CARD_KEYS)
    cid = ck.need(where, c, "id", str, "a string")
    if cid is not None and not ID_RE.match(cid):
        ck.err(where, f"id '{cid}' may only contain letters, digits, '-' and '_'")
    out = {"id": cid, "name": ck.need(where, c, "name", str, "a string")}
    if (sub := ck.opt(where, c, "subtitle", str, "a string")) is not None:
        out["subtitle"] = sub
    if (kind := ck.opt(where, c, "kind", str, "a string")) is not None:
        if kind not in CARD_KINDS:
            ck.err(where, f"kind must be one of {', '.join(CARD_KINDS)}, got {kind!r}")
        else:
            out["kind"] = kind
    if (check := _check(ck, where, c)) is not None:
        out["check"] = check
    if ck.opt(where, c, "large", bool, "true or false"):
        out["large"] = True
        out["size"] = "l"
    if (size := ck.opt(where, c, "size", str, "a string")) is not None:
        if size not in CARD_SIZES:
            ck.err(where, f"size must be one of {', '.join(CARD_SIZES)}, got {size!r}")
        else:
            out["size"] = size
    if (icon := _icon(ck, where, c)) is not None:
        out["icon"] = icon
    # hidden: the card is not shown on any screen (the apps' state leaves it out); its alerts go on
    if ck.opt(where, c, "hidden", bool, "true or false"):
        out["hidden"] = True
    if ck.opt(where, c, "on_demand", bool, "true or false"):
        out["on_demand"] = True
    # bookmarks = true: the [[bookmark]] links (or only one group: bookmarks = "<group>");
    # notes = true: the notes written in ⚙ or in the app
    bm = c.get("bookmarks")
    if bm is not None:
        if bm is True or (isinstance(bm, str) and bm.strip()):
            out["bookmarks"] = bm
        elif bm is not False:
            ck.err(where, 'bookmarks must be true or the name of a [[bookmark]] group')
    if ck.opt(where, c, "notes", bool, "true or false"):
        out["notes"] = True
    _widget(ck, where, c, out)
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
                if (marks := _marks(ck, mw, m)) is not None and key is not None:
                    out.setdefault("marks", {})[key] = marks
    for key, typ, what in (("list", str, "a dotted path into the data"),
                           ("limit", int, "a whole number"),
                           ("torrents", str, "a dotted path into the data"),
                           ("panel", str, "a string"),
                           ("sensors", str, "a dotted path into the data"),
                           # age: a Unix time in the data saying when the card's numbers were made,
                           # for data refreshed rarely (an hourly check): shown as "as of 40m ago"
                           ("age", str, "a dotted path into the data")):
        if (v := ck.opt(where, c, key, typ, what)) is not None:
            out[key] = v
    # url: the service's own page, offered as OPEN in the panel of the list view (?view=list)
    if (url := ck.opt(where, c, "url", str, "an http:// or https:// address")) is not None:
        if not re.match(r"^https?://", url):
            ck.err(where, f"url must start with http:// or https://, got {url!r}")
        out["url"] = url
    # links: buttons at the top of the card's panel that open other pages (logs, the host's UI)
    if "links" in c:
        links = c["links"]
        if not isinstance(links, list) or not all(isinstance(x, dict) for x in links):
            ck.err(where, 'links must be a list of { label = "...", url = "https://..." }')
        else:
            out["links"] = []
            for x in links:
                unknown = set(x) - {"label", "url"}
                if unknown:
                    ck.err(where, f"links: unknown key(s) {', '.join(sorted(unknown))}")
                if not isinstance(x.get("label"), str) or not re.match(r"^https?://", str(x.get("url", ""))):
                    ck.err(where, f"links: each needs a label and an http:// or https:// url, got {x!r}")
                else:
                    out["links"].append({"label": x["label"], "url": x["url"]})
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
    _card_agent(ck, where, c, out)
    return out


def _card_agent(ck, where, c, out):
    """agent = "<name>" (a host: its sensors, its reboot/update buttons) and, with it,
    agent_target = "<cap>:<target>" (one container, unit, game server, guest or MAC instead).
    wake = "<agent>:<mac>": WAKE from another agent (one that is up while this machine is off).
    The card's state comes from the agent unless the card has a check of its own."""
    name = ck.opt(where, c, "agent", str, "the name of an agent in ⚙ > Agents")
    target = ck.opt(where, c, "agent_target", str, '"<capability>:<target>", e.g. "docker:jellyfin"')
    if target is not None and name is None:
        ck.err(where, "agent_target needs agent = \"<name>\" too")
    wake = ck.opt(where, c, "wake", str, '"<agent>:<mac>", e.g. "pve2:aa:bb:cc:dd:ee:ff"')
    if wake is not None:
        by, _, mac = wake.partition(":")
        if not by.strip() or "." in by or not re.fullmatch(AGENT_TARGET["wol"], mac):
            ck.err(where, f'wake must be "<agent>:<mac>" (the agent that wakes it, a lowercase MAC), got {wake!r}')
        else:
            out["wake"] = wake
    if name is None:
        return
    if not name.strip() or "." in name:
        ck.err(where, f"agent must be an agent's name as ⚙ > Agents shows it (no dots), got {name!r}")
        return
    out["agent"] = name
    cap = ""
    if target is not None:
        cap, _, t = target.partition(":")
        if cap not in AGENT_TARGET or not re.fullmatch(AGENT_TARGET[cap], t):
            ck.err(where, f"agent_target must be one of {', '.join(f'{k}:<target>' for k in AGENT_TARGET)}, got {target!r}")
            return
        out["agent_target"] = target
    if target is None and "sensors" not in out:
        out["sensors"] = f"agents.{name}.sensors"
    if target is None:
        # the panel's charts of a host: what its agent measures (only those it sends get drawn)
        out["charts"] = [[f"agents.{name}.sensors.{key}", label, fmt] for key, label, fmt in AGENT_CHARTS]
    if "check" not in out:
        out["check"] = ["agent", name, cap, target.partition(":")[2] if target else ""]


def _search_and_bookmarks(ck, raw):
    """[search] url = "https://search.example/?q=%s": where the search box sends what matches no card.
    [[bookmark]] group, name, url: plain links, for the `bookmarks = true` card and the search."""
    search = {}
    if "search" in raw:
        s = raw["search"]
        if not isinstance(s, dict):
            ck.err("search", "must be a table: [search] url = \"https://…?q=%s\"")
        else:
            ck.unknown("search", s, SEARCH_KEYS)
            url = ck.opt("search", s, "url", str, '"https://…?q=%s"')
            if url is not None and not (re.match(r"^https?://", url) and "%s" in url):
                ck.err("search", f"url must start with http(s):// and have %s where the words go, got {url!r}")
            elif url:
                search["url"] = url
    bookmarks = []
    for i, b in enumerate(ck.tables("top level", raw, "bookmark")):
        where = f"bookmark #{i + 1}"
        ck.unknown(where, b, BOOKMARK_KEYS)
        name = ck.need(where, b, "name", str, "a string")
        url = ck.need(where, b, "url", str, "https://…")
        group = ck.opt(where, b, "group", str, "a string") or "Links"
        if url is not None and not re.match(r"^https?://", url):
            ck.err(where, f"url must start with http:// or https://, got {url!r}")
        elif name and url:
            bookmarks.append({"group": group, "name": name, "url": url})
    return search, bookmarks


def _alert_rules(ck, raw, cards):
    """[[alert]] (alerting.py): a list of {on, what, above?, minutes}; the defaults without any."""
    if "alert" not in raw:
        return [dict(r) for r in alerting.DEFAULT_RULES]
    by_id = {c["id"]: c for c in cards}
    out = []
    for i, r in enumerate(ck.tables("top level", raw, "alert")):
        where = f"alert #{i + 1}"
        ck.unknown(where, r, alerting.KEYS)
        on = ck.need(where, r, "on", str, 'a card id, or "*" for every host with an agent')
        what = ck.need(where, r, "what", str, "one of " + ", ".join(alerting.WHAT))
        if what is not None and what not in alerting.WHAT:
            ck.err(where, f"what must be one of {', '.join(alerting.WHAT)}, got {what!r}")
            what = None
        minutes = r.get("minutes", 0)
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 0 <= minutes <= 1440:
            ck.err(where, "minutes must be a whole number from 0 to 1440")
            minutes = None
        rule = {"on": on, "what": what, "minutes": minutes}
        if what in alerting.NO_MARK:
            if "above" in r:
                ck.err(where, f"{what} takes minutes, not above")
        elif what is not None:
            above = ck.need(where, r, "above", (int, float), "a number")
            if above is not None and not 0 < above <= (150 if what == "temp" else 100):
                ck.err(where, f"above must be from 1 to {150 if what == 'temp' else 100}, got {above}")
            rule["above"] = above
        if on is not None and on != "*":
            c = by_id.get(on)
            if c is None:
                hint = _closest(on, by_id)
                ck.err(where, f"card '{on}' does not exist" + (f" (did you mean '{hint}'?)" if hint else ""))
            elif not c.get("agent") or c.get("agent_target"):
                ck.err(where, f"card '{on}' has no agent of its own (agent = \"<name>\" without agent_target)")
        if None not in rule.values():
            out.append(rule)
    return out


def _cidrs(ck, where, table, key):
    """A list of addresses or networks ("10.0.0.0/8", "192.168.1.5", "fd00::/8"), kept as text."""
    xs = table.get(key, [])
    if not isinstance(xs, list) or not all(isinstance(x, str) for x in xs):
        ck.err(where, f"{key} must be a list of networks like \"192.168.1.0/24\"")
        return []
    out = []
    for x in xs:
        try:
            out.append(str(ipaddress.ip_network(x.strip(), strict=False)))
        except ValueError:
            ck.err(where, f"{key}: {x!r} is not an address or a network")
    return out


def _secure_url(u, path=False):
    """https://, or http:// only to this machine (a test identity provider)."""
    m = re.fullmatch(r"(https?)://([^/\s?#]+)(/[^\s#]*)?", u or "")
    if not m:
        return False
    host = m.group(2).rsplit("@", 1)[-1]
    local = re.fullmatch(r"(localhost|127\.0\.0\.1|\[::1\])(:\d+)?", host)
    return m.group(1) == "https" or bool(local)


def _admin(ck, raw):
    """[admin] (⚙ > Security): session_hours (1 to 720: how long a browser stays
    signed in without use), trusted_proxies, admin_networks and servers_networks (lists of networks: servers_networks are
    the only ones the zone Servers acts from, empty = as admin_networks says), and
    [admin.oidc] for single sign-on: issuer, client_id, allow (user names or emails) and/or
    allow_groups; the client secret is LABHUD_OIDC_CLIENT_SECRET (secrets.env)."""
    out = {"session": 30 * 86400, "trusted_proxies": [], "admin_networks": [], "servers_networks": [], "oidc": None}
    ad = raw.get("admin")
    if ad is None:
        return out
    if not isinstance(ad, dict):
        ck.err("top level", "admin must be written as an [admin] table")
        return out
    ck.unknown("admin", ad, ADMIN_KEYS)
    hours = ck.opt("admin", ad, "session_hours", int, "hours")
    if hours is not None and not 1 <= hours <= 720:
        ck.err("admin", f"session_hours must be from 1 to 720 (30 days), got {hours}")
    elif hours is not None:
        out["session"] = hours * 3600
    out["trusted_proxies"] = _cidrs(ck, "admin", ad, "trusted_proxies")
    out["admin_networks"] = _cidrs(ck, "admin", ad, "admin_networks")
    out["servers_networks"] = _cidrs(ck, "admin", ad, "servers_networks")
    oc = ad.get("oidc")
    if oc is None:
        return out
    if not isinstance(oc, dict):
        ck.err("admin", "oidc must be written as an [admin.oidc] table")
        return out
    w = "admin.oidc"
    ck.unknown(w, oc, OIDC_KEYS)
    issuer = ck.need(w, oc, "issuer", str, "the provider's issuer URL")
    client = ck.need(w, oc, "client_id", str, "the client id the provider gave")
    if issuer is not None and not _secure_url(issuer):
        ck.err(w, f"issuer must be an https:// URL, got {issuer!r}")
    if client is not None and not re.fullmatch(r"[\x21-\x7e]{1,200}", client):
        ck.err(w, "client_id: 1 to 200 visible characters")
    lists = {}
    for key in ("allow", "allow_groups", "scopes"):
        xs = oc.get(key, [])
        if not isinstance(xs, list) or not all(isinstance(x, str) and x.strip() for x in xs):
            ck.err(w, f"{key} must be a list of names")
            xs = []
        lists[key] = [x.strip() for x in xs]
    if not lists["allow"] and not lists["allow_groups"]:
        ck.err(w, "say who may sign in: allow (user names or emails) and/or allow_groups")
    redirect = ck.opt(w, oc, "redirect_uri", str, "a URL")
    if redirect is not None and not (_secure_url(redirect) and redirect.endswith("/api/admin/oidc/callback")):
        ck.err(w, "redirect_uri must be https://<labhud>/api/admin/oidc/callback")
    out["oidc"] = {"issuer": issuer or "", "client_id": client or "", "allow": lists["allow"],
                   "allow_groups": lists["allow_groups"], "redirect_uri": redirect or "",
                   "scopes": lists["scopes"] or ["openid", "profile", "email"]}
    return out


def _notify(ck, raw):
    """[notify]: where the news goes (ntfy, the app, both) and the quiet hours (an hour each;
    the same hour twice = none). Defaults: both, 23 to 7."""
    out = {"via": "both", "quiet": (23, 7)}
    nt = raw.get("notify")
    if nt is None:
        return out
    if not isinstance(nt, dict):
        ck.err("top level", "notify must be written as a [notify] table")
        return out
    ck.unknown("notify", nt, NOTIFY_KEYS)
    via = ck.opt("notify", nt, "via", str, "one of " + ", ".join(NOTIFY_VIA))
    if via is not None:
        if via not in NOTIFY_VIA:
            ck.err("notify", f"via must be one of {', '.join(NOTIFY_VIA)}, got {via!r}")
        else:
            out["via"] = via
    if "quiet_from" in nt or "quiet_to" in nt:
        a, b = _hour(ck, "notify", nt, "quiet_from"), _hour(ck, "notify", nt, "quiet_to")
        if a is not None and b is not None:
            out["quiet"] = None if a == b else (a, b)
    import notify   # [notify.email], [notify.gotify], [notify.events]: the channels' own checks
    out.update(notify.check(ck, nt))
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
    """Where the config file is: the one labhud keeps in LABHUD_DATA once it took it over
    (managed.py), else given_path()."""
    data = os.environ.get("LABHUD_DATA")
    if data and os.path.isfile(os.path.join(data, "config.toml")):
        return os.path.join(data, "config.toml")
    return given_path()


def given_path():
    """The config labhud is given: LABHUD_CONFIG, or config.toml next to this module."""
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
    alert_rules  [{on, what, above?, minutes}]: [[alert]] (alerting.py), the defaults without any
    notify       {via: ntfy | app | both, quiet: (from, to) or None, email, gotify, events}: [notify] (notify.py)
    night        {from, to, dim} or None: the screen dims in that window (the display's local time)
    calm         {after} or None: seconds with nothing red before the screen shows only the clock
    lock         {idle}: seconds without a touch before an unlocked screen locks again (with a PIN set)
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
    raw = _with_includes(raw, path)
    return _validate(raw, path)


# What an included file may hold: more pages, strip entries, actions and quiet hours. The rest
# (title, weather, night...) stays in config.toml, so there is one place to look for it.
INCLUDE_ARRAYS = ("page", "strip")
INCLUDE_TABLES = ("action", "quiet_hours")


def included(path, raw=None):
    """The files `include = [...]` names, as glob patterns relative to config.toml's folder, sorted."""
    import glob
    if raw is None:
        try:
            with open(path, "rb") as f:
                raw = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return []
    pats = raw.get("include") or []
    if isinstance(pats, str):
        pats = [pats]
    folder = os.path.dirname(os.path.abspath(path))
    files = []
    for pat in pats if isinstance(pats, list) else []:
        if isinstance(pat, str):
            files += sorted(glob.glob(os.path.join(folder, pat)))
    return [f for f in dict.fromkeys(files) if os.path.abspath(f) != os.path.abspath(path)]


def _with_includes(raw, path):
    """raw with every included file's pages, strip entries, actions and quiet hours added after
    its own, in file name order. Raises ConfigError naming the file at fault."""
    if "include" not in raw:
        return raw
    pats = raw["include"]
    if isinstance(pats, str):
        pats = [pats]
    if not isinstance(pats, list) or not all(isinstance(p, str) for p in pats):
        raise ConfigError(path, ["include must be a list of file patterns, e.g. [\"pages/*.toml\"]"])
    raw = dict(raw)
    problems = []
    for f in included(path, raw):
        name = os.path.relpath(f, os.path.dirname(os.path.abspath(path)))
        try:
            with open(f, "rb") as fh:
                part = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as e:
            problems.append(f"{name}: {e}")
            continue
        for key in part:
            if key not in INCLUDE_ARRAYS + INCLUDE_TABLES:
                problems.append(f"{name}: '{key}' belongs in config.toml (an included file holds only "
                                f"{', '.join(INCLUDE_ARRAYS + INCLUDE_TABLES)})")
        for key in INCLUDE_ARRAYS:
            if isinstance(part.get(key), list):
                raw[key] = list(raw.get(key) or []) + part[key]
        for key in INCLUDE_TABLES:
            if isinstance(part.get(key), dict):
                merged = dict(raw.get(key) or {})
                for k, v in part[key].items():
                    if k in merged:
                        problems.append(f"{name}: [{key}.{k}] is already defined")
                    merged[k] = v
                raw[key] = merged
    if problems:
        raise ConfigError(path, problems)
    raw.pop("include", None)
    return raw


def loads(text, path="config.toml"):
    """load() for a text not yet written anywhere (the setup page checks what it will write)."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(path, [f"not valid TOML: {e}"]) from None
    return _validate(_with_includes(raw, path), path)


def _validate(raw, path):
    ck = _Checker()
    ck.unknown("top level", raw, TOP_KEYS)
    title = ck.opt("top level", raw, "title", str, "a string")
    sparklines = ck.opt("top level", raw, "sparklines", bool, "true or false")
    search, bookmarks = _search_and_bookmarks(ck, raw)

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
        if (icon := _icon(ck, pw, p)) is not None:
            page["icon"] = icon
        # columns / density: how the apps and the browser lay this page out (⚙ > Dashboard)
        if (cols := p.get("columns")) is not None:
            if isinstance(cols, bool) or not isinstance(cols, int) or not PAGE_COLUMNS[0] <= cols <= PAGE_COLUMNS[1]:
                ck.err(pw, f"columns must be a whole number from {PAGE_COLUMNS[0]} to {PAGE_COLUMNS[1]}, got {cols!r}")
            else:
                page["columns"] = cols
        if (dens := ck.opt(pw, p, "density", str, "a string")) is not None:
            if dens not in PAGE_DENSITIES:
                ck.err(pw, f"density must be one of {', '.join(PAGE_DENSITIES)}, got {dens!r}")
            else:
                page["density"] = dens
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
            # guests = "<node>": labhud adds a card for every guest of that node that has none
            # elsewhere, as guests come and go; guests_tag keeps only those with that Proxmox tag
            if (node := ck.opt(gw, g, "guests", str, "a Proxmox node name")) is not None:
                group["guests"] = node
            if (tag := ck.opt(gw, g, "guests_tag", str, "a Proxmox tag")) is not None:
                if "guests" not in g:
                    ck.err(gw, "guests_tag has no effect without guests")
                group["guests_tag"] = tag.lower()
            # keep_together: no effect any more, when no group splits across columns;
            # still accepted so older configs load unchanged
            ck.opt(gw, g, "keep_together", bool, "true or false")
            # hidden: the group is not shown on any screen; its cards' alerts go on
            if ck.opt(gw, g, "hidden", bool, "true or false"):
                group["hidden"] = True
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

    # Calm: after `after` seconds with nothing red, the screen shows only a big clock and "all
    # good"; the first problem (or a touch) brings the cards back.
    calm = None
    if "calm" in raw:
        cm = raw["calm"]
        if not isinstance(cm, dict):
            ck.err("top level", "calm must be written as a [calm] table")
        else:
            ck.unknown("calm", cm, CALM_KEYS)
            after = ck.opt("calm", cm, "after", int, "seconds")
            if after is not None and not 30 <= after <= 86400:
                ck.err("calm", f"after must be from 30 to 86400 seconds, got {after}")
            calm = {"after": after or 300}

    # Lock: with a PIN set (from the display's settings or init.py pin), the screen is only for
    # looking; a PIN opens it, and it closes again after `idle` minutes without a touch.
    lock = {"idle": 120}
    if "lock" in raw:
        lk = raw["lock"]
        if not isinstance(lk, dict):
            ck.err("top level", "lock must be written as a [lock] table")
        else:
            ck.unknown("lock", lk, LOCK_KEYS)
            idle = ck.opt("lock", lk, "idle", int, "minutes")
            if idle is not None and not 1 <= idle <= 120:
                ck.err("lock", f"idle must be from 1 to 120 minutes, got {idle}")
            elif idle is not None:
                lock["idle"] = idle * 60

    # Public status page (LABHUD_PUBLIC_PORT): only the names and states of these groups' cards.
    public_page = None
    if "public" in raw:
        pb = raw["public"]
        if not isinstance(pb, dict):
            ck.err("top level", "public must be written as a [public] table")
        else:
            ck.unknown("public", pb, PUBLIC_KEYS)
            groups = pb.get("groups")
            if not isinstance(groups, list) or not groups or not all(isinstance(g, str) for g in groups):
                ck.err("public", "groups must be a list of group ids")
                groups = []
            for g in groups:
                if g not in cards:
                    ck.err("public", f"group '{g}' does not exist")
            public_page = {"title": ck.opt("public", pb, "title", str, "a string") or "Status", "groups": groups}

    # [display] warn/crit: where CPU, RAM and disk turn orange and red (a metric's own warn/crit wins)
    display = {"warn": DEFAULT_MARKS[0], "crit": DEFAULT_MARKS[1], "icons_online": True, "base": "oled", "accent": "blue"}
    if "display" in raw:
        dp = raw["display"]
        if not isinstance(dp, dict):
            ck.err("top level", "display must be written as a [display] table")
        else:
            ck.unknown("display", dp, DISPLAY_KEYS)
            merged = dict(display, **{k: dp[k] for k in DISPLAY_KEYS if k in dp})
            marks = _marks(ck, "display", merged)
            if marks and not 0 < marks[0] < marks[1] <= 100:
                ck.err("display", "warn and crit are percentages: 0 < warn < crit <= 100")
            elif marks:
                display = dict(display, warn=marks[0], crit=marks[1])
            if not isinstance(dp.get("icons_online", True), bool):
                ck.err("display", "icons_online must be true or false")
            else:
                display["icons_online"] = dp.get("icons_online", True)
            if dp.get("base", "oled") not in THEME_BASES:
                ck.err("display", f"base must be one of {', '.join(THEME_BASES)}, got {dp['base']!r}")
            else:
                display["base"] = dp.get("base", "oled")
            accent = dp.get("accent", "blue")
            if not isinstance(accent, str) or not (accent in THEME_ACCENTS or ACCENT_HEX_RE.match(accent)):
                ck.err("display", f"accent must be one of {', '.join(THEME_ACCENTS)} or a #rrggbb colour, got {accent!r}")
            else:
                display["accent"] = accent.lower()
    roles, role_views = _roles(ck, raw, page_ids)

    registries = _registries(ck, raw)
    alert_rules = _alert_rules(ck, raw, [c for cs in cards.values() for c in cs])
    notify_cfg = _notify(ck, raw)
    admin_cfg = _admin(ck, raw)
    update_cfg = {"check": True}
    if "update" in raw:
        if not isinstance(raw["update"], dict):
            ck.err("top level", "update must be written as an [update] table")
        else:
            ck.unknown("update", raw["update"], UPDATE_KEYS)
            if not isinstance(raw["update"].get("check", True), bool):
                ck.err("update", "check must be true or false")
            else:
                update_cfg["check"] = raw["update"].get("check", True)

    audit_cfg = {"keep_days": 90}
    if "audit" in raw:
        if not isinstance(raw["audit"], dict):
            ck.err("top level", "audit must be written as an [audit] table")
        else:
            ck.unknown("audit", raw["audit"], AUDIT_KEYS)
            kd = raw["audit"].get("keep_days", 90)
            if isinstance(kd, bool) or not isinstance(kd, int) or not 7 <= kd <= 730:
                ck.err("audit", "keep_days must be a whole number of days, 7 to 730")
            else:
                audit_cfg["keep_days"] = kd

    for name, a in actions.items():
        if a.get("choices") and not any(name in c.get("actions", ()) for cs in cards.values() for c in cs):
            ck.err(f"action.{name}", "has choices but no card offers it (add it to a card's actions)")
    if ck.problems:
        raise ConfigError(path, ck.problems)
    return {"title": title or DEFAULT_TITLE, "pages": pages, "actions": actions, "maintenance": maintenance, "alerts": alerts, "cards": cards, "strip": strip, "quiet_hours": quiet, "skip_sources": skip,
            "weather": weather, "night": night, "calm": calm, "lock": lock, "public": public_page,
            "alert_rules": alert_rules, "notify": notify_cfg, "search": search, "bookmarks": bookmarks,
            "display": display, "roles": roles, "role_views": role_views, "admin": admin_cfg, "update": update_cfg, "audit": audit_cfg, "registries": registries,
            "marks": {k: v for cs in cards.values() for c in cs for k, v in (c.get("marks") or {}).items()},
            "trends": [] if sparklines is False else sorted({
                m[0] for cs in cards.values() for c in cs for m in c.get("metrics", []) + c.get("charts", [])
                if m[2] in TREND_FORMATS})}


def registry_host(host):
    """A registry as written ("https://GHCR.io/", "index.docker.io") as labhud matches it with an
    image's (agentproto.registry_of): lower case, no scheme or path, Docker Hub as docker.io.
    None when it is not a host[:port]."""
    if not isinstance(host, str):
        return None
    h = host.strip().lower()
    for scheme in ("https://", "http://"):
        if h.startswith(scheme):
            h = h[len(scheme):]
    h = h.split("/", 1)[0]
    if h in DOCKER_HUB_NAMES:
        return "docker.io"
    return h if REGISTRY_HOST_RE.fullmatch(h) else None


def _registries(ck, raw):
    """[[registry]] host, user: the private registries labhud signs in to when an agent pulls one of
    their images (api/registries.py). The token is never here: it is LABHUD_REGISTRY_<HOST>_TOKEN in
    secrets.env (⚙ > Containers > Registries)."""
    out, seen = [], set()
    for i, r in enumerate(ck.tables("top level", raw, "registry")):
        where = f"registry #{i + 1}"
        ck.unknown(where, r, REGISTRY_KEYS)
        host = ck.need(where, r, "host", str, "a registry host, e.g. \"ghcr.io\"")
        user = ck.need(where, r, "user", str, "the user to sign in as")
        if host is None or user is None:
            continue
        norm = registry_host(host)
        if not norm:
            ck.err(where, f"host must be a registry's host[:port], got {host!r}")
            continue
        if norm in seen:
            ck.err(where, f"{norm} is listed twice")
            continue
        if not user.strip() or len(user) > 128 or any(c in user for c in "\r\n"):
            ck.err(where, "user must be one line of at most 128 characters")
            continue
        seen.add(norm)
        out.append({"host": norm, "user": user.strip()})
    return out


def _roles(ck, raw, page_ids):
    """[roles.<viewer|operator|admin>] pages = [page ids]: the pages that role sees in /api/v1/state;
    view = "glance" | "actions" | "full": how the app opens for it. A role without a table sees
    every page, one without `pages` too. -> ({role: [page ids]}, {role: view})"""
    if "roles" not in raw:
        return {}, {}
    rt = raw["roles"]
    if not isinstance(rt, dict):
        ck.err("top level", "roles must be written as [roles.viewer], [roles.operator] and [roles.admin] tables")
        return {}, {}
    ck.unknown("roles", rt, set(ROLE_NAMES))
    out, views = {}, {}
    for name in ROLE_NAMES:
        if name not in rt:
            continue
        where = f"roles.{name}"
        t = rt[name]
        if not isinstance(t, dict):
            ck.err(where, f"must be a [{where}] table")
            continue
        ck.unknown(where, t, ROLE_KEYS)
        view = t.get("view")
        if view is not None:
            if view not in ROLE_VIEWS:
                ck.err(where, f"view must be one of {', '.join(ROLE_VIEWS)}, got {view!r}")
            else:
                views[name] = view
        pages = t.get("pages")
        if pages is None and view is not None:
            continue
        if not isinstance(pages, list) or not pages or not all(isinstance(p, str) for p in pages):
            ck.err(where, "pages must be a list of page ids (at least one)")
            continue
        missing = [p for p in pages if p not in page_ids]
        if missing:
            ck.err(where, f"page {', '.join(repr(m) for m in missing)} does not exist")
            continue
        out[name] = list(dict.fromkeys(pages))
    return out, views


def public(topology):
    """What the browser gets from /api/config: the layout, without the server-side schedules
    and without the weather coordinates (only the city name is shown)."""
    out = {k: topology[k] for k in ("title", "pages", "cards", "strip", "actions", "alerts", "night", "trends")}
    out["search"] = topology.get("search") or {}
    out["bookmarks"] = topology.get("bookmarks") or []
    out["calm"] = topology.get("calm")
    out["display"] = topology.get("display") or {"warn": DEFAULT_MARKS[0], "crit": DEFAULT_MARKS[1], "icons_online": True,
                                                 "base": "oled", "accent": "blue"}
    out["marks"] = topology.get("marks") or {}
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
