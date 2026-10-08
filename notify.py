"""Notifications: the history's bad news (events.py), sent to a webhook.

Off unless LABHUD_NOTIFY_URL is set. The message goes out in one of these shapes
(LABHUD_NOTIFY_FORMAT):

    ntfy     the text as the body, the title in a header (ntfy.sh or your own ntfy server)
    gotify   {"title", "message", "priority"} (put ?token=<app token> in the URL)
    discord  {"content"} (a Discord or Slack-compatible incoming webhook)
    json     {"title", "text", "bad", "time"} (anything else: n8n, Home Assistant, your script)

LABHUD_NOTIFY_AUTH, when set, is sent as the Authorization header ("Bearer <token>" for ntfy).
Only bad news is sent, unless LABHUD_NOTIFY_ALL=1 (then recoveries and starts too). A host that
goes down is reported only if it is still down LABHUD_NOTIFY_DELAY seconds later (default 120),
so a reboot or a lost ping says nothing.
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
FORMATS = ("ntfy", "gotify", "discord", "json")

_queue = queue.Queue(maxsize=200)
# For /status: what was last sent, and the last failure (never the URL: it may hold a token).
state = {"sent": 0, "last_sent": None, "last_error": None, "last_error_at": None}


def enabled():
    return bool(URL)


def problem():
    """A setting that cannot work, or None."""
    if URL and FORMAT not in FORMATS:
        return f"LABHUD_NOTIFY_FORMAT must be one of {', '.join(FORMATS)}, got {FORMAT!r}"
    return None


def submit(title, text, bad, still_true=None):
    """Queues one message. `still_true`, when given, is asked again after DELAY seconds and the
    message is dropped if it answers False (the host came back in the meantime)."""
    if not URL or problem() or not (bad or ALL):
        return
    due = time.monotonic() + (DELAY if still_true else 0)
    try:
        _queue.put_nowait((due, title, text, bad, still_true))
    except queue.Full:
        pass  # a flood of events: the history on the screen still has them


def body(title, text, bad, now=None):
    """(bytes, headers) for one message in the configured format."""
    if FORMAT == "ntfy":
        headers = {"Title": title, "Tags": "warning" if bad else "white_check_mark",
                   "Priority": "high" if bad else "default"}
        return text.encode(), headers
    if FORMAT == "gotify":
        payload = {"title": title, "message": text, "priority": 8 if bad else 4}
    elif FORMAT == "discord":
        payload = {"content": f"**{title}**: {text}"}
    else:
        payload = {"title": title, "text": text, "bad": bad, "time": int(now or time.time())}
    return json.dumps(payload, ensure_ascii=False).encode(), {"Content-Type": "application/json"}


def _send(title, text, bad):
    data, headers = body(title, text, bad)
    req = urllib.request.Request(URL, data=data, method="POST")
    for k, v in headers.items():
        # HTTP headers are latin-1: a title with other characters would fail the whole request
        req.add_header(k, v.encode("latin-1", "replace").decode("latin-1"))
    if AUTH:
        req.add_header("Authorization", AUTH)
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


def worker():
    """Sends the queue in order; a message whose delay has not run out waits its turn."""
    waiting = []
    while True:
        try:
            waiting.append(_queue.get(timeout=0.5 if waiting else 5))
        except queue.Empty:
            pass
        now = time.monotonic()
        ready = [m for m in waiting if m[0] <= now]
        waiting = [m for m in waiting if m[0] > now]
        for _, title, text, bad, still_true in ready:
            if still_true and not still_true():
                continue
            try:
                _send(title, text, bad)
                state.update(sent=state["sent"] + 1, last_sent=int(time.time()))
            except Exception as e:  # a notification service down must not stop anything else
                message = str(e).replace(URL, "<url>").replace(AUTH or "\0", "<auth>")
                state.update(last_error=message[:160] or type(e).__name__, last_error_at=int(time.time()))


def start():
    if URL and not problem():
        threading.Thread(target=worker, daemon=True, name="notify").start()
