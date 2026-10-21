"""Writes TOML: the other half of tomllib, for the config labhud keeps itself (managed.py).

Enough for config.toml: tables, arrays of tables nested in them ([[page]], [[page.group]],
[[page.group.card]]), and values that are strings, numbers, booleans, arrays and inline tables.
dumps(tomllib.loads(text)) reads back to the same data; comments and layout are not kept.
"""

import json
import math
import re

_BARE = re.compile(r"[A-Za-z0-9_-]+")


def _key(k):
    return k if _BARE.fullmatch(k) else json.dumps(k, ensure_ascii=False)


def _is_tables(v):
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def value(v):
    """One value, inline."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        # JSON's escapes are TOML's, except that TOML wants \u escapes for control characters
        # only, which json.dumps already does
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {value(x)}" for k, x in v.items()) + " }" if v else "{}"
    raise TypeError(f"no TOML for {type(v).__name__}")


def _table(d, path, out, indent):
    pad = "  " * indent
    later = []
    for k, v in d.items():
        if isinstance(v, dict) and not _inline_only(v):
            later.append((k, v, False))
        elif _is_tables(v):
            later.append((k, v, True))
        else:
            out.append(f"{pad}{_key(k)} = {value(v)}")
    for k, v, many in later:
        sub = path + [_key(k)]
        if many:
            for item in v:
                out.append("")
                out.append(f"{pad}[[{'.'.join(sub)}]]")
                _table(item, sub, out, indent + 1)
        else:
            out.append("")
            out.append(f"{pad}[{'.'.join(sub)}]")
            _table(v, sub, out, indent + 1)


def _inline_only(d):
    """An empty table is written inline ({}); any other is a [table]."""
    return not d


def dumps(data, header=""):
    """The TOML text for `data` (a dict as tomllib returns it)."""
    out = [line for line in header.splitlines()] if header else []
    _table(data, [], out, 0)
    return "\n".join(out).lstrip("\n") + "\n"
