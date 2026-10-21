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

Besides ntfy and the app there are four more channels, set in ⚙ > Notifications (api/notifications.py):

    email    [notify.email] host, port, security (starttls | ssl | none), user, from, to; the password
             is LABHUD_NOTIFY_SMTP_PASSWORD
    webhook  a POST of {title, text, bad, time, event, card} to LABHUD_NOTIFY_WEBHOOK_URL, with
             LABHUD_NOTIFY_WEBHOOK_AUTH as the Authorization header when set
    discord  a channel's webhook, LABHUD_NOTIFY_DISCORD_URL
    gotify   [notify.gotify] url (the server); the app token is LABHUD_NOTIFY_GOTIFY_TOKEN

The secrets are in secrets.env (or the environment), never in config.toml and never in an answer.
Which channel gets what is [notify.events]: {event: [channel, …]} for the events in EVENTS. Without
it every bad event goes where `via` says, and to every other channel that is set; updates and
admin sign-ins go nowhere until they are ticked.

A phone with a UnifiedPush endpoint (devices.py) is woken when a message enters the outbox and
`via` names the app: a POST with no news in it ({"labhud": "news"}), so neither the push server
nor Google (when the distributor uses it) learns what happened. The app then reads the outbox.
"""

import json
import os
import queue
import smtplib
import ssl
import threading
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

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
TITLE = "LabHUD"      # the config's title (server.py), for the news notify.py makes itself
MUTED = False         # the demo: its made-up news reaches only the app's outbox, no channel outside
                        # (server.py sets it when the demo starts; configure() clears it again)

# What happens, in ⚙'s order, and who may get it. An event outside the list is an "alert".
EVENTS = ("alert", "host_down", "container_stopped", "update_available", "update_done",
          "agent_offline", "admin_login", "backup_failed")
EVENT_TEXT = {"alert": "An alert", "host_down": "A host down", "container_stopped": "A container or guest stopped",
              "update_available": "An update available", "update_done": "An update done",
              "agent_offline": "An agent offline", "admin_login": "An admin sign-in", "backup_failed": "A backup failed"}
CHANNELS = ("app", "ntfy", "email", "webhook", "discord", "gotify")
# News even when it is not bad (the rest of the good news only with LABHUD_NOTIFY_ALL); and,
# without [notify.events], sent nowhere until ticked: they were never sent before.
ALWAYS = frozenset({"update_available", "update_done", "admin_login"})
# The secrets of each channel, by field: in secrets.env (managed.set_secrets) or the environment.
SECRETS = {"ntfy": {"url": "LABHUD_NOTIFY_URL", "auth": "LABHUD_NOTIFY_AUTH"},
           "email": {"password": "LABHUD_NOTIFY_SMTP_PASSWORD"},
           "webhook": {"url": "LABHUD_NOTIFY_WEBHOOK_URL", "auth": "LABHUD_NOTIFY_WEBHOOK_AUTH"},
           "discord": {"url": "LABHUD_NOTIFY_DISCORD_URL"},
           "gotify": {"token": "LABHUD_NOTIFY_GOTIFY_TOKEN"}}
EMAIL_KEYS = {"host", "port", "security", "user", "from", "to"}
EMAIL_SECURITY = {"starttls": 587, "ssl": 465, "none": 25}
GOTIFY_KEYS = {"url"}
CFG = {"email": None, "gotify": None, "events": None}   # [notify.email], [notify.gotify], [notify.events]

_queue = queue.Queue(maxsize=200)
_outbox = []          # [{id, time, title, text, bad, card}], oldest first
_outbox_lock = threading.Lock()
_held = []            # (title, text, bad, card, event) waiting for the quiet hours to end
# For /status: what was last sent, and the last failure (never the URL: it may hold a token).
state = {"sent": 0, "last_sent": None, "last_error": None, "last_error_at": None}
# The same for every other channel
stats = {ch: {"sent": 0, "last_sent": None, "last_error": None, "last_error_at": None} for ch in CHANNELS[2:]}


def enabled():
    return bool(URL)


def configure(notify_cfg, title=None):
    """[notify] from the topology (config.py), and the config's title."""
    global VIA, QUIET, TITLE, MUTED
    VIA = notify_cfg.get("via", "both")
    QUIET = notify_cfg.get("quiet")
    for k in CFG:
        CFG[k] = notify_cfg.get(k)
    if title:
        TITLE = title
    MUTED = False   # the demo's own news is muted (server.py), but a real config never is


# --- the config: [notify.email], [notify.gotify], [notify.events] (config.py calls check()) ---

def check(ck, nt):
    """{email, gotify, events} from the [notify] table `nt`, each only when it is there; a problem
    goes to the config checker `ck` (config._Checker)."""
    out = {"email": None, "gotify": None, "events": None}
    em = nt.get("email")
    if em is not None:
        if not isinstance(em, dict):
            ck.err("notify", "email must be written as a [notify.email] table")
        else:
            out["email"] = _check_email(ck, em)
    go = nt.get("gotify")
    if go is not None:
        if not isinstance(go, dict):
            ck.err("notify", "gotify must be written as a [notify.gotify] table")
        else:
            ck.unknown("notify.gotify", go, GOTIFY_KEYS)
            url = ck.need("notify.gotify", go, "url", str, "the Gotify server's address (https://…)")
            if url is not None and not url.startswith(("http://", "https://")):
                ck.err("notify.gotify", "url must start with http:// or https://")
            elif url:
                out["gotify"] = {"url": url.rstrip("/")}
    ev = nt.get("events")
    if ev is not None:
        if not isinstance(ev, dict):
            ck.err("notify", "events must be written as a [notify.events] table")
        else:
            ck.unknown("notify.events", ev, set(EVENTS))
            matrix = {}
            for name in EVENTS:
                chans = ev.get(name, [])
                if not isinstance(chans, list) or not all(isinstance(c, str) for c in chans):
                    ck.err("notify.events", f"'{name}' must be a list of channels")
                    continue
                wrong = [c for c in chans if c not in CHANNELS]
                if wrong:
                    ck.err("notify.events", f"'{name}': no channel {wrong[0]!r} (one of {', '.join(CHANNELS)})")
                matrix[name] = [c for c in CHANNELS if c in chans]
            out["events"] = matrix
    return {k: v for k, v in out.items() if v is not None}


def _check_email(ck, em):
    w = "notify.email"
    ck.unknown(w, em, EMAIL_KEYS)
    host = ck.need(w, em, "host", str, "the SMTP server's name")
    security = ck.opt(w, em, "security", str, "starttls, ssl or none") or "starttls"
    if security not in EMAIL_SECURITY:
        ck.err(w, f"security must be starttls, ssl or none, got {security!r}")
        return None
    port = ck.opt(w, em, "port", int, "a port number")
    if port is not None and not 0 < port < 65536:
        ck.err(w, f"port must be from 1 to 65535, got {port}")
    user = ck.opt(w, em, "user", str, "a user name") or ""
    sender = ck.opt(w, em, "from", str, "an address") or user
    to = em.get("to")
    to = [to] if isinstance(to, str) else to
    if not isinstance(to, list) or not to or not all(isinstance(a, str) and _address(a) for a in to):
        ck.err(w, "'to' must be an address or a list of addresses")
        return None
    if not _address(sender):
        ck.err(w, "'from' must be an address (or 'user' one)")
        return None
    if not host:
        return None
    return {"host": host, "port": port or EMAIL_SECURITY[security], "security": security,
            "user": user, "from": sender, "to": to}


def _address(a):
    name, at, domain = a.rpartition("@")
    return bool(at and name and "." in domain and not any(c in a for c in " \r\n,<>"))


# --- which channel gets what ---

def secret(channel, field, env=None):
    name = SECRETS.get(channel, {}).get(field)
    if not name:
        return ""
    if env is not None and env.get(field):
        return env[field]
    return os.environ.get(name, "")


def channel_problem(channel, cfg=None, env=None):
    """Why `channel` cannot send now ("not set" when it is simply not set up), or None. `cfg` and
    `env` (its fields: password, url, auth, token) stand in for what is saved: the Test button."""
    if channel == "app":
        return None
    if channel == "ntfy":
        return "not set" if not URL else problem()
    if channel == "email":
        em = cfg if cfg is not None else CFG.get("email")
        if not em or not em.get("host") or not em.get("to"):
            return "not set"
        if em.get("security") == "none" and secret("email", "password", env):
            return "a password needs starttls or ssl: it would cross the network as it is"
        return None
    if channel in ("webhook", "discord"):
        url = secret(channel, "url", env)
        if not url:
            return "not set"
        if not url.startswith(("http://", "https://")):
            return "the address must start with http:// or https://"
        return None
    if channel == "gotify":
        go = cfg if cfg is not None else CFG.get("gotify")
        return None if go and go.get("url") and secret("gotify", "token", env) else "not set"
    return "no such channel"


def channels_set():
    return [ch for ch in CHANNELS if channel_problem(ch) is None]


def matrix():
    """{event: [channel, …]}: [notify.events], or what it means without it (see the top)."""
    if CFG.get("events") is not None:
        return {ev: list(CFG["events"].get(ev, [])) for ev in EVENTS}
    base = [ch for ch, on in (("app", VIA in ("app", "both")), ("ntfy", VIA in ("ntfy", "both"))) if on]
    extra = [ch for ch in CHANNELS[2:] if channel_problem(ch) is None]
    return {ev: [] if ev in ALWAYS else base + extra for ev in EVENTS}


def route(event):
    """The channels set up that get `event` now."""
    chans = matrix().get(event if event in EVENTS else "alert", [])
    return [ch for ch in chans if channel_problem(ch) is None and (ch == "app" or not MUTED)]


def effective_via():
    """`via` as the app reads it (/api/v1/notifications): whether any event goes to the app."""
    every = {ch for chans in matrix().values() for ch in chans}
    return "both" if {"app", "ntfy"} <= every else "app" if "app" in every else "ntfy"


def apply_via(events, via):
    """[notify.events] after ⚙ > Alerts' (or the app's) "send through ntfy / the app / both": every
    event that went to ntfy or the app goes to `via`'s now; the others stay as they are."""
    want = [ch for ch, on in (("app", via in ("app", "both")), ("ntfy", via in ("ntfy", "both"))) if on]
    out = {}
    for ev, chans in events.items():
        if "app" in chans or "ntfy" in chans:
            rest = [c for c in chans if c not in ("app", "ntfy")]
            out[ev] = [c for c in CHANNELS if c in want or c in rest]
        else:
            out[ev] = list(chans)
    return out


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
    """A setting of the ntfy webhook that cannot work, or None."""
    if URL and FORMAT not in FORMATS:
        return f"LABHUD_NOTIFY_FORMAT must be one of {', '.join(FORMATS)}, got {FORMAT!r}"
    if URL and CLICK not in ("", "app"):
        return f"LABHUD_NOTIFY_CLICK can only be app, got {CLICK!r}"
    return None


def submit(title, text, bad, still_true=None, card=None, urgent=True, delay=None, event=None):
    """Queues one message about `event` (one of EVENTS; "alert" when not given). `still_true`,
    when given, is asked again after `delay` seconds (DELAY by default) and the message is dropped
    if it answers False (the host came back meanwhile). One that is not `urgent` waits for the end
    of the quiet hours."""
    event = event if event in EVENTS else "alert"
    if not (bad or ALL or event in ALWAYS) or not route(event):
        return
    due = time.monotonic() + ((DELAY if delay is None else delay) if still_true else 0)
    try:
        _queue.put_nowait((due, title, text, bad, still_true, card, urgent, event))
    except queue.Full:
        pass  # a flood of events: the history on the screen still has them


# --- the channels ---

def body(title, text, bad, now=None, card=None):
    """(bytes, headers) for one message in the configured format (the ntfy webhook)."""
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


def _post(url, data, headers, timeout=10):
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        # HTTP headers are latin-1: a title with other characters would fail the whole request
        req.add_header(k, v.encode("latin-1", "replace").decode("latin-1"))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        r.read()


def _send(title, text, bad, card=None):
    data, headers = body(title, text, bad, card=card)
    if AUTH:
        headers["Authorization"] = AUTH
    _post(URL, data, headers)


def _json(payload):
    return json.dumps(payload, ensure_ascii=False).encode()


def tls_context():
    """The certificates SMTP over TLS checks against (the system's; a test swaps it)."""
    return ssl.create_default_context()


def send_email(title, text, bad, cfg, env=None):
    password = secret("email", "password", env)
    msg = EmailMessage()
    first = (text.splitlines() or [""])[0]
    msg["Subject"] = f"{'⚠ ' if bad else ''}{title}: {first[:140]}"
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid("labhud")
    msg.set_content(text + "\n\n-- \nLabHUD\n")
    if cfg["security"] == "ssl":
        s = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15, context=tls_context())
    else:
        s = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
    try:
        if cfg["security"] == "starttls":
            s.starttls(context=tls_context())
        if cfg.get("user") and password:
            s.login(cfg["user"], password)
        s.send_message(msg)
    finally:
        try:
            s.quit()
        except (smtplib.SMTPException, OSError):
            s.close()


def send_to(channel, title, text, bad, card=None, event="alert", now=None, cfg=None, env=None):
    """One message through one channel; raises what went wrong. `cfg`/`env` as channel_problem's."""
    now = int(now or time.time())
    if channel == "ntfy":
        return _send(title, text, bad, card)
    if channel == "email":
        return send_email(title, text, bad, cfg if cfg is not None else CFG["email"], env)
    if channel == "webhook":
        headers = {"Content-Type": "application/json"}
        auth = secret("webhook", "auth", env)
        if auth:
            headers["Authorization"] = auth
        payload = {"title": title, "text": text, "bad": bad, "time": now, "event": event, "card": card}
        return _post(secret("webhook", "url", env), _json(payload), headers)
    if channel == "discord":
        # no @everyone or @here from a container's name: Discord parses no mention at all
        payload = {"username": "LabHUD", "content": f"{'⚠ ' if bad else ''}**{title}**: {text}"[:2000],
                   "allowed_mentions": {"parse": []}}
        return _post(secret("discord", "url", env), _json(payload), {"Content-Type": "application/json"})
    if channel == "gotify":
        go = cfg if cfg is not None else CFG["gotify"]
        # the token in a header, not in the address: an address ends up in logs
        payload = {"title": title, "message": text, "priority": 8 if bad else 4}
        return _post(go["url"].rstrip("/") + "/message", _json(payload),
                     {"Content-Type": "application/json", "X-Gotify-Key": secret("gotify", "token", env)})
    raise ValueError(f"no channel {channel}")


def scrub(message, channel, env=None):
    """An error's text without the channel's secrets (an address may hold a token)."""
    for field in SECRETS.get(channel, {}):
        value = secret(channel, field, env)
        if value:
            message = message.replace(value, f"<{field}>")
    return message


def _error(e, channel, env=None):
    return scrub(str(e), channel, env)[:160] or type(e).__name__


def deliver(title, text, bad, card=None, now=None, event="alert", channels=None):
    """To every channel that gets `event` (or `channels`): the app's outbox (and its wake-up
    call), ntfy, email, the webhooks."""
    now = now or time.time()
    chans = route(event) if channels is None else channels
    if "app" in chans:
        with _outbox_lock:
            mid = max(int(now * 1000), _outbox[-1]["id"] + 1 if _outbox else 0)
            _outbox.append({"id": mid, "time": int(now), "title": title, "text": text, "bad": bad, "card": card})
            del _outbox[:-OUTBOX_SIZE]
        threading.Thread(target=wake_phones, daemon=True, name="push").start()
    for ch in chans:
        if ch == "app":
            continue
        st = state if ch == "ntfy" else stats[ch]
        try:
            send_to(ch, title, text, bad, card, event, now)
            st.update(sent=st["sent"] + 1, last_sent=int(time.time()))
        except Exception as e:  # a notification service down must not stop anything else
            message = _error(e, ch).replace(URL or "\0", "<url>").replace(AUTH or "\0", "<auth>")
            st.update(last_error=message, last_error_at=int(time.time()))


WAKE = b'{"labhud":"news"}'


def wake_phones():
    """One content-free POST to every phone's UnifiedPush endpoint; one its server says is gone
    (404, 410) is forgotten. Never raises: a push server down only delays the phone."""
    import devices
    for endpoint in devices.push_endpoints():
        req = urllib.request.Request(endpoint, data=WAKE, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("TTL", "86400")
        req.add_header("Urgency", "high")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                devices.drop_push(endpoint)
        except Exception:
            pass


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


def test_channel(channel, cfg=None, env=None):
    """One test message through email, webhook, discord or gotify, with `cfg`/`env` standing in
    for what is saved. None, or what went wrong (never a secret)."""
    if channel in ("app", "ntfy") or channel not in CHANNELS:
        return "test ntfy with its own button; the app with ⚙ > Phones"
    why = channel_problem(channel, cfg, env)
    if why:
        return "nothing to test: " + why if why == "not set" else why
    try:
        send_to(channel, TITLE, "Test: notifications from LabHUD reach you.", False, cfg=cfg, env=env)
        return None
    except Exception as e:
        return _error(e, channel, env)


def where():
    """The webhook's host name, for ⚙ (never the whole address: a topic or a token may be in it)."""
    return host_of(URL)


def host_of(url):
    import urllib.parse
    try:
        return urllib.parse.urlsplit(url).hostname or "" if url else ""
    except ValueError:
        return ""


def summary(held):
    """One message for what the quiet hours held: (title, text, bad, card)."""
    if len(held) == 1:
        return tuple(held[0][:4])
    lines = [f"{'✕' if h[2] else '✓'} {h[1]}" for h in held[-20:]]
    more = f"\n…and {len(held) - 20} more" if len(held) > 20 else ""
    return (held[0][0], f"{len(held)} while quiet:\n" + "\n".join(lines) + more,
            any(h[2] for h in held), None)


def step(waiting, now_mono, now=None):
    """One turn of the worker: delivers what is due, holds what the quiet hours hold, sends the
    summary when they are over. Returns what is still waiting."""
    ready = [m for m in waiting if m[0] <= now_mono]
    waiting = [m for m in waiting if m[0] > now_mono]
    quiet = quiet_now(now)
    for m in ready:
        _, title, text, bad, still_true, card, urgent = m[:7]
        event = m[7] if len(m) > 7 else "alert"
        if still_true and not still_true():
            continue
        if quiet and not urgent:
            _held.append((title, text, bad, card, event))
            del _held[:-200]
            continue
        deliver(title, text, bad, card, now, event)
    if _held and not quiet:
        held = list(_held)
        _held.clear()
        chans = [ch for ch in CHANNELS if any(ch in route(h[4] if len(h) > 4 else "alert") for h in held)]
        deliver(*summary(held), now, channels=chans)
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


# --- news notify.py finds itself: admin sign-ins (admin.LOGIN_HOOKS), new updates ---

HOW = {"password": "password", "phone": "the app", "pair": "pairing", "oidc": "single sign-on"}
FAILED_EVERY = 60     # at most one message a minute about failed sign-ins; the next says how many
_failed = {"at": 0.0, "skipped": 0}


def on_login(user, how, ok, where="", what=""):
    """admin.LOGIN_HOOKS: every admin sign-in, and every failed one (a minute apart at most)."""
    by = HOW.get(how, how)
    place = f" from {where}" if where else ""
    if ok:
        on = f" on the phone {what}" if how == "phone" and what else ""
        return submit(TITLE, f"Admin {user or '?'} signed in with {by}{on}{place}", False, event="admin_login")
    now = time.monotonic()
    if now - _failed["at"] < FAILED_EVERY:
        _failed["skipped"] += 1
        return
    more = f" (and {_failed['skipped']} more since the last message)" if _failed["skipped"] else ""
    _failed.update(at=now, skipped=0)
    submit(TITLE, f"Failed admin sign-in with {by}{place}" + (f" as {user}" if user else "") + more, True,
           event="admin_login")


UPDATES_KEY = "notify_updates_seen"
UPDATES_FORGET = 7 * 86400   # an update gone this long is news again when it comes back
_updates = {"seen": None}


def new_updates(rows, now=None):
    """The rows of fleet.updates (server.fleet_updates) not seen before, for one message. Kept in
    the store, so a restart (or an agent that reconnects and checks again) says nothing twice; the
    first time ever, what is there is taken as known."""
    import store
    now = int(now or time.time())
    seen = _updates["seen"]
    if seen is None:
        kept = store.load(UPDATES_KEY)
        seen = kept if isinstance(kept, dict) else None
    current = {f"{r.get('host')}/{r.get('name')}/{r.get('image', '')}": r for r in rows if isinstance(r, dict)}
    first = seen is None
    seen = dict(seen or {})
    fresh = [] if first else [r for k, r in current.items() if k not in seen and not r.get("updating")]
    changed = first
    for k in list(seen):
        if now - seen[k] >= UPDATES_FORGET:
            del seen[k]
            changed = True
    for k in current:   # still there: its time is refreshed (an hour apart, so the store is not written each time)
        if k not in seen or now - seen[k] > 3600:
            seen[k] = now
            changed = True
    _updates["seen"] = seen
    if changed:
        store.save(UPDATES_KEY, seen)
    return fresh


def updates_text(rows):
    if len(rows) == 1:
        return f"Update available: {rows[0].get('name')} on {rows[0].get('host')}"
    names = ", ".join(f"{r.get('name')} ({r.get('host')})" for r in rows[:8])
    return f"{len(rows)} updates available: {names}" + ("…" if len(rows) > 8 else "")


def start():
    import admin
    if on_login not in admin.LOGIN_HOOKS:
        admin.LOGIN_HOOKS.append(on_login)
    threading.Thread(target=worker, daemon=True, name="notify").start()
