# Pushed sources: an agent sends its JSON to labhud instead of waiting to be polled, so the host
# it runs on needs no open port at all.
#
#   LABHUD_PUSH_<NAME>_KEY     a secret shared with that agent only (one per agent)
#   LABHUD_PUSH_<NAME>_STALE   seconds without a push before the source counts as failing (60)
#
# The agent POSTs the JSON object to /api/push/<name> (lowercase) with two headers:
#   X-Labhud-Time        unix seconds, within 30 s of labhud's clock
#   X-Labhud-Signature   hex HMAC-SHA256, keyed with the secret, of "<time>.<name>." + the body
# Anything else gets 403. A key opens one source only, and a copied push is worthless after
# 30 seconds. The data is then "<name>.<key>", like any other source; see docs/live-agent.md.

import hashlib
import hmac
import json
import os
import re
import threading
import time

from ._common import PREFIX, env, source

_VAR = re.compile(rf"^{PREFIX}PUSH_([A-Z0-9_]+)_KEY$")
MAX_AGE = 30
MAX_BODY = 1 << 20

KEYS = {}        # source name -> key (bytes)
_STALE = {}      # source name -> seconds
_received = {}   # source name -> (unix time, data)
_lock = threading.Lock()


def verify(name, headers, body, now=None):
    """None if the push is genuine, else why not (for the log, never for the answer)."""
    key = KEYS.get(name)
    if not key:
        return "no such pushed source"
    ts, sig = headers.get("X-Labhud-Time", ""), headers.get("X-Labhud-Signature", "")
    if not ts.isdigit() or abs((now or time.time()) - int(ts)) > MAX_AGE:
        return "time missing or more than 30 s off (check the agent's clock)"
    expected = hmac.new(key, f"{ts}.{name}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), sig.encode("utf-8", "replace")):
        return "bad signature"
    return None


def receive(name, body):
    """Stores a verified push. Raises ValueError if the body is not a JSON object."""
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("the body must be a JSON object")
    with _lock:
        _received[name] = (time.time(), data)


def _make(name):
    def fetch():
        with _lock:
            got = _received.get(name)
        if not got:
            raise RuntimeError("no push received yet")
        age = time.time() - got[0]
        if age > _STALE[name]:
            raise RuntimeError(f"no push for {int(age)} s")
        return dict(got[1])
    return fetch


for _var, _value in sorted(os.environ.items()):
    _m = _VAR.match(_var)
    if not _m or not _value.strip():
        continue
    _name = _m.group(1).lower()
    KEYS[_name] = _value.strip().encode()
    _STALE[_name] = int(env(f"PUSH_{_m.group(1)}_STALE", "60"))
    # Checked every 2 s, never backed off: a push can arrive at any moment.
    source(_name, every=2, env=(f"PUSH_{_m.group(1)}_KEY",), backoff=False)(_make(_name))
