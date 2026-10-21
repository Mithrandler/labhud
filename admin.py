"""The admin: one account, with a password and a second factor (TOTP, any authenticator app).

What it guards: everything a display can change. ⚙ (this screen's look as well as the phones,
the agents and the PIN), the cards' buttons and maintenance, and the config editor. A display
that is not signed in only shows; so does every display of an install without an admin. The app
pairs by signing in (`/api/v1/login-pair`) and then acts with its own token.

There is exactly one admin. It is made by the setup page, or by `init.py admin` (which also
replaces it: a forgotten password or a lost authenticator); `init.py admin --remove` deletes it.

Kept like the display's PIN (lock.py): in the store (LABHUD_DATA) as {user, hash, totp}, or
else in LABHUD_ADMIN as "user:hash:secret" (`init.py admin --env` prints the line). The password
is kept only as a scrypt hash; the TOTP secret has to be kept as it is, like any key in .env.

A right sign-in gets a ticket, a session for that browser that lasts while it is used and ends
after IDLE seconds (30 days) without use, or at Sign out. Sessions survive a restart: the store
keeps each one's SHA-256, never the ticket, with when it was made and last used and from where.
A new password or authenticator (init.py admin) ends them all. Wrong tries are slowed down as for the PIN: after
five, nothing is checked for a minute, then two, four... up to an hour. A TOTP code works once.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import threading
import time
import urllib.parse

import store
import util

STORE_KEY = "admin"
ENV_KEY = "LABHUD_ADMIN"
USER = re.compile(r"[A-Za-z0-9._@-]{1,40}")
MIN_PASSWORD = 10
N, R, P = 2 ** 14, 8, 1
IDLE = 30 * 86400
MAX_TICKETS = 16
SESSIONS_KEY = "admin-sessions"
SAVE_EVERY = 3600  # a session's "last used" reaches the store at most this often
FAILS_PER_ROUND = 5
FIRST_WAIT = 60
MAX_WAIT = 3600
STEP = 30          # TOTP: seconds per code
DIGITS = 6
WINDOW = 1         # codes one step early or late still count (a phone's clock drifts)

# Told of every admin sign-in, right or wrong, whatever the way (password+TOTP here, OIDC...):
# f(user=, how=, ok=, where=, what=). The audit log and the notifications register here; a hook
# that fails never stops a sign-in.
LOGIN_HOOKS = []


def told(user, how, ok, where="", what=""):
    for f in list(LOGIN_HOOKS):
        try:
            f(user=str(user or "")[:40], how=how, ok=bool(ok), where=str(where)[:64], what=str(what)[:64])
        except Exception:
            pass


def _sign(rec):
    """Which admin the sessions belong to: a new password or authenticator is another one."""
    if not rec:
        return None
    return hashlib.sha256("\0".join((rec["user"], rec["hash"], rec["totp"])).encode()).hexdigest()


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# --- the password ---

def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(key)}"


def verify_password(password, stored):
    if not isinstance(password, str) or not password or not stored:
        return False
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        key = _unb64(key)
        got = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(key))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, key)


def password_problem(password):
    """Why a new password is not accepted, or None."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return f"the password needs at least {MIN_PASSWORD} characters"
    if len(password) > 200:
        return "the password is too long"
    return None


# --- TOTP (RFC 6238: HMAC-SHA1, 30 s, 6 digits), what every authenticator app speaks ---

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def totp_counter(code, secret, now=None):
    """The time step `code` belongs to (within WINDOW of now), or None."""
    code = re.sub(r"\s", "", str(code or ""))
    if not re.fullmatch(r"\d{%d}" % DIGITS, code):
        return None
    now = int((now or time.time()) // STEP)
    for c in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(totp(secret, c), code):
            return c
    return None


def otpauth_uri(user, secret, issuer="LabHUD"):
    """The link an authenticator app scans: otpauth://totp/LabHUD:<user>?secret=...&issuer=LabHUD."""
    label = urllib.parse.quote(f"{issuer}:{user}")
    q = urllib.parse.urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1",
                                "digits": DIGITS, "period": STEP})
    return f"otpauth://totp/{label}?{q}"


# --- the account ---

def record(user, password, secret):
    """The kept form of a new admin, after checking it."""
    if not isinstance(user, str) or not USER.fullmatch(user):
        raise ValueError("the user name: 1 to 40 letters, digits, . _ @ -")
    problem = password_problem(password)
    if problem:
        raise ValueError(problem)
    return {"user": user, "hash": hash_password(password), "totp": secret}


def env_line(rec):
    return f"{ENV_KEY}={rec['user']}:{rec['hash']}:{rec['totp']}"


class Admin:
    def __init__(self, idle=None, clock=time.monotonic, wall=time.time, session_clock=None):
        self._idle = idle        # None: [admin] session_hours (SETTINGS), read at every use
        self.clock = clock       # the waits after wrong tries
        self.wall = wall         # the TOTP codes
        self.days = session_clock or wall   # the sessions: their times are kept across restarts
        self.on_block = None     # called with a line of text when wrong tries start a wait
        self._lock = threading.Lock()
        self._sessions = {}      # sha256(ticket) -> {id, made, last, where, what}
        self._saved = {}         # sha256(ticket) -> the "last" the store has
        self._fails = 0
        self._rounds = 0
        self._until = 0.0
        self._last_counter = -1  # the TOTP step last used: a code works once
        self._seen = ()          # not read yet: the first _sync loads the kept sessions
        self._sync(self.stored())

    @property
    def idle(self):
        return self._idle if self._idle is not None else SETTINGS["session"]

    @idle.setter
    def idle(self, seconds):
        self._idle = seconds

    def _sync(self, rec):
        """Notices an admin replaced or removed elsewhere (init.py admin): every session ends, and
        the new authenticator starts with no code used. The first call loads the sessions the
        store kept for this same admin. Call with the lock free."""
        sign = _sign(rec)
        with self._lock:
            if sign == self._seen:
                return
            first, self._seen = self._seen == (), sign
            self._sessions.clear()
            self._last_counter = -1
            if first and sign and store.enabled():
                kept = store.load(SESSIONS_KEY) or {}
                if isinstance(kept, dict) and kept.get("sign") == sign and isinstance(kept.get("sessions"), dict):
                    self._sessions = {k: dict(v) for k, v in kept["sessions"].items() if isinstance(v, dict)}
            self._saved = {k: v.get("last") for k, v in self._sessions.items()}
            unsaved = not first
        if unsaved:
            self._save()

    def _save(self):
        """The sessions to the store (when there is one). Call with the lock free."""
        if not store.enabled():
            return
        with self._lock:
            kept = {"sign": self._seen, "sessions": {k: dict(v) for k, v in self._sessions.items()}}
            self._saved = {k: v.get("last") for k, v in self._sessions.items()}
        store.save(SESSIONS_KEY, kept)

    def stored(self):
        """{user, hash, totp}: the store's (an empty one = removed there), else the environment's."""
        kept = store.load(STORE_KEY) if store.enabled() else None
        if kept is not None:
            return kept or None
        raw = os.environ.get(ENV_KEY, "")
        parts = raw.split(":")
        if len(parts) == 3 and all(parts):
            return {"user": parts[0], "hash": parts[1], "totp": parts[2]}
        return None

    def is_on(self):
        return self.stored() is not None

    def sign(self):
        """Who the admin is now (a new password or authenticator is someone else), or None:
        what a phone signed in as the admin keeps (devices.py)."""
        return _sign(self.stored())

    def user(self):
        rec = self.stored()
        return rec["user"] if rec else None

    def public(self):
        """What /api/config tells the page: is there an admin, and may it sign in with OIDC."""
        return {"on": self.is_on(), "oidc": self.is_on() and oidc_on()}

    # --- signing in ---

    def wait(self):
        return max(0, int(self._until - self.clock() + 0.999))

    def check(self, user, password, code):
        """(right, wait): the three together, slowed after wrong tries. Does not make a ticket
        (pairing from the app uses this alone)."""
        with self._lock:
            if self.clock() < self._until:
                return False, self.wait()
        rec = self.stored()
        self._sync(rec)
        right = False
        if rec and isinstance(user, str) and hmac.compare_digest(user.strip().lower().encode(), rec["user"].lower().encode()):
            # the password is checked even when the code is wrong: the time taken says nothing
            pw = verify_password(password, rec["hash"])
            counter = totp_counter(code, rec["totp"], self.wall())
            with self._lock:
                if pw and counter is not None and counter > self._last_counter:
                    self._last_counter = counter
                    right = True
        else:
            verify_password(password or "x", hash_password("not the password"))   # same time as a real check
        with self._lock:
            if right:
                self._fails = self._rounds = 0
                return True, 0
            self._fails += 1
            if self._fails < FAILS_PER_ROUND:
                return False, 0
            self._fails = 0
            self._rounds += 1
            seconds = min(FIRST_WAIT * 2 ** (self._rounds - 1), MAX_WAIT)
            self._until = self.clock() + seconds
        if self.on_block:
            self.on_block(f"Admin: {FAILS_PER_ROUND} wrong sign-ins, locked for {seconds // 60} min")
        return False, seconds

    def login(self, user, password, code, where="", what=""):
        """(ticket, wait): a ticket for a right sign-in, else None and the seconds to wait.
        `where` (an IP) and `what` (the browser) are shown in the list of sessions."""
        right, wait = self.check(user, password, code)
        if not right:
            told(user, "password", False, where, what)
            return None, wait
        t = self.open_session(where, what)
        told(user, "password", True, where, what)
        return t, 0

    def open_session(self, where="", what=""):
        """A new ticket, for a sign-in already checked (by login(), or by another way such as
        OIDC, which then calls told() itself)."""
        now = int(self.days())
        t = secrets.token_urlsafe(32)
        with self._lock:
            while len(self._sessions) >= MAX_TICKETS:
                self._sessions.pop(min(self._sessions, key=lambda k: self._sessions[k].get("last", 0)))
            self._sessions[util.sha256_hex(t)] = {"id": secrets.token_hex(6), "made": now, "last": now,
                                        "where": str(where)[:64], "what": str(what)[:64]}
        self._save()
        return t

    def _live(self, ticket):
        """The session of a live ticket (this use keeps it alive for another `idle` seconds), or
        None. Saves when a session ended or its "last used" in the store is an hour old."""
        if not isinstance(ticket, str) or not ticket:
            return None
        rec = self.stored()
        self._sync(rec)
        if rec is None:
            return None
        key, now = util.sha256_hex(ticket), int(self.days())
        with self._lock:
            s = self._sessions.get(key)
            if s is None:
                return None
            if now - s.get("last", 0) > self.idle:
                del self._sessions[key]
                s, save = None, True
            else:
                s["last"] = now
                save = now - (self._saved.get(key) or 0) >= SAVE_EVERY
                s = dict(s)
        if save:
            self._save()
        return s

    def valid(self, ticket):
        """True for a live ticket, which this use keeps alive for another `idle` seconds."""
        return self._live(ticket) is not None

    def session_id(self, ticket):
        """The id of a live ticket's session (to mark "this browser" in the list), or None."""
        s = self._live(ticket)
        return s["id"] if s else None

    def sessions(self):
        """The live sessions, the last used first: {id, made, last, where, what}. No tickets."""
        self._sync(self.stored())
        now = int(self.days())
        with self._lock:
            gone = [k for k, s in self._sessions.items() if now - s.get("last", 0) > self.idle]
            for k in gone:
                del self._sessions[k]
            out = sorted((dict(s) for s in self._sessions.values()), key=lambda s: -s.get("last", 0))
        if gone:
            self._save()
        return out

    def revoke(self, sid):
        """Ends one session by its id (the list's Sign out). True if there was one."""
        with self._lock:
            key = next((k for k, s in self._sessions.items() if s.get("id") == sid), None)
            if key is None:
                return False
            del self._sessions[key]
        self._save()
        return True

    def close(self, ticket):
        with self._lock:
            found = self._sessions.pop(util.sha256_hex(ticket or ""), None)
        if found:
            self._save()

    def close_all(self):
        """Sign out everywhere."""
        with self._lock:
            self._sessions.clear()
        self._save()


# --- the settings of ⚙ > Security ([admin] in config.toml, applied by configure()) ---
#
# session_hours: how long a browser stays signed in without use (1 hour to 30 days).
# trusted_proxies: the reverse proxies whose X-Forwarded-For is believed; any other peer is the
#   client itself, whatever it writes in that header.
# admin_networks: the only networks the admin may sign in from and act from (empty = anywhere).
#   The CLI beside the server (loopback, its own key) is not concerned.
# oidc: single sign-on (api/oidc.py); None when not set.

MIN_SESSION = 3600
MAX_SESSION = 30 * 86400
SETTINGS = {"session": IDLE, "trusted_proxies": (), "admin_networks": (), "oidc": None}


def configure(cfg):
    """Takes [admin] as config.py checked it ({session, trusted_proxies, admin_networks, oidc}),
    or None for the defaults. Called by server.py at every config (re)load."""
    import ipaddress
    cfg = cfg or {}
    nets = lambda xs: tuple(ipaddress.ip_network(x, strict=False) for x in xs or ())
    SETTINGS.update(session=min(max(int(cfg.get("session") or IDLE), MIN_SESSION), MAX_SESSION),
                    trusted_proxies=nets(cfg.get("trusted_proxies")),
                    admin_networks=nets(cfg.get("admin_networks")),
                    oidc=cfg.get("oidc") or None)


def _addr(text):
    import ipaddress
    try:
        a = ipaddress.ip_address(str(text).strip().strip("[]"))
    except ValueError:
        return None
    return a.ipv4_mapped or a if a.version == 6 else a


def _within(addr, nets):
    return addr is not None and any(addr.version == n.version and addr in n for n in nets)


def client_ip(peer, forwarded=None, trusted=None):
    """The client's address: the peer, unless the peer is a trusted proxy; then X-Forwarded-For
    read from the right, skipping the trusted proxies, and the first other address is the client.
    A header that does not parse gives the last trusted hop, never a made-up address."""
    trusted = SETTINGS["trusted_proxies"] if trusted is None else trusted
    here = _addr(peer)
    if not trusted or not _within(here, trusted) or not forwarded:
        return str(here) if here is not None else str(peer)
    for hop in reversed([x for x in str(forwarded).split(",")][:32]):
        a = _addr(hop)
        if a is None:
            break
        here = a
        if not _within(a, trusted):
            break
    return str(here)


def network_ok(ip, networks=None):
    """May the admin sign in and act from `ip`? Anywhere when admin_networks is empty."""
    networks = SETTINGS["admin_networks"] if networks is None else networks
    return not networks or _within(_addr(ip), networks)


def oidc_on():
    return bool(SETTINGS.get("oidc"))
