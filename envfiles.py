"""Secrets from files: every LABHUD_<NAME> may be given as LABHUD_<NAME>_FILE instead, a path whose
content becomes the value (Docker and Compose secrets, systemd LoadCredential=). A key kept in a
file is not in `docker inspect`, nor in the environment of every process the container starts.

Imported first by server.py: the other modules read their settings when they are imported.
A value set directly wins over a file, and a file that cannot be read stops the start, with the
variable's name: a missing key would otherwise show up much later, as a source "not configured".
"""

import os

PREFIX = "LABHUD_"
SUFFIX = "_FILE"


def load(environ=os.environ):
    """Reads every LABHUD_*_FILE into its variable. Returns the names it set (never the values)."""
    done = []
    for name in sorted(k for k in environ if k.startswith(PREFIX) and k.endswith(SUFFIX)):
        target = name[:-len(SUFFIX)]
        if environ.get(target):
            continue
        path = environ[name]
        try:
            with open(path, encoding="utf-8") as f:
                value = f.read().strip()
        except OSError as e:
            raise SystemExit(f"{name}: cannot read {path} ({e.strerror})")
        if not value:
            raise SystemExit(f"{name}: {path} is empty")
        environ[target] = value
        done.append(target)
    return done


LOADED = load()
