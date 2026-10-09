"""Data sources, one module per service, each registered with @source.

A source runs only when its settings are present: the variables it names (LABHUD_<SERVICE>_URL
plus a key, a token or a user and password) or, for the weather, a [weather] table in config.toml.
A source without them is not an error: the snapshot holds {"not_configured": true, "needs": [...]}
for it once, and the card says "not configured".

Adding a source: a new module in this package with a function decorated with
@source("name", every=<seconds>, env=("NAME_URL", "NAME_KEY")). Its data is then addressable from
config.toml as "name.<key>". Modules whose name starts with "_" are helpers, not sources.
Give it title=, about= and hints={"NAME_URL": "http://host:port", ...} too: the setup page builds
the source's form from them (onboard.catalog()).
"""

import importlib
import pkgutil

from ._common import PINS, env_name, REGISTRY, RIGHTS, TLS_SEEN, in_window, scrub, urlopen  # noqa: F401  (re-exported for server.py)
from ._status import host_status  # noqa: F401


def check_rights(active):
    """{source: {"extra": [(where, [privilege])]} or {"error": text}} for the active sources whose
    keys can be limited. An empty `extra` means read-only, as it should be."""
    out = {}
    for name, check in RIGHTS.items():
        if name not in active:
            continue
        try:
            out[name] = {"extra": [(where, privs) for where, privs in check() if privs]}
        except Exception as e:  # the host may be off; the check runs again after a reload
            out[name] = {"error": scrub(e)[:160] or type(e).__name__}
    return out


def load(topology):
    """Imports every source module. Returns ({name: Source} that can run, {name: [missing settings]})."""
    for m in pkgutil.iter_modules(__path__):
        if not m.name.startswith("_"):
            importlib.import_module(f"{__name__}.{m.name}")
    active, inactive = {}, {}
    for name, src in REGISTRY.items():
        lacking = src.missing(topology)
        if lacking:
            inactive[name] = lacking
        else:
            active[name] = src
    return active, inactive
