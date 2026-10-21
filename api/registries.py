"""⚙ > Containers > Registries and Vulnerability scan.

    POST /api/registries/list                  the registries (host, user, whether a token is set:
                                               never the token), the scan results, the hold option
    POST /api/registries/save {host, user, token?}   adds or changes one ([[registry]] in the kept
                                               config; the token in secrets.env); no token = unchanged
    POST /api/registries/remove {host}         the registry and its token
    POST /api/registries/hold {on}             hold back an update labhud starts on its own when the
                                               new image has more critical CVEs than the running one

A registry's token lives only in secrets.env (LABHUD_REGISTRY_<HOST>_TOKEN, mode 600) or labhud's
environment. It leaves labhud only inside a docker update command, sealed for that command
(agentproto.seal), only to an agent that understands it, and only for an image of that very
registry (agentproto.registry_of: an image named without one is Docker Hub's, docker.io). It is
never logged and never in an answer.
"""

import os
import re

import agentproto
import agents
import config
import envfiles
import managed
from routes import route

MAX_REGISTRIES = 32


def token_name(host):
    """LABHUD_REGISTRY_GHCR_IO_TOKEN for ghcr.io; None when the name would be too long for a key."""
    name = "LABHUD_REGISTRY_" + re.sub(r"[^A-Z0-9]", "_", host.upper()) + "_TOKEN"
    return name if managed.NAME_RE.fullmatch(name) else None


def token(host):
    """The registry's token, or "": secrets.env first (⚙ writes it there), else the environment
    labhud was started with."""
    name = token_name(host)
    if not name:
        return ""
    return managed.read_secrets().get(name) or envfiles.GIVEN.get(name, "") or ""


def configured():
    return [r for r in agents.registries if isinstance(r, dict) and r.get("host")]


def login_for(image):
    """(registry, user, token) for the image's registry, when labhud has one with a token; else None.
    agents.update_extras calls this (through agents.registry_auth) for every docker update."""
    host = agentproto.registry_of(image)
    if not host:
        return None
    for r in configured():
        if r["host"] == host:
            t = token(host)
            return (host, r["user"], t) if t else None
    return None


agents.registry_auth = login_for


def _snapshot():
    every = dict(agents.snapshot())
    if os.environ.get("LABHUD_DEMO", "").lower() in ("1", "true", "yes"):
        import demo
        every = dict(demo.agents(), **every)
    return every


def scans(every=None):
    """Every container with a scan result, worst first: [{agent, name, image, critical, high, tool, t}]."""
    rows = []
    for name, a in (every if every is not None else _snapshot()).items():
        if not isinstance(a, dict):
            continue
        for c, x in (a.get("containers") or {}).items():
            sc = x.get("scan") if isinstance(x, dict) else None
            if isinstance(sc, dict) and isinstance(sc.get("critical"), int) and isinstance(sc.get("high"), int):
                rows.append({"agent": name, "name": c, "image": str(x.get("image", ""))[:200],
                             "critical": sc["critical"], "high": sc["high"],
                             "tool": str(sc.get("tool", ""))[:16], "t": sc.get("t") if isinstance(sc.get("t"), int) else 0})
    return sorted(rows, key=lambda r: (-r["critical"], -r["high"], r["agent"], r["name"]))


def _scanners():
    """The agents that can scan (trivy or grype found): whether scan is ticked on each."""
    return [{"agent": a["name"], "on": "scan" in a.get("caps", []), "online": a["online"]}
            for a in agents.listing() if "scan" in a.get("found", [])]


def _images_by_registry(every):
    used = {}
    for a in every.values():
        for x in ((a or {}).get("containers") or {}).values() if isinstance(a, dict) else ():
            if isinstance(x, dict):
                host = agentproto.registry_of(x.get("image", ""))
                if host:
                    used[host] = used.get(host, 0) + 1
    return used


@route("POST", "/api/registries/list")
def listing(h, body):
    every = _snapshot()
    used = _images_by_registry(every)
    return 200, {"ok": True, "managed": managed.active(),
                 "registries": [{"host": r["host"], "user": r["user"], "token": bool(token(r["host"])),
                                 "key": token_name(r["host"]) or "", "containers": used.get(r["host"], 0)}
                                for r in configured()],
                 "hold_critical": agents.hold_critical(), "scanners": _scanners(), "scans": scans(every)}


def _edit(change):
    """Applies change(list of [[registry]] tables) to the kept config. Raises ValueError."""
    raw = managed.read_config()
    regs = [dict(r) for r in raw.get("registry") or [] if isinstance(r, dict)]
    regs = change(regs)
    if regs:
        raw["registry"] = regs
    else:
        raw.pop("registry", None)
    managed.save_config(raw)


@route("POST", "/api/registries/save")
def save(h, body):
    host = config.registry_host(body.get("host"))
    user = body.get("user")
    tok = body.get("token")
    if not host:
        return 400, {"ok": False, "error": "host is a registry's host[:port], e.g. ghcr.io"}
    if not isinstance(user, str) or not user.strip() or len(user) > 128 or "\n" in user or "\r" in user:
        return 400, {"ok": False, "error": "user is one line of at most 128 characters"}
    if tok is not None and (not isinstance(tok, str) or len(tok) > 4000 or "\n" in tok or "\r" in tok):
        return 400, {"ok": False, "error": "the token is one line of text"}
    name = token_name(host)
    if not name:
        return 400, {"ok": False, "error": "that host is too long for its key in secrets.env"}

    def change(regs):
        kept = [r for r in regs if config.registry_host(r.get("host")) != host]
        if len(kept) >= MAX_REGISTRIES:
            raise ValueError(f"at most {MAX_REGISTRIES} registries")
        return kept + [{"host": host, "user": user.strip()}]
    try:
        _edit(change)
        if tok and tok.strip():
            managed.set_secrets({name: tok.strip()})
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}
    print(f"setup: registry {host} (user {user.strip()}) saved from {h._who()}"
          + ("; its token changed" if tok and tok.strip() else ""), flush=True)
    return 200, {"ok": True, "host": host, "token": bool(token(host))}


@route("POST", "/api/registries/remove")
def remove(h, body):
    host = config.registry_host(body.get("host"))
    if not host:
        return 400, {"ok": False, "error": "no such registry"}
    try:
        _edit(lambda regs: [r for r in regs if config.registry_host(r.get("host")) != host])
        if token_name(host) and token_name(host) in managed.read_secrets():
            managed.set_secrets({token_name(host): ""})
    except ValueError as e:
        return 409, {"ok": False, "error": str(e)}
    print(f"setup: registry {host} removed from {h._who()}", flush=True)
    return 200, {"ok": True}


@route("POST", "/api/registries/hold")
def hold(h, body):
    on = body.get("on")
    if not isinstance(on, bool):
        return 400, {"ok": False, "error": "on is true or false"}
    if not agents.enabled():
        return 409, {"ok": False, "error": "LABHUD_DATA is not set"}
    agents.set_hold_critical(on)
    print(f"setup: hold back updates with new critical CVEs {'on' if on else 'off'} from {h._who()}", flush=True)
    return 200, {"ok": True, "hold_critical": on}
