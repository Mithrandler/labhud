"""An admin for the route tests: a display that is not signed in only shows, so the
tests that press buttons, pair phones or tick agents sign in first, once per server (a code works
once, and only the next 30 s step brings a new one)."""

import http.client
import json
import time

import admin

SECRET = admin.new_secret()
REC = admin.record("boss", "correct horse 1", SECRET)
ENV = {admin.ENV_KEY: admin.env_line(REC).split("=", 1)[1]}


def sign_in(port, host):
    """A live admin ticket from the server on `port` (known to it as `host`)."""
    code = admin.totp(SECRET, int(time.time()) // admin.STEP)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("POST", "/api/admin/login", body=json.dumps({"user": "boss", "password": "correct horse 1", "code": code}),
                 headers={"Host": host, "Origin": f"http://{host}", "Content-Type": "application/json",
                          "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"})
    r = conn.getresponse()
    out = json.loads(r.read())
    conn.close()
    if not out.get("ok"):
        raise RuntimeError(f"admin sign-in refused: {out}")
    return out["ticket"]
