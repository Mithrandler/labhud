"""What changed: a short history of state changes, shown as the `events` source.

The server compares each new result of a source with the previous one and keeps the last
MAX_EVENTS changes in memory (the container's filesystem is read-only, so a restart starts a
new history). A card shows them like any list:

    [[page.group.card]]
    id = "events"
    name = "Recent"
    list = "events.recent"
    limit = 8

Each row is {name, t, bad}: what happened, when (Unix time, shown by the display in its own
clock) and whether it is bad news.
"""

import collections
import threading
import time

MAX_EVENTS = 50


class Log:
    def __init__(self, seed=()):
        self._rows = collections.deque(seed, maxlen=MAX_EVENTS)
        self._lock = threading.Lock()

    def add(self, text, bad=False, t=None):
        with self._lock:
            self._rows.appendleft({"name": text, "t": int(t or time.time()), "bad": bool(bad)})

    def snapshot(self):
        with self._lock:
            return {"recent": list(self._rows)}


def changes(name, old, new, topology):
    """[(text, bad)] for what differs between two results of the source `name`. A first result
    (old is None), a failed one or one off on schedule says nothing: only real transitions count."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    if any(k in r for r in (old, new) for k in ("unavailable", "scheduled", "not_configured")):
        return []
    if name == "status":
        return _hosts(old, new, topology)
    out = []
    if name == "proxmox":
        out += _guests(old, new)
    alerts = topology.get("alerts")
    if alerts and alerts["path"].split(".")[0] == name:
        out += _alerts(old, new, alerts["path"])
    return out


def _card_names(topology):
    return {c["id"]: c for cards in topology["cards"].values() for c in cards}


def _hosts(old, new, topology):
    cards = _card_names(topology)
    out = []
    for cid, now in new.items():
        was = old.get(cid)
        if cid.startswith("_") or was is None or was == now or "scheduled" in (was, now):
            continue
        card = cards.get(cid, {})
        label = card.get("name", cid)
        if now is True:
            out.append((f"{label} up", False))
        elif now is False:
            # a host that is off by design (on_demand) going off is not bad news
            out.append((f"{label} {'off' if card.get('on_demand') else 'down'}", not card.get("on_demand")))
    return out


def _guests(old, new):
    out = []
    for node, data in new.items():
        if not isinstance(data, dict) or not isinstance(old.get(node), dict):
            continue
        before = old[node].get("guests") or {}
        for vmid, g in (data.get("guests") or {}).items():
            was = (before.get(vmid) or {}).get("running")
            if was is None or was == g.get("running"):
                continue
            out.append((f"{g.get('name') or vmid} {'started' if g.get('running') else 'stopped'}", False))
    return out


def _lookup(data, path):
    for part in path.split(".")[1:]:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def _alerts(old, new, path):
    before = {a.get("id") for a in _lookup(old, path) or [] if isinstance(a, dict)}
    return [(a.get("text") or str(a.get("id")), True)
            for a in _lookup(new, path) or [] if isinstance(a, dict) and a.get("id") not in before]
