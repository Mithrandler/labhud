# Proxmox VE: a single /cluster/resources call covers both the counts and every guest.
#
#   LABHUD_PROXMOX_NODES                  node names, comma separated, exactly as Proxmox spells them
#   LABHUD_PROXMOX_<NODE>_URL             https://host:8006
#   LABHUD_PROXMOX_<NODE>_TOKEN_ID        user@realm!tokenname (PVEAuditor is enough)
#   LABHUD_PROXMOX_<NODE>_TOKEN_SECRET
#
# Each node is asked separately, so standalone nodes work as well as a cluster. <NODE> is the
# name in capitals, with anything other than letters and digits turned into "_".

import re
import time
import urllib.parse

from ._common import beyond_reading, env, env_name, fmt_bytes, percent, request, rights, source


@rights("proxmox")
def _rights():
    """What each node's token may do beyond reading (PVEAuditor gives nothing more)."""
    out = []
    for node, url, header in _nodes():
        if header:
            perms = request(f"{url}/api2/json/access/permissions", header, timeout=8).get("data")
            out.append((node, beyond_reading(perms)))
    return out


def _nodes():
    """[(node, url, auth header or None)] in the order of LABHUD_PROXMOX_NODES."""
    out = []
    for node in (n.strip() for n in env("PROXMOX_NODES").split(",") if n.strip()):
        key = env_name(node)
        url = env(f"PROXMOX_{key}_URL").rstrip("/")
        ident, secret = env(f"PROXMOX_{key}_TOKEN_ID"), env(f"PROXMOX_{key}_TOKEN_SECRET")
        header = {"Authorization": f"PVEAPIToken={ident}={secret}"} if url and ident and secret else None
        out.append((node, url, header))
    return out


def _missing(node):
    return RuntimeError(f"missing LABHUD_PROXMOX_{env_name(node)}_URL/_TOKEN_ID/_TOKEN_SECRET")


# A virtual machine's used disk does not come from the Proxmox API (`disk` is always 0 for VMs):
# it is read through the guest agent (get-fsinfo). The agent is slow and the disk changes slowly,
# so the result is remembered for 5 minutes. LXCs have `disk`/`maxdisk` right in the resource list.
_VM_DISK = {}
_DISK_TTL = 300


def _vm_disk(url, header, node, vmid):
    """(used, total) in bytes, summed over the VM's real filesystems; None if the agent does not
    answer. On error the last good value is kept."""
    now = time.time()
    cache = _VM_DISK.get((node, vmid))
    if cache and now - cache[0] < _DISK_TTL:
        return cache[1]
    try:
        fs = (request(f"{url}/api2/json/nodes/{node}/qemu/{vmid}/agent/get-fsinfo", header,
                      timeout=8).get("data") or {}).get("result") or []
        seen, used, total = set(), 0, 0
        for f in fs:
            if f.get("name") in seen or not f.get("total-bytes"):
                continue
            seen.add(f.get("name"))
            used += f.get("used-bytes") or 0
            total += f["total-bytes"]
        val = (used, total) if total else None
    except (OSError, ValueError, TypeError, KeyError):
        val = cache[1] if cache else None
    _VM_DISK[(node, vmid)] = (now, val)
    return val


def _node(node, url, header):
    status = request(f"{url}/api2/json/nodes/{node}/status", header, timeout=10).get("data") or {}
    resources = request(f"{url}/api2/json/cluster/resources?type=vm", header, timeout=10).get("data") or []
    guests = {}
    vms = lxc = 0
    for r in resources:
        if r.get("node") != node:
            continue
        vmid = r.get("vmid")
        running = r.get("status") == "running"
        if r.get("type") == "lxc":
            lxc += 1 if running else 0
        else:
            vms += 1 if running else 0
        guests[str(vmid)] = {
            "name": r.get("name"), "running": running, "type": r.get("type"),
            "cpu": round(100 * (r.get("cpu") or 0), 1),
            "mem": percent(r.get("mem"), r.get("maxmem")),
            "mem_bytes": r.get("mem"), "mem_max": r.get("maxmem"),
            "disk": r.get("maxdisk"), "uptime": r.get("uptime"),
        }
        if running and r.get("type") == "lxc":
            guests[str(vmid)]["disk_used"] = r.get("disk")
            guests[str(vmid)]["disk_total"] = r.get("maxdisk")
        elif running:
            d = _vm_disk(url, header, node, vmid)
            if d:
                guests[str(vmid)]["disk_used"], guests[str(vmid)]["disk_total"] = d
    # A guest's RAM percentage lies: Proxmox computes it as `total - MemFree`, so it counts
    # the page cache too. Any Linux machine that ran for a day reaches ~95% without any
    # problem (95% reported, 5.2 GB actually available). The balloon does NOT expose
    # "available", so the right number cannot be had from here. What can be had is PSI — how
    # long processes actually stalled waiting for memory. It is asked only for guests that
    # would be coloured anyway (rarely more than two), so as not to add fifteen requests per cycle.
    for vmid, g in guests.items():
        if not g["running"] or (g["mem"] or 0) < 80:
            continue
        try:
            kind = "lxc" if g["type"] == "lxc" else "qemu"
            d = request(f"{url}/api2/json/nodes/{node}/{kind}/{vmid}/status/current",
                        header, timeout=8).get("data") or {}
            g["pressure"] = float(d.get("pressurememorysome") or 0)
            g["pressure_full"] = float(d.get("pressurememoryfull") or 0)
            if d.get("freemem") is not None:
                g["free"] = d["freemem"]
        except (OSError, ValueError, TypeError, KeyError):
            pass

    storage = []
    try:
        for st in request(f"{url}/api2/json/nodes/{node}/storage", header, timeout=10).get("data") or []:
            if st.get("active") and st.get("total") and st.get("type") != "pbs":
                p = round(100 * (st.get("used") or 0) / st["total"])
                storage.append({"name": st["storage"], "type": st.get("type"),
                                "used": st.get("used"), "total": st["total"],
                                "value": f"{fmt_bytes(st.get('used'))}/{fmt_bytes(st['total'])} {p}%",
                                "bad": p >= 90})
    except (OSError, ValueError, KeyError):
        pass

    mem = status.get("memory") or {}
    return {
        "storage": storage,
        # for the HEALTH cards: without `local` (the system directory, ISOs)
        "storage_list": [x for x in storage if x["type"] != "dir"],
        "vms": vms, "lxc": lxc,
        "cpu": round(100 * (status.get("cpu") or 0), 1),
        "mem": percent(mem.get("used"), mem.get("total")),
        "mem_used": mem.get("used"), "mem_total": mem.get("total"),
        "mem_used_of": [mem.get("used"), mem.get("total")],
        # The host's disk = the local storages (no NFS, a NAS has its own card)
        "disk_used_of": [sum(x["used"] or 0 for x in storage if x["type"] != "nfs"),
                         sum(x["total"] for x in storage if x["type"] != "nfs")],
        "uptime": status.get("uptime"),
        "guests": guests,
    }


@source("proxmox", every=10, env=("PROXMOX_NODES",))
def proxmox():
    out = {}
    nodes = _nodes()
    for node, url, header in nodes:
        try:
            if not header:
                raise _missing(node)
            out[node] = _node(node, url, header)
        except (OSError, ValueError, RuntimeError, KeyError) as e:
            out[node] = {"unavailable": str(e)[:120] or type(e).__name__}
    # A single storage list for the HEALTH card (every node, short names), attached to the first
    # node in LABHUD_PROXMOX_NODES — put the one that is always on first. A node that is down
    # just has no rows.
    every = []
    for node, _, _ in nodes:
        for x in (out.get(node) or {}).get("storage_list") or []:
            short = x["name"].replace("local-lvm", "lvm")
            every.append({"name": f"{node} {short}", "value": x["value"].rsplit(" ", 1)[0], "bad": x["bad"]})
    if nodes and "unavailable" not in out[nodes[0][0]]:
        out[nodes[0][0]]["storage_all"] = every
    return out


# The last backup of every guest, from the vzdump job logs (every node). A node that sleeps at night
# keeps its last known result for as long as it does not answer.
_backup_cache = {}
_RE_BK_OK = re.compile(r"Finished Backup of VM (\d+) \((\S+)\)")
_RE_BK_ERR = re.compile(r"ERROR: Backup of VM (\d+) failed - (.*)")


def _node_backups(node, url, header):
    resources = request(f"{url}/api2/json/cluster/resources?type=vm", header, timeout=10).get("data") or []
    guests = {str(r["vmid"]): r.get("name") or str(r["vmid"]) for r in resources if r.get("node") == node}
    excluded = set()
    try:
        for job in request(f"{url}/api2/json/cluster/backup", header, timeout=10).get("data") or []:
            if job.get("enabled", 1) and job.get("all"):
                excluded |= set(str(job.get("exclude") or "").replace(" ", "").split(",")) - {""}
    except (OSError, ValueError):
        pass
    tasks = request(f"{url}/api2/json/nodes/{node}/tasks?typefilter=vzdump&limit=30", header, timeout=10).get("data") or []
    per = {}
    for t in tasks[:10]:  # newest first; stop once every guest has a result
        if all(v in per for v in guests if v not in excluded):
            break
        log = request(f"{url}/api2/json/nodes/{node}/tasks/{urllib.parse.quote(t['upid'])}/log?limit=5000",
                      header, timeout=15).get("data") or []
        for line in (x.get("t", "") for x in log):
            m, e = _RE_BK_OK.search(line), _RE_BK_ERR.search(line)
            if m and m.group(1) not in per:
                per[m.group(1)] = {"ts": t.get("endtime") or t.get("starttime"), "ok": True}
            elif e and e.group(1) not in per:
                per[e.group(1)] = {"ts": t.get("endtime") or t.get("starttime"), "ok": False, "error": e.group(2)[:80]}
    return {vmid: dict(per.get(vmid, {}), name=name, excluded=vmid in excluded) for vmid, name in guests.items()}


@source("backups", every=1800, env=("PROXMOX_NODES",))
def backups():
    out = {}
    for node, url, header in _nodes():
        try:
            if header:
                _backup_cache[node] = _node_backups(node, url, header)
        except (OSError, ValueError, RuntimeError, KeyError):
            pass  # the node sleeps: the last known result stays
        if node in _backup_cache:
            out[node] = _backup_cache[node]
    if not out:
        raise RuntimeError("no Proxmox node answered")
    now = time.time()
    rows = []
    for node, guests in out.items():
        for vmid, g in guests.items():
            name = f"{g['name']} ({vmid})"
            if g.get("excluded"):
                rows.append({"name": name, "value": "excluded", "order": -1})
            elif "ts" not in g:
                rows.append({"name": name, "value": "never", "bad": True, "order": 10 ** 9})
            else:
                age = now - g["ts"]
                # floored, like ago(): a backup 5.5 days old is "5d ago", not "6d"
                text = f"{age // 86400:.0f}d ago" if age >= 86400 else f"{age // 3600:.0f}h ago"
                rows.append({"name": name, "value": text if g["ok"] else "FAILED " + text,
                             "bad": not g["ok"] or age > 8 * 86400, "order": age})
    # the oldest (and the failed) first; the excluded ones last
    rows.sort(key=lambda r: (not r.get("bad"), -r["order"]))
    return {"nodes": out, "display": [{k: v for k, v in r.items() if k != "order"} for r in rows],
            "stale": sum(1 for r in rows if r.get("bad"))}
