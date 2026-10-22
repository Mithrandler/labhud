"""R · Discovered, to approve: what labhud sees but has no card for, and turning one into a card.

Three kinds, all read from what the server already knows (nothing is probed here):

    pve:<node>/<vmid>        a Proxmox guest (not a template) no card points at
    docker:<agent>/<name>    a container an agent reports that no card controls
    svc:<source>@<url>       a service the network scan found (scan.py) that is not configured

candidates() lists them without the ones dismissed (kept in the store under "discovery"); approve()
writes cards into the config like every ⚙ save. Nothing is added by itself: that is the point.
"""

import copy
import re

import onboard

STORE_KEY = "discovery"
KEY_RE = re.compile(r"^(pve:[A-Za-z0-9_.-]{1,64}/[0-9]{1,9}|docker:[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}|svc:[a-z0-9-]{1,40}@https?://[^\s\"'<>]{1,190})$")
DEFAULT_PAGE = ("discovered", "Discovered")


def _ident(s):
    return re.sub(r"[^A-Za-z0-9_-]", "-", s)[:64].strip("-") or "x"


def candidates(topology, data, scan_found=(), dismissed=()):
    """[{key, kind, name, detail}] for what has no card yet, in a steady order."""
    cards = [c for cs in topology["cards"].values() for c in cs]
    have_pve = {(c["check"][1], int(c["check"][2])) for c in cards if (c.get("check") or [None])[0] == "proxmox"}
    have_docker = {(c.get("agent"), c["agent_target"].partition(":")[2]) for c in cards
                   if str(c.get("agent_target") or "").startswith("docker:")}
    have_urls = {c.get("url") for c in cards if c.get("url")}
    have_src = {c["id"] for c in cards}
    out = []
    for node, ans in sorted(((data or {}).get("proxmox") or {}).items()):
        if not isinstance(ans, dict):  # a node that did not answer, or a non-node entry
            continue
        for vmid, g in sorted((ans.get("guests") or {}).items(), key=lambda kv: int(kv[0])):
            if not isinstance(g, dict) or g.get("template") or (node, int(vmid)) in have_pve:
                continue
            out.append({"key": f"pve:{node}/{vmid}", "kind": "pve", "name": g.get("name") or str(vmid),
                        "detail": f"{node} · {vmid}" + (f" · {g['os']}" if g.get("os") else "")})
    for agent, a in sorted(((data or {}).get("agents") or {}).items()):
        if agent.startswith("_") or not isinstance(a, dict):
            continue
        for name, x in sorted((a.get("containers") or {}).items()):
            if (agent, name) in have_docker:
                continue
            image = (x or {}).get("image") if isinstance(x, dict) else None
            out.append({"key": f"docker:{agent}/{name}", "kind": "docker", "name": name,
                        "detail": f"{agent}" + (f" · {image}" if image else "")})
    for f in scan_found or ():
        if f.get("configured") or f.get("url") in have_urls or f"src-{onboard.ident(f.get('source', ''))}" in have_src:
            continue
        out.append({"key": f"svc:{f['source']}@{f['url']}", "kind": "svc", "name": f["source"], "detail": f["url"]})
    gone = set(dismissed)
    return [c for c in out if c["key"] not in gone]


def new_card(key, data):
    """The raw config card for a candidate `key`. Raises ValueError for a key that is not one."""
    if not KEY_RE.match(key or ""):
        raise ValueError("not something that can be discovered")
    kind, _, rest = key.partition(":")
    if kind == "pve":
        node, _, vmid = rest.partition("/")
        g = ((((data or {}).get("proxmox") or {}).get(node) or {}).get("guests") or {}).get(vmid) or {}
        return {"id": _ident(f"pve-{node}-{vmid}"), "name": g.get("name") or vmid, "proxmox": f"{node}/{vmid}"}
    if kind == "docker":
        agent, _, name = rest.partition("/")
        return {"id": _ident(f"docker-{agent}-{name}"), "name": name[:40], "agent": agent, "agent_target": f"docker:{name}"}
    source, _, url = rest.partition("@")
    host = re.sub(r"^https?://", "", url).rsplit("/", 1)[0]
    card = {"id": _ident(f"svc-{source}"), "name": source[:40], "url": url}
    hp = host.rpartition(":")
    if hp[1] and hp[2].isdigit():
        card["tcp"] = f"{hp[0].strip('[]')}:{hp[2]}"
    return card


def approve(raw, keys, data, page_id=None, group_id=None):
    """raw with a card for each of `keys` in group `group_id` of page `page_id` (default: the page
    "discovered", made when missing). Returns (raw, [card ids added]). A card whose id is taken is
    skipped. Raises ValueError for a bad key, or a page or group that does not exist."""
    raw = copy.deepcopy(raw)
    pages = raw.setdefault("page", [])
    page_id = page_id or DEFAULT_PAGE[0]
    page = next((p for p in pages if p.get("id") == page_id), None)
    if page is None:
        if page_id != DEFAULT_PAGE[0]:
            raise ValueError(f"no page {page_id!r}")
        page = {"id": DEFAULT_PAGE[0], "title": DEFAULT_PAGE[1].upper()}
        pages.append(page)
    if page.get("games"):
        raise ValueError("a games page has no cards of its own")
    taken_cards = {c.get("id") for p in pages for g in p.get("group") or [] for c in g.get("card") or []}
    taken_groups = {g.get("id") for p in pages for g in p.get("group") or []}
    groups = page.setdefault("group", [])
    if group_id:
        group = next((g for g in groups if g.get("id") == group_id), None)
        if group is None:
            raise ValueError(f"no group {group_id!r} on page {page_id!r}")
    else:
        group = next((g for g in groups if g.get("id", "").startswith("found")), None)
        if group is None:
            gid = "found" if "found" not in taken_groups else f"found-{page_id}"
            group = {"id": gid, "title": "Found", "card": []}
            groups.append(group)
    added = []
    for key in keys:
        card = new_card(key, data)
        if card["id"] in taken_cards:
            continue
        group.setdefault("card", []).append(card)
        taken_cards.add(card["id"])
        added.append(card["id"])
    return raw, added
