"""Secrets from files: every LABHUD_<NAME> may be given as LABHUD_<NAME>_FILE instead, a path whose
content becomes the value (Docker and Compose secrets, systemd LoadCredential=). A key kept in a
file is not in `docker inspect`, nor in the environment of every process the container starts.

It also reads a .env file of its own: LABHUD_ENV_FILE, or `.env` next to the config file when
there is one (the setup page writes it there). Its LABHUD_* lines fill only the variables the
environment does not already set, so compose's `environment:` and `env_file:` still win.

Imported first by server.py: the other modules read their settings when they are imported.
A value set directly wins over a file, and a file that cannot be read stops the start, with the
variable's name: a missing key would otherwise show up much later, as a source "not configured".
"""

import os

PREFIX = "LABHUD_"
SUFFIX = "_FILE"


def dotenv_path(environ=os.environ):
    """LABHUD_ENV_FILE, else .env in the config file's folder (LABHUD_CONFIG, or this folder)."""
    if environ.get(PREFIX + "ENV_FILE"):
        return environ[PREFIX + "ENV_FILE"]
    config = environ.get(PREFIX + "CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    return os.path.join(os.path.dirname(os.path.abspath(config)), ".env")


def load_dotenv(path, environ=os.environ):
    """KEY=value lines (comments, blank lines, `export ` and matching quotes allowed); only LABHUD_*
    names, and only those not already set. Returns the names it set. A missing file is no error
    unless LABHUD_ENV_FILE named it."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        if environ.get(PREFIX + "ENV_FILE"):
            raise SystemExit(f"LABHUD_ENV_FILE: {path} does not exist")
        return []
    except OSError as e:
        raise SystemExit(f"cannot read {path} ({e.strerror})")
    done = []
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or line.startswith("#") or not name.startswith(PREFIX) or not name.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value and not environ.get(name):
            environ[name] = value
            done.append(name)
    return done


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


DOTENV = dotenv_path()
FROM_DOTENV = load_dotenv(DOTENV)
LOADED = load()
