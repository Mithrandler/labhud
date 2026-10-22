"""The scheduler of Servers: jobs that run on a host at a time of day (the server's local time).

A job is {id, name, kind, agent, days, at, enabled, last}:
  kind   "update"      the host's `auto` containers whose registry has a newer image (agent "*" = every host)
         "prune"       the unused images of the host are removed
         "host-update" the host's packages are updated
         "backup"      the host's ticked guests (`vmids`) are backed up to `storage` in `mode` (Proxmox vzdump, one after the other)
  days   Monday = 0 ... Sunday = 6;  at  "HH:MM"
  last   {day, t, ok, text} of its latest run, {} for none

The update job is what the single update window used to be: the first time the jobs are read, that
window becomes this job (`agents.window()` and `agents.set_window()` keep working on it). Only the
shape of a job is checked here: what a kind does, and whether the host allows it, is agents.py's.
"""

import os
import re
import time

import store

KEY = "servers_jobs"
OLD_WINDOW_KEY = "agents_update_window"
KINDS = ("update", "prune", "host-update", "backup")
MODES = ("snapshot", "suspend", "stop")
MAX_VMIDS = 50
VMID = re.compile(r"[0-9]{1,9}")
STORAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
MAX_JOBS = 30
HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")
ID = re.compile(r"[0-9a-f]{8}")
ALL = "*"
OPEN = 60            # minutes a job stays due after its time: a labhud restarted at 04:20 still runs a 04:00 job
DEFAULT = {"days": [0, 1, 2, 3, 4, 5, 6], "at": "04:00"}
UPDATE_ID = "00000001"   # the migrated window, so it is always the same job


def _clip(v, n):
    return re.sub(r"[\x00-\x1f\x7f]", " ", v).strip()[:n] if isinstance(v, str) else ""


def _days(v):
    ok = isinstance(v, list) and v and all(type(d) is int and 0 <= d <= 6 for d in v)
    return sorted(set(v)) if ok else None


def clean(raw):
    """The job, rebuilt field by field, or None when it is not one."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    days, at = _days(raw.get("days")), raw.get("at")
    name, agent = _clip(raw.get("name"), 60), _clip(raw.get("agent"), 60)
    if days is None or not isinstance(at, str) or not HHMM.fullmatch(at) or not name:
        return None
    if raw["kind"] == "update":
        agent = agent or ALL
    elif not agent or agent == ALL:
        return None
    extra = {}
    if raw["kind"] == "backup":
        ids = raw.get("vmids")
        ids = [str(v) for v in ids] if isinstance(ids, list) and ids and all(type(v) is int and v > 0 or isinstance(v, str) for v in ids) else None
        storage, mode = raw.get("storage"), raw.get("mode")
        if not ids or len(ids) > MAX_VMIDS or not all(VMID.fullmatch(v) for v in ids) or not isinstance(storage, str) \
                or not STORAGE.fullmatch(storage) or mode not in MODES:
            return None
        extra = {"vmids": sorted(set(ids), key=int), "storage": storage, "mode": mode}
    last = raw.get("last") if isinstance(raw.get("last"), dict) else {}
    return {"id": raw.get("id") if isinstance(raw.get("id"), str) and ID.fullmatch(raw["id"]) else "", "name": name, "kind": raw["kind"],
            "agent": agent, "days": days, "at": at, "enabled": raw.get("enabled") is not False, **extra,
            "last": {"day": _clip(last.get("day"), 10), "t": last["t"] if type(last.get("t")) is int else 0,
                     "ok": last.get("ok") is True, "text": _clip(last.get("text"), 300)} if last else {}}


def load():
    """The jobs, in the order they were made; the old update window becomes the first one."""
    if not store.enabled():
        return []
    raw = store.load(KEY)
    if isinstance(raw, list):
        return [j for j in map(clean, raw) if j and j["id"]][:MAX_JOBS]
    w = store.load(OLD_WINDOW_KEY)
    w = w if isinstance(w, dict) and _days(w.get("days")) and HHMM.fullmatch(str(w.get("at"))) else DEFAULT
    jobs = [clean({"id": UPDATE_ID, "name": "Update containers", "kind": "update", "agent": ALL, "days": w["days"], "at": w["at"]})]
    store.save(KEY, jobs)
    return jobs


def save(jobs):
    store.save(KEY, jobs[:MAX_JOBS])


def put(raw):
    """Makes the job, or replaces the one with that id (keeping its last run). The saved job, or None."""
    job = clean(raw)
    if not job:
        return None
    jobs = load()
    if job["id"]:
        old = next((j for j in jobs if j["id"] == job["id"]), None)
        if not old or old["kind"] != job["kind"]:
            return None
        job["last"] = old["last"]
        jobs = [job if j["id"] == job["id"] else j for j in jobs]
    else:
        if len(jobs) >= MAX_JOBS:
            return None
        job["id"] = os.urandom(4).hex()
        jobs.append(job)
    save(jobs)
    return job


def delete(job_id):
    jobs = load()
    if job_id == UPDATE_ID or not any(j["id"] == job_id for j in jobs):
        return False
    save([j for j in jobs if j["id"] != job_id])
    return True


def record(job_id, ok, text, now=None):
    """Writes the result of a run into the job."""
    now = now or time.time()
    jobs = load()
    for j in jobs:
        if j["id"] == job_id:
            j["last"] = {"day": time.strftime("%Y-%m-%d", time.localtime(now)), "t": int(now), "ok": bool(ok), "text": _clip(text, 300)}
    save(jobs)


def due(job, now=None):
    """True when the job is open at `now` (today, from its time for an hour) and has not run today."""
    if not job["enabled"]:
        return False
    t = time.localtime(now or time.time())
    today = time.strftime("%Y-%m-%d", t)
    if t.tm_wday not in job["days"] or job["last"].get("day") == today:
        return False
    h, m = map(int, job["at"].split(":"))
    since = (t.tm_hour * 60 + t.tm_min) - (h * 60 + m)
    return 0 <= since < OPEN


def update_window():
    """The days and time of the update job, what `agents.window()` shows."""
    j = next((j for j in load() if j["id"] == UPDATE_ID), None)
    return {"days": j["days"], "at": j["at"]} if j else dict(DEFAULT)


def set_update_window(days, at):
    jobs = load()
    j = next((j for j in jobs if j["id"] == UPDATE_ID), None)
    new = clean({"id": UPDATE_ID, "name": j["name"] if j else "Update containers", "kind": "update", "agent": ALL, "days": days, "at": at,
                 "enabled": j["enabled"] if j else True, "last": j["last"] if j else {}})
    if not new:
        return False
    save([new if x["id"] == UPDATE_ID else x for x in jobs] if j else [new] + jobs)
    return True
