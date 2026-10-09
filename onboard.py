"""What setting labhud up needs, shared by init.py (the terminal) and the setup page.

Discovery of a Proxmox VE (nodes, guests, whether the token only reads), the certificate
fingerprint to pin, the weather city, the text of config.toml and .env, and for every other
source: what it needs (catalog) and whether given values work (try_source), without touching the
environment of the running server.

Standard library only, like the rest of labhud.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sources  # noqa: E402
from sources._common import _NO_VERIFY, PREFIX, REGISTRY, beyond_reading, env_name, scrub, trying  # noqa: E402

def fingerprint(url):
    """The SHA-256 of the certificate a service presents, as LABHUD_PINS wants it. Compare it with
    what the service itself shows (Proxmox: Node > System > Certificates) before you trust it."""
    import hashlib
    import ssl
    u = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=10)
    return f"{u.hostname}:{u.port or 443}", hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def get(url, header=None, timeout=10):
    req = urllib.request.Request(url, headers=header or {})
    with urllib.request.urlopen(req, timeout=timeout, context=_NO_VERIFY) as r:
        return json.loads(r.read() or b"{}")


def discover(url, token_id, secret):
    """{"version", "nodes": [name], "guests": {node: [(vmid, name, type)]}, "extra": [privilege],
    "addresses": {node: ip, or "" for the node at `url`}, "running": [[node, vmid]]}."""
    header = {"Authorization": f"PVEAPIToken={token_id}={secret}"}
    version = get(f"{url}/api2/json/version", header).get("data", {}).get("version", "?")
    nodes = sorted(n["node"] for n in get(f"{url}/api2/json/nodes", header).get("data", []))
    guests = {n: [] for n in nodes}
    running = []
    for r in get(f"{url}/api2/json/cluster/resources?type=vm", header).get("data", []):
        if r.get("node") in guests and not r.get("template"):
            guests[r["node"]].append((int(r["vmid"]), r.get("name") or str(r["vmid"]), r.get("type", "qemu")))
            if r.get("status") == "running":
                running.append([r["node"], int(r["vmid"])])
    for g in guests.values():
        g.sort()
    extra = beyond_reading(get(f"{url}/api2/json/access/permissions", header).get("data"))
    # what each node card pings: the address given for the node that answered, the others' own IPs
    addresses = {}
    try:
        for r in get(f"{url}/api2/json/cluster/status", header).get("data", []):
            if r.get("type") == "node" and r.get("name") in guests:
                addresses[r["name"]] = "" if r.get("local") else r.get("ip", "")
    except Exception:
        pass  # older versions or missing rights: the cards then ping the node names
    return {"version": version, "nodes": nodes, "guests": guests, "extra": extra, "addresses": addresses,
            "running": sorted(running)}


def geocode(city):
    """(latitude, longitude, timezone, name) from Open-Meteo's free geocoding, or None."""
    q = urllib.parse.urlencode({"name": city, "count": 1})
    found = get(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
    if not found:
        return None
    r = found[0]
    return round(r["latitude"], 2), round(r["longitude"], 2), r.get("timezone", ""), r.get("name", city)


def ident(text):
    """A config id from any name: "My Node" -> "my-node"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def q(text):
    """A TOML string (JSON's escaping is valid TOML for a basic string)."""
    return json.dumps(text, ensure_ascii=False)


# A first card for each source set up on the setup page: (metrics, extra lines). Edit freely
# afterwards; config.example.toml has every option.
CARDS = {
    "synology": ([("used_percent", "Used", "percent"), ("cpu", "CPU", "percent"), ("mem", "RAM", "percent")], {}),
    "pbs": ([("used_percent", "Used", "percent"), ("failed", "Failed 24h", "count")], {}),
    "opnsense": ([("cpu", "CPU", "percent"), ("mem", "RAM", "percent"), ("wan_dn", "WAN ↓", "rate"),
                  ("wan_up", "WAN ↑", "rate")], {"panel": "network:traffic"}),
    "qbt": ([("active", "Active", "count"), ("dl", "↓", "rate"), ("ul", "↑", "rate")],
            {"torrents": "qbt.torrents", "panel": "media:torrents"}),
    "sonarr": ([("series", "Series", "count"), ("wanted", "Missing", "count"), ("queued", "Queued", "count")],
               {"panel": "media:sonarr"}),
    "radarr": ([("movies", "Movies", "count"), ("wanted", "Missing", "count"), ("queued", "Queued", "count")],
               {"panel": "media:radarr"}),
    "jellyfin": ([("MovieCount", "Movies", "count"), ("SeriesCount", "Series", "count"),
                  ("streams", "Watching", "count")], {"list": "jellyfin.now", "panel": "media:jellyfin"}),
    "navidrome": ([("songs", "Songs", "count"), ("listening", "Listening", "count")], {"list": "navidrome.now"}),
    "seerr": ([("pending", "Pending", "count"), ("total", "Requests", "count")], {"list": "seerr.waiting"}),
    "prowlarr": ([("numberOfGrabs", "Grabs", "count"), ("numberOfFailGrabs", "Failed", "count")], {}),
    "healthchecks": ([("up", "Up", "count"), ("down", "Down", "count")], {"list": "healthchecks.checks"}),
    "uptimekuma": ([("up", "Up", "count"), ("down", "Down", "count")], {"list": "uptimekuma.monitors"}),
    "docker": ([("running", "Up", "count"), ("unhealthy", "Unhealthy", "count"), ("restarting", "Restarting", "count")],
               {"list": "docker.containers"}),
    "nut": ([("charge", "Battery", "percent1"), ("runtime_min", "Minutes", "count"), ("load", "Load", "percent1")], {}),
    "certs": ([("cert_min_days", "Soonest", "count"), ("cert_under_14", "Under 14d", "count")], {"list": "certs.list"}),
    "technitium": ([("queries", "Queries", "count"), ("blocked_percent", "Blocked", "percent1")], {"list": "technitium.top_blocked"}),
    "pihole": ([("queries", "Queries", "count"), ("blocked_percent", "Blocked", "percent1")], {}),
    "adguard": ([("queries", "Queries", "count"), ("blocked_percent", "Blocked", "percent1")], {"list": "adguard.top_blocked"}),
    "beszel": ([("up", "Up", "count"), ("down", "Down", "count")], {"list": "beszel.systems"}),
    "updates": ([("available", "Updates", "count")], {"list": "updates.containers"}),
    "games": ([("online", "Online", "count"), ("players", "Players", "count")], {"list": "games.servers"}),
    "traefik": ([("routers", "Routers", "count"), ("router_errors", "Errors", "count")], {"list": "traefik.problems"}),
    "speedtest": ([("download", "↓", "rate"), ("upload", "↑", "rate")], {}),
    "immich": ([("photos", "Photos", "count"), ("videos", "Videos", "count"), ("usage", "Size", "bytes")], {}),
    "agenda": ([("today", "Today", "count")], {"list": "agenda.events"}),
    "releases": ([], {"list": "releases.list"}),
    "scrutiny": ([("failed", "Failed", "count"), ("hottest", "Hottest", "celsius")], {"list": "scrutiny.disks"}),
}


def source_cards(names):
    """config.toml lines: a SERVICES group with one card per source in `names` that has one."""
    chosen = [n for n in names if n in CARDS]
    if not chosen:
        return []
    out = ["", "  [[page.group]]", '  id = "services"', '  title = "SERVICES"']
    for name in chosen:
        metrics, extra = CARDS[name]
        title = REGISTRY[name].title if name in REGISTRY else name
        out += ["", "    [[page.group.card]]", f'    id = "src-{ident(name)}"', f"    name = {q(title)}"]
        if metrics:
            out.append("    metrics = [")
            out += [f'      {{ key = "{name}.{k}", label = {q(label)}, format = "{fmt}" }},' for k, label, fmt in metrics]
            out.append("    ]")
        out += [f"    {k} = {q(v)}" for k, v in extra.items()]
    return out


def build_config(found, host, weather=None, title="LABHUD", include=None, services=()):
    """The text of config.toml for what discover() found (None: no Proxmox). `host` is what the
    node cards ping; `include`, a set of (node, vmid), limits the guest cards to those (None:
    every guest); `services` adds a card for each of those sources (CARDS)."""
    found = found or {"nodes": [], "guests": {}}
    out = [
        "# Made by labhud init. Every option is explained in config.example.toml; check this",
        "# file after an edit with: python3 config.py config.toml",
        "",
        f"title = {q(title)}",
        "",
        "[[page]]",
        'id = "general"',
        'title = "GENERAL"',
    ]
    strip = []
    for node in found["nodes"]:
        nid = ident(node)
        out += ["", "  [[page.group]]", f'  id = "node-{nid}"', f"  title = {q(node.upper())}", "",
                "    [[page.group.card]]", f'    id = "host-{nid}"', f"    name = {q(node.upper())}"]
        # the node that answered is pinged at the address given; the others at their own IP
        addr = found.get("addresses", {}).get(node)
        out.append(f"    ping = {q(host if addr == '' or len(found['nodes']) == 1 else addr or node)}")
        out += ["    large = true", "    metrics = ["]
        for key, label, fmt in (("vms", "VM", "count"), ("lxc", "LXC", "count"), ("cpu", "CPU", "percent"),
                                ("mem_used_of", "RAM", "used_of"), ("disk_used_of", "Disk", "used_of")):
            out.append(f'      {{ key = "proxmox.{node}.{key}", label = "{label}", format = "{fmt}" }},')
        out += ["    ]", f"    panel = {q('host:' + node)}"]
        for vmid, name, _ in found["guests"][node]:
            if include is not None and (node, vmid) not in include:
                continue
            out += ["", "    [[page.group.card]]", f'    id = "vm-{nid}-{vmid}"', f"    name = {q(name)}",
                    f'    proxmox = "{node}/{vmid}"']
        strip.append((node.upper()[:4], node, f"host-{nid}"))
    out += source_cards(services)
    for code, name, card in strip:
        out += ["", "[[strip]]", f"code = {q(code)}", f"name = {q(name)}", f"card = {q(card)}"]
    if weather:
        lat, lon, tz, city = weather
        out += ["", "[weather]", f"latitude = {lat}", f"longitude = {lon}"]
        if tz:
            out.append(f"timezone = {q(tz)}")
        out.append(f"city = {q(city)}")
    return "\n".join(out) + "\n"


def build_env(found, url, token_id, secret, hosts, pin=None):
    out = ["# Made by labhud init. Keep this file readable only by you (chmod 600).",
           "# Every other source and setting: .env.example.",
           f"LABHUD_HOSTS={','.join(hosts)}",
           ""]
    if pin:
        out += ["# The certificate Proxmox presented during init: anything else is refused (docs/security.md).",
                f"LABHUD_PINS={pin[0]}={pin[1]}", ""]
    if not found:  # no Proxmox
        return "\n".join(out) + "\n"
    out += [f"LABHUD_PROXMOX_NODES={','.join(found['nodes'])}"]
    for node in found["nodes"]:
        key = env_name(node)
        out += [f"LABHUD_PROXMOX_{key}_URL={url}", f"LABHUD_PROXMOX_{key}_TOKEN_ID={token_id}",
                f"LABHUD_PROXMOX_{key}_TOKEN_SECRET={secret}"]
    return "\n".join(out) + "\n"


def write(folder, name, text, mode):
    path = os.path.join(folder, name)
    if os.path.exists(path):
        path += ".new"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)
    return path


# ---------------------------------------------------------------------------------------------
# The other sources
# ---------------------------------------------------------------------------------------------

# Set up by their own steps (Proxmox, the weather), not from a form.
OWN_STEP = {"proxmox", "backups", "weather"}


def catalog():
    """[{"name", "title", "about", "fields", "configured", "with"}] for every source a form can set
    up. "with" names the sources whose settings turn a derived one on (recent: Sonarr, Radarr);
    such a source has no form of its own. Values are never included, only whether they are set."""
    sources.load({})
    own = {}
    for name, src in REGISTRY.items():
        if name not in OWN_STEP and len(src.env) == 1:
            for group in src.env:
                own.setdefault(group, name)
    out = []
    for name, src in sorted(REGISTRY.items(), key=lambda kv: kv[1].title.lower()):
        if name in OWN_STEP or not src.env or not src.backoff:  # pushed sources: init.py agent
            continue
        derived = [own[g] for g in src.env if own.get(g) not in (None, name)]
        out.append({"name": name, "title": src.title, "about": src.about,
                    "fields": [] if derived else src.fields(),
                    "configured": not src.missing({}), "with": derived})
    return out


def try_source(name, values):
    """Runs source `name` once with `values` ({"LABHUD_X": "..."}, only its own variables count)
    in place of the environment. {"ok": True, "keys": [...]} or {"ok": False, "error": text}."""
    sources.load({})
    src = REGISTRY.get(name)
    if src is None or name in OWN_STEP:
        return {"ok": False, "error": f"no source {name!r} to try"}
    allowed = {f["name"] for f in src.fields()}
    given = {k: str(v).strip() for k, v in (values or {}).items() if k in allowed and str(v).strip()}
    with trying(given):
        lacking = src.missing({})
        if lacking:
            return {"ok": False, "error": "missing: " + ", ".join(lacking)}
        try:
            result = src.run({})
        except Exception as e:
            return {"ok": False, "error": scrub(e)[:200] or type(e).__name__}
    if not isinstance(result, dict) or result.get("unavailable"):
        return {"ok": False, "error": scrub((result or {}).get("unavailable", "no answer"))[:200]}
    return {"ok": True, "keys": sorted(result)[:20]}


_LINE = re.compile(r"^\s*(?:export\s+)?(" + PREFIX + r"[A-Z0-9_]+)\s*=")


def merge_env(existing, values):
    """`existing` .env text with `values` ({"LABHUD_X": "..."}) set: a variable already there is
    replaced on its own line, a new one is added at the end; comments and the rest stay. An empty
    value removes the variable."""
    values = {k: v for k, v in values.items() if k.startswith(PREFIX) and "\n" not in str(v)}
    out, done = [], set()
    for line in (existing or "").splitlines():
        m = _LINE.match(line)
        if m and m.group(1) in values:
            done.add(m.group(1))
            if values[m.group(1)] != "":
                out.append(f"{m.group(1)}={values[m.group(1)]}")
            continue
        out.append(line)
    added = [f"{k}={v}" for k, v in values.items() if k not in done and v != ""]
    if added:
        if out and out[-1].strip():
            out.append("")
        out += added
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------------
# A pushing agent (init.py agent <name>)
# ---------------------------------------------------------------------------------------------

AGENT_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")


def agent_card(name):
    """config.toml lines: a group with one large card showing what labhud-agent pushes."""
    title = name.upper().replace("_", "-")
    lines = ["", "  [[page.group]]", f'  id = "agent-{ident(name)}"', f"  title = {q(title)}", "",
             "    [[page.group.card]]", f'    id = "host-agent-{ident(name)}"', f"    name = {q(title)}",
             "    large = true", "    metrics = ["]
    for key, label, fmt in (("cpu", "CPU", "percent"), ("mem_used_of", "RAM", "used_of"),
                            ("disk_used_of", "Disk", "used_of"), ("cpu_temp", "Temp", "celsius")):
        lines.append(f'      {{ key = "{name}.{key}", label = "{label}", format = "{fmt}" }},')
    return lines + ["    ]"]


def add_agent(name, config_path, env_path):
    """Adds pushed source `name`: a new key in .env (600), then a card in config.toml (labhud
    reads both when the config changes). Returns the key. Raises ValueError, writing nothing, if
    the name is taken or the config would not load."""
    import secrets
    if not AGENT_NAME.fullmatch(name or ""):
        raise ValueError("the name must be a-z, 0-9 and _, starting with a letter, at most 32")
    var = f"{PREFIX}PUSH_{name.upper()}_KEY"
    try:
        with open(env_path, encoding="utf-8") as f:
            env_text = f.read()
    except FileNotFoundError:
        env_text = ""
    if re.search(rf"^\s*(export\s+)?{var}\s*=", env_text, re.M) or os.environ.get(var):
        raise ValueError(f"{var} is already set: that agent exists")
    with open(config_path, encoding="utf-8") as f:
        cfg = f.read()
    new_cfg = cfg.rstrip("\n") + "\n" + "\n".join(agent_card(name)) + "\n"
    import config
    try:
        config.loads(new_cfg, config_path)
    except config.ConfigError as e:
        raise ValueError("config.toml would not load: " + "; ".join(e.problems)) from None
    key = secrets.token_hex(32)
    _write_file(env_path, merge_env(env_text, {var: key}), 0o600)
    _write_file(config_path, new_cfg, None)
    return key


def _write_file(path, text, mode):
    """Replaces the file's content in place, keeping its inode (a single mounted file follows)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode if mode is not None else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


# ---------------------------------------------------------------------------------------------
# Finding services on given hosts (the setup page's "Find services", only when asked)
# ---------------------------------------------------------------------------------------------

# source -> (port, scheme, path, marker): the service is taken as found only when the answer at
# that path contains the marker, not because a port is open.
KNOWN = [
    ("jellyfin", 8096, "http", "/System/Info/Public", "jellyfin"),
    ("sonarr", 8989, "http", "/", "sonarr"),
    ("radarr", 7878, "http", "/", "radarr"),
    ("prowlarr", 9696, "http", "/", "prowlarr"),
    ("bazarr", 6767, "http", "/", "bazarr"),
    ("seerr", 5055, "http", "/api/v1/status", "committag"),
    ("navidrome", 4533, "http", "/app/", "navidrome"),
    ("qbt", 8080, "http", "/", "qbittorrent"),
    ("uptimekuma", 3001, "http", "/", "uptime kuma"),
    ("scrutiny", 8080, "http", "/web/", "scrutiny"),
    ("healthchecks", 8000, "http", "/", "healthchecks"),
    ("pbs", 8007, "https", "/", "proxmox backup server"),
    ("synology", 5000, "http", "/", "synology"),
    ("opnsense", 443, "https", "/", "opnsense"),
    ("docker", 2375, "http", "/version", "apiversion"),
]
MAX_HOSTS = 32
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}$|^[0-9A-Fa-f:]{2,45}$")


def _probe(host, port, scheme, path, marker):
    import socket
    try:
        socket.create_connection((host, port), timeout=0.6).close()
    except OSError:
        return False
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    try:
        req = urllib.request.Request(f"{scheme}://{netloc}{path}", headers={"User-Agent": "labhud-setup"})
        with urllib.request.urlopen(req, timeout=3, context=_NO_VERIFY) as r:
            body = r.read(65536)
    except urllib.error.HTTPError as e:  # some answer 401 with their name in the page
        body = e.read(65536) if hasattr(e, "read") else b""
    except Exception:
        return False
    return marker in body.decode("utf-8", "replace").lower()


def suggest(hosts):
    """[{"source", "url"}] for the known services found on `hosts` (names or IPs, at most
    MAX_HOSTS). Raises ValueError for anything that is not a host name or address (no ranges)."""
    import concurrent.futures
    hosts = [h.strip() for h in hosts if h and h.strip()]
    if len(hosts) > MAX_HOSTS:
        raise ValueError(f"at most {MAX_HOSTS} hosts at once")
    bad = [h for h in hosts if not _HOST.match(h)]
    if bad:
        raise ValueError("not a host name or address: " + ", ".join(bad[:5]))
    jobs = [(h, k) for h in dict.fromkeys(hosts) for k in KNOWN]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        for (h, (name, port, scheme, path, marker)), ok in zip(
                jobs, pool.map(lambda j: _probe(j[0], *j[1][1:]), jobs)):
            if ok:
                netloc = f"[{h}]:{port}" if ":" in h else f"{h}:{port}"
                found.append({"source": name, "url": f"{scheme}://{netloc}"})
    return found
