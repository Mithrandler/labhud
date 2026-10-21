"""Pages, groups and cards as ⚙ > Pages edits them, on the config labhud keeps (managed.py).

    view(raw, agents)            the pages as the editor draws them
    apply(raw, pages)            raw with the editor's pages: order, titles, cards moved, new ones
    organize(raw, page, agents)  the editor's pages with one page's cards grouped by kind (cardtype)

The editor only moves what exists: a card is never lost or made up (apply refuses a layout that
drops or repeats one), and every other key of a page, group or card stays as it was. The result
is checked by config.loads before managed.save_config writes it.
"""

import copy
import re

import cardtype
from config import CARD_SIZES, ICON_RE

PAGE_ID = re.compile(r"[a-z]{1,32}")
GROUP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
MAX_TITLE = 60


def view(raw, agents=None):
    out = []
    for p in raw.get("page") or []:
        groups = []
        for g in p.get("group") or []:
            cards = [{"id": c["id"], "name": c.get("name") or c["id"], "kind": c.get("kind") or "",
                      "guess": cardtype.kind({k: v for k, v in c.items() if k != "kind"}, agents),
                      "node": cardtype.node(c), "icon": c.get("icon") or "",
                      "size": c.get("size") or ("l" if c.get("large") else "")} for c in g.get("card") or []]
            groups.append({"id": g["id"], "title": g.get("title") or g["id"], "auto": bool(g.get("guests")),
                           "cards": cards})
        out.append({"id": p["id"], "title": p.get("title") or p["id"], "icon": p.get("icon") or "",
                    "games": bool(p.get("games")), "groups": groups})
    return {"pages": out, "kinds": list(cardtype.KINDS), "titles": cardtype.TITLES}


def _title(v, what):
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > MAX_TITLE or "\n" in v:
        raise ValueError(f"{what}: a title of 1 to {MAX_TITLE} characters")
    return v.strip()


def _set_icon(t, v, what):
    """icon from the editor: None leaves it, "" removes it, else a dashboard-icons name."""
    if v is None:
        return
    if v == "":
        t.pop("icon", None)
    elif isinstance(v, str) and ICON_RE.match(v):
        t["icon"] = v
    else:
        raise ValueError(f"{what}: icon must be a name like \"proxmox\"")


def apply(raw, pages):
    """A copy of raw laid out as `pages` ([{id, title, icon?, groups: [{id, title,
    cards: [id or {id, kind?, icon?, size?}]}]}]). A missing kind/icon/size leaves the card's own,
    "" removes it. Raises ValueError for a layout that loses, repeats or invents a card, or reuses an id."""
    if not isinstance(pages, list) or not pages:
        raise ValueError("no pages")
    raw = copy.deepcopy(raw)
    old_pages = {p["id"]: p for p in raw.get("page") or []}
    old_groups, old_cards = {}, {}
    for p in raw.get("page") or []:
        for g in p.get("group") or []:
            old_groups[g["id"]] = g
            for c in g.get("card") or []:
                old_cards[c["id"]] = c
    seen_pages, seen_groups, seen_cards = set(), set(), set()
    new_pages = []
    for p in pages:
        if not isinstance(p, dict):
            raise ValueError("a page must be an object")
        pid = p.get("id")
        if not isinstance(pid, str) or not PAGE_ID.fullmatch(pid) or pid in seen_pages:
            raise ValueError(f"page id {pid!r}: a-z only, and once")
        seen_pages.add(pid)
        page = {k: v for k, v in old_pages.get(pid, {}).items() if k != "group"}
        page["id"] = pid
        page["title"] = _title(p.get("title"), f"page {pid}")
        _set_icon(page, p.get("icon"), f"page {pid}")
        groups = []
        for g in p.get("groups") or []:
            gid = g.get("id") if isinstance(g, dict) else None
            if not isinstance(gid, str) or not GROUP_ID.fullmatch(gid) or gid in seen_groups:
                raise ValueError(f"group id {gid!r}: letters, digits, - and _, and once")
            seen_groups.add(gid)
            group = {k: v for k, v in old_groups.get(gid, {}).items() if k != "card"}
            group["id"] = gid
            group["title"] = _title(g.get("title"), f"group {gid}")
            cards = []
            for c in g.get("cards") or []:
                c = c if isinstance(c, dict) else {"id": c} if isinstance(c, str) else {}
                cid, kind = c.get("id"), c.get("kind")
                if cid not in old_cards:
                    raise ValueError(f"no card {cid!r}")
                if cid in seen_cards:
                    raise ValueError(f"card {cid} is in two places")
                seen_cards.add(cid)
                card = dict(old_cards[cid])
                if kind is not None:
                    if kind == "":
                        card.pop("kind", None)
                    elif kind in cardtype.KINDS:
                        card["kind"] = kind
                    else:
                        raise ValueError(f"card {cid}: kind must be one of {', '.join(cardtype.KINDS)}")
                _set_icon(card, c.get("icon"), f"card {cid}")
                size = c.get("size")
                if size is not None:
                    if size not in ("", *CARD_SIZES):
                        raise ValueError(f"card {cid}: size must be one of {', '.join(CARD_SIZES)}")
                    card.pop("large", None)   # the old way of saying size "l"
                    if size:
                        card["size"] = size
                    else:
                        card.pop("size", None)
                cards.append(card)
            if cards:
                group["card"] = cards
            elif not group.get("guests"):
                continue   # an empty group (new, or emptied by a move) is left out
            groups.append(group)
        if groups:
            page["group"] = groups
        elif not page.get("games"):
            continue       # likewise an empty page
        new_pages.append(page)
    lost = set(old_cards) - seen_cards
    if lost:
        raise ValueError("cards left out: " + ", ".join(sorted(lost)))
    lost = {gid for gid, g in old_groups.items() if g.get("guests")} - seen_groups
    if lost:
        raise ValueError("groups that add guests by themselves cannot be dropped: " + ", ".join(sorted(lost)))
    raw["page"] = new_pages
    return raw


def organize(raw, page_id, agents=None):
    """view() with page `page_id` regrouped by kind: Proxmox, NAS & backup, Docker hosts, VMs & LXC,
    PCs & other (only those that have cards). Groups that add guests by themselves stay as they are,
    first; cards that are not machines stay in their groups, after. Raises ValueError for no such page."""
    v = view(raw, agents)
    page = next((p for p in v["pages"] if p["id"] == page_id), None)
    if page is None:
        raise ValueError(f"no page {page_id!r}")
    taken = {g["id"] for p in v["pages"] for g in p["groups"] if p is not page}
    kept = [g for g in page["groups"] if g["auto"]]
    buckets = {k: [] for k in cardtype.KINDS}
    rest = []   # groups of what is not a machine (a list, the events), as they were, after the others
    for g in page["groups"]:
        if not g["auto"]:
            others = [c for c in g["cards"] if not (c["kind"] or c["guess"])]
            for c in g["cards"]:
                if c["kind"] or c["guess"]:
                    buckets[c["kind"] or c["guess"]].append(c)
            if others:
                rest.append(dict(g, cards=others))
    taken |= {g["id"] for g in rest}
    groups = list(kept)
    for k in cardtype.KINDS:
        if buckets[k]:
            gid = "type-" + k
            while gid in taken:
                gid += "-" + page_id
            groups.append({"id": gid, "title": cardtype.TITLES[k], "auto": False, "cards": buckets[k]})
    page["groups"] = groups + rest
    return v


FORMAT = "labhud-board"


def export(raw):
    """The pages as a document that import (adopt) takes back: order, titles, icons, groups, and per
    card its id, kind, icon and size. Nothing else of a card (addresses, keys) leaves labhud."""
    pages = []
    for p in raw.get("page") or []:
        pages.append({"id": p["id"], "title": p.get("title") or p["id"], "icon": p.get("icon") or "",
                      "groups": [{"id": g["id"], "title": g.get("title") or g["id"],
                                  "cards": [{"id": c["id"], "kind": c.get("kind") or "", "icon": c.get("icon") or "",
                                             "size": c.get("size") or ("l" if c.get("large") else "")}
                                            for c in g.get("card") or []]}
                                 for g in p.get("group") or []]})
    return {"format": FORMAT, "version": 1, "pages": pages}


def adopt(raw, doc):
    """(pages for apply, report) from an exported document laid over this labhud's cards. A card the
    document names that is not here is dropped ("unknown"); a card here that the document leaves out
    is kept, in a group "unplaced" on the last page ("unplaced"). Raises ValueError for a document
    that is not one of ours."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("version") != 1:
        raise ValueError("not a labhud board document (format, version 1)")
    pages = doc.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("the document has no pages")
    have = {c["id"] for p in raw.get("page") or [] for g in p.get("group") or [] for c in g.get("card") or []}
    seen, unknown, out = set(), [], []
    for p in pages:
        if not isinstance(p, dict):
            raise ValueError("a page must be an object")
        groups = []
        for g in p.get("groups") or []:
            if not isinstance(g, dict):
                raise ValueError("a group must be an object")
            cards = []
            for c in g.get("cards") or []:
                c = c if isinstance(c, dict) else {"id": c}
                cid = c.get("id")
                if cid not in have:
                    unknown.append(str(cid)[:64])
                elif cid not in seen:
                    seen.add(cid)
                    cards.append(c)
            groups.append({**g, "cards": cards})
        out.append({**p, "groups": groups})
    left = sorted(have - seen)
    if left:
        out[-1]["groups"].append({"id": "unplaced", "title": "Unplaced", "cards": [{"id": i} for i in left]})
    return out, {"unknown": unknown, "unplaced": left}
