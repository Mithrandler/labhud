"""Demo mode (LABHUD_DEMO=1): made-up data for demo/config.toml, with no network, no keys and no ping.

Every source in the demo topology is replaced by a function here that returns the same shape the
real source would. Numbers drift slowly with time, so the display looks alive on screenshots and in
a browser. Addresses are from the ranges reserved for documentation (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24).
"""

import math
import os
import time

from sources._common import Source

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")

GB = 1024 ** 3
TB = 1024 ** 4
DOWN = {"backup", "desktop"}  # cards whose host is off in the demo (both on_demand)


def _wave(base, amp, period, phase=0.0):
    """A slow, smooth change around `base`: the same value for everyone at the same moment."""
    return base + amp * math.sin(2 * math.pi * (time.time() / period + phase))


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
        "cpu_temp": round(_wave(52, 6, 300), 1), "gpu_count": 1,
        "gpu0_util": round(max(0.0, _wave(20, 25, 600)), 1), "gpu0_mem": round(_wave(30, 5, 900), 1),
        "gpu0_temp": round(_wave(46, 5, 600), 1),
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
    return {"temp": round(_wave(14, 4, 86400), 1), "code": 2, "max": 18.0, "min": 9.0}


FETCHERS = {"proxmox": (proxmox, 10), "temp": (temp, 10), "synology": (synology, 30), "pbs": (pbs, 60),
            "opnsense": (opnsense, 5), "live": (live, 10), "qbt": (qbt, 5), "recent": (recent, 300),
            "sonarr": (sonarr, 300), "radarr": (radarr, 300), "jellyfin": (jellyfin, 30), "weather": (weather, 600)}


def sources():
    """{name: Source}, in place of what sources.load() would find."""
    return {name: Source(name, fetch, every) for name, (fetch, every) in FETCHERS.items()}


def status(targets):
    """The ping/TCP checks, without sending a packet: every host is up except the ones in DOWN."""
    return {ident: ident not in DOWN for ident in targets}
