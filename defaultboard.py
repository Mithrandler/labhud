"""The board a first run gets: what the server already knows, laid out as a page of cards.

    needs(raw)                       true when the config has no card, no games page and no strip entry
    build(raw, data, found=())       (raw with the board, [card ids added]), or (raw, []) when there is nothing to show

Nothing here overwrites: a config that has any card is left exactly as it is, and the board is made
once (server.py remembers that in the store, so a board emptied on purpose stays empty). The cards
are the same ones ⚙ > Dashboard > Discovered would offer (discovery.py), grouped: one group of
Proxmox nodes, one of guests per node, one of containers per agent, the services the scan found,
and a games page when the data has game servers.
"""

import copy

import discovery

STORE_KEY = "defaultboard"
GAMES_PATH = "live.games.servers"
PAGE = ("general", "GENERAL")
NODE_METRICS = (("vms", "VM", "count"), ("lxc", "LXC", "count"), ("cpu", "CPU", "percent"),
                ("mem_used_of", "RAM", "used_of"), ("disk_used_of", "Disk", "used_of"))


def _cards(raw):
    return [c for p in raw.get("page") or [] for g in p.get("group") or [] for c in g.get("card") or []]


def needs(raw):
    if _cards(raw) or raw.get("strip"):
        return False
    return not any(p.get("games") for p in raw.get("page") or [])


def _dig(data, path):
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def _node_card(node):
    return {"id": discovery._ident(f"host-{node}"), "name": node.upper(), "size": "l",
            "metrics": [{"key": f"proxmox.{node}.{k}", "label": label, "format": fmt} for k, label, fmt in NODE_METRICS],
            "panel": f"host:{node}"}


def build(raw, data, found=()):
    """Does not touch `raw` (a copy comes back). `data` is the server's collected data."""
    raw = copy.deepcopy(raw)
    data = data or {}
    groups, added = [], []
    nodes = sorted(k for k, v in (data.get("proxmox") or {}).items() if isinstance(v, dict) and "guests" in v)
    if nodes:
        groups.append({"id": "nodes", "title": "Proxmox", "card": [_node_card(n) for n in nodes]})
    by_group = {}
    for item in discovery.candidates({"cards": {}}, data, found):
        kind, _, rest = item["key"].partition(":")
        if kind == "pve":
            gid, title = f"guests-{discovery._ident(rest.partition('/')[0])}", rest.partition("/")[0]
        elif kind == "docker":
            gid, title = f"containers-{discovery._ident(rest.partition('/')[0])}", rest.partition("/")[0]
        else:
            gid, title = "services", "Services"
        by_group.setdefault(gid, {"id": gid, "title": title.upper() if kind != "svc" else title, "card": []})
        by_group[gid]["card"].append(discovery.new_card(item["key"], data))
    groups += [by_group[k] for k in sorted(by_group)]
    taken = set()
    for g in groups:   # card ids are unique across the whole config
        g["card"] = [c for c in g["card"] if not (c["id"] in taken or taken.add(c["id"]))]
    if groups:
        page = {"id": PAGE[0], "title": PAGE[1], "group": groups}
        old = next((p for p in raw.get("page") or [] if p.get("id") == PAGE[0] and not p.get("games")), None)
        if old is not None:
            old["group"] = (old.get("group") or []) + groups
        else:
            raw.setdefault("page", []).append(page)
        added = sorted(taken)
    servers = _dig(data, GAMES_PATH)
    if isinstance(servers, list) and servers and not any(p.get("games") for p in raw.get("page") or []):
        raw.setdefault("page", []).append({"id": "games", "title": "GAMES", "games": GAMES_PATH})
        added.append("games")
    return raw, added
