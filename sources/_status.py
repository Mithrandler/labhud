# Host status: ICMP ping or a plain TCP connection. Not a registered source: the server polls it
# itself, because the targets come from the cards in config.toml.

import socket
import subprocess

from ._common import in_window


def _ping(host, timeout=1):
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", str(timeout), host],
                           capture_output=True, timeout=timeout + 2)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _tcp(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def host_status(targets, quiet_hours=None):
    """targets: {card_id: ("ping", host) | ("tcp", host, port)} -> {card_id: True | False | "scheduled"}

    A host that is off on schedule is not an outage: it is not coloured red and not even polled
    in its window. Without this, a NAS that sleeps at night would show "down" for eight hours.
    """
    quiet_hours = quiet_hours or {}
    out = {}
    for ident, t in targets.items():
        if in_window(quiet_hours.get(ident)):
            out[ident] = "scheduled"
            continue
        out[ident] = _ping(t[1]) if t[0] == "ping" else _tcp(t[1], t[2])
    return out
