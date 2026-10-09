# Docker: every container's state, the unhealthy and restarting ones first, from one or more
# Docker engines. Read-only: give labhud a docker-socket-proxy with only CONTAINERS=1, never the
# socket itself (whoever holds the socket is root on that host).
#
#   LABHUD_DOCKER_URL   http://socket-proxy:2375, or several as "label=url,label=url";
#                       unix:///var/run/docker.sock also works (mount it read-only, at your risk)
#
# Containers can describe themselves with labels, so they need no line in config.toml:
#   labhud.enable=true      listed in docker.labelled
#   labhud.name=Jellyfin    the name shown (default: the container name)
#   labhud.group=media      also listed in docker.group.media
#
# Data (with several engines, under docker.<label>.):
#   docker.{total, running, stopped, unhealthy, restarting}
#   docker.containers   every container, problems first: list = "docker.containers"
#   docker.labelled     only those with labhud.enable=true
#   docker.group.<g>    those with labhud.group=<g>

import http.client
import json
import socket
import urllib.parse

from ._common import env, labelled_urls, request, source

LABEL = "labhud."


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def _get(url, path, timeout=10):
    if url.startswith("unix://"):
        conn = _UnixConnection(urllib.parse.urlsplit(url).path or "/var/run/docker.sock", timeout)
        try:
            conn.request("GET", path, headers={"Host": "docker"})
            r = conn.getresponse()
            body = r.read()
            if r.status != 200:
                raise OSError(f"docker answered {r.status}")
            return json.loads(body)
        finally:
            conn.close()
    return request(url.rstrip("/") + path, timeout=timeout)


def state(c):
    """("running" | "unhealthy" | "restarting" | "stopped" | "paused", shown text, bad?)."""
    st = (c.get("State") or "").lower()
    status = c.get("Status") or st
    if st == "restarting":
        return "restarting", "restarting", True
    if st == "running" and "(unhealthy)" in status:
        return "unhealthy", "unhealthy", True
    if st == "running":
        return "running", status.split(" (")[0].replace("Up ", "up "), False
    if st == "paused":
        return "paused", "paused", False
    # exited with 0 is a finished job; anything else died
    bad = st == "dead" or ("Exited (" in status and not status.startswith("Exited (0)"))
    return "stopped", status.split(" ago")[0].replace("Exited ", "exited ") + (" ago" if " ago" in status else ""), bad


def summarize(containers):
    out = {"total": 0, "running": 0, "stopped": 0, "unhealthy": 0, "restarting": 0}
    rows, labelled, groups = [], [], {}
    for c in containers:
        labels = c.get("Labels") or {}
        name = ((c.get("Names") or ["/?"])[0]).lstrip("/")
        kind, text, bad = state(c)
        out["total"] += 1
        out[kind if kind in out else "stopped"] += 1
        if kind in ("unhealthy", "restarting"):
            out["running"] += kind == "unhealthy"
        row = {"name": labels.get(LABEL + "name") or name, "value": text, "bad": bad, "_rank": 0 if bad else 1 if kind == "stopped" else 2}
        if labels.get(LABEL + "url", "").startswith(("http://", "https://")):
            row["url"] = labels[LABEL + "url"]
        rows.append(row)
        if labels.get(LABEL + "enable", "").lower() in ("true", "1", "yes"):
            labelled.append(row)
        group = labels.get(LABEL + "group", "").strip().lower()
        if group:
            groups.setdefault(group, []).append(row)

    def ordered(rs):
        return [{k: v for k, v in r.items() if k != "_rank"} for r in sorted(rs, key=lambda r: (r["_rank"], r["name"].lower()))]
    out["containers"] = ordered(rows) or [{"name": "no containers", "value": "—"}]
    out["labelled"] = ordered(labelled) or [{"name": "no container has labhud.enable=true", "value": "—"}]
    out["group"] = {g: ordered(rs) for g, rs in sorted(groups.items())}
    return out


@source("docker", every=15, env=("DOCKER_URL",),
        title="Docker", about="Every container's state, unhealthy and restarting ones first; labhud.* labels group them.",
        hints={"DOCKER_URL": "http://socket-proxy:2375 (a docker-socket-proxy with CONTAINERS=1), or label=url,label=url"})
def docker():
    urls = labelled_urls(env("DOCKER_URL"))
    if list(urls) == [""]:
        return summarize(_get(urls[""], "/containers/json?all=1") or [])
    out = {}
    for label, url in urls.items():
        try:
            out[label] = summarize(_get(url, "/containers/json?all=1") or [])
        except (OSError, ValueError) as e:
            out[label] = {"unavailable": str(e)[:120]}
    return out
