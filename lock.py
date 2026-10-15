"""The display's lock: a 4-digit PIN that turns a screen anyone can walk up to into one that is
only for looking.

Only the PIN's scrypt hash is kept: in the store (LABHUD_DATA, set from the display's settings or
`init.py pin`), or else in LABHUD_LOCK_PIN_HASH (`init.py pin --hash` prints the line). The right
PIN gets a ticket, a random string held in memory, that the page sends with every request that
changes something (maintenance, the action log, signed actions, a new PIN). A ticket lasts while
the screen is used and ends after [lock] idle seconds without a touch; a restart ends them all.

Four digits are 10,000 tries, so the tries are slowed: after five wrong PINs nothing is checked
for a minute, then two, four... up to an hour. One counter for everyone: there is one display.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import threading
import time

import store

PIN = re.compile(r"\d{4}")
STORE_KEY = "lock_pin"
ENV_KEY = "LABHUD_LOCK_PIN_HASH"
N, R, P = 2 ** 14, 8, 1
MAX_TICKETS = 8
FAILS_PER_ROUND = 5
FIRST_WAIT = 60
MAX_WAIT = 3600


def _b64(b):
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def hash_pin(pin):
    """scrypt$n$r$p$salt$hash, for a PIN of exactly four digits."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        raise ValueError("the PIN must be exactly 4 digits")
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(pin.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${_b64(salt)}${_b64(key)}"


def verify(pin, stored):
    """True when `pin` is the PIN `stored` was made from; a malformed hash matches nothing."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin) or not stored:
        return False
    try:
        kind, n, r, p, salt, key = stored.split("$")
        if kind != "scrypt":
            return False
        key = _unb64(key)
        got = hashlib.scrypt(pin.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(key))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, key)


class Lock:
    def __init__(self, idle=600, clock=time.monotonic):
        self.idle = idle
        self.clock = clock
        self.on_block = None   # called with a line of text when the wrong tries start a wait
        self._lock = threading.Lock()
        self._tickets = {}     # ticket -> last use
        self._fails = 0
        self._rounds = 0
        self._until = 0.0
        self._seen = self.stored()

    # --- the PIN's hash ---

    def stored(self):
        """The hash in force: the store's (an empty one = removed there), else the environment's."""
        kept = store.load(STORE_KEY) if store.enabled() else None
        if kept is not None:
            return kept or None
        return os.environ.get(ENV_KEY) or None

    def is_on(self):
        return self.stored() is not None

    def settable(self):
        """The display can set the PIN itself only where it can keep it."""
        return store.enabled()

    def public(self):
        """What /api/config tells the page."""
        return {"on": self.is_on(), "idle": self.idle, "settable": self.settable()}

    # --- tries ---

    def wait(self):
        """Seconds before a PIN is checked again (0 = now)."""
        return max(0, int(self._until - self.clock() + 0.999))

    def _check(self, pin):
        """(right, wait). A try during a wait is not checked and not counted."""
        with self._lock:
            if self.clock() < self._until:
                return False, self.wait()
        right = verify(pin, self.stored())
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
            self.on_block(f"Wrong PIN ×{FAILS_PER_ROUND} on the display: locked for {seconds // 60} min")
        return False, seconds

    # --- tickets ---

    def _new_ticket(self):
        with self._lock:
            while len(self._tickets) >= MAX_TICKETS:
                self._tickets.pop(min(self._tickets, key=self._tickets.get))
            t = secrets.token_urlsafe(24)
            self._tickets[t] = self.clock()
            return t

    def unlock(self, pin):
        """(ticket, wait): a ticket for the right PIN, else None and the seconds to wait (0 = try again)."""
        if not self.is_on():
            return None, 0
        right, wait = self._check(pin)
        return (self._new_ticket(), 0) if right else (None, wait)

    def valid(self, ticket):
        """True for a live ticket, which this use keeps alive for another `idle` seconds."""
        if not isinstance(ticket, str) or not ticket:
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

    def allows(self, ticket):
        """A request that changes something: always with no PIN set, else only with a live ticket."""
        return not self.is_on() or self.valid(ticket)

    def close(self, ticket):
        with self._lock:
            self._tickets.pop(ticket or "", None)

    def set_pin(self, old, new):
        """(ok, ticket, error, wait). The first PIN needs no `old`; changing or removing (new=None)
        needs the current one. A change ends every other ticket; the one returned is the caller's."""
        if not self.settable():
            return False, None, "no LABHUD_DATA: set it with init.py pin --hash", 0
        if new is not None and (not isinstance(new, str) or not PIN.fullmatch(new)):
            return False, None, "the PIN must be exactly 4 digits", 0
        if self.is_on():
            right, wait = self._check(old)
            if not right:
                return False, None, "wrong PIN", wait
        elif new is None:
            return False, None, "no PIN is set", 0
        store.save(STORE_KEY, hash_pin(new) if new is not None else "")
        with self._lock:
            self._tickets.clear()
        self._seen = self.stored()
        return True, (self._new_ticket() if new is not None else None), None, 0

    def outside_change(self):
        """True once after the PIN was changed elsewhere (init.py pin, .env): every ticket ends."""
        now = self.stored()
        if now == self._seen:
            return False
        self._seen = now
        with self._lock:
            self._tickets.clear()
        return True
