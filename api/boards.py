"""Boards as files: ⚙ > Dashboard's export and import of the pages, groups and cards' places.

    POST /api/boards/export {}                  -> {ok, doc}  (layout.export: ids, titles, icons, kinds, sizes)
    POST /api/boards/import {doc, apply?}       -> {ok, report: {unknown, unplaced}, applied}
                                                   without apply: only the report (nothing is written)

Import lays the document over the cards this labhud has (layout.adopt): unknown cards are dropped,
cards the document leaves out go to a group "unplaced". It is written like every ⚙ save
(layout.apply, then managed.save_config, no restart) and heard by the audit log.
"""

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
