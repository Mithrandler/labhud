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
