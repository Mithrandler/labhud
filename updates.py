"""Is there a newer LabHUD? Every six hours the latest release of labhud, its agent and its Android app
is read from GitHub (`releases/latest`, nothing sent but the request), and ⚙ > System > About says
"Update available" when the server itself is behind. The same news goes out once per version as
`update_available` (notify.EVENTS), to the channels ticked for it.

    [update]
    check = true      # false: LabHUD never asks GitHub

The answer of /api/brand/about carries `latest` ({server, agent, app}: the newest tags, or ""),
`update` (the server is behind), `outdated` ({agent, app}: what runs is behind; the agent is the one
this server pins in agents/agent-release.json, the app the one that asks, X-Labhud-App) and `checked`.
"""

import json
import os
import re
import threading
import time

REPOS = {"server": "Mithrandler/labhud", "agent": "Mithrandler/labhud-agent", "app": "Mithrandler/labhud-android"}
EVERY = 6 * 3600
NOTIFIED_KEY = "update_notified"
_SEMVER = re.compile(r"^v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:-([0-9A-Za-z.-]{1,32}))?(?:\+.*)?$")

CHECK = True                 # [update] check, set by configure()
_lock = threading.Lock()
_state = {"latest": {k: "" for k in REPOS}, "checked": 0, "error": ""}


def configure(cfg):
    global CHECK
    CHECK = bool((cfg or {}).get("check", True))


def parse(tag):
    """(major, minor, patch, final) of "v0.1.2" / "0.1.2-rc1"; None for anything else ("dev", "latest")."""
    m = _SEMVER.match(str(tag or "").strip())
    return (int(m[1]), int(m[2]), int(m[3]), 0 if m[4] else 1) if m else None


def _in_order(v):
    """A parsed version with its patch in release order. The series wraps instead of growing a digit (0.1.19, 0.1.2, 0.1.21, ...
    0.1.29, 0.1.3), so a patch of one digit counts as tens, as in the app's versionCode: 0.1.2 comes after 0.1.19."""
    major, minor, patch, final = v
    return (major, minor, patch * 10 if 0 < patch < 10 else patch, final)


def newer(latest, current):
    """True if `latest` is a later version than `current`. A version nobody can read is never behind."""
    a, b = parse(latest), parse(current)
    return bool(a and b and _in_order(a) > _in_order(b))


def fetch(repo, request):
    """The tag of a repository's latest release, "" when it has none. Raises OSError/ValueError."""
    try:
        r = request(f"https://api.github.com/repos/{repo}/releases/latest",
                    {"Accept": "application/vnd.github+json", "User-Agent": "labhud"}, timeout=10, public=True)
    except OSError as e:
        if "404" in str(e):
            return ""
        raise
    tag = r.get("tag_name") if isinstance(r, dict) else None
    return tag if isinstance(tag, str) and parse(tag) else ""


def check(request=None, now=None):
    """Asks GitHub once for the three repositories; the ones that answer are updated, the others keep
    what they had. Returns the state."""
    if request is None:
        from sources._common import request
    found, error = {}, ""
    for kind, repo in REPOS.items():
        try:
            found[kind] = fetch(repo, request)
        except (OSError, ValueError) as e:
            error = f"{repo}: {str(e)[:80]}"
    with _lock:
        _state["latest"].update(found)
        _state["error"] = error
        if found:
            _state["checked"] = int(time.time() if now is None else now)
        return snapshot_locked()


def snapshot_locked():
    return {"latest": dict(_state["latest"]), "checked": _state["checked"], "error": _state["error"]}


def pinned_agent():
    """The agent release this server installs (agents/agent-release.json), "" without one."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "agent-release.json"), encoding="utf-8") as f:
            return str(json.load(f).get("version") or "")
    except (OSError, ValueError):
        return ""


def about(current, app=None):
    """What /api/brand/about adds: the latest tags, whether the server (`current`) is behind, what
    else is, and when it was last read. Without `[update] check`, nothing but `enabled: false`."""
    if not CHECK:
        return {"enabled": False, "latest": {k: "" for k in REPOS}, "update": False, "outdated": {"agent": False, "app": False}, "checked": 0}
    with _lock:
        s = snapshot_locked()
    latest = s["latest"]
    return {"enabled": True, "latest": latest, "update": newer(latest["server"], current),
            "outdated": {"agent": newer(latest["agent"], pinned_agent()), "app": newer(latest["app"], app)},
            "checked": s["checked"]}


def news(current, store, now=None):
    """The text of the one message about a newer server, once per version (kept in `store`), else None."""
    with _lock:
        tag = _state["latest"]["server"]
    if not CHECK or not newer(tag, current) or store.load(NOTIFIED_KEY) == tag:
        return None
    store.save(NOTIFIED_KEY, tag)
    return f"LabHUD {tag.lstrip('v')} is out (this one is {current})"


def run(current, store, said, stop=None):
    """The loop of the thread: a check now, then every six hours; `said(text)` gets the news."""
    stop = stop or threading.Event()
    while not stop.is_set():
        if CHECK:
            try:
                check()
                text = news(current, store)
                if text:
                    said(text)
            except Exception as e:      # a thread that dies never checks again
                print(f"updates: {str(e)[:100]}", flush=True)
        stop.wait(EVERY)
