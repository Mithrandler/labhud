"""The config labhud keeps itself, for ⚙ > Setup: config.toml and secrets.env in LABHUD_DATA.

Until then labhud reads the config it is given (LABHUD_CONFIG, often mounted read-only) and the
keys in its environment. **Take over** copies that config into LABHUD_DATA, with every file it
includes merged in, and from then on that copy is the config: config.config_path() returns it,
⚙ > Setup writes it, and the mounted one is no longer read.

secrets.env (mode 600) holds the keys typed into ⚙ > Setup. Its values **win over** the
environment (envfiles.py applies it first), so a key changed in the page is not undone by an old
one in compose's environment. A new key needs a restart of the process (restart() below: the
same PID, so Docker sees nothing), because every source reads its settings when imported; a new
config does not, the server reads it again by itself.
"""

import os
import re
import sys
import threading
import time
import tomllib

import config
import envfiles
import tomlw

CONFIG_NAME = "config.toml"
SECRETS_NAME = envfiles.SECRETS_NAME
HEADER = ("# Kept by labhud (⚙ > Setup). Edit it here or from the page; it is read again when it changes.\n"
          "# Taken over from {source} on {when}.\n\n")
# Never set from the page: where labhud's own files are, and how it listens.
FORBIDDEN = envfiles.NOT_MANAGED
NAME_RE = re.compile(r"LABHUD_[A-Z0-9_]{1,64}")
_lock = threading.Lock()


def data_dir():
    return os.environ.get("LABHUD_DATA", "")


def config_file():
    return os.path.join(data_dir(), CONFIG_NAME) if data_dir() else ""


def secrets_file():
    return envfiles.secrets_path()


def active():
    """The config in LABHUD_DATA is the one in use."""
    return bool(config_file()) and os.path.isfile(config_file())


def mounted():
    """The config labhud was given (read before a take over)."""
    return config.given_path()


def writable():
    """None when LABHUD_DATA can hold the config, else why not."""
    d = data_dir()
    if not d:
        return "LABHUD_DATA is not set (mount a folder labhud can write, see compose.example.yaml)"
    if not os.path.isdir(d):
        return f"LABHUD_DATA: {d} is not a folder"
    if not os.access(d, os.W_OK):
        return f"LABHUD_DATA: {d} is not writable by uid {os.getuid()}"
    return None


def status():
    """What ⚙ > Setup shows before anything else. Never a value, only names."""
    given = mounted()
    out = {"managed": active(), "problem": writable(), "mounted": given,
           "secrets": sorted(read_secrets())}
    if not out["managed"]:
        out["includes"] = [os.path.relpath(f, os.path.dirname(os.path.abspath(given)))
                           for f in config.included(given)]
        out["can_take_over"] = out["problem"] is None and os.path.isfile(given)
    return out


def _write(path, text, mode):
    """Atomically, through a temporary file in the same folder; the old one stays as .bak."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read()
            _replace(path + ".bak", old, mode)
        except OSError:
            pass
    _replace(path, text, mode)


def _replace(path, text, mode):
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def take_over(now=None):
    """Copies the given config (its includes merged in) into LABHUD_DATA. Returns the new path.
    Raises ValueError, writing nothing, when it cannot: no folder, already done, or a config
    that would not load (then the problems are in the message)."""
    with _lock:
        problem = writable()
        if problem:
            raise ValueError(problem)
        if active():
            raise ValueError("the config is already kept by labhud")
        given = mounted()
        try:
            with open(given, "rb") as f:
                raw = tomllib.load(f)
        except FileNotFoundError:
            raise ValueError(f"{given}: no config to take over") from None
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ValueError(f"{given}: {e}") from None
        try:
            raw = config._with_includes(raw, given)
        except config.ConfigError as e:
            raise ValueError("; ".join(e.problems)) from None
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        text = tomlw.dumps(raw, HEADER.format(source=given, when=when))
        dest = config_file()
        _check(text, dest)
        _replace(dest, text, 0o600)
        return dest


def _check(text, path):
    try:
        config.loads(text, path)
    except config.ConfigError as e:
        raise ValueError("; ".join(e.problems)) from None


def read_config():
    """The kept config as data (as tomllib reads it). Raises ValueError before a take over."""
    if not active():
        raise ValueError("take over the config first")
    with open(config_file(), "rb") as f:
        return tomllib.load(f)


def save_config(raw):
    """Writes `raw` as the kept config, if it loads. Raises ValueError (nothing written) if not.
    The server notices the new file and applies it, without a restart."""
    with _lock:
        if not active():
            raise ValueError("take over the config first")
        try:
            text = tomlw.dumps(raw, _header())
        except TypeError as e:
            raise ValueError(str(e)) from None
        _check(text, config_file())
        _write(config_file(), text, 0o600)


def save_text(text):
    """Writes the editor's text as the kept config, if it loads (comments and layout stay as typed)."""
    with _lock:
        if not active():
            raise ValueError("take over the config first")
        _check(text, config_file())
        _write(config_file(), text, 0o600)


def _header():
    """The comment at the top of the kept file, kept as it is."""
    try:
        with open(config_file(), encoding="utf-8") as f:
            lines = []
            for line in f:
                if not line.startswith("#"):
                    break
                lines.append(line.rstrip("\n"))
            return "\n".join(lines) + "\n" if lines else ""
    except OSError:
        return ""


def read_secrets():
    """{name: value} from secrets.env (empty without one)."""
    path = secrets_file()
    if not path:
        return {}
    env = {}
    try:
        envfiles.load_dotenv(path, env)
    except SystemExit:
        return {}
    return env


def check_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or name.endswith("_FILE"):
        raise ValueError(f"{name!r} is not a LABHUD_* setting")
    if name in FORBIDDEN:
        raise ValueError(f"{name} is not set from the page (it is part of how labhud starts)")


def set_secrets(values):
    """Sets (or with "" removes) keys in secrets.env, mode 600. Returns the names that changed.
    Raises ValueError, writing nothing, for a name the page may not set or a value on two lines.
    A change takes effect at restart()."""
    import onboard
    with _lock:
        problem = writable()
        if problem:
            raise ValueError(problem)
        for name, value in values.items():
            check_name(name)
            if not isinstance(value, str) or "\n" in value or "\r" in value:
                raise ValueError(f"{name}: the value must be one line of text")
        old = read_secrets()
        changed = sorted(k for k, v in values.items() if old.get(k, "") != v.strip())
        if not changed:
            return []
        path = secrets_file()
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = "# Keys set in ⚙ > Setup. They win over the environment. Kept by labhud.\n"
        _replace(path, onboard.merge_env(text, {k: values[k].strip() for k in changed}), 0o600)
        return changed


def restart(delay=1.5):
    """The same process starts again with the environment it was given (so a key removed from
    secrets.env is gone, not left over from before): execv keeps the PID. In a thread, after
    `delay`, so the answer to the request that asked for it gets out first."""
    def go():
        time.sleep(delay)
        sys.stdout.flush()
        os.execve(sys.executable, [sys.executable] + sys.argv, envfiles.GIVEN)
    threading.Thread(target=go, daemon=True, name="restart").start()
