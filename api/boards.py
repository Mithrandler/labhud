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

Both are checked by config.loads before they are written (managed.save_config) and heard by the audit log.
"""

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
