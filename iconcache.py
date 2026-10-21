"""The service icons that are not shipped (static/icons/svc/), fetched once from dashboard-icons
(homarr-labs, Apache-2.0) through jsDelivr and kept in LABHUD_DATA/icons/, so a card whose image
or name has an icon the pack lacks still gets it, and the CDN is asked once per icon, not per page.

    GET /icons/svc/<slug>.svg|png    the shipped file (server._static), else what get() finds here

Without LABHUD_DATA there is nowhere to keep it: only the shipped set. `[display] icons_online =
false` turns the requests off, and a name the CDN does not have is not asked again for a day.
"""

import os
import re
import threading
import time
import urllib.error
import urllib.request

import store
from config import ICON_RE
from sources._common import urlopen

CDN = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons"
MAX_BYTES = {"svg": 256 * 1024, "png": 512 * 1024}
MISSING_FOR = 86400          # a 404 is remembered for a day
MAX_MISSING = 2000           # ... for this many names, so a scan of names cannot fill the memory
TYPES = {"svg": "image/svg+xml", "png": "image/png"}
# a picture the page only shows as <img>, where scripts never run; still, none is kept
_BAD_SVG = re.compile(rb"<\s*script|\son\w+\s*=|javascript\s*:|<\s*foreignObject|<!ENTITY", re.I)

_lock = threading.Lock()
_missing = {}                # "slug.ext" -> when it was found missing
_busy = {}                   # "slug.ext" -> an Event, while one request is out for it


def directory():
    return os.path.join(store.DIR, "icons") if store.DIR and store.enabled() else ""


def valid(slug, ext):
    return ext in TYPES and bool(ICON_RE.match(slug or ""))


def safe(ext, data):
    """True if `data` is what `ext` says it is, within its size, and (an SVG) free of anything active."""
    if not data or len(data) > MAX_BYTES[ext]:
        return False
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    head = data.lstrip()[:512].lower()
    return head.startswith((b"<svg", b"<?xml", b"<!doctype svg")) and b"<svg" in data.lower() and not _BAD_SVG.search(data)


def _download(slug, ext):
    """The bytes from the CDN; None for "no such icon". An error of the network is raised."""
    kind = "svg" if ext == "svg" else "png"
    req = urllib.request.Request(f"{CDN}/{kind}/{slug}.{ext}", headers={"User-Agent": "labhud"})
    try:
        with urlopen(req, 10, True) as r:
            return r.read(MAX_BYTES[ext] + 1)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404, 410):
            return None
        raise


def get(slug, ext, online=True, now=None):
    """(bytes, content type) of an icon from the cache or the CDN, else None. Never raises."""
    if not valid(slug, ext):
        return None
    folder = directory()
    if not folder:
        return None
    path = os.path.join(folder, f"{slug}.{ext}")
    try:
        with open(path, "rb") as f:
            return f.read(), TYPES[ext]
    except OSError:
        pass
    if not online:
        return None
    now = time.time() if now is None else now
    key = f"{slug}.{ext}"
    with _lock:
        if now - _missing.get(key, -MISSING_FOR) < MISSING_FOR:
            return None
        wait = _busy.get(key)
        if wait is None:
            _busy[key] = threading.Event()
    if wait is not None:               # another request is fetching the same one: use its answer
        wait.wait(12)
        return get(slug, ext, online=False)
    try:
        data = _download(slug, ext)
        if data is None or not safe(ext, data):
            with _lock:
                if len(_missing) >= MAX_MISSING:
                    _missing.clear()
                _missing[key] = now
            return None
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return data, TYPES[ext]
    except (OSError, ValueError):      # no network, a full disk: not remembered, asked again later
        return None
    finally:
        with _lock:
            _busy.pop(key).set()
