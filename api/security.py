"""⚙ > Security (package F): the session length, the PIN's idle time, the networks, single sign-on,
and two rows of ⚙ > System's Danger zone.

    POST /api/security/get            what the section shows (no secrets: only whether one is set)
    POST /api/security/save {...}     any of session_hours, pin_minutes, trusted_proxies,
                                      admin_networks, oidc ({issuer, client_id, allow, allow_groups,
                                      redirect_uri, secret} or null to turn it off)
    POST /api/security/oidc-test      discovery and keys of an issuer (the saved one, or {issuer})
    POST /api/security/new-keys       {confirm: "NEW KEYS"}: every agent, phone and the CLI key anew
    POST /api/security/remove-phones  {confirm: "REMOVE"}: every paired phone, at once

All behind ⚙'s guard (the admin, unlocked, from an admin network). The settings are written to the
kept config.toml ([admin], [admin.oidc], [lock] idle: managed.save_config checks them first) and
the client secret to secrets.env, so they need the config taken over (409 before). A change of
admin_networks or trusted_proxies that would leave this very browser outside is refused.
"""

import os
import sys

import admin
import agents
import devices
import managed
import store
from api import oidc
from routes import route

LIST_MAX = 32


def _srv(h):
    return sys.modules[type(h).__module__]


def _state(h):
    topo = _srv(h).TOPOLOGY
    ad = topo.get("admin") or {}
    oc = ad.get("oidc")
    peer = h.client_address[0]
    return {
        "ok": True,
        "kept": managed.active(),
        "session_hours": int((ad.get("session") or admin.IDLE) // 3600),
        "pin_minutes": int((topo.get("lock") or {}).get("idle", 120) // 60),
        "pin_on": _srv(h).LOCK.is_on(),
        "trusted_proxies": list(ad.get("trusted_proxies") or []),
        "admin_networks": list(ad.get("admin_networks") or []),
        "your_ip": h._ip(),
        "peer": peer,
        "via_proxy": h._ip() != admin.client_ip(peer, None),
        "oidc": None if not oc else {k: oc.get(k) for k in ("issuer", "client_id", "allow", "allow_groups", "redirect_uri")},
        "secret_set": bool(os.environ.get(oidc.SECRET_ENV)),
        "callback": oidc._redirect_uri(h, oc or {}),
    }


@route("POST", "/api/security/get")
def get(h, body):
    return 200, _state(h)


def _names(v, key):
    if not isinstance(v, list) or len(v) > LIST_MAX or not all(isinstance(x, str) and 0 < len(x.strip()) <= 200 for x in v):
        raise ValueError(f"{key}: a list of up to {LIST_MAX} names")
    return [x.strip() for x in v]


@route("POST", "/api/security/save", limit=16 * 1024)
def save(h, body):
    if not managed.active():
        return 409, {"ok": False, "error": "take over the config first (⚙ > System)"}
    try:
        raw = managed.read_config()
        ad = dict(raw.get("admin") or {})
        lk = dict(raw.get("lock") or {})
        secret = None
        if "session_hours" in body:
            n = body["session_hours"]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 720:
                raise ValueError("session_hours: from 1 to 720 (30 days)")
            ad["session_hours"] = n
        if "pin_minutes" in body:
            n = body["pin_minutes"]
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 120:
                raise ValueError("pin_minutes: from 1 to 120")
            lk["idle"] = n
        for key in ("trusted_proxies", "admin_networks"):
            if key in body:
                ad[key] = _names(body[key], key)
                if not ad[key]:
                    del ad[key]
        if "oidc" in body:
            oc = body["oidc"]
            if oc is None:
                ad.pop("oidc", None)
                secret = ""
            elif isinstance(oc, dict):
                new = {"issuer": str(oc.get("issuer") or "").strip(), "client_id": str(oc.get("client_id") or "").strip()}
                for key in ("allow", "allow_groups"):
                    new[key] = _names(oc.get(key) or [], key)
                if str(oc.get("redirect_uri") or "").strip():
                    new["redirect_uri"] = str(oc["redirect_uri"]).strip()
                ad["oidc"] = new
                if isinstance(oc.get("secret"), str) and oc["secret"].strip():
                    secret = oc["secret"].strip()
            else:
                raise ValueError("oidc: a table or null")
        # never lock this browser out: the new networks must still hold the address it comes from
        import ipaddress
        try:
            proxies = tuple(ipaddress.ip_network(x, strict=False) for x in ad.get("trusted_proxies", []))
            nets = tuple(ipaddress.ip_network(x, strict=False) for x in ad.get("admin_networks", []))
        except ValueError as e:
            raise ValueError(f"not a network: {e}") from None
        ip = admin.client_ip(h.client_address[0], h.headers.get("X-Forwarded-For"), proxies)
        if not h._cli() and not admin.network_ok(ip, nets):
            raise ValueError(f"this would lock you out: you come from {ip}, which is not in admin_networks")
        if ad:
            raw["admin"] = ad
        else:
            raw.pop("admin", None)
        if lk:
            raw["lock"] = lk
        managed.save_config(raw)
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}
    if secret is not None:
        try:
            managed.set_secrets({oidc.SECRET_ENV: secret})
        except ValueError as e:
            return 400, {"ok": False, "error": f"saved, but not the client secret: {e}"}
        if secret:
            os.environ[oidc.SECRET_ENV] = secret
        else:
            os.environ.pop(oidc.SECRET_ENV, None)
    print(f"security: settings saved from {h._who()} ({', '.join(sorted(body))})", flush=True)
    return 200, {"ok": True}


@route("POST", "/api/security/oidc-test")
def oidc_test(h, body):
    cfg = admin.SETTINGS.get("oidc") or {}
    issuer = body.get("issuer") if isinstance(body.get("issuer"), str) and body["issuer"].strip() else cfg.get("issuer")
    if not issuer:
        return 400, {"ok": False, "error": "no issuer"}
    import config
    if not config._secure_url(issuer.strip()):
        return 400, {"ok": False, "error": "the issuer must be an https:// URL"}
    p = oidc.Provider(issuer.strip())
    try:
        meta = p.meta()
        try:
            p.key(None)
        except oidc.OidcError as e:
            if "several" not in str(e):   # several keys is fine: a token names its own
                raise
    except oidc.OidcError as e:
        return 200, {"ok": False, "error": str(e)}
    return 200, {"ok": True, "authorization_endpoint": meta["authorization_endpoint"],
                 "keys": len(p._keys or [])}


def _confirm(body, word):
    return isinstance(body.get("confirm"), str) and body["confirm"].strip().upper() == word


def remove_phones():
    """Every paired phone and every unused pairing code: their tokens stop working at once."""
    if not devices.enabled():
        return 0
    with devices._lock:
        n = len(store.load(devices.DEVICES_KEY) or {})
        store.save(devices.DEVICES_KEY, {})
        store.save(devices.CODES_KEY, {})
        devices._seen.clear()
    return n


@route("POST", "/api/security/remove-phones")
def remove_phones_route(h, body):
    if not _confirm(body, "REMOVE"):
        return 400, {"ok": False, "error": "type REMOVE to confirm"}
    n = remove_phones()
    print(f"security: every phone ({n}) removed from {h._who()}", flush=True)
    _srv(h)._log([(f"Every phone removed ({n})", False)])
    return 200, {"ok": True, "phones": n}


@route("POST", "/api/security/new-keys")
def new_keys(h, body):
    """Every agent's key (and its webhooks) and every phone's token end; the agents are added
    again with a new code, the phones paired again. The `labhud` command gets a new key too."""
    if not _confirm(body, "NEW KEYS"):
        return 400, {"ok": False, "error": "type NEW KEYS to confirm"}
    srv = _srv(h)
    gone = [aid for aid in list(agents._load()) if agents.remove(aid)]
    if agents.enabled():
        with agents._lock:
            store.save(agents.CODES_KEY, {})
    phones = remove_phones()
    if hasattr(srv, "write_cli_key"):
        srv.write_cli_key()
    print(f"security: new keys from {h._who()}: {len(gone)} agents and {phones} phones removed", flush=True)
    srv._log([(f"New keys: {len(gone)} agents and {phones} phones to add again", True)])
    return 200, {"ok": True, "agents": len(gone), "phones": phones}
