# Custom JSON endpoints: any service of your own that answers GET with a JSON object.
#
#   LABHUD_JSON_<NAME>_URL      one URL, or several as "label=url,label=url"
#   LABHUD_JSON_<NAME>_EVERY    seconds between polls (default 10)
#   LABHUD_JSON_<NAME>_TIMEOUT  seconds per request (default 10)
#
# The data is then addressable as "<name>.<key>" (lowercase name), or "<name>.<label>.<key>" with
# several URLs. With one URL a failed request is the source's error; with several, a label that
# does not answer is just empty ({}) and the others still show — a host that sleeps at night
# should not blank its neighbours.

import os
import re

from ._common import PREFIX, env, labelled_urls, request, source

_VAR = re.compile(rf"^{PREFIX}JSON_([A-Z0-9_]+)_URL$")


def _make(urls, timeout):
    if list(urls) == [""]:
        return lambda: request(urls[""], timeout=timeout)

    def fetch():
        out = {}
        for label, url in urls.items():
            try:
                out[label] = request(url, timeout=timeout)
            except (OSError, ValueError):
                out[label] = {}
        return out
    return fetch


for _var, _value in sorted(os.environ.items()):
    _m = _VAR.match(_var)
    if not _m or not _value.strip():
        continue
    _name = _m.group(1)
    _fetch = _make(labelled_urls(_value), int(env(f"JSON_{_name}_TIMEOUT", "10")))
    source(_name.lower(), every=int(env(f"JSON_{_name}_EVERY", "10")), env=(f"JSON_{_name}_URL",))(_fetch)
