"""R · Discovered, to approve (⚙ > Dashboard > Discovered): what labhud sees and has no card for.

    GET  /api/discover/list                       admin: {items: [{key, kind, name, detail}], dismissed: n}
    POST /api/discover/approve {keys, page?, group?}   cards for those, in that group (default: page "discovered")
    POST /api/discover/dismiss {keys}             stop offering them
    POST /api/discover/restore {}                 offer the dismissed ones again

Approve is written like every ⚙ save (managed.save_config: checked, applied without a restart) and
heard by the audit log. See discovery.py.
"""

import sys

import discovery
import managed
import scan
import store
from routes import route


def _srv(h):
    return sys.modules[type(h).__module__]


def _audit(h, action, target, ok=True):
    try:
        from api import audit
        audit.record(h, action, target, ok)
    except Exception as e:
        print(f"audit: not written: {e!r}", flush=True)


def _dismissed():
    v = store.load(discovery.STORE_KEY, [])
    return v if isinstance(v, list) else []


def _keys(body):
    keys = body.get("keys")
    if not isinstance(keys, list) or not keys or len(keys) > 200 or not all(isinstance(k, str) for k in keys):
        raise ValueError("keys is a list of 1 to 200 keys from /api/discover/list")
    return list(dict.fromkeys(keys))


@route("GET", "/api/discover/list", guard="admin")
def list_items(h, body):
    srv = _srv(h)
    with srv._lock:
        data = dict(srv._data)
    dismissed = _dismissed()
    return 200, {"ok": True, "items": discovery.candidates(srv.TOPOLOGY, data, scan.status()["found"], dismissed),
                 "dismissed": len(dismissed)}


@route("POST", "/api/discover/approve")
def approve(h, body):
    srv = _srv(h)
    try:
        keys = _keys(body)
        with srv._lock:
            data = dict(srv._data)
        raw, added = discovery.approve(managed.read_config(), keys, data, body.get("page") or None, body.get("group") or None)
        if added:
            managed.save_config(raw)
    except ValueError as e:
        _audit(h, "discover-approve", "", False)
        return 400, {"ok": False, "error": str(e)}
    _audit(h, "discover-approve", f"{len(added)} card(s): {', '.join(added)[:200]}")
    print(f"setup: {len(added)} discovered card(s) added from {h._who()}", flush=True)
    return 200, {"ok": True, "added": added}


@route("POST", "/api/discover/dismiss")
def dismiss(h, body):
    try:
        keys = _keys(body)
        if not all(discovery.KEY_RE.match(k) for k in keys):
            raise ValueError("not something that can be discovered")
    except ValueError as e:
        _audit(h, "discover-dismiss", "", False)
        return 400, {"ok": False, "error": str(e)}
    store.save(discovery.STORE_KEY, sorted(set(_dismissed()) | set(keys))[-2000:])
    _audit(h, "discover-dismiss", f"{len(keys)} item(s)")
    return 200, {"ok": True}


@route("POST", "/api/discover/restore")
def restore(h, body):
    store.save(discovery.STORE_KEY, [])
    _audit(h, "discover-restore", "")
    return 200, {"ok": True}
