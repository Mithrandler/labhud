"""Boards as files: ⚙ > Dashboard's export and import of the pages, groups and cards' places.

    POST /api/boards/export {}                  -> {ok, doc}  (layout.export: ids, titles, icons, kinds, sizes)
    POST /api/boards/import {doc, apply?}       -> {ok, report: {unknown, unplaced}, applied}
                                                   without apply: only the report (nothing is written)

Import lays the document over the cards this labhud has (layout.adopt): unknown cards are dropped,
cards the document leaves out go to a group "unplaced". It is written like every ⚙ save
(layout.apply, then managed.save_config, no restart) and heard by the audit log.

    POST /api/boards/theme {base, accent}       [display] base / accent: the theme of every screen that
                                                has not picked its own (⚙ > Look); apps read it in /api/v1/state
    GET  /api/boards/roles                      {roles: {role: [page ids]}}, for admins only
    POST /api/boards/roles {role, pages}        [roles.<role>] pages: what that role sees in /api/v1/state;
                                                pages [] removes it (the role sees every page). Alerts are
                                                not touched: a card a role does not see still raises its alert.

    POST /api/boards/widget {op, ...}           X · small cards, from ⚙ > Dashboard > Widgets:
        op "list"                               {widgets: [{id, name, page, group, kind, timezone, url, height}]}
        op "save" {page, group, kind, name, id?, timezone?, url?, height?}
                                                kind clock (timezone) / calendar / frame (url, height 120-800);
                                                with id: changes that widget card (moving it to page/group)
        op "remove" {id}                        removes a widget card; any other card is refused

Both are checked by config.loads before they are written (managed.save_config) and heard by the audit log.
"""

import re

import config
import layout
import managed
from routes import route


def _audit(h, action, target, ok=True):
    try:
        from api import audit
        audit.record(h, action, target, ok)
    except Exception as e:
        print(f"audit: not written: {e!r}", flush=True)


@route("POST", "/api/boards/export")
def export_board(h, body):
    try:
        return 200, {"ok": True, "doc": layout.export(managed.read_config())}
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}


@route("POST", "/api/boards/import")
def import_board(h, body):
    try:
        raw = managed.read_config()
        pages, report = layout.adopt(raw, body.get("doc"))
        if not body.get("apply"):
            return 200, {"ok": True, "report": report, "applied": False}
        managed.save_config(layout.apply(raw, pages))
    except ValueError as e:
        _audit(h, "board-import", "", False)
        return 400, {"ok": False, "error": str(e)}
    _audit(h, "board-import", f"{len(report['unknown'])} unknown, {len(report['unplaced'])} unplaced")
    print(f"setup: board imported from {h._who()}", flush=True)
    return 200, {"ok": True, "report": report, "applied": True}


@route("POST", "/api/boards/theme")
def save_theme(h, body):
    base, accent = body.get("base"), body.get("accent")
    if base not in config.THEME_BASES:
        return 400, {"ok": False, "error": f"base is one of {', '.join(config.THEME_BASES)}"}
    if not isinstance(accent, str) or not (accent in config.THEME_ACCENTS or config.ACCENT_HEX_RE.match(accent)):
        return 400, {"ok": False, "error": f"accent is one of {', '.join(config.THEME_ACCENTS)} or a #rrggbb colour"}
    accent = accent.lower()
    try:
        raw = managed.read_config()
        raw["display"] = {**(raw.get("display") or {}), "base": base, "accent": accent}
        managed.save_config(raw)
    except ValueError as e:
        _audit(h, "board-theme", f"{base} {accent}", False)
        return 409, {"ok": False, "error": str(e)}
    _audit(h, "board-theme", f"{base} {accent}")
    print(f"setup: board theme {base}/{accent} saved from {h._who()}", flush=True)
    return 200, {"ok": True, "base": base, "accent": accent}


@route("GET", "/api/boards/roles", guard="admin")
def read_roles(h, body):
    try:
        return 200, {"ok": True, "roles": {k: v.get("pages", []) for k, v in (managed.read_config().get("roles") or {}).items()}}
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}


@route("POST", "/api/boards/roles")
def save_roles(h, body):
    role, pages = body.get("role"), body.get("pages")
    if isinstance(pages, list):
        pages = list(dict.fromkeys(p for p in pages))
    if role not in config.ROLE_NAMES:
        return 400, {"ok": False, "error": f"role is one of {', '.join(config.ROLE_NAMES)}"}
    if not isinstance(pages, list) or not all(isinstance(p, str) for p in pages):
        return 400, {"ok": False, "error": "pages is a list of page ids ([] lets the role see every page)"}
    try:
        raw = managed.read_config()
        roles = dict(raw.get("roles") or {})
        if pages:
            roles[role] = {**(roles.get(role) or {}), "pages": pages}
        else:
            roles.pop(role, None)
        if roles:
            raw["roles"] = roles
        else:
            raw.pop("roles", None)
        managed.save_config(raw)
    except ValueError as e:
        _audit(h, "board-roles", role, False)
        return 400, {"ok": False, "error": str(e)}
    _audit(h, "board-roles", f"{role}: {', '.join(pages) or 'every page'}")
    print(f"setup: pages for {role} saved from {h._who()}", flush=True)
    return 200, {"ok": True, "role": role, "pages": pages}


WIDGET_KINDS = (*config.WIDGETS, "frame")
WIDGET_KEYS = ("widget", "timezone", "frame", "frame_height")


def _widgets(raw):
    out = []
    for p in raw.get("page") or []:
        for g in p.get("group") or []:
            for c in g.get("card") or []:
                if c.get("widget") or c.get("frame"):
                    out.append({"id": c["id"], "name": c.get("name") or c["id"], "page": p["id"], "group": g["id"],
                                "kind": c.get("widget") or "frame", "timezone": c.get("timezone") or "",
                                "url": c.get("frame") or "", "height": c.get("frame_height") or 0})
    return out


def _widget_card(body, old):
    kind = body.get("kind")
    if kind not in WIDGET_KINDS:
        raise ValueError(f"kind is one of {', '.join(WIDGET_KINDS)}")
    name = body.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40 or "\n" in name:
        raise ValueError("name: 1 to 40 characters")
    card = {k: v for k, v in (old or {}).items() if k not in WIDGET_KEYS}
    card["name"] = name.strip()
    if kind == "frame":
        card["frame"] = body.get("url")
        if body.get("height") not in (None, "", 0):
            card["frame_height"] = body.get("height")
    else:
        card["widget"] = kind
        if kind == "clock" and body.get("timezone"):
            card["timezone"] = body.get("timezone")
    return card


def _new_id(raw, name):
    taken = {c["id"] for p in raw.get("page") or [] for g in p.get("group") or [] for c in g.get("card") or []}
    base = "w-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "widget")
    cid, n = base, 2
    while cid in taken:
        cid, n = f"{base}-{n}", n + 1
    return cid


@route("POST", "/api/boards/widget")
def widget(h, body):
    op = body.get("op")
    try:
        raw = managed.read_config()
        if op == "list":
            return 200, {"ok": True, "widgets": _widgets(raw)}
        if op == "save":
            page = next((p for p in raw.get("page") or [] if p.get("id") == body.get("page")), None)
            group = next((g for g in (page or {}).get("group") or [] if g.get("id") == body.get("group")), None)
            if group is None or page.get("games"):
                raise ValueError("choose a page and a group of it")
            cid = body.get("id")
            if cid is None:
                card = _widget_card(body, None)
                card["id"] = _new_id(raw, card["name"])
            else:
                where = next(((g, c) for p in raw.get("page") or [] for g in p.get("group") or []
                              for c in g.get("card") or [] if c.get("id") == cid and (c.get("widget") or c.get("frame"))), None)
                if where is None:
                    raise ValueError("no such widget")
                card = _widget_card(body, where[1])
                where[0]["card"] = [c for c in where[0]["card"] if c["id"] != cid]
            group.setdefault("card", []).append(card)
            managed.save_config(raw)
            _audit(h, "board-widget", f"{card['id']} {'frame' if 'frame' in card else card['widget']}")
            print(f"setup: widget {card['id']} saved from {h._who()}", flush=True)
            return 200, {"ok": True, "id": card["id"]}
        if op == "remove":
            cid = body.get("id")
            for p in raw.get("page") or []:
                for g in p.get("group") or []:
                    for c in g.get("card") or []:
                        if c.get("id") == cid and (c.get("widget") or c.get("frame")):
                            g["card"] = [x for x in g["card"] if x is not c]
                            managed.save_config(raw)
                            _audit(h, "board-widget", f"{cid} removed")
                            return 200, {"ok": True}
            raise ValueError("no such widget (only clock, calendar and frame cards are removed here)")
        raise ValueError("op is list, save or remove")
    except ValueError as e:
        if op in ("save", "remove"):
            _audit(h, "board-widget", str(body.get("id") or ""), False)
        return 400, {"ok": False, "error": str(e)}
