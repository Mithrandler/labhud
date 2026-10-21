"""⚙ > Notifications: the channels (notify.py) and which one gets what.

    POST /api/notify/channels  every channel: set or not, what is not secret, sent / last error;
                               the events and the matrix
    POST /api/notify/save      {channel, config?, secrets?, forget?}: email, webhook, discord, gotify
    POST /api/notify/test      {channel, config?, secrets?}: one message now, with what is typed over
                               what is saved
    POST /api/notify/events    {events: {event: [channel, …]}}: [notify.events]

A secret (SMTP password, webhook address, Discord address, Gotify token) goes to secrets.env and
into this process's environment, so it works at once, without a restart; it never comes back in an
answer (only whether it is set, and a webhook's host name). The rest goes to [notify.<channel>] in
the kept config (managed.save_config: checked first, applied without a restart). ntfy keeps its own
card (⚙ > Notifications, alerts.js) and the app needs nothing.
"""

import os

import config
import managed
import notify
from routes import route

EDITABLE = ("email", "webhook", "discord", "gotify")
CONFIG_OF = {"email": notify.EMAIL_KEYS, "gotify": notify.GOTIFY_KEYS}


def _stats(ch):
    st = notify.state if ch == "ntfy" else notify.stats.get(ch, {})
    return {k: st.get(k) for k in ("sent", "last_sent", "last_error", "last_error_at")}


def view():
    def problem(ch):
        p = notify.channel_problem(ch)
        return None if p in (None, "not set") else p
    em, go = notify.CFG.get("email") or {}, notify.CFG.get("gotify") or {}
    chans = {
        "app": {"set": True},
        "ntfy": {"set": notify.enabled(), "host": notify.where(), "problem": notify.problem(), **_stats("ntfy")},
        "email": {"set": notify.channel_problem("email") is None, "problem": problem("email"),
                  **{k: em.get(k) for k in ("host", "port", "security", "user", "from", "to")},
                  "password": bool(notify.secret("email", "password")), **_stats("email")},
        "webhook": {"set": notify.channel_problem("webhook") is None, "problem": problem("webhook"),
                    "host": notify.host_of(notify.secret("webhook", "url")),
                    "auth": bool(notify.secret("webhook", "auth")), **_stats("webhook")},
        "discord": {"set": notify.channel_problem("discord") is None, "problem": problem("discord"),
                    "host": notify.host_of(notify.secret("discord", "url")), **_stats("discord")},
        "gotify": {"set": notify.channel_problem("gotify") is None, "problem": problem("gotify"),
                   "url": go.get("url"), "token": bool(notify.secret("gotify", "token")), **_stats("gotify")},
    }
    return {"ok": True, "managed": managed.active(), "channels": chans, "order": list(notify.CHANNELS),
            "events": [[e, notify.EVENT_TEXT[e]] for e in notify.EVENTS], "matrix": notify.matrix(),
            "explicit": notify.CFG.get("events") is not None, "always": sorted(notify.ALWAYS)}


def _config_part(channel, given):
    """The channel's table from what ⚙ sent, checked like config.toml's; ValueError if wrong."""
    if channel not in CONFIG_OF:
        return None
    if not isinstance(given, dict):
        raise ValueError("config: the channel's settings")
    table = {k: v for k, v in given.items() if k in CONFIG_OF[channel] and v not in (None, "")}
    if channel == "email" and isinstance(table.get("to"), str):
        table["to"] = [a.strip() for a in table["to"].split(",") if a.strip()]
    ck = config._Checker()
    out = notify.check(ck, {channel: table})
    if ck.problems:
        raise ValueError("; ".join(ck.problems))
    return table, out.get(channel)


def _secrets_part(channel, given):
    """{field: value} typed (empty = keep what is saved). ValueError for a field it does not have."""
    if given is None:
        return {}
    if not isinstance(given, dict):
        raise ValueError("secrets: {field: value}")
    out = {}
    for field, value in given.items():
        if field not in notify.SECRETS.get(channel, {}):
            raise ValueError(f"{channel} has no secret {field!r}")
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ValueError(f"{field}: one line of text")
        if value.strip():
            out[field] = value.strip()
    if out.get("url") and not out["url"].startswith(("http://", "https://")):
        raise ValueError("the address must start with http:// or https://")
    return out


def _set_env(channel, values):
    """Into secrets.env, and into the environment now (notify reads it at each message)."""
    names = {notify.SECRETS[channel][f]: v for f, v in values.items()}
    managed.set_secrets(names)
    for name, value in names.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def _answer(fn):
    def wrapped(h, body):
        try:
            return fn(h, body)
        except ValueError as e:
            return 409, {"ok": False, "error": str(e)[:400]}
    wrapped.__name__ = fn.__name__
    return wrapped


@route("POST", "/api/notify/channels")
def channels(h, body):
    return 200, view()


@route("POST", "/api/notify/save")
@_answer
def save(h, body):
    channel = body.get("channel")
    if channel not in EDITABLE:
        return 400, {"ok": False, "error": f"channel: one of {', '.join(EDITABLE)}"}
    if body.get("forget") is True:
        if channel in CONFIG_OF and managed.active():
            raw = managed.read_config()
            nt = dict(raw.get("notify") or {})
            nt.pop(channel, None)
            if isinstance(nt.get("events"), dict):
                nt["events"] = {e: [c for c in chans if c != channel] for e, chans in nt["events"].items()}
            raw["notify"] = nt
            managed.save_config(raw)
            notify.CFG[channel] = None
        _set_env(channel, {f: "" for f in notify.SECRETS[channel]})
        print(f"notify: {channel} forgotten from {h._who()}", flush=True)
        return 200, {"ok": True}
    secrets = _secrets_part(channel, body.get("secrets"))
    if channel in CONFIG_OF:
        table, parsed = _config_part(channel, body.get("config"))
        raw = managed.read_config()   # ValueError before a take over: the 409
        raw["notify"] = dict(raw.get("notify") or {}, **{channel: table})
        managed.save_config(raw)
        notify.CFG[channel] = parsed   # at once; the config's reload says the same a moment later
    elif not secrets:
        return 400, {"ok": False, "error": "nothing typed"}
    if secrets:
        _set_env(channel, secrets)
    print(f"notify: {channel} saved from {h._who()}" + (f" (secrets: {', '.join(sorted(secrets))})" if secrets else ""), flush=True)
    return 200, {"ok": True}


@route("POST", "/api/notify/test")
@_answer
def test(h, body):
    channel = body.get("channel")
    if channel not in EDITABLE:
        return 400, {"ok": False, "error": f"channel: one of {', '.join(EDITABLE)}"}
    env = _secrets_part(channel, body.get("secrets"))
    cfg = None
    if channel in CONFIG_OF and isinstance(body.get("config"), dict) and any(body["config"].values()):
        _, cfg = _config_part(channel, body["config"])
    error = notify.test_channel(channel, cfg, env)
    print(f"notify: test of {channel} from {h._who()}: {error or 'sent'}", flush=True)
    return 200, {"ok": error is None, "error": error}


@route("POST", "/api/notify/events")
@_answer
def events(h, body):
    given = body.get("events")
    if not isinstance(given, dict):
        return 400, {"ok": False, "error": "events: {event: [channel, …]}"}
    ck = config._Checker()
    matrix = notify.check(ck, {"events": given}).get("events")
    if ck.problems or matrix is None:
        return 400, {"ok": False, "error": "; ".join(ck.problems)[:400] or "events: {event: [channel, …]}"}
    raw = managed.read_config()
    nt = dict(raw.get("notify") or {}, events=matrix)
    every = {c for chans in matrix.values() for c in chans}
    if every & {"app", "ntfy"}:   # `via` follows, for ⚙ > Alerts and older apps
        nt["via"] = "both" if {"app", "ntfy"} <= every else "app" if "app" in every else "ntfy"
    raw["notify"] = nt
    managed.save_config(raw)
    notify.CFG["events"] = matrix
    print(f"notify: events saved from {h._who()}", flush=True)
    return 200, {"ok": True, "matrix": matrix}
