"""What a card is (a Proxmox node, a NAS, a Docker host, a guest, a PC), guessed from its keys, for
⚙ > Pages > Organize by type. A card's own `kind` wins; the guess is only a proposal the admin
accepts or changes.

    kind(card, agents)   one of KINDS, or "" for a card that is not a machine (a list, a chart)
    agents               {agent name: [what it found: "docker", "pve", ...]} (agents.listing())
"""

import json
import os
import re

from config import ICON_RE

# in the order the groups are proposed
KINDS = ("proxmox", "nas", "docker", "vm", "pc")
TITLES = {"proxmox": "Proxmox", "nas": "NAS & backup", "docker": "Docker hosts", "vm": "VMs & LXC",
          "pc": "PCs & other"}
NAS = re.compile(r"synology|\bomv\b|openmediavault|truenas|unraid|qnap|\bpbs\b|backup|\bnas\b", re.I)
DOCKER = re.compile(r"docker|portainer", re.I)
# Sources that describe a machine itself. A card that only pings and reads its numbers from any other source
# (sonarr, radarr, bazarr, qbt, ...) is a service on some machine, not a machine.
MACHINE_SOURCES = {"proxmox", "pbs", "synology", "omv", "opnsense", "agents", "truenas", "unraid", "qnap"}


def _sources(card):
    """The first part of every data path a card reads: its metrics, list and torrents."""
    paths = []
    for m in card.get("metrics") or []:
        paths.append(m.get("key") if isinstance(m, dict) else m[0] if isinstance(m, (list, tuple)) and m else m)
    paths += [card[k] for k in ("list", "torrents") if isinstance(card.get(k), str)]
    return {str(p).split(".", 1)[0] for p in paths if p}


def address(card):
    """The host a card pings or opens a TCP port on ("192.0.2.7:47989" -> "192.0.2.7"), or ""."""
    check = card.get("check") or []
    target = card.get("ping") or card.get("tcp") or (check[1] if len(check) > 1 and check[0] in ("ping", "tcp") else "")
    return str(target).rsplit(":", 1)[0] if target else ""


def _text(card):
    return " ".join(str(card.get(k) or "") for k in ("id", "name", "subtitle")).replace("-", " ").replace("_", " ")


def kind(card, agents=None):
    agents = agents or {}
    own = card.get("kind")
    if own in KINDS:
        return own
    panel = str(card.get("panel") or "")
    guest = bool(card.get("proxmox"))
    # the agent that IS this machine (not one that only wakes it: agent_target)
    found = set(agents.get(card.get("agent"), ())) if card.get("agent") and not card.get("agent_target") else set()
    if not (guest or card.get("ping") or card.get("tcp") or card.get("agent") or panel.startswith("host:")):
        return ""   # not a machine: the events, a list, a number; it stays where it is
    if card.get("list") and not (card.get("agent") or panel.startswith("host:") or guest):
        return ""   # a list that only pings (the backup report, a hygiene list): a report, not a machine
    if panel.startswith("host:") and not guest or "pve" in found and not guest:
        return "proxmox"
    if not (guest or found or panel.startswith("host:")) and _sources(card) - MACHINE_SOURCES:
        return ""   # a service (Sonarr, Radarr...) that answers a ping because its machine does: it is not a machine
    if guest:
        return "vm"   # a guest is never a NAS or a Docker host: its Docker is seen in Servers
    if NAS.search(_text(card)):
        return "nas"
    if "docker" in found or DOCKER.search(_text(card)):
        return "docker"
    return "pc"


def node(card):
    """The Proxmox node a guest runs on ("pve2/306" -> "pve2"), or None."""
    p = card.get("proxmox")
    return str(p).split("/", 1)[0] if p else None


# A card's icon when it names none: its source's (the first metric's or list's path), else its kind's.
# Service icons are files in static/icons/svc/ (design/icons.txt); "server", "nas", "vm", "pc" and
# "app" are labhud's own line icons (design/tokens.json), drawn where no logo fits.
SOURCE_ICONS = {
    "proxmox": "proxmox", "pbs": "proxmox", "synology": "synology", "omv": "openmediavault",
    "opnsense": "opnsense", "qbt": "qbittorrent", "sonarr": "sonarr", "radarr": "radarr",
    "prowlarr": "prowlarr", "jellyfin": "jellyfin", "navidrome": "navidrome", "seerr": "jellyseerr",
    "healthchecks": "healthchecks", "crowdsec": "crowdsec", "gitea": "gitea", "nextcloud": "nextcloud",
    "authentik": "authentik", "uptimekuma": "uptime-kuma", "docker": "docker", "nut": "network-ups-tools",
    "pihole": "pi-hole", "adguard": "adguard-home", "beszel": "beszel", "traefik": "traefik",
    "speedtest": "ookla-speedtest", "immich": "immich", "scrutiny": "scrutiny", "technitium": "app",
    "certs": "security", "updates": "update", "games": "steam", "agenda": "activity", "releases": "update",
}
KIND_ICONS = {"proxmox": "proxmox", "nas": "nas", "docker": "docker", "vm": "vm", "pc": "pc"}


def icon(card, agents=None):
    """The icon a card shows: its own `icon`, else its source's, else its kind's, else ""."""
    if card.get("icon"):
        return card["icon"]
    paths = [m[0] for m in card.get("metrics") or [] if isinstance(m, (list, tuple)) and m]
    paths += [card[k] for k in ("list", "torrents") if isinstance(card.get(k), str)]
    for p in paths:
        if p.split(".", 1)[0] in SOURCE_ICONS:
            return SOURCE_ICONS[p.split(".", 1)[0]]
    check = card.get("check") or []
    raw = dict(card)
    if check and check[0] in ("ping", "tcp"):
        raw.setdefault(check[0], check[1])
    elif check and check[0] == "proxmox" and len(check) >= 3:
        raw.setdefault("proxmox", f"{check[1]}/{check[2]}")
    if check and check[0] == "agent" and len(check) > 2 and check[2] == "docker":
        return "docker"
    return KIND_ICONS.get(kind(raw, agents), "")


# --- the icon of a machine or a container from what is known about it ---

_ALIASES = None
# Proxmox's `ostype` of an LXC (the distribution) and of a VM (l26 is any Linux: the name decides)
OS_ICONS = {"debian": "debian-linux", "ubuntu": "ubuntu-linux", "alpine": "alpine-linux", "archlinux": "arch-linux",
            "fedora": "fedora", "centos": "centos", "nixos": "nixos", "opensuse": "opensuse",
            "win11": "windows-11", "win10": "windows-10", "win8": "windows-10", "win7": "windows-10",
            "w2k19": "windows-10", "w2k22": "windows-11", "w2k25": "windows-11", "wvista": "windows-10", "wxp": "windows-10",
            "w2k": "windows-10", "w2k8": "windows-10"}
# the last part of an image name that says nothing about the program ("goauthentik/server")
_GENERIC = {"server", "app", "web", "api", "core", "main", "latest", "community", "release", "backend", "frontend", "ui", "service"}


def aliases():
    """{alternative name: icon slug}, from static/icons/aliases.json (the icons it names are shipped)."""
    global _ALIASES
    if _ALIASES is None:
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "icons", "aliases.json"), encoding="utf-8") as f:
                _ALIASES = {str(k): str(v) for k, v in json.load(f).items()}
        except (OSError, ValueError):
            _ALIASES = {}
    return _ALIASES


_SHIPPED = None


def shipped():
    """The icons labhud carries (static/icons/svc/*.svg), as slugs."""
    global _SHIPPED
    if _SHIPPED is None:
        try:
            names = os.listdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "icons", "svc"))
        except OSError:
            names = []
        _SHIPPED = {n[:-4] for n in names if n.endswith(".svg")}
    return _SHIPPED


def _slug(word):
    """An icon name from a word: through the alias table, else the word itself if it could be one."""
    word = re.sub(r"[^a-z0-9-]+", "-", str(word or "").lower()).strip("-")
    word = aliases().get(word, word)
    return word if ICON_RE.match(word) else ""


def image_name(image):
    """The program an image is: "lscr.io/linuxserver/sonarr:latest" -> "sonarr", "docker.io/library/postgres:16"
    -> "postgres", "ghcr.io/goauthentik/server" -> "goauthentik"; "" when it says nothing."""
    ref = str(image or "").lower().split("@")[0]
    parts = ref.split("/")
    if len(parts) > 1 and ("." in parts[0] or ":" in parts[0] or parts[0] == "localhost"):
        parts = parts[1:]                    # the registry
    parts = [p.split(":")[0] for p in parts if p]
    parts = [p for p in parts if p not in ("library", "linuxserver", "lscr")] or parts
    while len(parts) > 1 and parts[-1] in _GENERIC:
        parts.pop()
    return parts[-1] if parts else ""


def icon_for(name="", *, os=None, image=None, game=None, tags=()):
    """The icon of a machine, a container or a game, from the most to the least certain: a Proxmox
    tag `icon-<slug>`, the guest's OS, the container's image, the game, then the words of its name
    compared with the alias table. "" when nothing is known (the caller keeps its own fallback)."""
    for t in tags or ():
        if str(t).lower().startswith("icon-") and ICON_RE.match(str(t).lower()[5:]):
            return str(t).lower()[5:]
    if os and str(os).lower() in OS_ICONS:
        return OS_ICONS[str(os).lower()]
    if image:
        slug = _slug(image_name(image))
        if slug:
            return slug
    if game:
        slug = _slug(game)
        if slug in aliases().values():
            return slug
    words = re.split(r"[^a-z0-9]+", str(name or "").lower())
    for cand in ["-".join(words[i:j]) for j in range(len(words), 0, -1) for i in range(0, len(words) - j + 1)]:
        for c in (cand, cand.rstrip("0123456789-")):    # "pve2" and "pihole-2" are the second of their kind
            if c in aliases():
                return aliases()[c]
            if c in shipped():
                return c
    return ""


# Games with no logo in dashboard-icons have their Steam store art (design/game_icons.py): fragments of the
# slugified game + name, the first that matches wins. Other Steam games get steam's logo, anything else the gamepad.
GAME_ICONS = (("counter", "counter-strike"), ("cs16", "counter-strike"), ("cs2", "counter-strike"), ("csgo", "counter-strike"),
              ("zombie", "counter-strike"), ("deathmatch", "counter-strike"), ("valheim", "valheim"), ("palworld", "palworld"),
              ("terraria", "terraria"), ("7-days", "7-days-to-die"), ("7days", "7-days-to-die"), ("enshrouded", "enshrouded"),
              ("subnautica", "subnautica"), ("ark", "ark"), ("factorio", "factorio"), ("satisfactory", "satisfactory"),
              ("garry", "garrys-mod"), ("gmod", "garrys-mod"), ("dayz", "dayz"), ("conan", "conan-exiles"),
              ("left-4", "left-4-dead"), ("half-life", "half-life"), ("team-fortress", "team-fortress-2"),
              ("zomboid", "project-zomboid"), ("unturned", "unturned"), ("starbound", "starbound"),
              ("v-rising", "v-rising"), ("vrising", "v-rising"), ("arma", "arma-3"))
GAME_STEAM = ("nitrox", "gta", "fivem")


def game_icon(game="", name=""):
    """The icon of a game server: its own logo when the set has one, else its Steam store art (GAME_ICONS), else
    steam's logo, else "games" (the gamepad). Never empty. static/app.js game_icon() makes the same choice."""
    own = icon_for(game or "", game=game) or icon_for(name or "")
    if own:
        return own
    text = re.sub(r"[^a-z0-9]+", "-", f"{game} {name}".lower()).strip("-")
    for fragment, icon in GAME_ICONS:
        if fragment in text:
            return icon
    return "steam" if any(f in text for f in GAME_STEAM) else "games"
