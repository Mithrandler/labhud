"""Demo mode (LABHUD_DEMO=1): made-up data for demo/config.toml, with no network, no keys and no ping.

Every source in the demo topology is replaced by a function here that returns the same shape the
real source would. Numbers drift slowly with time, so the display looks alive on screenshots and in
a browser. Addresses are from the ranges reserved for documentation (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24).
"""

import json
import math
import os
import re
import time

from sources._common import Source

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")

GB = 1024 ** 3
TB = 1024 ** 4
DOWN = {"backup", "desktop"}  # cards whose host is off in the demo (both on_demand)


# Seconds the made-up clock runs behind the real one: history() moves it back to compute the past.
_shift = 0


def _wave(base, amp, period, phase=0.0):
    """A slow, smooth change around `base`: the same value for everyone at the same moment."""
    return base + amp * math.sin(2 * math.pi * ((time.time() - _shift) / period + phase))


def _ago(seconds):
    return int(time.time() - seconds)


# ---------------------------------------------------------------------------------------------
# Hosts and storage
# ---------------------------------------------------------------------------------------------

GUESTS = [  # vmid, name, type, running, RAM in GB, disk in GB
    (100, "docker", "qemu", True, 16, 120),
    (101, "homeassistant", "qemu", True, 4, 32),
    (102, "nextcloud", "qemu", True, 8, 64),
    (103, "jellyfin", "lxc", True, 6, 40),
    (104, "pihole", "lxc", True, 1, 8),
    (105, "gitea", "lxc", True, 2, 20),
    (106, "windows", "qemu", False, 16, 128),
]


def proxmox():
    guests = {}
    for i, (vmid, name, typ, running, ram, disk) in enumerate(GUESTS):
        mem = _wave(45, 20, 900, i / 7) if running else 0
        guests[str(vmid)] = {
            "name": name, "running": running, "type": typ,
            "cpu": round(_wave(8, 6, 300, i / 5), 1) if running else 0,
            "mem": round(mem, 1), "mem_bytes": int(ram * GB * mem / 100), "mem_max": ram * GB,
            "disk": 0, "uptime": 86400 * (3 + i) if running else 0,
            "disk_used": int(disk * GB * (0.35 + i / 20)), "disk_total": disk * GB,
        }
    mem_total = 64 * GB
    mem_used = int(mem_total * _wave(0.55, 0.05, 1200))
    storage = [
        {"name": "local-lvm", "type": "lvmthin", "used": int(0.62 * TB), "total": 1 * TB},
        {"name": "tank", "type": "zfspool", "used": int(2.1 * TB), "total": 4 * TB},
        {"name": "nas-backup", "type": "nfs", "used": int(9.4 * TB), "total": 24 * TB},
    ]
    for s in storage:
        pct = 100 * s["used"] / s["total"]
        s["value"] = f"{s['used'] / TB:.1f}T / {s['total'] / TB:.0f}T · {pct:.0f}%"
        s["bad"] = pct > 90
    return {"pve": {
        "vms": sum(1 for g in GUESTS if g[2] == "qemu"), "lxc": sum(1 for g in GUESTS if g[2] == "lxc"),
        "cpu": round(_wave(14, 8, 240), 1),
        "mem": round(100 * mem_used / mem_total, 1), "mem_used": mem_used, "mem_total": mem_total,
        "mem_used_of": [mem_used, mem_total], "disk_used_of": [int(0.62 * TB), 1 * TB],
        "uptime": 86400 * 12, "storage": storage,
        "storage_all": [{"name": f"pve {s['name']}", "value": s["value"], "bad": s["bad"]} for s in storage],
        "guests": guests,
    }}


def temp():
    return {"pve": {
        "cpu_temp": round(_wave(52, 6, 300), 1), "gpu_count": 2,
        "gpu0_util": round(max(0.0, _wave(20, 25, 600)), 1), "gpu0_mem": round(_wave(30, 5, 900), 1),
        "gpu0_temp": round(_wave(46, 5, 600), 1),
        "gpu1_util": round(max(0.0, _wave(60, 30, 500)), 1), "gpu1_mem": round(_wave(70, 8, 700), 1),
        "gpu1_temp": round(_wave(62, 6, 500), 1),
    }}


def synology():
    total, used = 21.8 * TB, 14.3 * TB
    return {"cpu": round(_wave(6, 4, 200), 1), "mem": round(_wave(38, 4, 900)),
            "total": int(total), "used": int(used), "free": int(total - used),
            "used_of": [int(used), int(total)], "used_percent": round(100 * used / total, 1)}


def pbs():
    return {"unavailable": "host is off"}  # off on purpose: the card says "started on demand"


# ---------------------------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------------------------

def opnsense():
    dn = max(0.0, _wave(1.8e6, 1.6e6, 90))
    up = max(0.0, _wave(2.4e5, 2.0e5, 70, 0.3))
    return {
        "cpu": round(_wave(9, 5, 120), 1), "mem": round(_wave(31, 2, 900), 1),
        "wan_dn": dn, "wan_up": up,
        "interfaces": {"wan": {"dn": dn, "up": up}, "lan": {"dn": up * 0.9, "up": dn * 0.95},
                       "iot": {"dn": 2.1e4, "up": 6.3e3}, "guest": {"dn": 0.0, "up": 0.0}},
        "top_lan": {"lan": {"status": "ok", "records": [
            {"address": "192.0.2.20", "rate_bits_in": int(dn * 4), "rate_bits_out": int(up * 2)},
            {"address": "192.0.2.10", "rate_bits_in": int(dn * 3), "rate_bits_out": int(up * 5)},
            {"address": "192.0.2.3", "rate_bits_in": int(dn), "rate_bits_out": int(up)},
        ]}},
    }


# ---------------------------------------------------------------------------------------------
# The live agent (see docs/live-agent.md)
# ---------------------------------------------------------------------------------------------

def live():
    total = int(_wave(48000, 3000, 3600))
    blocked = int(total * 0.16)
    devices = [
        {"name": "phone-alex", "mac": "02:00:00:00:00:11", "ip": "192.0.2.111"},
        {"name": "laptop-sam", "mac": "02:00:00:00:00:12", "ip": "192.0.2.112"},
        {"name": "tv-living", "mac": "02:00:00:00:00:13", "ip": "192.0.2.113"},
        {"name": "thermostat", "mac": "02:00:00:00:00:14", "ip": "192.0.2.114"},
        {"name": "printer", "mac": "02:00:00:00:00:15", "ip": "192.0.2.115"},
    ]
    log = [
        {"text": "backup of 100 docker finished", "ts": _ago(1800)},
        {"text": "ban 203.0.113.45 (ssh brute force)", "ts": _ago(3900)},
        {"text": "new device: thermostat", "ts": _ago(7200)},
        {"text": "update: 14 packages on pve", "ts": _ago(14400)},
        {"text": "certificate renewed: cloud", "ts": _ago(30000)},
        {"text": "login: admin from 192.0.2.112", "ts": _ago(43000)},
    ]
    servers = [
        {"id": "a1f3", "name": "mc", "game": "Minecraft", "state": "running", "address": "192.0.2.40:25565",
         "ram_mb": 3100, "ram_max_mb": 6144, "uptime_s": 7200,
         "query": {"players": 3, "max": 20, "map": None, "version": "1.21", "names": ["alex", "sam", "kim"]}},
        {"id": "b7c2", "name": "valheim", "game": "Valheim", "state": "running", "address": "192.0.2.40:2456",
         "ram_mb": 2600, "ram_max_mb": 4096, "uptime_s": 3600,
         "query": {"players": 1, "max": 10, "map": "Midgard", "version": None, "names": []}},
        {"id": "c9d4", "name": "terraria", "game": "Terraria", "state": "offline", "address": "192.0.2.40:7777",
         "ram_mb": 0, "ram_max_mb": 2048, "uptime_s": 0, "query": None},
        {"id": "d2e8", "name": "factorio", "game": "Factorio", "state": "offline", "address": "192.0.2.40:34197",
         "ram_mb": 0, "ram_max_mb": 4096, "uptime_s": 0, "query": None},
    ]
    return {
        "dns": {"total": total, "blocked": blocked, "percent": round(100 * blocked / total, 1)},
        "devices": {"active": len(devices) + 14, "list": devices},
        "suricata": {"day": 412, "day_blocked": 388, "hour": int(_wave(17, 8, 600)), "hour_blocked": 15,
                     "signatures": [{"name": "ET SCAN Suspicious inbound to SSH", "n": 211},
                                    {"name": "ET DROP Spamhaus DROP listed traffic", "n": 104},
                                    {"name": "ET SCAN NMAP OS detection probe", "n": 61}]},
        "logins": {"hour": 2, "recent": [
            {"app": "ssh", "user": "root", "ip": "203.0.113.45", "ts": _ago(600)},
            {"app": "nextcloud", "user": "admin", "ip": "198.51.100.7", "ts": _ago(2400)}]},
        "log": log,
        "alerts": [{"id": "demo-disk", "text": "Disk on nas-backup above 85% in 12 days, at the current rate"}],
        "containers": {"100": [
            {"name": "traefik", "state": "running", "status": "Up 5 days"},
            {"name": "immich", "state": "running", "status": "Up 5 days"},
            {"name": "paperless", "state": "running", "status": "Up 2 days"},
            {"name": "uptime-kuma", "state": "exited", "status": "Exited (1) 3 hours ago"}]},
        "games": {"servers": servers},
        "display": {
            "dns_blocked": [{"name": "telemetry.example.com", "value": "1,204"},
                            {"name": "ads.example.net", "value": "988"},
                            {"name": "tracker.example.org", "value": "611"},
                            {"name": "metrics.example.com", "value": "402"}],
            "dns_clients": [{"name": d["name"], "value": str(900 - 150 * i)} for i, d in enumerate(devices)],
            "new_devices": [{"name": "thermostat", "value": "2h"}, {"name": "printer", "value": "3d"}],
            "sources": [{"name": "203.0.113.45", "value": "NL"}, {"name": "198.51.100.23", "value": "US"},
                        {"name": "203.0.113.77", "value": "CN"}, {"name": "198.51.100.90", "value": "BR"}],
            "countries": [{"name": "NL", "value": "120"}, {"name": "US", "value": "84"}, {"name": "CN", "value": "51"}],
            "tripwire": [{"name": "/etc/ssh/sshd_config", "value": "12d"}],
            "logins": [{"name": "ssh", "value": "2 / hour"}, {"name": "nextcloud", "value": "0 / hour"}],
            "ports": [{"name": "unexpected open", "value": "none"},
                      {"name": "forwarded open", "value": "2 of 3"},
                      {"name": "last scan", "value": "9h ago"}],
            "ports_detail": [
                {"title": "how it works", "rows": [
                    {"name": "scanned from", "value": "outside, nightly"},
                    {"name": "expected open", "value": "the router's port forwards"},
                    {"name": "alarm", "value": "any other open port"}]},
                {"title": "forwarded TCP ports", "rows": [
                    {"name": "443 reverse proxy", "value": "open"},
                    {"name": "25565 minecraft", "value": "open"},
                    {"name": "27015 game server", "value": "closed · server off"}]},
            ],
            "log": [{"name": x["text"], "value": f"{max(1, (int(time.time()) - x['ts']) // 3600)}h"} for x in log],
        },
    }


# ---------------------------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------------------------

def qbt():
    p = (time.time() / 30) % 100
    torrents = [
        {"name": "debian-13.1.0-amd64-netinst.iso", "progress": round(p, 1), "remaining": int((100 - p) * 7e6), "eta": int((100 - p) * 4)},
        {"name": "ubuntu-24.04.3-desktop-amd64.iso", "progress": 62.0, "remaining": int(2.3e9), "eta": 1500},
        {"name": "archlinux-2026.10.01-x86_64.iso", "progress": 8.5, "remaining": int(1.1e9), "eta": 5400},
    ]
    return {"dl": int(max(0.0, _wave(3.2e6, 1.5e6, 60))), "ul": int(max(0.0, _wave(4.1e5, 2e5, 45))),
            "active": len(torrents), "torrents": torrents}


def recent():
    return {"added": [{"name": "Big Buck Bunny (2008)", "value": "today"},
                      {"name": "Sintel (2010)", "value": "yesterday"},
                      {"name": "Tears of Steel (2012)", "value": "2d"},
                      {"name": "Elephants Dream (2006)", "value": "4d"},
                      {"name": "Cosmos Laundromat (2015)", "value": "6d"}],
            "queue": [{"name": "Spring (2019)", "value": "41%"}]}


def sonarr():
    return {"series": 86, "wanted": 3, "queued": 1, "missing": ["Example Show S02E05", "Another Show S01E09", "Third Show S04E01"]}


def radarr():
    return {"movies": 412, "wanted": 2, "queued": 1, "missing": ["Agent 327 (2017)", "Charge (2022)"]}


def jellyfin():
    return {"MovieCount": 412, "SeriesCount": 86, "EpisodeCount": 3120, "BoxSetCount": 24,
            "TrailerCount": 0, "ItemCount": 9480, "streams": 1,
            "sessions": [{"user": "alex", "title": "Sintel", "client": "Android TV"}],
            "now": [{"name": "alex: Sintel", "value": "Android TV"}]}


def weather():
    import datetime
    today = datetime.date.today()
    days = [{"date": (today + datetime.timedelta(days=i)).isoformat(), "max": hi, "min": lo, "code": code, "rain": rain}
            for i, (hi, lo, code, rain) in enumerate([(18.0, 9.0, 2, 10), (16.0, 8.0, 61, 70), (12.0, 4.0, 3, 30), (15.0, 6.0, 0, 0)])]
    return {"temp": round(_wave(14, 4, 86400), 1), "code": 2, "max": 18.0, "min": 9.0, "days": days}


def agents():
    """labhud-agent in the docker VM (agents.<name>, as agents.snapshot() gives it): its numbers
    for the panel's charts, and a disk past the mark of the default [[alert]] rule."""
    return {"docker": {"online": True, "version": "0.1.0", "caps": ["sensors", "docker"], "sensors": {
        # a day's rhythm plus a little of the minute, so 1 hour and 7 days both look like a host
        "cpu": round(_wave(28, 14, 86400) + _wave(0, 6, 1500), 1), "mem": round(_wave(62, 6, 86400, 0.3) + _wave(0, 2, 2400), 1),
        "disk": round(_wave(92.4, 0.5, 4 * 86400), 1), "cpu_temp": round(_wave(56, 7, 86400, 0.1) + _wave(0, 3, 1500)),
        "net_rx": int(_wave(2.2e6, 1.4e6, 86400, 0.6) + _wave(0, 4e5, 900)),
        "net_tx": int(_wave(0.8e6, 0.5e6, 86400, 0.6) + _wave(0, 1.5e5, 1200)),
        "load1": round(_wave(1.2, 0.8, 1800), 2), "uptime": 86400 * 3},
        "containers": {name: dict(x, image=img, **_usage(name, x)) for name, (img, x) in DEMO_CONTAINERS.items()},
        "failed_units": ["backup-offsite.service"],
        "history": _history(),
        "buttons": {"docker": {name: [f"agent-{DEMO_AGENT}-docker-{name}-{v}" for v in ("start", "stop", "restart", "update")]
                               for name in DEMO_CONTAINERS if name != "portainer"}}}}


def _usage(name, x):
    """A running container's numbers as the agent measures them: a day's rhythm per container."""
    if x.get("state") != "running":
        return {}
    base = {"jellyfin": (18, 900e6), "sonarr": (2, 260e6), "radarr": (3, 280e6), "qbittorrent": (9, 520e6), "portainer": (0.5, 40e6)}
    cpu, mem = base.get(name, (1, 100e6))
    return {"cpu": round(max(0.1, _wave(cpu, cpu * 0.6, 86400, len(name) / 10)), 1), "mem": int(_wave(mem, mem * 0.1, 3600)),
            "mem_pct": round(100 * mem / 8e9, 1), **({"restarts": 2} if name == "qbittorrent" else {})}


def _history():
    """What the agent told about the containers lately (agents.<name>.history)."""
    now = int(time.time())
    return {"docker:jellyfin": [{"t": now - 9 * 86400, "kind": "update", "ok": True, "from": "10.10.1", "to": "10.10.3",
                                 "text": "jellyfin updated (10.10.1 → 10.10.3)"}],
            "docker:qbittorrent": [{"t": now - 2 * 86400, "kind": "update", "ok": True, "from": "5.0.1", "to": "5.0.2",
                                    "text": "qbittorrent updated (5.0.1 → 5.0.2)"},
                                   {"t": now - 5 * 3600, "kind": "oom", "ok": False, "text": "qbittorrent ran out of memory"}],
            "docker:flaresolverr": [{"t": now - 2 * 3600, "kind": "crash", "ok": False,
                                     "text": "flaresolverr stopped by itself (exit code 1)"}]}


# The docker VM's containers: one with a newer image, one being updated, one stopped, one not ticked,
# in two compose projects (and one in none).
DEMO_AGENT = "d0c4e100"
_SCANNED = int(time.time()) - 5 * 3600   # the containers' image scans: this morning
DEMO_CONTAINERS = {
    "jellyfin": ("jellyfin/jellyfin:10.10.3", {"state": "running", "status": "Up 3 days (healthy)", "health": "healthy", "update": True,
                                               "project": "media", "scan": {"critical": 2, "high": 7, "tool": "trivy", "t": _SCANNED}}),
    "sonarr": ("lscr.io/linuxserver/sonarr:latest", {"state": "running", "status": "Up 3 days", "project": "arr",
                                                     "scan": {"critical": 0, "high": 3, "tool": "trivy", "t": _SCANNED}}),
    "radarr": ("lscr.io/linuxserver/radarr:latest", {"state": "running", "status": "Up 3 days", "update": True, "updating": True,
                                                     "project": "arr", "scan": {"critical": 0, "high": 0, "tool": "trivy", "t": _SCANNED}}),
    "qbittorrent": ("lscr.io/linuxserver/qbittorrent:latest", {"state": "running", "status": "Up 3 days", "project": "arr",
                                                               "labels": {"labhud.name": "qBittorrent", "labhud.page": "media",
                                                                          "labhud.url": "http://192.0.2.30:8080"}}),
    "flaresolverr": ("ghcr.io/flaresolverr/flaresolverr:latest", {"state": "exited", "status": "Exited (1) 2 hours ago",
                                                                  "project": "arr"}),
    "portainer": ("portainer/portainer-ce:2.21.4", {"state": "running", "status": "Up 3 days",
                                                    "labels": {"labhud.name": "Portainer", "labhud.url": "https://192.0.2.30:9443",
                                                               "labhud.description": "from its labhud.* labels"}}),
}


# ⚙ > Devices > Agents in the demo: the docker VM's agent and one on the Proxmox node, with what each
# found. Their ticks can be changed (in memory, until a restart), so the tabs and the app can be tried.
DEMO_PVE_AGENT = "d0c4e101"
MAC = re.compile(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}")
_ROWS = {
    DEMO_AGENT: {"name": "docker", "addr": "192.0.2.30", "install": "container", "version": "0.1.0", "found": ["sensors", "docker", "systemd", "host"],
                 "host": {"hostname": "docker", "os": "Debian GNU/Linux 13 (trixie)", "kernel": "6.12.48+deb13-amd64", "arch": "amd64"},
                 "caps": ["sensors", "docker"],
                 "targets": {"docker": [n for n in DEMO_CONTAINERS if n != "portainer"] + ["images"]},
                 "choices": {"docker": sorted(DEMO_CONTAINERS) + ["images"], "systemd": ["cron.service", "docker.service", "ssh.service"],
                             "host": ["host"]},
                 "labels": {"docker": {"images": "Unused images"}}, "prune": True, "auto": ["sonarr", "radarr"]},
    DEMO_PVE_AGENT: {"name": "pve", "addr": "192.0.2.10", "install": "service", "version": "0.0.9",   # one release behind
                     "found": ["sensors", "host", "pve", "wol", "systemd"],
                     "host": {"hostname": "pve", "os": "Proxmox VE 9.1", "kernel": "6.17.2-1-pve", "arch": "amd64"},
                     "caps": ["sensors", "host", "pve", "wol"],
                     "targets": {"host": ["host"], "pve": ["100", "101", "106"], "wol": ["7c:83:34:bb:36:47"]},
                     "choices": {"pve": [str(g[0]) for g in GUESTS], "wol": ["7c:83:34:bb:36:47", "b8:27:eb:12:34:56"],
                                 "host": ["host"], "systemd": ["pveproxy.service", "pvedaemon.service", "ssh.service"]},
                     "labels": {"pve": {str(g[0]): g[1] for g in GUESTS},
                                "wol": {"7c:83:34:bb:36:47": "192.0.2.50", "b8:27:eb:12:34:56": "192.0.2.61"}}},
}


def agent_rows(now=None):
    """The made-up agents as agents.listing() gives real ones."""
    now = int(now or time.time())
    return [dict({k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in r.items()},
                 id=aid, online=True, paired=now - 86400 * (12 if aid == DEMO_PVE_AGENT else 9), seen=now)
            for aid, r in _ROWS.items()]


def agent_change(aid, op, body, caps, verbs):
    """A tick in ⚙ or the app on a made-up agent: True, False (not a valid choice), or None (not one of them)."""
    r = _ROWS.get(aid)
    if r is None:
        return None
    if op == "caps":
        want = body.get("caps")
        if not (isinstance(want, list) and all(c in r["found"] and c in caps for c in want)):
            return False
        r["caps"] = list(dict.fromkeys(want))
        return True
    if op == "targets":
        cap, want = body.get("cap"), body.get("targets")
        if cap not in verbs or cap not in r["caps"] or not (isinstance(want, list) and all(isinstance(t, str) for t in want)):
            return False
        known = set(r["choices"].get(cap, []))
        if cap == "wol":   # a machine that is off: its MAC typed in
            known |= {t for t in want if MAC.fullmatch(t)}
        if not set(want) <= known:
            return False
        r["targets"][cap] = list(dict.fromkeys(want))
        if cap == "wol":
            r["choices"]["wol"] = sorted(set(r["choices"]["wol"]) | set(want))
        return True
    if op == "autoupdate":
        want = body.get("targets")
        if not (isinstance(want, list) and set(want) <= set(r["targets"].get("docker", [])) - {"images"}):
            return False
        r["auto"] = sorted(set(want))
        return True
    if op == "update":   # as if it had downloaded the release and dialled in again
        r["version"] = body.get("version") or r["version"]
        return True
    return False   # rename and remove: the demo's agents stay as they are



def logs(name):
    """A container's last lines, made up (the demo's POST /api/setup/logs)."""
    now = time.time()
    lines = {
        "jellyfin": ["[INF] [{}] Emby.Server.Implementations.ScheduledTasks.TaskManager: Scan Media Library Completed",
                     "[INF] [{}] MediaBrowser.MediaEncoding.Transcoding.TranscodeManager: FFmpeg exited with code 0",
                     "[WRN] [{}] Jellyfin.Networking.Manager.NetworkManager: Unable to bind to 0.0.0.0:1900"],
        "flaresolverr": ["{} INFO     FlareSolverr 3.3.21", "{} INFO     Testing web browser installation...",
                         "{} ERROR    Error: Chrome failed to start: exited abnormally"],
    }.get(name, ["{} [Info] RssSyncService: RSS Sync Completed. Reports found: 100, Reports grabbed: 0",
                 "{} [Info] DownloadDecisionMaker: Processing 100 releases"])
    out = []
    for i in range(40):
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - (40 - i) * 97))
        out.append(lines[i % len(lines)].format(t))
    return "\n".join(out) + "\n"


def backups():
    """The last vzdump of each guest of the node (sources/proxmox.py backups.nodes): one is old, one failed."""
    now = time.time()
    per = {}
    for i, (vmid, name, typ, running, ram, disk) in enumerate(GUESTS):
        g = {"name": name, "ts": now - 3600 * (3 + i), "ok": True}
        if i == 2:
            g.update(ok=False, error="timeout")
        if i == 4:
            g["ts"] = now - 9 * 86400
        per[str(vmid)] = g
    return {"nodes": {"pve": per}}


FETCHERS = {"proxmox": (proxmox, 10), "temp": (temp, 10), "synology": (synology, 30), "pbs": (pbs, 60),
            "opnsense": (opnsense, 5), "live": (live, 10), "qbt": (qbt, 5), "recent": (recent, 300),
            "sonarr": (sonarr, 300), "radarr": (radarr, 300), "jellyfin": (jellyfin, 30), "weather": (weather, 600),
            "agents": (agents, 10), "backups": (backups, 300)}


def events():
    """A made-up recent past for the RECENT card, newest first (see events.py)."""
    rows = [(25, "jellyfin answering again", False), (41, "jellyfin not answering: HTTP Error 401", True),
            (130, "windows stopped", False), (310, "BACKUP off", False), (365, "BACKUP up", False),
            (540, "Disk on nas-backup above 85% in 12 days, at the current rate", True)]
    return [{"name": text, "t": _ago(minutes * 60), "bad": bad} for minutes, text, bad in rows]


def history(points, step, value):
    """The sparklines' past, as if the server had run for the last `points` minutes: every
    source is computed again at those moments, then `value(data)` picks the numbers."""
    global _shift
    out = []
    try:
        for i in range(points, 0, -1):
            _shift = i * step
            out.append(value({name: fetch() for name, (fetch, _) in FETCHERS.items()}))
    finally:
        _shift = 0
    return out


def sources():
    """{name: Source}, in place of what sources.load() would find."""
    return {name: Source(name, fetch, every) for name, (fetch, every) in FETCHERS.items()}


def status(targets):
    """The ping/TCP checks, without sending a packet: every host is up except the ones in DOWN."""
    return {ident: ident not in DOWN for ident in targets}


def inspect(name):
    """How a container is made, as the agent's `inspect` says it (no values of the environment)."""
    img, x = DEMO_CONTAINERS[name]
    port = {"jellyfin": "8096", "sonarr": "8989", "radarr": "7878", "qbittorrent": "8080", "flaresolverr": "8191",
            "portainer": "9443"}.get(name, "80")
    return json.dumps({"image": img, "created": "2026-10-14T08:12:00Z", "started": "2026-10-14T08:12:03Z",
                       "restart": "unless-stopped", "restarts": 2 if name == "qbittorrent" else 0,
                       "ports": [f"{port}→{port}/tcp"], "networks": [x.get("project", "bridge") + "_default"],
                       "mounts": [f"/opt/{name}/config → /config (rw)", "/srv/media → /media (ro)"],
                       "env": ["PGID", "PUID", "TZ"] + (["WEBUI_PASSWORD"] if name == "qbittorrent" else []),
                       "labels": {"com.docker.compose.project": x.get("project", "")} if x.get("project") else {},
                       "health": x.get("health", "")})


# ⚙ > Devices > Agents > the host's Docker: what `inventory` reads and what each cleaning says
CLEANED = {"images": "freed 2.4 GB (31 image layers)", "dangling": "freed 310 MB (6 untagged layers)",
           "networks": "removed 2 unused networks", "volumes": "freed 120 MB (3 anonymous volumes)"}


def inventory():
    gb, mb = 1 << 30, 1 << 20
    return {
        "images": [
            {"id": "5f2a9c1d0b3e", "tags": ["jellyfin/jellyfin:10.10.3"], "size": int(1.2 * gb), "created": 1760000000, "used": 1, "dangling": False},
            {"id": "a81c33e0f7d2", "tags": ["jellyfin/jellyfin:10.10.2"], "size": int(1.2 * gb), "created": 1757000000, "used": 0, "dangling": False},
            {"id": "0c9e7714ab55", "tags": ["lscr.io/linuxserver/sonarr:latest"], "size": 330 * mb, "created": 1759500000, "used": 1, "dangling": False},
            {"id": "77d0be2a9c41", "tags": ["lscr.io/linuxserver/radarr:latest"], "size": 310 * mb, "created": 1759400000, "used": 1, "dangling": False},
            {"id": "e3b1f0a2c874", "tags": [], "size": 280 * mb, "created": 1750000000, "used": 0, "dangling": True},
            {"id": "19ad5c60e8b3", "tags": ["portainer/portainer-ce:2.21.4"], "size": 290 * mb, "created": 1758000000, "used": 1, "dangling": False}],
        "volumes": [
            {"name": "jellyfin-config", "driver": "local", "size": int(2.1 * gb), "used": 1, "anonymous": False},
            {"name": "arr-config", "driver": "local", "size": 410 * mb, "used": 2, "anonymous": False},
            {"name": "d4" * 32, "driver": "local", "size": 120 * mb, "used": 0, "anonymous": True},
            {"name": "old-test-data", "driver": "local", "size": 8 * mb, "used": 0, "anonymous": False}],
        "networks": [
            {"name": "bridge", "driver": "bridge", "scope": "local", "used": 0, "builtin": True},
            {"name": "arr_default", "driver": "bridge", "scope": "local", "used": 3, "builtin": False},
            {"name": "media_default", "driver": "bridge", "scope": "local", "used": 1, "builtin": False},
            {"name": "test_default", "driver": "bridge", "scope": "local", "used": 0, "builtin": False}],
        "more": {},
        "totals": {"images": int(3.6 * gb) + 0, "unused_images": int(1.5 * gb), "volumes": int(2.6 * gb)},
    }
