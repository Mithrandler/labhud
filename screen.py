"""The display's own screen, through Fully Kiosk Browser's REST API: off (or dimmed) at night,
on again in the morning, and lit for a few minutes when a new problem comes in at night.

    LABHUD_FULLY_URL          http://<tablet>:2323 (Fully: Settings > Remote Administration)
    LABHUD_FULLY_PASS         its Remote Administration password
    LABHUD_FULLY_BRIGHTNESS   daytime brightness, 0-255 (optional: left as it is)

The window and the night brightness are [night] from/to/dim in config.toml: dim = 0 turns the
screen off, anything else sets that brightness. Without [night] nothing is sent. The browser's
own dimming ([night] shade) still applies on top, so a display without Fully dims too.
"""

import os
import threading
import time
import urllib.parse

from sources._common import in_window, request, scrub

URL = os.environ.get("LABHUD_FULLY_URL", "").rstrip("/")
PASSWORD = os.environ.get("LABHUD_FULLY_PASS", "")
DAY_BRIGHTNESS = os.environ.get("LABHUD_FULLY_BRIGHTNESS", "")
WAKE = 180  # seconds a new problem keeps the screen lit at night, like the display's focus

state = {"sent": None, "error": None, "wake_until": 0}


def enabled():
    return bool(URL and PASSWORD)


def _cmd(**params):
    q = urllib.parse.urlencode(dict(params, type="json", password=PASSWORD))
    answer = request(f"{URL}/?{q}", timeout=8)
    if isinstance(answer, dict) and answer.get("status") == "Error":
        raise RuntimeError(answer.get("statustext") or "Fully refused the command")


def wanted(night, now=None):
    """("on", brightness or None) | ("off", None) | ("dim", brightness) for this moment."""
    now = now or time.time()
    if night and night.get("from") is not None and in_window((night["from"], night["to"])) \
            and now >= state["wake_until"]:
        dim = night.get("dim", 30)
        return ("off", None) if dim == 0 else ("dim", round(dim * 255 / 100))
    return ("on", int(DAY_BRIGHTNESS) if DAY_BRIGHTNESS.isdigit() else None)


def apply(target):
    kind, level = target
    if kind == "off":
        _cmd(cmd="screenOff")
        return
    _cmd(cmd="screenOn")
    if level is not None:
        _cmd(cmd="setStringSetting", key="screenBrightness", value=str(level))


def wake():
    """A new problem: light the screen for WAKE seconds even at night."""
    state["wake_until"] = time.time() + WAKE


def loop(get_night):
    """Every 15 s, sends a command only when what the screen should be changes."""
    while True:
        night = get_night()
        target = wanted(night) if night else None
        if target and target != state["sent"]:
            try:
                apply(target)
                state.update(sent=target, error=None)
                print(f"screen: {target[0]}" + (f", brightness {target[1]}" if target[1] is not None else ""), flush=True)
            except Exception as e:
                msg = scrub(str(e).replace(PASSWORD, "<hidden>"))[:160]
                if msg != state["error"]:
                    print(f"screen: Fully Kiosk did not take the command: {msg}", flush=True)
                state["error"] = msg
        time.sleep(15)


def start(get_night):
    if enabled():
        threading.Thread(target=loop, args=(get_night,), daemon=True, name="screen").start()
