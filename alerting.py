"""Alert rules on hosts ([[alert]] in config.toml, ⚙ > Alerts): a host's CPU, RAM, disk or
temperature past a mark for some minutes, or its agent gone for some minutes.

A rule names the cards it is for (`on`: a card id, or "*" for every card with an agent of its own,
that is a host and not one container or MAC of it) and what it watches:

    [[alert]]
    on = "*"          # or a card id; a card's own rule wins over "*" for the same `what`
    what = "cpu"      # cpu | mem | disk | temp | offline
    above = 90        # % (°C for temp); not for offline
    minutes = 10      # how long it must last; 0 = at once

Without any [[alert]] the defaults below apply; `alert = []` turns them all off. The numbers come
from the agent (agents.<name>.sensors); temp is the hottest of the CPU and the GPUs. Like
thresholds.py, a problem clears only below the mark minus a margin (5 points, 3 °C), so a disk
hovering at 90% gives one event. A card in maintenance, off on schedule or started on demand is
skipped. cpu and mem are not urgent: during the quiet hours (notify.py) they wait for the morning.
"""

import time

WHAT = {"cpu": "CPU", "mem": "RAM", "disk": "disk", "temp": "temperature", "offline": "offline"}
URGENT = {"offline", "disk", "temp"}
KEYS = {"on", "what", "above", "minutes"}
DEFAULT_RULES = [
    {"on": "*", "what": "cpu", "above": 90, "minutes": 10},
    {"on": "*", "what": "mem", "above": 90, "minutes": 10},
    {"on": "*", "what": "disk", "above": 90, "minutes": 0},
    {"on": "*", "what": "temp", "above": 85, "minutes": 5},
    {"on": "*", "what": "offline", "minutes": 5},
]


def hosts(cards):
    """The cards "*" means: those with an agent of their own (not agent_target)."""
    return [c for c in cards if c.get("agent") and not c.get("agent_target")]


def rules_for(card, rules):
    """{what: rule} for one card: its own rules, then the "*" ones for what it has none of."""
    out = {}
    for r in rules:
        if r["on"] == card["id"]:
            out[r["what"]] = r
    if card.get("agent") and not card.get("agent_target"):
        for r in rules:
            if r["on"] == "*":
                out.setdefault(r["what"], r)
    return out


def covered(cards, rules):
    """The ids of the cards some rule watches (thresholds.py leaves their numbers alone)."""
    return {c["id"] for c in cards if rules_for(c, rules)}


def reading(what, sensors):
    """The number a rule compares, from an agent's sensors, or None."""
    if not isinstance(sensors, dict):
        return None
    if what == "temp":
        temps = [v for k, v in sensors.items()
                 if (k == "cpu_temp" or (k.startswith("gpu") and k.endswith("_temp")))
                 and isinstance(v, (int, float)) and not isinstance(v, bool)]
        return max(temps) if temps else None
    v = sensors.get(what)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def shown(what, v):
    return f"{v:.0f}°C" if what == "temp" else f"{v:.0f}%"


def _for(minutes):
    return f" for {minutes} min" if minutes else ""


class Rules:
    """The state of every (card, what) between checks. check() gives the events, active() what
    is wrong now (the bell)."""

    def __init__(self):
        self.since = {}   # (card id, what) -> when it went past the mark (not yet reported)
        self.on = {}      # (card id, what) -> {"text", "since", "card"} reported

    def check(self, cards, rules, agents, skip=(), status=None, now=None):
        """[(text, bad, ("rule", card id), urgent)]. `agents` is the snapshot's agents.<name>,
        `skip` the card ids not to judge now, `status(card)` the card's raw state (cardstate)."""
        now = now or time.time()
        out, seen = [], set()
        for c in cards:
            name = c.get("name", c["id"])
            a = agents.get(c.get("agent")) if c.get("agent") else None
            for what, r in rules_for(c, rules).items():
                key = (c["id"], what)
                seen.add(key)
                st = status(c) if status else None
                if c["id"] in skip or st in ("on-demand", "scheduled") or not isinstance(a, dict):
                    self.since.pop(key, None)   # not judged now: neither raise nor clear
                    continue
                hold = 60 * r["minutes"]
                if what == "offline":
                    bad, back = not a.get("online"), bool(a.get("online"))
                    text, back_text = f"{name} offline{_for(r['minutes'])}", f"{name} back online"
                else:
                    v = reading(what, a.get("sensors")) if a.get("online") else None
                    if v is None:
                        self.since.pop(key, None)
                        continue
                    margin = 3 if what == "temp" else 5
                    bad, back = v >= r["above"], v < r["above"] - margin
                    text = f"{name} {WHAT[what]} {shown(what, v)}{_for(r['minutes'])}"
                    back_text = f"{name} {WHAT[what]} back to {shown(what, v)}"
                if bad:
                    if key in self.on:
                        continue
                    start = self.since.setdefault(key, now)
                    if now - start >= hold:
                        self.since.pop(key, None)
                        self.on[key] = {"text": text, "since": int(start), "card": c["id"], "what": what}
                        out.append((text, True, ("rule", c["id"]), what in URGENT))
                else:
                    self.since.pop(key, None)
                    if key in self.on and back:
                        del self.on[key]
                        out.append((back_text, False, ("rule", c["id"]), what in URGENT))
        for key in set(self.on) - seen:     # the rule or the card is gone: forget it quietly
            del self.on[key]
        for key in set(self.since) - seen:
            del self.since[key]
        return out

    def active(self):
        """[{id, text, card, since, what}] wrong now, oldest first: alerts for the bell."""
        return sorted(({"id": f"rule:{card}:{what}:{v['since']}", **v} for (card, what), v in self.on.items()),
                      key=lambda x: x["since"])
