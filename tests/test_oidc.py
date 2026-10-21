"""Single sign-on (api/oidc.py) against a fake provider (tests/fake_idp.py): the RS256 check, the
claims, PKCE and the client secret, the binding of a result to whoever started, and the whole way
through a running server for the page and for a phone."""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.parse

import admin
from api import oidc
from tests import fake_idp, signin
from tests import test_smoke

CFG = {"client_id": "labhud", "allow": ["demo"], "allow_groups": [], "scopes": ["openid", "profile", "email"]}


def follow_authorize(url):
    """The provider's answer to the authorization URL: the redirect back, as (path+query, query dict)."""
    u = urllib.parse.urlsplit(url)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
    conn.request("GET", u.path + "?" + u.query)
    r = conn.getresponse()
    r.read()
    conn.close()
    assert r.status == 302, r.status
    loc = urllib.parse.urlsplit(r.getheader("Location"))
    return loc, {k: v[0] for k, v in urllib.parse.parse_qs(loc.query).items()}


class Rs256(unittest.TestCase):
    def test_signature(self):
        k = fake_idp.key()
        sig = fake_idp.sign_rs256(b"hello", k)
        self.assertTrue(oidc.rs256_ok(b"hello", sig, k[0], k[1]))
        self.assertFalse(oidc.rs256_ok(b"hellO", sig, k[0], k[1]))
        self.assertFalse(oidc.rs256_ok(b"hello", sig[:-1] + bytes([sig[-1] ^ 1]), k[0], k[1]))
        self.assertFalse(oidc.rs256_ok(b"hello", sig[1:], k[0], k[1]))                     # wrong length
        self.assertFalse(oidc.rs256_ok(b"hello", k[0].to_bytes(256, "big"), k[0], k[1]))   # s >= n

    def test_small_key_refused(self):
        n, e, d = fake_idp.rsa_key(1024)
        sig = fake_idp.sign_rs256(b"x", (n, e, d))
        self.assertFalse(oidc.rs256_ok(b"x", sig, n, e))


class Flow(unittest.TestCase):
    """begin -> the provider -> complete -> claim, in this process."""

    @classmethod
    def setUpClass(cls):
        cls.idp = fake_idp.FakeIdP().start()
        os.environ[oidc.SECRET_ENV] = "s3cret"

    @classmethod
    def tearDownClass(cls):
        cls.idp.stop()
        os.environ.pop(oidc.SECRET_ENV, None)

    def setUp(self):
        self.cfg = dict(CFG, issuer=self.idp.issuer)
        self.idp.tamper, self.idp.claims, self.idp.groups = None, {}, ["labhud admins"]
        self.idp.secret = "s3cret"

    def run_flow(self, mode="web", device=None, cfg=None):
        url, bind = oidc.begin(cfg or self.cfg, mode, "https://hud.example/api/admin/oidc/callback", device)
        loc, q = follow_authorize(url)
        self.assertEqual(loc.path, "/api/admin/oidc/callback")
        return oidc.complete(cfg or self.cfg, q["state"], q["code"]), bind

    def test_authorization_url(self):
        url, bind = oidc.begin(self.cfg, "web", "https://hud.example/api/admin/oidc/callback")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["response_type"], "code")
        self.assertIn("openid", q["scope"].split())
        for k in ("state", "nonce", "code_challenge"):
            self.assertGreaterEqual(len(q[k]), 40)
        self.assertNotIn(bind, url)

    def test_whole_way(self):
        (mode, r, user), bind = self.run_flow()
        self.assertEqual((mode, user), ("web", "demo"))
        form = self.idp.token_calls[-1]
        self.assertEqual(form["client_secret"], "s3cret")
        self.assertGreaterEqual(len(form["code_verifier"]), 43)
        self.assertIsNone(oidc.claim(r, "wrong bind", "web"))
        self.assertIsNone(oidc.claim(r, bind, "app"))          # another mode
        self.assertEqual(oidc.claim(r, bind, "web"), "demo")
        self.assertIsNone(oidc.claim(r, bind, "web"))          # used once

    def test_phone_result_is_its_own(self):
        (mode, r, user), bind = self.run_flow("app", device="aaaa1111")
        self.assertIsNone(oidc.claim(r, bind, "app", "bbbb2222"))
        self.assertEqual(oidc.claim(r, bind, "app", "aaaa1111"), "demo")

    def test_result_expires(self):
        (mode, r, user), bind = self.run_flow()
        self.assertIsNone(oidc.claim(r, bind, "web", now=time.time() + oidc.RESULT_TTL + 1))

    def test_state(self):
        url, _ = oidc.begin(self.cfg, "web", "https://hud.example/api/admin/oidc/callback")
        loc, q = follow_authorize(url)
        with self.assertRaises(oidc.OidcError):
            oidc.complete(self.cfg, "not-the-state", q["code"])
        oidc.complete(self.cfg, q["state"], q["code"])
        with self.assertRaises(oidc.OidcError):                # a state works once
            oidc.complete(self.cfg, q["state"], q["code"])

    def test_state_expires(self):
        url, _ = oidc.begin(self.cfg, "web", "https://hud.example/api/admin/oidc/callback", now=time.time() - oidc.PENDING_TTL - 5)
        loc, q = follow_authorize(url)
        with self.assertRaises(oidc.OidcError):
            oidc.complete(self.cfg, q["state"], q["code"])

    def test_bad_tokens(self):
        for t in ("aud", "exp", "nonce", "iss", "sig", "alg", "otherkey", "kid"):
            with self.subTest(tamper=t):
                self.idp.tamper = t
                with self.assertRaises(oidc.OidcError):
                    self.run_flow()

    def test_wrong_secret(self):
        self.idp.secret = "another"
        with self.assertRaisesRegex(oidc.OidcError, "401"):
            self.run_flow()

    def test_who_may(self):
        self.idp.claims = {"preferred_username": "mallory", "email": "mallory@example.org", "groups": []}
        with self.assertRaises(oidc.OidcError) as e:
            self.run_flow()
        self.assertEqual(e.exception.user, "mallory")
        self.idp.claims = {"preferred_username": "mallory", "email": "DEMO@example.org", "email_verified": True}
        cfg = dict(self.cfg, allow=["demo@example.org"])
        self.assertEqual(self.run_flow(cfg=cfg)[0][2], "mallory")      # by email, any case
        self.idp.claims = {"preferred_username": "mallory", "email": "demo@example.org", "email_verified": False}
        with self.assertRaises(oidc.OidcError):                         # not a verified email
            self.run_flow(cfg=cfg)
        self.idp.claims = {"preferred_username": "mallory", "groups": ["labhud admins"]}
        cfg = dict(self.cfg, allow=[], allow_groups=["labhud admins"])
        self.assertEqual(self.run_flow(cfg=cfg)[0][2], "mallory")      # by group
        cfg = dict(self.cfg, allow=[], allow_groups=["Labhud Admins"])
        with self.assertRaises(oidc.OidcError):                         # groups are exact
            self.run_flow(cfg=cfg)

    def test_audience_list_needs_azp(self):
        self.idp.claims = {"aud": ["labhud", "other"]}
        with self.assertRaises(oidc.OidcError):
            self.run_flow()
        self.idp.claims = {"aud": ["labhud", "other"], "azp": "labhud"}
        self.assertEqual(self.run_flow()[0][2], "demo")

    def test_key_rotation(self):
        now = [1000.0]
        p = oidc.Provider(self.idp.issuer, clock=lambda: now[0])
        tok = self.idp.id_token("n")
        oidc.verify_id_token(tok, p, "labhud", "n")
        old_key, old_kid = self.idp.key, self.idp.kid
        self.idp.key, self.idp.kid = fake_idp.key("other"), "k2"
        try:
            tok = self.idp.id_token("n")
            with self.assertRaises(oidc.OidcError):          # the JWKS was just fetched: not again so soon
                oidc.verify_id_token(tok, p, "labhud", "n")
            now[0] += oidc.REFETCH_EVERY + 1
            self.assertEqual(oidc.verify_id_token(tok, p, "labhud", "n")["sub"], "sub-demo")
        finally:
            self.idp.key, self.idp.kid = old_key, old_kid

    def test_discovery_for_another_issuer(self):
        p = oidc.Provider(self.idp.base + "/somewhere/else/")
        with self.assertRaises(oidc.OidcError):
            p.meta()


def demo_config(extra):
    import demo
    with open(demo.CONFIG, encoding="utf-8") as f:
        return f.read() + "\n" + extra


class ServerOidc(test_smoke.DemoServer):
    """The whole way through server.py: the page and a phone."""
    data = tempfile.TemporaryDirectory()
    idp = fake_idp.FakeIdP().start()
    extra_env = {"LABHUD_DATA": data.name, **signin.ENV, oidc.SECRET_ENV: "s3cret"}

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(cls.data.name, "config.toml"), "w", encoding="utf-8") as f:
            f.write(demo_config(f'[admin]\nsession_hours = 12\n\n[admin.oidc]\nissuer = "{cls.idp.issuer}"\n'
                                f'client_id = "labhud"\nallow_groups = ["labhud admins"]\n'))
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.idp.stop()
        shutil.rmtree(cls.data.name, ignore_errors=True)

    def call(self, method, path, body=None, headers=None, origin=True):
        h = {"Host": self.host, "Content-Type": "application/json", **(headers or {})}
        if origin:
            h["Origin"] = f"http://{self.host}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        return r.status, (json.loads(raw) if raw and r.getheader("Content-Type", "").startswith("application/json") else {}), r.getheader("Location")

    def through_provider(self, url):
        loc, q = follow_authorize(url)
        self.assertEqual(loc.netloc, self.host)   # redirect_uri: this server's own address
        status, _, back = self.call("GET", loc.path + "?" + loc.query, origin=False)
        self.assertEqual(status, 303)
        return back

    def test_config_says_oidc(self):
        status, cfg, _ = self.call("GET", "/api/config", origin=False)
        self.assertEqual(cfg["admin"], {"on": True, "oidc": True})

    def test_page(self):
        status, r, _ = self.call("POST", "/api/admin/oidc/start", {"mode": "web"})
        self.assertEqual(status, 200, r)
        back = self.through_provider(r["url"])
        self.assertTrue(back.startswith("/?oidc="), back)
        rid = urllib.parse.parse_qs(back[2:])["oidc"][0]
        self.assertEqual(self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": "x"})[0], 403)
        status, done, _ = self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": r["bind"]}, origin=False)
        self.assertEqual(status, 403)                      # not from the page
        status, done, _ = self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": r["bind"]})
        self.assertEqual(status, 200, done)
        self.assertEqual(done["idle"], 12 * 3600)          # [admin] session_hours
        status, chk, _ = self.call("POST", "/api/admin/check", {}, {"X-Labhud-Admin": done["ticket"]})
        self.assertEqual((status, chk["user"]), (200, "boss"))   # the same admin as with a password
        self.assertEqual(self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": r["bind"]})[0], 403)   # once

    def test_start_refused(self):
        self.assertEqual(self.call("POST", "/api/admin/oidc/start", {"mode": "web"}, origin=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/admin/oidc/start", {"mode": "nope"})[0], 403)
        self.assertEqual(self.call("POST", "/api/admin/oidc/start", {"mode": "app"})[0], 403)   # no phone token

    def test_refused_at_the_provider(self):
        self.idp.claims = {"groups": ["someone else"]}
        try:
            status, r, _ = self.call("POST", "/api/admin/oidc/start", {"mode": "web"})
            back = self.through_provider(r["url"])
        finally:
            self.idp.claims = {}
        self.assertEqual(back, "/?oidc_error=refused")
        status, _, back = self.call("GET", "/api/admin/oidc/callback?state=made-up&code=x", origin=False)
        self.assertEqual((status, back), (303, "/?oidc_error=failed"))
        status, _, back = self.call("GET", "/api/admin/oidc/callback?error=access_denied&state=x", origin=False)
        self.assertEqual(back, "/?oidc_error=denied")

    def test_phone(self):
        code = admin.totp(signin.SECRET, int(time.time()) // admin.STEP)
        status, p, _ = self.call("POST", "/api/v1/login-pair",
                                 {"user": "boss", "password": "correct horse 1", "code": code, "name": "Pixel"}, origin=False)
        if status != 200:   # this step's code was used: wait for the next
            time.sleep(admin.STEP - time.time() % admin.STEP + 0.5)
            code = admin.totp(signin.SECRET, int(time.time()) // admin.STEP)
            status, p, _ = self.call("POST", "/api/v1/login-pair",
                                     {"user": "boss", "password": "correct horse 1", "code": code, "name": "Pixel"}, origin=False)
        self.assertEqual(status, 200, p)
        bearer = {"Authorization": "Bearer " + p["token"]}
        self.call("POST", "/api/v1/admin-logout", {}, bearer, origin=False)
        status, info, _ = self.call("GET", "/api/v1/info", None, bearer, origin=False)
        self.assertFalse(info["device_admin"])
        status, r, _ = self.call("POST", "/api/admin/oidc/start", {"mode": "app"}, bearer, origin=False)
        self.assertEqual(status, 200, r)
        back = self.through_provider(r["url"])
        self.assertTrue(back.startswith("labhud://oidc?r="), back)
        rid = urllib.parse.parse_qs(urllib.parse.urlsplit(back).query)["r"][0]
        self.assertEqual(self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": r["bind"]})[0], 403)   # the page, not the phone
        status, done, _ = self.call("POST", "/api/admin/oidc/finish", {"r": rid, "bind": r["bind"]}, bearer, origin=False)
        self.assertEqual(status, 200, done)
        self.assertTrue(done["device_admin"])
        status, info, _ = self.call("GET", "/api/v1/info", None, bearer, origin=False)
        self.assertTrue(info["device_admin"])


def _skip_smoke_tests(cls):
    """The demo's own smoke tests run in test_smoke, not again here. In a function, so that the
    names of the loop stay out of the module: unittest would find the class a second time."""
    for name in [n for n in dir(test_smoke.DemoServer) if n.startswith("test_")]:
        setattr(cls, name, None)


_skip_smoke_tests(ServerOidc)


if __name__ == "__main__":
    unittest.main()
