"""Signing in as the admin with an OpenID Connect provider (Authentik).

    POST /api/admin/oidc/start {mode}        -> {url, bind}   "web": this page; "app": a paired phone
    GET  /api/admin/oidc/callback?code&state -> 303 to the page (/?oidc=<r>) or the app (labhud://oidc?r=<r>)
    POST /api/admin/oidc/finish {r, bind}    -> web: {ticket, user, idle}; app: the phone is the admin

The flow is the authorization code with PKCE (S256), a `state` and a `nonce`. The id_token comes
from the token endpoint (labhud asks it with the code, the PKCE verifier and the client secret)
and is still checked as if it came from anywhere: an RS256 signature by a key of the provider's
JWKS (found by discovery: <issuer>/.well-known/openid-configuration, kept for an hour), then iss,
aud (and azp), exp, iat, nbf and the nonce. The user must be in [admin.oidc] allow (a user name or
an email) or one of its groups in allow_groups. Only then is it the admin: the same session as a
password and code would give (admin.open_session), told to admin.LOGIN_HOOKS as how="oidc".

Binding: start answers a random `bind` that only its caller has (the page keeps it in
sessionStorage, the app in memory); the callback ends with a one-time result id `r` in the
address, worth nothing without that bind. For the app, the result is also tied to the phone's own
token: finish has to come from the same paired phone that started. A result lasts two minutes,
a started sign-in ten; both are used once.

No new dependency: discovery, JWKS and the token endpoint are urllib, and the RS256 check is
RSASSA-PKCS1-v1_5 (RFC 8017 §8.2.2) with Python's own pow(): the signature raised to e mod n must
be exactly 00 01 FF..FF 00 DigestInfo(SHA-256) hash. Verifying needs no secret, so a timing leak
here gives nothing away.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import admin
import util
from routes import redirect, route

SECRET_ENV = "LABHUD_OIDC_CLIENT_SECRET"
PENDING_TTL = 600         # seconds from start to callback (the user may have to sign in at Authentik)
RESULT_TTL = 120          # seconds from callback to finish
MAX_PENDING = 32
CACHE_TTL = 3600          # discovery and JWKS
REFETCH_EVERY = 60        # an unknown key id fetches the JWKS again at most this often
LEEWAY = 120              # clocks drift
MAX_BODY = 512 * 1024
TIMEOUT = 10
APP_RETURN = "labhud://oidc"
CALLBACK = "/api/admin/oidc/callback"
SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
RID = re.compile(r"[A-Za-z0-9_-]{20,64}")


class OidcError(Exception):
    """A refusal; str() is a short reason for the log (never shown with secrets in it)."""


def _b64d(s):
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", s):
        raise OidcError("not base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _int(s):
    return int.from_bytes(_b64d(s), "big")


# --- RS256 ---

def rs256_ok(signed, signature, n, e):
    """RSASSA-PKCS1-v1_5 with SHA-256: True when `signature` is the key (n, e)'s over `signed`."""
    k = (n.bit_length() + 7) // 8
    if k < 256 or len(signature) != k:          # a key under 2048 bits is refused
        return False
    s = int.from_bytes(signature, "big")
    if s >= n:
        return False
    em = pow(s, e, n).to_bytes(k, "big")
    t = SHA256_INFO + hashlib.sha256(signed).digest()
    want = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, want)


# --- the provider: discovery and keys, kept a while ---

def _fetch(url, data=None, headers=None):
    """JSON from an http(s) URL, at most MAX_BODY bytes. Raises OidcError."""
    if not re.match(r"https?://", url or ""):
        raise OidcError("not an http(s) URL")
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read(4096) or b"{}").get("error", "")
        except ValueError:
            said = ""
        raise OidcError(f"{urllib.parse.urlsplit(url).path}: HTTP {e.code} {said}".strip()) from None
    except (OSError, ValueError) as e:
        raise OidcError(f"cannot reach {urllib.parse.urlsplit(url).netloc}: {getattr(e, 'reason', e)}") from None
    if len(raw) > MAX_BODY:
        raise OidcError("answer too large")
    try:
        out = json.loads(raw)
    except ValueError:
        raise OidcError("answer is not JSON") from None
    if not isinstance(out, dict):
        raise OidcError("answer is not a JSON object")
    return out


class Provider:
    """One issuer's discovery document and JWKS. clock and fetch are swappable for the tests."""

    def __init__(self, issuer, fetch=_fetch, clock=time.time):
        self.issuer = issuer
        self.fetch = fetch
        self.clock = clock
        self._lock = threading.Lock()
        self._meta, self._meta_at = None, 0.0
        self._keys, self._keys_at, self._tried_at = None, 0.0, -1e9

    def meta(self):
        with self._lock:
            if self._meta and self.clock() - self._meta_at < CACHE_TTL:
                return self._meta
        m = self.fetch(self.issuer.rstrip("/") + "/.well-known/openid-configuration")
        if str(m.get("issuer", "")).rstrip("/") != self.issuer.rstrip("/"):
            raise OidcError("the discovery document is for another issuer")
        for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(m.get(k), str) or not re.match(r"https?://", m[k]):
                raise OidcError(f"the discovery document has no {k}")
        if "RS256" not in (m.get("id_token_signing_alg_values_supported") or ["RS256"]):
            raise OidcError("the provider does not sign with RS256")
        with self._lock:
            self._meta, self._meta_at = m, self.clock()
        return m

    def key(self, kid):
        """(n, e) of the RSA signing key `kid` (None: the only one). Fetches the JWKS again once
        when the id is unknown (the provider rotated its key), at most every REFETCH_EVERY."""
        for again in (False, True):
            with self._lock:
                keys = self._keys if self._keys and self.clock() - self._keys_at < CACHE_TTL else None
                if again or keys is None:
                    if again and self.clock() - self._tried_at < REFETCH_EVERY:
                        break
                    keys = None
            if keys is None:
                with self._lock:
                    self._tried_at = self.clock()
                got = self.fetch(self.meta()["jwks_uri"]).get("keys")
                if not isinstance(got, list):
                    raise OidcError("the JWKS has no keys")
                keys = [k for k in got if isinstance(k, dict) and k.get("kty") == "RSA"
                        and k.get("use", "sig") == "sig" and k.get("alg", "RS256") == "RS256"
                        and isinstance(k.get("n"), str) and isinstance(k.get("e"), str)]
                with self._lock:
                    self._keys, self._keys_at = keys, self.clock()
            found = [k for k in keys if kid is None or k.get("kid") == kid]
            if kid is None and len(found) > 1:
                raise OidcError("the token names no key and the JWKS has several")
            if found:
                return _int(found[0]["n"]), _int(found[0]["e"])
        raise OidcError("the token is signed by a key the provider does not list")


def verify_id_token(token, provider, client_id, nonce, now=None):
    """The id_token's claims, after checking its signature and its claims. Raises OidcError."""
    now = time.time() if now is None else now
    if not isinstance(token, str) or token.count(".") != 2 or len(token) > 16384:
        raise OidcError("not a JWT")
    head64, body64, sig64 = token.split(".")
    try:
        head = json.loads(_b64d(head64))
        claims = json.loads(_b64d(body64))
    except (ValueError, UnicodeDecodeError):
        raise OidcError("the token does not parse") from None
    if not isinstance(head, dict) or not isinstance(claims, dict):
        raise OidcError("the token does not parse")
    if head.get("alg") != "RS256":
        raise OidcError(f"the token is signed with {str(head.get('alg'))[:10]!r}, not RS256")
    if "crit" in head:
        raise OidcError("the token has critical extensions")
    kid = head.get("kid")
    if kid is not None and not isinstance(kid, str):
        raise OidcError("the token's key id is not text")
    n, e = provider.key(kid)
    if not rs256_ok(f"{head64}.{body64}".encode(), _b64d(sig64), n, e):
        raise OidcError("bad signature")
    if claims.get("iss") != provider.meta()["issuer"]:
        raise OidcError("the token is from another issuer")
    aud = claims.get("aud")
    auds = [aud] if isinstance(aud, str) else aud if isinstance(aud, list) else []
    if client_id not in auds:
        raise OidcError("the token is for another client")
    if ("azp" in claims or len(auds) > 1) and claims.get("azp") != client_id:
        raise OidcError("the token was asked by another client")
    num = lambda k: claims.get(k) if isinstance(claims.get(k), (int, float)) and not isinstance(claims.get(k), bool) else None
    if num("exp") is None or num("exp") < now - LEEWAY:
        raise OidcError("the token has expired")
    if num("iat") is not None and num("iat") > now + LEEWAY:
        raise OidcError("the token is from the future")
    if num("nbf") is not None and num("nbf") > now + LEEWAY:
        raise OidcError("the token is not valid yet")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"].encode(), nonce.encode()):
        raise OidcError("the nonce does not match")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise OidcError("the token names no subject")
    return claims


def who(claims):
    """The name shown for this sign-in: preferred_username, else email, else sub."""
    for k in ("preferred_username", "email", "sub"):
        if isinstance(claims.get(k), str) and claims[k]:
            return claims[k]
    return ""


def allowed(claims, cfg):
    """In [admin.oidc] allow (user name, email or subject; case does not matter) or in a group of
    allow_groups (exact). An email counts only when the provider says it is verified, if it says."""
    names = {claims.get("preferred_username"), claims.get("sub")}
    if claims.get("email_verified", True) is not False:
        names.add(claims.get("email"))
    names = {n.lower() for n in names if isinstance(n, str) and n}
    if names & {a.lower() for a in cfg.get("allow") or ()}:
        return True
    groups = claims.get("groups")
    groups = {g for g in groups if isinstance(g, str)} if isinstance(groups, list) else set()
    return bool(groups & set(cfg.get("allow_groups") or ()))


# --- the sign-ins in progress ---

_lock = threading.Lock()
_pending = {}     # state -> {verifier, nonce, bind, redirect, mode, device, made}
_results = {}     # r -> {mode, user, bind, device, made}
_providers = {}   # issuer -> Provider


def provider(issuer):
    with _lock:
        if issuer not in _providers:
            _providers[issuer] = Provider(issuer)
        return _providers[issuer]


def _sweep(now):
    for box, ttl in ((_pending, PENDING_TTL), (_results, RESULT_TTL)):
        for k in [k for k, v in box.items() if now - v["made"] > ttl]:
            del box[k]
        while len(box) >= MAX_PENDING:
            box.pop(min(box, key=lambda k: box[k]["made"]))


def begin(cfg, mode, redirect_uri, device=None, now=None):
    """(authorization URL, bind) for a new sign-in. Raises OidcError when the provider is not there."""
    now = time.time() if now is None else now
    meta = provider(cfg["issuer"]).meta()
    state, nonce, verifier, bind = (secrets.token_urlsafe(32) for _ in range(4))
    challenge = _b64e(hashlib.sha256(verifier.encode()).digest())
    with _lock:
        _sweep(now)
        _pending[state] = {"verifier": verifier, "nonce": nonce, "bind": util.sha256_hex(bind), "redirect": redirect_uri,
                           "mode": mode, "device": device, "made": now}
    scopes = list(dict.fromkeys(["openid", *cfg.get("scopes", ["profile", "email"])]))
    q = urllib.parse.urlencode({"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": redirect_uri,
                                "scope": " ".join(scopes), "state": state, "nonce": nonce,
                                "code_challenge": challenge, "code_challenge_method": "S256"})
    sep = "&" if "?" in meta["authorization_endpoint"] else "?"
    return meta["authorization_endpoint"] + sep + q, bind


def complete(cfg, state, code, now=None):
    """The callback: (mode, r, user) for an allowed user; raises OidcError (with .mode and .user
    set when known) for anything else. The started sign-in is used up either way."""
    now = time.time() if now is None else now
    with _lock:
        p = _pending.pop(state, None) if isinstance(state, str) else None
    if p is None or now - p["made"] > PENDING_TTL:
        raise OidcError("unknown or expired state")
    try:
        if not isinstance(code, str) or not code or len(code) > 2048:
            raise OidcError("no code")
        prov = provider(cfg["issuer"])
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": p["redirect"],
                "client_id": cfg["client_id"], "code_verifier": p["verifier"]}
        secret = os.environ.get(SECRET_ENV, "")
        if secret:
            form["client_secret"] = secret
        tok = prov.fetch(prov.meta()["token_endpoint"], urllib.parse.urlencode(form).encode(),
                         {"Content-Type": "application/x-www-form-urlencoded"})
        claims = verify_id_token(tok.get("id_token"), prov, cfg["client_id"], p["nonce"], now)
        user = who(claims)
        if not allowed(claims, cfg):
            err = OidcError("not in allow or allow_groups")
            err.user = user
            raise err
    except OidcError as e:
        e.mode = p["mode"]
        raise
    r = secrets.token_urlsafe(32)
    with _lock:
        _sweep(now)
        _results[r] = {"mode": p["mode"], "user": user, "bind": p["bind"], "device": p["device"], "made": now}
    return p["mode"], r, user


def claim(r, bind, mode, device=None, now=None):
    """The user of a finished sign-in, used up; None when r, bind, mode or phone do not match."""
    now = time.time() if now is None else now
    if not isinstance(r, str) or not RID.fullmatch(r) or not isinstance(bind, str):
        return None
    with _lock:
        res = _results.get(r)
        if (res is None or now - res["made"] > RESULT_TTL or res["mode"] != mode or res["device"] != device
                or not hmac.compare_digest(res["bind"], util.sha256_hex(bind))):
            return None
        del _results[r]
    return res["user"]


# --- the routes ---

def _srv(h):
    """The server module (server.py is __main__ when run, `server` in the tests)."""
    return sys.modules[type(h).__module__]


def _cfg():
    return admin.SETTINGS.get("oidc")


def _redirect_uri(h, cfg):
    """[admin.oidc] redirect_uri, else this request's own address + the callback path."""
    if cfg.get("redirect_uri"):
        return cfg["redirect_uri"]
    host = (h.headers.get("Host") or "").lower()
    tls = bool(getattr(_srv(h), "TLS_CERT", ""))
    if not tls and admin._within(admin._addr(h.client_address[0]), admin.SETTINGS["trusted_proxies"]):   # behind a proxy
        tls = (h.headers.get("X-Forwarded-Proto") or "").lower() == "https"
    return f"{'https' if tls else 'http'}://{host}{CALLBACK}"


def _forbidden():
    return 403, {"ok": False, "error": "forbidden"}


@route("POST", "/api/admin/oidc/start", guard="open")
def start(h, body):
    srv, cfg, mode = _srv(h), _cfg(), body.get("mode")
    if not cfg or not srv.ADMIN.is_on() or not h._admin_net() or mode not in ("web", "app"):
        return _forbidden()
    device = None
    if mode == "web":
        if not h._same_page() or h._device()[1] == "bearer":
            return _forbidden()
    else:
        if h._device()[1] != "bearer":
            return _forbidden()
        device = h._dev_rec[0]
    try:
        url, bind = begin(cfg, mode, _redirect_uri(h, cfg), device)
    except OidcError as e:
        print(f"oidc: cannot start from {h._ip()}: {e}", flush=True)
        return 502, {"ok": False, "error": f"the identity provider: {e}"}
    return 200, {"ok": True, "url": url, "bind": bind}


@route("GET", CALLBACK)
def callback(h, body):
    q = urllib.parse.parse_qs(h.path.partition("?")[2], max_num_fields=12)
    one = lambda k: (q.get(k) or [""])[0]
    cfg, where, ua = _cfg(), h._ip(), h.headers.get("User-Agent", "")
    if not cfg:
        return redirect("/?oidc_error=off")
    state = one("state")
    with _lock:
        mode = (_pending.get(state) or {}).get("mode", "web")
    back = (lambda r, err: f"{APP_RETURN}?" + urllib.parse.urlencode({"r": r} if r else {"error": err})) if mode == "app" \
        else (lambda r, err: "/?" + urllib.parse.urlencode({"oidc": r} if r else {"oidc_error": err}))
    if one("error"):     # the user said no at the provider, or it refused
        with _lock:
            _pending.pop(state, None)
        admin.told("", "oidc", False, where, ua)
        print(f"oidc: the provider answered {one('error')[:40]!r} for {where}", flush=True)
        return redirect(back(None, "denied"))
    if not h._admin_net():
        return redirect(back(None, "network"))
    try:
        mode, r, user = complete(cfg, state, one("code"))
    except OidcError as e:
        user = getattr(e, "user", "")
        admin.told(user, "oidc", False, where, ua)
        print(f"oidc: sign-in refused for {user or 'someone'} from {where}: {e}", flush=True)
        return redirect(back(None, "refused" if user else "failed"))
    print(f"oidc: {user} checked at the provider from {where}", flush=True)
    return redirect(back(r, None))


@route("POST", "/api/admin/oidc/finish", guard="open")
def finish(h, body):
    srv, where = _srv(h), h._ip()
    if not _cfg() or not srv.ADMIN.is_on() or not h._admin_net():
        return _forbidden()
    bearer = h._device()[1] == "bearer"
    if bearer:
        did, name = h._dev_rec[0], h._dev_rec[1]["name"]
        user = claim(body.get("r"), body.get("bind"), "app", did)
        if user is None:
            return _forbidden()
        srv.devices.set_admin(did, srv.ADMIN.sign())
        admin.told(user, "oidc", True, where, name)
        print(f"admin: phone {name} signed in with OIDC as {user} from {where}", flush=True)
        srv._log([(f"Admin signed in on the phone {name} (single sign-on)", False)])
        return 200, {"ok": True, **h._app_rights_after(True)}
    if not h._same_page():
        return _forbidden()
    user = claim(body.get("r"), body.get("bind"), "web")
    if user is None:
        return _forbidden()
    ua = h.headers.get("User-Agent", "")
    label = getattr(srv, "browser_label", lambda x: str(x or "")[:64])(ua)
    ticket = srv.ADMIN.open_session(where, label)
    admin.told(user, "oidc", True, where, label)
    print(f"admin: signed in with OIDC as {user} from {where}", flush=True)
    srv._log([(f"Admin signed in from {where} (single sign-on)", False)])
    return 200, {"ok": True, "ticket": ticket, "idle": srv.ADMIN.idle, "user": srv.ADMIN.user(), "sso": user}
