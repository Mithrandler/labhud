"""Routes that labhud's own modules add, so a new part (OIDC, tokens, audit, backup…) is a file
in api/ and server.py's dispatch is left alone:

    # api/audit.py
    from routes import route

    @route("POST", "/api/audit/list")             # guard "manage": ⚙'s rule (_may_manage)
    def audit_list(h, body):
        return 200, {"ok": True, "rows": []}

    @route("GET", "/api/brand/about", guard="public")
    def about(h, body):
        return 200, {"version": "…"}

A handler gets the request handler `h` (h._who(), h.client_address, h.headers) and the JSON body
(POST; {} for GET), and returns (status, a dict to send as JSON). Guards:

- "manage" (POST only): the request comes from labhud's own page or the CLI, as the admin, from
  an unlocked screen: the same check as every ⚙ call (server._may_manage), JSON of at most
  `limit` bytes. One answer for every refusal: 403 forbidden.
- "public" (GET only): anyone who may see the display (the LABHUD_HOSTS check already ran).
- "open" (POST only): JSON of at most `limit` bytes and nothing else: for the ways in, before
  anyone is the admin (OIDC's start and finish). The handler makes every check itself.
- "admin" (GET only): the admin by any way in: a browser's ticket, a phone signed in as the admin,
  an admin API token (api/tokens.py) or the CLI (server._admin_any). No PIN: it only reads.

A GET handler may answer redirect(url) instead of a dict: a 303 to that address (OIDC's callback
sends the browser back to the page, or to the app).

Every module in api/ is imported once by load(), at start.
"""

import importlib
import os
import pkgutil
import re

ROUTES = {}   # (method, path) -> (handler, guard, limit)
GUARDS = {"POST": ("manage", "open"), "GET": ("public", "admin")}
PATH_RE = re.compile(r"^/api/[a-z0-9/_-]{1,120}$")


def route(method, path, guard=None, limit=64 * 1024):
    guard = guard or GUARDS.get(method, ("",))[0]
    if guard not in GUARDS.get(method, ()):
        raise ValueError(f"route {method} {path}: guard {guard!r} is not one of {GUARDS.get(method)}")
    if not PATH_RE.match(path):
        raise ValueError(f"route {path}: a path like /api/<part>/<name>")
    if (method, path) in ROUTES:
        raise ValueError(f"route {method} {path} is added twice")

    def add(fn):
        ROUTES[(method, path)] = (fn, guard, limit)
        return fn
    return add


def redirect(url):
    """What a GET handler returns to send the browser on: (303, {"location": url})."""
    return 303, {"location": url}


def find(method, path):
    return ROUTES.get((method, path))


def load(package="api"):
    """Imports every module of api/ (their @route lines run). Returns the names loaded."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), package)
    names = []
    for info in sorted(pkgutil.iter_modules([here]), key=lambda i: i.name):
        if not info.name.startswith("_"):
            importlib.import_module(f"{package}.{info.name}")
            names.append(info.name)
    return names
