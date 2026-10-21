"""A small OpenID Connect provider for the tests of api/oidc.py: discovery, an authorize endpoint
that says yes at once, a token endpoint that checks the PKCE verifier and the client secret, and a
JWKS. Its RSA key is made here, in pure Python (no dependency), once per process.

    idp = FakeIdP(client_id="labhud", secret="s3cret").start()
    idp.issuer            # http://127.0.0.1:<port>/application/o/labhud/
    idp.claims = {...}    # extra or replaced claims of the next id_tokens
    idp.tamper = "aud"    # aud | exp | nonce | iss | sig | alg | otherkey | kid: a bad token
    idp.stop()
"""

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse

SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _probably_prime(n, rounds=24):
    if n < 4:
        return n in (2, 3)
    for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def _prime(bits):
    while True:
        c = secrets.randbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if _probably_prime(c):
            return c


def rsa_key(bits=2048, e=65537):
    """(n, e, d)."""
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e and (p * q).bit_length() == bits:
            return p * q, e, pow(e, -1, phi)


_KEYS = {}


def key(name="main"):
    if name not in _KEYS:
        _KEYS[name] = rsa_key()
    return _KEYS[name]


def b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _int_b64(i):
    return b64(i.to_bytes((i.bit_length() + 7) // 8, "big"))


def sign_rs256(signed, k):
    n, _, d = k
    size = (n.bit_length() + 7) // 8
    t = SHA256_INFO + hashlib.sha256(signed).digest()
    em = b"\x00\x01" + b"\xff" * (size - len(t) - 3) + b"\x00" + t
    return pow(int.from_bytes(em, "big"), d, n).to_bytes(size, "big")


def jwt(head, claims, k):
    h = b64(json.dumps(head).encode())
    c = b64(json.dumps(claims).encode())
    return f"{h}.{c}.{b64(sign_rs256(f'{h}.{c}'.encode(), k))}"


def jwk(k, kid):
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _int_b64(k[0]), "e": _int_b64(k[1])}


class FakeIdP:
    def __init__(self, client_id="labhud", secret="s3cret", user="demo", groups=("labhud admins",)):
        self.client_id, self.secret = client_id, secret
        self.user, self.groups = user, list(groups)
        self.claims, self.tamper = {}, None
        self.codes = {}
        self.token_calls = []
        self.jwks_calls = 0
        self.key, self.kid = key("main"), "k1"

    # --- the server ---

    def start(self):
        idp = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None, location=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path, _, q = self.path.partition("?")
                base = urllib.parse.urlsplit(idp.issuer).path.rstrip("/")
                if path == base + "/.well-known/openid-configuration":
                    return self._send(200, idp.discovery())
                if path == "/jwks":
                    idp.jwks_calls += 1
                    return self._send(200, {"keys": [jwk(idp.key, idp.kid)]})
                if path == "/authorize":
                    return self._send(302, location=idp.authorize(urllib.parse.parse_qs(q)))
                self._send(404, {"error": "not_found"})

            def do_POST(self):
                if self.path != "/token":
                    return self._send(404, {"error": "not_found"})
                form = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
                code, out = idp.token({k: v[0] for k, v in form.items()})
                self._send(code, out)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.issuer = f"{self.base}/application/o/labhud/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- the endpoints ---

    def discovery(self):
        return {"issuer": self.issuer, "authorization_endpoint": self.base + "/authorize",
                "token_endpoint": self.base + "/token", "jwks_uri": self.base + "/jwks",
                "id_token_signing_alg_values_supported": ["RS256"]}

    def authorize(self, q):
        """Says yes for the user at once: back to redirect_uri with a code and the state."""
        one = lambda k: (q.get(k) or [""])[0]
        assert one("response_type") == "code" and one("code_challenge_method") == "S256", q
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"nonce": one("nonce"), "challenge": one("code_challenge"),
                            "redirect": one("redirect_uri"), "client_id": one("client_id")}
        sep = "&" if "?" in one("redirect_uri") else "?"
        return one("redirect_uri") + sep + urllib.parse.urlencode({"code": code, "state": one("state")})

    def token(self, form):
        self.token_calls.append(form)
        c = self.codes.pop(form.get("code", ""), None)
        if c is None:
            return 400, {"error": "invalid_grant"}
        verifier = form.get("code_verifier", "")
        if b64(hashlib.sha256(verifier.encode()).digest()) != c["challenge"]:
            return 400, {"error": "invalid_grant", "error_description": "PKCE"}
        if form.get("redirect_uri") != c["redirect"] or form.get("client_id") != self.client_id:
            return 400, {"error": "invalid_grant"}
        if self.secret and form.get("client_secret") != self.secret:
            return 401, {"error": "invalid_client"}
        return 200, {"access_token": "x", "token_type": "Bearer", "id_token": self.id_token(c["nonce"])}

    def id_token(self, nonce):
        now = int(time.time())
        claims = {"iss": self.issuer, "sub": "sub-" + self.user, "aud": self.client_id, "exp": now + 300, "iat": now,
                  "nonce": nonce, "preferred_username": self.user, "email": self.user + "@example.org",
                  "email_verified": True, "groups": self.groups, **self.claims}
        head, k = {"alg": "RS256", "kid": self.kid, "typ": "JWT"}, self.key
        t = self.tamper
        if t == "aud":
            claims["aud"] = "someone-else"
        elif t == "exp":
            claims["exp"] = now - 3600
        elif t == "nonce":
            claims["nonce"] = "not-the-nonce"
        elif t == "iss":
            claims["iss"] = self.base + "/other/"
        elif t == "otherkey":
            k = key("other")
        elif t == "kid":
            head["kid"] = "unknown"
        if t == "alg":
            h = b64(json.dumps({"alg": "none"}).encode())
            return f"{h}.{b64(json.dumps(claims).encode())}."
        token = jwt(head, claims, k)
        if t == "sig":
            a, b, s = token.split(".")
            token = f"{a}.{b64(json.dumps(dict(claims, preferred_username='mallory')).encode())}.{s}"
        return token
