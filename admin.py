"""The admin: one account, with a password and a second factor (TOTP, any authenticator app).

What it guards, once it exists: pairing and removing phones (the display's ⚙ > Phone app, and
pairing from the app by signing in), and opening the config editor from ⚙. Without an admin,
those work as before (the display's own page, its PIN when one is set), so an install that never
sets one up changes nothing.

There is exactly one admin. It is made by the setup page, or by `init.py admin` (which also
replaces it: a forgotten password or a lost authenticator); `init.py admin --remove` deletes it.

Kept like the display's PIN (lock.py): in the store (LABHUD_DATA) as {user, hash, totp}, or
else in LABHUD_ADMIN as "user:hash:secret" (`init.py admin --env` prints the line). The password
is kept only as a scrypt hash; the TOTP secret has to be kept as it is, like any key in .env.

A right sign-in gets a ticket held in memory (a restart ends them all) that lasts while it is
used and ends after IDLE seconds without use. Wrong tries are slowed down as for the PIN: after
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

STORE_KEY = "admin"
ENV_KEY = "LABHUD_ADMIN"
USER = re.compile(r"[A-Za-z0-9._@-]{1,40}")
MIN_PASSWORD = 10
N, R, P = 2 ** 14, 8, 1
IDLE = 15 * 60
MAX_TICKETS = 8
FAILS_PER_ROUND = 5
FIRST_WAIT = 60
MAX_WAIT = 3600
STEP = 30          # TOTP: seconds per code
DIGITS = 6
WINDOW = 1         # codes one step early or late still count (a phone's clock drifts)


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
    def __init__(self, idle=IDLE, clock=time.monotonic, wall=time.time):
        self.idle = idle
        self.clock = clock
        self.wall = wall
        self.on_block = None     # called with a line of text when wrong tries start a wait
        self._lock = threading.Lock()
        self._tickets = {}       # ticket -> last use
        self._fails = 0
        self._rounds = 0
        self._until = 0.0
        self._last_counter = -1  # the TOTP step last used: a code works once
        self._seen = None
        self._sync(self.stored())

    def _sync(self, rec):
        """Notices an admin replaced or removed elsewhere (init.py admin): every ticket ends, and
        the new authenticator starts with no code used. Call with the lock free."""
        sign = (rec["user"], rec["hash"], rec["totp"]) if rec else None
        with self._lock:
            if sign != self._seen:
                self._seen = sign
                self._tickets.clear()
                self._last_counter = -1

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

    def user(self):
        rec = self.stored()
        return rec["user"] if rec else None

    def public(self):
        """What /api/config tells the page."""
        return {"on": self.is_on()}

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

    def login(self, user, password, code):
        """(ticket, wait): a ticket for a right sign-in, else None and the seconds to wait."""
        right, wait = self.check(user, password, code)
        if not right:
            return None, wait
        with self._lock:
            while len(self._tickets) >= MAX_TICKETS:
                self._tickets.pop(min(self._tickets, key=self._tickets.get))
            t = secrets.token_urlsafe(24)
            self._tickets[t] = self.clock()
            return t, 0

    def valid(self, ticket):
        """True for a live ticket, which this use keeps alive for another `idle` seconds."""
        if not isinstance(ticket, str) or not ticket:
            return False
        rec = self.stored()
        self._sync(rec)
        if rec is None:
            return False
        now = self.clock()
        with self._lock:
            last = self._tickets.get(ticket)
            if last is None:
                return False
            if now - last > self.idle:
                del self._tickets[ticket]
                return False
            self._tickets[ticket] = now
            return True

    def close(self, ticket):
        with self._lock:
            self._tickets.pop(ticket or "", None)
