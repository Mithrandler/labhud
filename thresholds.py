"""Numbers past the critical mark, as history events (and so notifications), with hysteresis.

The display colours a card's numbers by severity() in static/app.js. A colour can change on
every poll; a notification should not. So, for the same numbers and the same critical marks:
- a number becomes a problem only after it stays at or past the mark for LABHUD_ALERT_HOLD
  seconds (300): one busy minute on a CPU is not news;
- it stops being one only when it is back under the mark minus a margin (5 points, 3 °C), so a
  disk hovering at 92% gives one event, not one every poll.
Only the critical marks count (warn is for the eye). Cards in maintenance are skipped.
"""

import os
import re
import time

HOLD = int(os.environ.get("LABHUD_ALERT_HOLD", "300"))
_PERCENT = re.compile(r"\.(cpu|mem|used_percent|disk\.percent)$")
_USED_OF = re.compile(r"\.(mem_used_of|disk_used_of)$")


def lookup(data, path):
    """The raw value at a dotted path ([used, total] stays a list)."""
    for part in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


def measure(path, fmt, v):
    """(value, critical mark, margin, shown text) for a number that has a critical mark, else None.
    The same marks as raw_severity() in app.js."""
    if fmt == "used_of" and _USED_OF.search(path) and isinstance(v, list) and len(v) == 2 and v[1]:
        p = 100.0 * (v[0] or 0) / v[1]
        return p, 92, 5, f"{p:.0f}%"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if fmt == "celsius":
        return v, 85, 3, f"{v:.0f}°C"
    if fmt == "percent" and _PERCENT.search(path):
        return v, 92, 5, f"{v:.0f}%"
    return None


class Watch:
    """The state of every (card, metric) with a critical mark, between polls."""

    def __init__(self, hold=HOLD):
        self.hold = hold
        self.since = {}   # (card id, path) -> when it went past the mark (not yet reported)
        self.on = set()   # (card id, path) reported as a problem

    def check(self, cards, lookup, source, skip=(), now=None):
        """[(text, bad)] for the cards whose metrics come from `source`. `lookup(path)` gives a
        value, `skip` holds card ids in maintenance."""
        now = now or time.time()
        out = []
        for c in cards:
            for path, label, fmt in c.get("metrics") or []:
                if path.split(".", 1)[0] != source:
                    continue
                key = (c["id"], path)
                m = measure(path, fmt, lookup(path))
                if m is None or c["id"] in skip:
                    self.since.pop(key, None)  # unknown now: neither raise nor clear
                    continue
                value, mark, margin, text = m
                name = f"{c.get('name', c['id'])} {label}"
                if value >= mark:
                    if key in self.on:
                        continue
                    start = self.since.setdefault(key, now)
                    if now - start >= self.hold:
                        self.on.add(key)
                        self.since.pop(key, None)
                        out.append((f"{name} {text}", True))
                else:
                    self.since.pop(key, None)
                    if key in self.on and value < mark - margin:
                        self.on.discard(key)
                        out.append((f"{name} back to {text}", False))
        return out
