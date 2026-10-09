# Custom JSON endpoints: any service of your own that answers GET with a JSON object.
#
#   LABHUD_JSON_<NAME>_URL      one URL, or several as "label=url,label=url"
#   LABHUD_JSON_<NAME>_EVERY    seconds between polls (default 10)
#   LABHUD_JSON_<NAME>_TIMEOUT  seconds per request (default 10)
#   LABHUD_JSON_<NAME>_AUTH     sent as the Authorization header, e.g. "Bearer <key>" for an agent
#                               with LABHUD_AGENT_READ_KEY
#
# Any JSON API, not only an object made for labhud, without writing code:
#   LABHUD_JSON_<NAME>_PICK     key=path,key=path: keeps only those values, under your names.
#                               A path is dotted, with list indexes: "data.0.stats.total"
#   LABHUD_JSON_<NAME>_LIST     path to a list of objects, turned into a card list at
#                               "<name>.list"; LABHUD_JSON_<NAME>_LIST_NAME / _LIST_VALUE name the
#                               fields shown (default "name" / "value"), e.g. a GitHub issues list
#                               with _LIST_NAME=title and _LIST_VALUE=state
#   LABHUD_JSON_<NAME>_HEADERS  more headers, "Name: value; Name: value" (an API key header)
#   LABHUD_JSON_<NAME>_PUBLIC   on: a service on the internet, certificate always checked
#
# The data is then addressable as "<name>.<key>" (lowercase name), or "<name>.<label>.<key>" with
# several URLs. With one URL a failed request is the source's error; with several, a label that
# does not answer is just empty ({}) and the others still show — a host that sleeps at night
# should not blank its neighbours.

import os
import re

from ._common import PREFIX, env, labelled_urls, request, source

_VAR = re.compile(rf"^{PREFIX}JSON_([A-Z0-9_]+)_URL$")


def path_get(data, path):
    """The value at a dotted path; a number part indexes a list. None when it is not there."""
    for part in (p for p in path.split(".") if p):  # "." alone: the answer itself
        if isinstance(data, list) and part.lstrip("-").isdigit() and -len(data) <= int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def shape(data, pick=None, list_path="", list_name="name", list_value="value"):
    """What a JSON answer becomes: all of it, or only the picked values, plus an optional list."""
    if not pick and not list_path:
        if not isinstance(data, dict):
            raise ValueError("the answer is not a JSON object: set _PICK or _LIST to use it")
        return data
    out = {key: path_get(data, path) for key, path in (pick or {}).items()}
    if list_path:
        items = path_get(data, list_path)
        rows = []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict):
                v = path_get(item, list_value)
                rows.append({"name": str(path_get(item, list_name) or "?"), "value": "" if v is None else str(v)})
        out["list"] = rows or [{"name": "nothing", "value": "—"}]
    return out


def parse_pick(value):
    out = {}
    for part in (p.strip() for p in value.split(",") if p.strip()):
        key, sep, path = part.partition("=")
        if not sep or not key.strip() or not path.strip():
            raise SystemExit(f"_PICK: {part!r} is not key=path")
        out[key.strip()] = path.strip()
    return out


def parse_headers(value):
    out = {}
    for part in (p.strip() for p in value.split(";") if p.strip()):
        name, sep, v = part.partition(":")
        if sep and name.strip():
            out[name.strip()] = v.strip()
    return out


def _make(urls, timeout, auth="", extra=None, shaping=None, public=False):
    headers = dict(extra or {})
    if auth:
        headers["Authorization"] = auth
    headers = headers or None
    shaping = shaping or {}

    def get(url):
        return shape(request(url, headers, timeout=timeout, public=public), **shaping)
    if list(urls) == [""]:
        return lambda: get(urls[""])

    def fetch():
        out = {}
        for label, url in urls.items():
            try:
                out[label] = get(url)
            except (OSError, ValueError):
                out[label] = {}
        return out
    return fetch


for _var, _value in sorted(os.environ.items()):
    _m = _VAR.match(_var)
    if not _m or not _value.strip():
        continue
    _name = _m.group(1)
    _shaping = {"pick": parse_pick(env(f"JSON_{_name}_PICK")), "list_path": env(f"JSON_{_name}_LIST"),
                "list_name": env(f"JSON_{_name}_LIST_NAME", "name"), "list_value": env(f"JSON_{_name}_LIST_VALUE", "value")}
    _fetch = _make(labelled_urls(_value), int(env(f"JSON_{_name}_TIMEOUT", "10")), env(f"JSON_{_name}_AUTH"),
                   parse_headers(env(f"JSON_{_name}_HEADERS")), _shaping,
                   env(f"JSON_{_name}_PUBLIC").lower() in ("1", "on", "true", "yes"))
    source(_name.lower(), every=int(env(f"JSON_{_name}_EVERY", "10")), env=(f"JSON_{_name}_URL",))(_fetch)
