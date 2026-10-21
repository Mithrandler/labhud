"""What a card is (a Proxmox node, a NAS, a Docker host, a guest, a PC), guessed from its keys, for
⚙ > Pages > Organize by type. A card's own `kind` wins; the guess is only a proposal the admin
accepts or changes.

    kind(card, agents)   one of KINDS, or "" for a card that is not a machine (a list, a chart)
    agents               {agent name: [what it found: "docker", "pve", ...]} (agents.listing())
"""

import re

# in the order the groups are proposed
KINDS = ("proxmox", "nas", "docker", "vm", "pc")
TITLES = {"proxmox": "Proxmox", "nas": "NAS & backup", "docker": "Docker hosts", "vm": "VMs & LXC",
          "pc": "PCs & other"}
NAS = re.compile(r"synology|\bomv\b|openmediavault|truenas|unraid|qnap|\bpbs\b|backup|\bnas\b", re.I)
DOCKER = re.compile(r"docker|portainer", re.I)


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
    if panel.startswith("host:") and not guest or "pve" in found and not guest:
        return "proxmox"
    if NAS.search(_text(card)):
        return "nas"
    if "docker" in found or DOCKER.search(_text(card)):
        return "docker"
    if guest:
        return "vm"
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
