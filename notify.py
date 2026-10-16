"""Notifications: the history's bad news (events.py), sent to a webhook.

Off unless LABHUD_NOTIFY_URL is set. The message goes out in one of these shapes
(LABHUD_NOTIFY_FORMAT):

    ntfy     the text as the body, the title in a header (ntfy.sh or your own ntfy server)
    gotify   {"title", "message", "priority"} (put ?token=<app token> in the URL)
    discord  {"content"} (a Discord or Slack-compatible incoming webhook)
    json     {"title", "text", "bad", "time"} (anything else: n8n, Home Assistant, your script)

LABHUD_NOTIFY_AUTH, when set, is sent as the Authorization header ("Bearer <token>" for ntfy).
LABHUD_NOTIFY_CLICK=app (ntfy only) makes a tap on the notification open the LabHUD app on the
card it is about (labhud://card/<id>), or on its home screen for news about no card.
Only bad news is sent, unless LABHUD_NOTIFY_ALL=1 (then recoveries and starts too). A host that
goes down is reported only if it is still down LABHUD_NOTIFY_DELAY seconds later (default 120),
so a reboot or a lost ping says nothing.

Where the news goes is [notify] in config.toml (configure()): `via` = ntfy (the webhook above), app
(the LabHUD app reads the outbox, /api/v1/notifications, and shows it itself) or both. In the
quiet hours a message that is not urgent (cpu and mem rules, alerting.py) is held; when they end,
what was held goes out as one message. The outbox keeps the last messages, for the app, whatever
`via` says: the app shows them only when `via` names it.
"""

import json
import os
import queue
import threading
import time
import urllib.request

URL = os.environ.get("LABHUD_NOTIFY_URL", "")
FORMAT = os.environ.get("LABHUD_NOTIFY_FORMAT", "json").lower()
AUTH = os.environ.get("LABHUD_NOTIFY_AUTH", "")
ALL = os.environ.get("LABHUD_NOTIFY_ALL", "").lower() in ("1", "true", "yes")
DELAY = int(os.environ.get("LABHUD_NOTIFY_DELAY", "120"))
CLICK = os.environ.get("LABHUD_NOTIFY_CLICK", "").lower()
FORMATS = ("ntfy", "gotify", "discord", "json")

VIA = "both"
QUIET = (23, 7)       # (from, to) hours in labhud's local time, or None
OUTBOX_SIZE = 100

_queue = queue.Queue(maxsize=200)
_outbox = []          # [{id, time, title, text, bad, card}], oldest first
_outbox_lock = threading.Lock()
_held = []            # (title, text, bad, card) waiting for the quiet hours to end
# For /status: what was last sent, and the last failure (never the URL: it may hold a token).
state = {"sent": 0, "last_sent": None, "last_error": None, "last_error_at": None}


def enabled():
    return bool(URL)


def configure(notify_cfg):
    """[notify] from the topology (config.py)."""
    global VIA, QUIET
    VIA = notify_cfg.get("via", "both")
    QUIET = notify_cfg.get("quiet")


def to_ntfy():
    return bool(URL) and not problem() and VIA in ("ntfy", "both")


def quiet_now(now=None):
    if not QUIET:
        return False
    h = time.localtime(now).tm_hour
    a, b = QUIET
    return a <= h < b if a < b else (h >= a or h < b)


def outbox(after=0):
    """The messages after id `after` (milliseconds since 1970, rising), oldest first."""
    with _outbox_lock:
        return [m for m in _outbox if m["id"] > after]


def problem():
    """A setting that cannot work, or None."""
    if URL and FORMAT not in FORMATS:
        return f"LABHUD_NOTIFY_FORMAT must be one of {', '.join(FORMATS)}, got {FORMAT!r}"
    if URL and CLICK not in ("", "app"):
        return f"LABHUD_NOTIFY_CLICK can only be app, got {CLICK!r}"
    return None


def submit(title, text, bad, still_true=None, card=None, urgent=True, delay=None):
    """Queues one message. `still_true`, when given, is asked again after `delay` seconds (DELAY
    by default) and the message is dropped if it answers False (the host came back meanwhile).
    One that is not `urgent` waits for the end of the quiet hours."""
    if problem() or not (bad or ALL):
        return
    due = time.monotonic() + ((DELAY if delay is None else delay) if still_true else 0)
    try:
        _queue.put_nowait((due, title, text, bad, still_true, card, urgent))
    except queue.Full:
        pass  # a flood of events: the history on the screen still has them


def body(title, text, bad, now=None, card=None):
    """(bytes, headers) for one message in the configured format."""
    if FORMAT == "ntfy":
        headers = {"Title": title, "Tags": "warning" if bad else "white_check_mark",
                   "Priority": "high" if bad else "default"}
        if CLICK == "app":
            headers["Click"] = f"labhud://card/{card}" if card else "labhud://open"
        return text.encode(), headers
    if FORMAT == "gotify":
        payload = {"title": title, "message": text, "priority": 8 if bad else 4}
    elif FORMAT == "discord":
        payload = {"content": f"**{title}**: {text}"}
    else:
        payload = {"title": title, "text": text, "bad": bad, "time": int(now or time.time())}
    return json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type": "application/json"}


def _send(title, text, bad, card=None):
    data, headers = body(title, text, bad, card=card)
    req = urllib.request.Request(URL, data=data, method="POST")
    for k, v in headers.items():
        # HTTP headers are latin-1: a title with other characters would fail the whole request
        req.add_header(k, v.encode("latin-1", "replace").decode("latin-1"))
    if AUTH:
        req.add_header("Authorization", AUTH)
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def deliver(title, text, bad, card=None, now=None):
    """Into the outbox (the app) and, if `via` says so, to the webhook."""
    now = now or time.time()
    with _outbox_lock:
        mid = max(int(now * 1000), _outbox[-1]["id"] + 1 if _outbox else 0)
        _outbox.append({"id": mid, "time": int(now), "title": title, "text": text, "bad": bad, "card": card})
        del _outbox[:-OUTBOX_SIZE]
    if not to_ntfy():
        return
    try:
        _send(title, text, bad, card)
        state.update(sent=state["sent"] + 1, last_sent=int(time.time()))
    except Exception as e:  # a notification service down must not stop anything else
        message = str(e).replace(URL or "\0", "<url>").replace(AUTH or "\0", "<auth>")
        state.update(last_error=message[:160] or type(e).__name__, last_error_at=int(time.time()))


def test(url=None, auth=None, fmt=None):
    """Sends one test message now, with the given webhook or the one set. Returns None, or what
    went wrong (without the URL or the token, which may hold a secret)."""
    global URL, AUTH, FORMAT
    saved = URL, AUTH, FORMAT
    URL, AUTH, FORMAT = url or URL, auth if auth is not None else AUTH, fmt or FORMAT
    try:
        if not URL:
            return "no address set"
        if problem():
            return problem()
        _send("LabHUD", "Test: notifications from LabHUD reach you.", False)
        return None
    except Exception as e:
        return (str(e).replace(URL, "<url>").replace(AUTH or "\0", "<auth>")[:160] or type(e).__name__)
    finally:
        URL, AUTH, FORMAT = saved


def where():
    """The webhook's host name, for ⚙ (never the whole address: a topic or a token may be in it)."""
    import urllib.parse
    return urllib.parse.urlsplit(URL).hostname or "" if URL else ""


def summary(held):
    """One message for what the quiet hours held: (title, text, bad, card)."""
    if len(held) == 1:
        return held[0]
    lines = [f"{'✕' if bad else '✓'} {text}" for _, text, bad, _ in held[-20:]]
    more = f"\n…and {len(held) - 20} more" if len(held) > 20 else ""
    return (held[0][0], f"{len(held)} while quiet:\n" + "\n".join(lines) + more,
            any(bad for _, _, bad, _ in held), None)


def step(waiting, now_mono, now=None):
    """One turn of the worker: delivers what is due, holds what the quiet hours hold, sends the
    summary when they are over. Returns what is still waiting."""
    ready = [m for m in waiting if m[0] <= now_mono]
    waiting = [m for m in waiting if m[0] > now_mono]
    quiet = quiet_now(now)
    for _, title, text, bad, still_true, card, urgent in ready:
        if still_true and not still_true():
            continue
        if quiet and not urgent:
            _held.append((title, text, bad, card))
            del _held[:-200]
            continue
        deliver(title, text, bad, card, now)
    if _held and not quiet:
        held = list(_held)
        _held.clear()
        deliver(*summary(held), now)
    return waiting


def worker():
    """Sends the queue in order; a message whose delay has not run out waits its turn."""
    waiting = []
    while True:
        try:
            waiting.append(_queue.get(timeout=0.5 if waiting else 5))
        except queue.Empty:
            pass
        waiting = step(waiting, time.monotonic())


def start():
    if not problem():
        threading.Thread(target=worker, daemon=True, name="notify").start()
