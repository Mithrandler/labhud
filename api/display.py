"""⚙ > Alerts > Thresholds and ⚙ > Dashboard's icon picker.

    POST /api/display/marks {warn, crit}   [display] warn/crit in the kept config: where CPU, RAM and
                                           disk turn orange and red on every screen and in the app
    GET  /api/display/icons                the service icons labhud ships (static/icons/svc/)

The marks are written like every ⚙ save (managed.save_config: checked by config.loads first, applied
without a restart); a metric's own warn/crit still wins over them.
"""

import os

import managed
from routes import route

ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "icons", "svc")


def _mark(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


@route("POST", "/api/display/marks")
def save_marks(h, body):
    warn, crit = _mark(body.get("warn")), _mark(body.get("crit"))
    if warn is None or crit is None or not 0 < warn < crit <= 100:
        return 400, {"ok": False, "error": "warn and crit are percentages: 0 < warn < crit <= 100"}
    try:
        raw = managed.read_config()
        raw["display"] = {**(raw.get("display") or {}), "warn": warn, "crit": crit}
        managed.save_config(raw)
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}
    print(f"setup: thresholds {warn}/{crit} saved from {h._who()}", flush=True)
    return 200, {"ok": True, "warn": warn, "crit": crit}


@route("GET", "/api/display/icons", guard="public")
def icons(h, body):
    try:
        names = sorted({f.rsplit(".", 1)[0] for f in os.listdir(ICON_DIR) if f.endswith(".svg")})
    except OSError:
        names = []
    return 200, {"icons": names}
