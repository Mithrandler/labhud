"""Secrets from files: every LABHUD_<NAME> may be given as LABHUD_<NAME>_FILE instead, a path whose
content becomes the value (Docker and Compose secrets, systemd LoadCredential=). A key kept in a
file is not in `docker inspect`, nor in the environment of every process the container starts.

It also reads a .env file of its own: LABHUD_ENV_FILE, or `.env` next to the config file when
there is one (the setup page writes it there). Its LABHUD_* lines fill only the variables the
environment does not already set, so compose's `environment:` and `env_file:` still win.

With LABHUD_DATA it also reads secrets.env there (managed.py: the keys typed into ⚙ > Setup),
before anything else and the other way round: its values win over the environment.

Imported first by server.py: the other modules read their settings when they are imported.
A value set directly wins over a file, and a file that cannot be read stops the start, with the
variable's name: a missing key would otherwise show up much later, as a source "not configured".
"""

import os

PREFIX = "LABHUD_"
SUFFIX = "_FILE"
SECRETS_NAME = "secrets.env"
# Never taken from secrets.env: where labhud's own files are and how it listens and acts.
NOT_MANAGED = frozenset({"LABHUD_DATA", "LABHUD_CONFIG", "LABHUD_ENV_FILE", "LABHUD_PORT", "LABHUD_HOSTS",
                         "LABHUD_HTTP", "LABHUD_TLS_CERT", "LABHUD_TLS_KEY", "LABHUD_SETUP", "LABHUD_DEMO",
                         "LABHUD_ADMIN", "LABHUD_ACTION_URL", "LABHUD_ACTION_SECRET"})


def secrets_path(environ=os.environ):
    """LABHUD_DATA/secrets.env, or "" without LABHUD_DATA."""
    d = environ.get(PREFIX + "DATA")
    return os.path.join(d, SECRETS_NAME) if d else ""


def managed(environ=os.environ):
    """labhud keeps the config itself (LABHUD_DATA/config.toml exists, see managed.py)."""
    d = environ.get(PREFIX + "DATA")
    return bool(d) and os.path.isfile(os.path.join(d, "config.toml"))


def dotenv_path(environ=os.environ):
    """Where a new key goes: secrets.env once labhud keeps the config, else given_dotenv()."""
    if managed(environ) and not environ.get(PREFIX + "ENV_FILE"):
        return secrets_path(environ)
    return given_dotenv(environ)


def given_dotenv(environ=os.environ):
    """LABHUD_ENV_FILE, else .env in the config file's folder (LABHUD_CONFIG, or this folder)."""
    if environ.get(PREFIX + "ENV_FILE"):
        return environ[PREFIX + "ENV_FILE"]
    config = environ.get(PREFIX + "CONFIG") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
    return os.path.join(os.path.dirname(os.path.abspath(config)), ".env")


def load_dotenv(path, environ=os.environ, override=False):
    """KEY=value lines (comments, blank lines, `export ` and matching quotes allowed); only LABHUD_*
    names, and only those not already set (with `override`, all but NOT_MANAGED, whatever is set).
    Returns the names it set. A missing file is no error unless LABHUD_ENV_FILE named it."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        if environ.get(PREFIX + "ENV_FILE") == path:
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
        if override and name in NOT_MANAGED:
            continue
        if value and (override and environ.get(name) != value or not environ.get(name)):
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


GIVEN = dict(os.environ)  # the environment as started, for a restart (managed.restart)
FROM_SECRETS = load_dotenv(secrets_path(), override=True) if secrets_path() else []
DOTENV = dotenv_path()
FROM_DOTENV = load_dotenv(given_dotenv())
LOADED = load()
