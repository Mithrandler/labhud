"""⚙ > System > Backup and restore: config.toml and LabHUD's data (LABHUD_DATA) in one .tar.gz.

    POST /api/backup/list      the backups kept, the settings, the last one's result
    POST /api/backup/create    {secrets, password?}: one now
    POST /api/backup/settings  {daily, keep, at, password?, forget_password?}
    POST /api/backup/download  {name}: the file, base64 (a route answers JSON)
    POST /api/backup/upload    {id?, part, last, data}: a file from the browser, in parts of <= 1 MiB
    POST /api/backup/delete    {name}
    POST /api/backup/restore   {name, password?, word: "RESTORE"}: ⚙'s Danger zone

What is in one (tar, gzip): manifest.json; config.toml (the kept one, or the given one with its
includes merged in); data/labhud.db, a copy of the store without the sessions and one-time codes
(SKIP_KEYS); every other file of LABHUD_DATA under data/ (the audit log, …) but not backups/,
cli.json, *.bak and what a restore leaves behind. With `secrets`, also secrets.env and data/tls/
(the HTTPS key the phones pin), and then the whole file is encrypted with a password:

    LABHUD-BACKUP-ENC1\\n
    {"kdf": "scrypt", "n": 131072, "r": 8, "p": 1, "salt": <b64 16 bytes>,
     "cipher": "aes-256-gcm", "nonce": <b64 12 bytes>}\\n
    AES-256-GCM(key = scrypt(password, salt), nonce, the .tar.gz, aad = the two lines above)

scrypt is the standard library's (hashlib, OpenSSL); AES-GCM comes from the `cryptography` package
(in the Docker image). Without it, backups still work, only not encrypted ones. A password can be
given for one backup, or kept as LABHUD_BACKUP_PASSWORD (secrets.env): the daily backup then holds
the secrets too, encrypted.

The daily backup runs at `at` o'clock (or the first minute after, if LabHUD was down) and keeps the
last `keep`; manual ones stay until deleted. They are in LABHUD_DATA/backups (mode 700): download
them, a disk that dies takes them with it.

A restore checks the whole file first (only plain files and folders, relative paths, no "..", no
links; config.toml must load; labhud.db must be a sound SQLite store), backs up what is there now
(before-restore), then, with the store locked, puts the backup's files in place and restarts.
Files of LABHUD_DATA the backup does not have are left alone. The sessions of now are kept, but a
backup with another admin needs that admin's password.
"""

import base64
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import tarfile
import threading
import time
import zlib

import config
import envfiles
import managed
import store
import tomlw
from routes import route

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:   # a checkout without it: backups, but no encrypted ones
    AESGCM = InvalidTag = None

DIR_NAME = "backups"
SETTINGS_KEY = "backup_settings"
LAST_KEY = "backup_last"
DEFAULTS = {"daily": True, "keep": 7, "at": 3}
PASSWORD_ENV = "LABHUD_BACKUP_PASSWORD"
MIN_PASSWORD = 8
# Not data: who is signed in now, and the one-time codes. Another part of LabHUD with keys like
# these adds them here (or names them with one of the endings).
SKIP_KEYS = {"admin-sessions", "pair_codes", "agent_codes"}
SKIP_ENDINGS = ("-sessions", "_sessions", "_codes", "-codes", "-tickets", "_tickets", "-pending", "_pending")
# Files of LABHUD_DATA that are not copied as they are (or not at all)
SKIP_FILES = {"cli.json", "labhud.db", "labhud.db-wal", "labhud.db-shm", "labhud.db-journal",
              envfiles.SECRETS_NAME, managed.CONFIG_NAME}
SECRET_DIRS = {"tls"}
MAGIC = b"LABHUD-BACKUP-ENC1\n"
SCRYPT = {"n": 2 ** 17, "r": 8, "p": 1}
MAX_FILE = 256 * 1024 * 1024        # one backup, as kept or uploaded
MAX_UNPACKED = 1024 * 1024 * 1024   # what it unpacks to
MAX_MEMBERS = 5000
PART = 1024 * 1024                  # one part of an upload
NAME_RE = re.compile(r"labhud-(manual|auto|before-restore|upload)-\d{8}-\d{6}(-\d{1,3})?\.tar\.gz(\.enc)?")
PART_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")
KEEP_OTHER = {"before-restore": 3, "upload": 3}

_lock = threading.Lock()
on_event = lambda text, bad: None   # noqa: E731  (server.py: the timeline and notify's "backup_failed")


class Refused(ValueError):
    """What the user is told (400)."""


# --- where ---

def data_dir():
    return store.DIR or managed.data_dir()


def folder(create=True):
    d = os.path.join(data_dir(), DIR_NAME)
    if create:
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    return d


def problem():
    if not data_dir():
        return "LABHUD_DATA is not set: there is nothing to back up, and nowhere to keep a backup"
    return managed.writable()


def settings():
    kept = store.load(SETTINGS_KEY) if store.enabled() else None
    return {**DEFAULTS, **({k: kept[k] for k in DEFAULTS if k in kept} if isinstance(kept, dict) else {})}


def password_set():
    return bool(os.environ.get(PASSWORD_ENV))


# --- encryption ---

def encrypt(data, password):
    if AESGCM is None:
        raise Refused("encrypted backups need the Python package cryptography (it is in the Docker image)")
    salt, nonce = os.urandom(16), os.urandom(12)
    head = {"kdf": "scrypt", **SCRYPT, "salt": base64.b64encode(salt).decode(),
            "cipher": "aes-256-gcm", "nonce": base64.b64encode(nonce).decode()}
    line = json.dumps(head, separators=(",", ":")).encode() + b"\n"
    key = _key(password, salt, SCRYPT["n"], SCRYPT["r"], SCRYPT["p"])
    return MAGIC + line + AESGCM(key).encrypt(nonce, data, MAGIC + line)


def decrypt(blob, password):
    if AESGCM is None:
        raise Refused("this backup is encrypted, and decrypting needs the Python package cryptography")
    if not password:
        raise Refused("this backup is encrypted: type its password")
    end = blob.find(b"\n", len(MAGIC), len(MAGIC) + 1024)
    try:
        if end < 0:
            raise ValueError
        line = blob[len(MAGIC):end + 1]
        head = json.loads(line)
        n, r, p = int(head["n"]), int(head["r"]), int(head["p"])
        salt, nonce = base64.b64decode(head["salt"], validate=True), base64.b64decode(head["nonce"], validate=True)
        if head.get("kdf") != "scrypt" or head.get("cipher") != "aes-256-gcm" or len(nonce) != 12 or len(salt) < 16:
            raise ValueError
        # a made-up header must not make scrypt take all the memory
        if not (2 ** 14 <= n <= 2 ** 20 and n & (n - 1) == 0 and 1 <= r <= 16 and 1 <= p <= 4):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise Refused("not a LabHUD backup (the encrypted file's header is damaged)") from None
    try:
        return AESGCM(_key(password, salt, n, r, p)).decrypt(nonce, blob[end + 1:], MAGIC + line)
    except InvalidTag:
        raise Refused("wrong password, or the file is damaged") from None


def _key(password, salt, n, r, p):
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=160 * r * n + (1 << 20), dklen=32)


# --- making one ---

def _skip_key(name):
    return name in SKIP_KEYS or name.endswith(SKIP_ENDINGS)


def _db_copy():
    """The store as bytes, without the sessions and codes; None without one."""
    path = os.path.join(data_dir(), "labhud.db")
    if not os.path.isfile(path):
        return None
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    mem = sqlite3.connect(":memory:")
    try:
        src.backup(mem)
        names = [r[0] for r in mem.execute("SELECT name FROM kv")]
        mem.executemany("DELETE FROM kv WHERE name = ?", [(n,) for n in names if _skip_key(n)])
        mem.commit()
        mem.execute("VACUUM")
        return mem.serialize()
    finally:
        src.close()
        mem.close()


def _config_text():
    """config.toml as LabHUD reads it now: the kept one, or the given one with its includes merged."""
    if managed.active():
        with open(managed.config_file(), encoding="utf-8") as f:
            return f.read()
    given = config.given_path()
    if not os.path.isfile(given):
        return None
    import tomllib
    with open(given, "rb") as f:
        raw = tomllib.load(f)
    raw = config._with_includes(raw, given)
    return tomlw.dumps(raw, f"# From {given}, backed up by labhud on {time.strftime('%Y-%m-%d %H:%M')}.\n\n")


def _data_files(with_secrets):
    """(relative path, absolute path) of the plain files of LABHUD_DATA a backup takes as they are."""
    root = data_dir()
    out = []
    for here, dirs, files in os.walk(root, followlinks=False):
        rel_here = os.path.relpath(here, root)
        top = rel_here == "."
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(here, d)) and not _leftover(d)
                         and not (top and (d == DIR_NAME or (d in SECRET_DIRS and not with_secrets))))
        for name in sorted(files):
            full = os.path.join(here, name)
            rel = name if top else os.path.join(rel_here, name)
            if (top and name in SKIP_FILES) or _leftover(name) or name.endswith(".bak") or ".tmp-" in name \
                    or os.path.islink(full) or not os.path.isfile(full):
                continue
            out.append((rel.replace(os.sep, "/"), full))
    return out


def _leftover(name):
    return name.startswith((".restore", ".upload")) or ".before-restore" in name


def archive(with_secrets):
    """The .tar.gz, as bytes."""
    buf = io.BytesIO()
    now = int(time.time())
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        def add(name, data):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), now, 0o600
            tar.addfile(info, io.BytesIO(data))
        manifest = {"format": 1, "created": now, "version": os.environ.get("LABHUD_VERSION", "dev"),
                    "secrets": bool(with_secrets), "managed": managed.active()}
        add("manifest.json", json.dumps(manifest, indent=1).encode())
        text = _config_text()
        if text is not None:
            add("config.toml", text.encode())
        db = _db_copy()
        if db is not None:
            add("data/labhud.db", db)
        for rel, full in _data_files(with_secrets):
            with open(full, "rb") as f:
                add("data/" + rel, f.read())
        if with_secrets and os.path.isfile(managed.secrets_file() or ""):
            with open(managed.secrets_file(), "rb") as f:
                add("secrets.env", f.read())
    return buf.getvalue()


def _name(kind, encrypted, now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    base, n = f"labhud-{kind}-{stamp}", 0
    ext = ".tar.gz.enc" if encrypted else ".tar.gz"
    while os.path.exists(os.path.join(folder(), base + ("" if not n else f"-{n}") + ext)):
        n += 1
    return base + ("" if not n else f"-{n}") + ext


def _write(path, data):
    tmp = f"{path}.tmp-{os.getpid()}-{threading.get_ident()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create(kind="manual", with_secrets=False, password=None):
    """Makes one backup in LABHUD_DATA/backups. Returns its name. A password encrypts it (and the
    secrets need one). Raises Refused."""
    why = problem()
    if why:
        raise Refused(why)
    if with_secrets and not password:
        raise Refused("the secrets go only into an encrypted backup: give it a password")
    if password and len(password) < MIN_PASSWORD:
        raise Refused(f"a password of at least {MIN_PASSWORD} characters")
    if password and AESGCM is None:
        raise Refused("encrypted backups need the Python package cryptography (it is in the Docker image)")
    with _lock:
        data = archive(with_secrets)
        if password:
            data = encrypt(data, password)
        if len(data) > MAX_FILE:
            raise Refused(f"the backup would be {len(data) >> 20} MiB, more than {MAX_FILE >> 20}")
        name = _name(kind, bool(password))
        _write(os.path.join(folder(), name), data)
        prune()
    return name


def listing():
    try:
        names = os.listdir(folder(create=False))
    except OSError:
        return []
    out = []
    for n in names:
        m = NAME_RE.fullmatch(n)
        if not m:
            continue
        try:
            st = os.stat(os.path.join(folder(create=False), n))
        except OSError:
            continue
        out.append({"name": n, "kind": m.group(1), "size": st.st_size, "t": int(st.st_mtime), "encrypted": n.endswith(".enc")})
    return sorted(out, key=lambda b: (b["t"], b["name"]), reverse=True)


def prune():
    """The last `keep` daily backups, the last few of a restore's and of uploads; manual ones stay."""
    keep = {"auto": settings()["keep"], **KEEP_OTHER}
    seen = {}
    for b in listing():
        if b["kind"] in keep:
            seen[b["kind"]] = seen.get(b["kind"], 0) + 1
            if seen[b["kind"]] > keep[b["kind"]]:
                try:
                    os.unlink(os.path.join(folder(), b["name"]))
                except OSError:
                    pass


def _path(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise Refused("no such backup")
    path = os.path.join(folder(create=False), name)
    if not os.path.isfile(path) or os.path.islink(path):
        raise Refused("no such backup")
    return path


def _remember(ok, kind, name="", error=""):
    if store.enabled():
        store.save(LAST_KEY, {"t": int(time.time()), "ok": ok, "kind": kind, "name": name, "error": error[:200]})


# --- the daily one ---

def due(now=None):
    """The daily backup is due: on, past `at` today, and none made today yet."""
    st = settings()
    if not st["daily"] or problem():
        return False
    lt = time.localtime(now)
    if lt.tm_hour < st["at"]:
        return False
    today = time.strftime("%Y%m%d", lt)
    return not any(b["kind"] == "auto" and b["name"].split("-")[2] == today for b in listing())


def daily(now=None):
    """One turn of the scheduler: makes the daily backup if it is due. Never raises."""
    if not due(now):
        return None
    pw = os.environ.get(PASSWORD_ENV) or None
    try:
        name = create("auto", with_secrets=bool(pw), password=pw)
        _remember(True, "auto", name)
        print(f"backup: {name}", flush=True)
        return name
    except Exception as e:   # a full disk: said once a day, and LabHUD goes on
        error = str(e) or type(e).__name__
        _remember(False, "auto", error=error)
        print(f"backup failed: {error}", flush=True)
        on_event(f"Daily backup failed: {error[:120]}", True)
        return None


def scheduler(stop, every=60):
    while not stop.wait(every):
        daily()


def start(log=None):
    """server.py: the daily backup's thread; `log(text, bad)` for its failures."""
    global on_event
    if log:
        on_event = log
    if data_dir():
        threading.Thread(target=scheduler, args=(threading.Event(),), daemon=True, name="backup").start()


# --- reading one back ---

def _member_name(name):
    """A safe relative path, or None: no absolute path, no "..", no empty or odd part."""
    if not isinstance(name, str) or "\\" in name or "\0" in name or name.startswith("/"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or not PART_RE.fullmatch(p) for p in parts):
        return None
    return "/".join(parts)


def _allowed(name):
    if name in ("manifest.json", "config.toml", "secrets.env"):
        return True
    if not name.startswith("data/"):
        return False
    rel = name[5:]
    top = rel.split("/")[0]
    return bool(rel) and top != DIR_NAME and (rel == "labhud.db" or top not in SKIP_FILES) and not _leftover(top) \
        and not any(_leftover(p) for p in rel.split("/"))


def unpack(blob, password=None):
    """{name: bytes} of a backup, checked through (see the top). Raises Refused."""
    if len(blob) > MAX_FILE:
        raise Refused(f"more than {MAX_FILE >> 20} MiB")
    if blob.startswith(MAGIC):
        blob = decrypt(blob, password)
    if not blob.startswith(b"\x1f\x8b"):
        raise Refused("not a LabHUD backup (neither .tar.gz nor encrypted)")
    files, total = {}, 0
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            for i, m in enumerate(tar):
                if i >= MAX_MEMBERS:
                    raise Refused(f"more than {MAX_MEMBERS} files")
                name = _member_name(m.name)
                if name is None:
                    raise Refused(f"refused: an unsafe path in the backup ({m.name[:60]!r})")
                if m.isdir():
                    continue
                if not m.isreg():
                    raise Refused(f"refused: {name} is a link or a device, not a file")
                if not _allowed(name):
                    raise Refused(f"refused: {name} is not something a LabHUD backup holds")
                total += m.size
                if total > MAX_UNPACKED:
                    raise Refused(f"refused: it unpacks to more than {MAX_UNPACKED >> 20} MiB")
                if name in files:
                    raise Refused(f"refused: {name} is in it twice")
                files[name] = tar.extractfile(m).read(m.size + 1)
                if len(files[name]) != m.size:
                    raise Refused("the file is damaged")
    except (tarfile.TarError, EOFError, OSError, gzip.BadGzipFile, zlib.error) as e:
        raise Refused(f"the file is damaged ({type(e).__name__})") from None
    _check(files)
    return files


def _check(files):
    try:
        manifest = json.loads(files.get("manifest.json", b"null"))
    except ValueError:
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("format") != 1:
        raise Refused("not a LabHUD backup (no manifest.json of format 1)")
    if "config.toml" in files:
        try:
            config.loads(files["config.toml"].decode("utf-8"), os.path.join(data_dir(), managed.CONFIG_NAME))
        except UnicodeDecodeError:
            raise Refused("config.toml is not text") from None
        except config.ConfigError as e:
            raise Refused("its config.toml does not load: " + "; ".join(e.problems)[:300]) from None
    if "data/labhud.db" in files:
        _check_db(files["data/labhud.db"])
    if "secrets.env" in files:
        try:
            lines = files["secrets.env"].decode("utf-8").splitlines()
        except UnicodeDecodeError:
            raise Refused("secrets.env is not text") from None
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name = line.removeprefix("export ").partition("=")[0].strip()
            try:
                managed.check_name(name)
            except ValueError as e:
                raise Refused(f"secrets.env: {e}") from None


def _check_db(data):
    if not data.startswith(b"SQLite format 3\x00"):
        raise Refused("data/labhud.db is not a LabHUD store")
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(data)
        ok = db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        cols = [r[1] for r in db.execute("PRAGMA table_info(kv)")]
        if not ok or cols[:2] != ["name", "value"]:
            raise Refused("data/labhud.db is damaged, or not a LabHUD store")
    except sqlite3.Error:
        raise Refused("data/labhud.db is damaged, or not a LabHUD store") from None
    finally:
        db.close()


# --- restoring ---

def restore(name, password=None, restart=True):
    """Checks backup `name`, backs up what is there now, stages the files and (with `restart`)
    puts them in place and restarts LabHUD a moment later. Returns the name of the before-restore
    backup. Raises Refused, changing nothing."""
    why = problem()
    if why:
        raise Refused(why)
    with open(_path(name), "rb") as f:
        blob = f.read()
    files = unpack(blob, password)
    keep_pw = os.environ.get(PASSWORD_ENV) or (password if blob.startswith(MAGIC) else None)
    if keep_pw and len(keep_pw) < MIN_PASSWORD:
        keep_pw = None
    if keep_pw and AESGCM is None:
        keep_pw = None
    before = create("before-restore", with_secrets=bool(keep_pw), password=keep_pw)
    staging = stage(files, keep_secrets_aside=not keep_pw)
    if restart:
        threading.Thread(target=_finish, args=(staging,), daemon=True, name="restore").start()
    return before


def stage(files, keep_secrets_aside=False):
    """The checked files in LABHUD_DATA/.restore-staging (mode 700), ready for apply()."""
    root = data_dir()
    staging = os.path.join(root, ".restore-staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, mode=0o700)
    for name, data in files.items():
        path = os.path.join(staging, *name.split("/"))
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        _write(path, data)
    if keep_secrets_aside:
        open(os.path.join(staging, ".aside"), "w").close()
    return staging


def _finish(staging, delay=1.0):
    time.sleep(delay)
    sys.stdout.flush()
    with store._lock:   # nothing else writes the store from here on: the process is replaced
        try:
            apply(staging, locked=True)
            print("restore: done; restarting", flush=True)
        except Exception as e:
            print(f"restore failed halfway: {e!r}; the before-restore backup has what was there", flush=True)
        os.execve(sys.executable, [sys.executable] + sys.argv, envfiles.GIVEN)


def _safe_dest(root, rel):
    """LABHUD_DATA/<rel>, refusing a path that would leave it through a link already there."""
    dest = os.path.join(root, *rel.split("/"))
    real_root = os.path.realpath(root)
    parent = os.path.dirname(dest)
    probe = parent
    while len(probe) > len(root):
        if os.path.islink(probe):
            raise Refused(f"{rel}: a link in the way")
        probe = os.path.dirname(probe)
    if os.path.islink(dest):
        os.unlink(dest)
    if not os.path.realpath(parent).startswith(real_root + os.sep) and os.path.realpath(parent) != real_root:
        raise Refused(f"{rel}: outside LABHUD_DATA")
    return dest


def apply(staging, locked=False):
    """Puts the staged files in place: the store's content (copied into the open database, the
    sessions of now kept), config.toml, secrets.env, the other files. With `locked`, the caller
    holds store._lock."""
    root = data_dir()
    aside = os.path.exists(os.path.join(staging, ".aside"))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    db_new = os.path.join(staging, "data", "labhud.db")
    if os.path.isfile(db_new):
        _restore_db(db_new, locked)
    conf = os.path.join(staging, "config.toml")
    if os.path.isfile(conf):
        with open(conf, encoding="utf-8") as f:
            managed._write(os.path.join(root, managed.CONFIG_NAME), f.read(), 0o600)
    sec = os.path.join(staging, "secrets.env")
    if os.path.isfile(sec):
        live = os.path.join(root, envfiles.SECRETS_NAME)
        if aside and os.path.isfile(live):
            shutil.copy2(live, f"{live}.before-restore-{stamp}")
            os.chmod(f"{live}.before-restore-{stamp}", 0o600)
        with open(sec, "rb") as f:
            _write(live, f.read())
    data = os.path.join(staging, "data")
    moved = set()
    for here, _dirs, names in os.walk(data):
        for n in names:
            full = os.path.join(here, n)
            rel = os.path.relpath(full, data).replace(os.sep, "/")
            if rel == "labhud.db":
                continue
            top = rel.split("/")[0]
            if aside and top in SECRET_DIRS and top not in moved and os.path.isdir(os.path.join(root, top)):
                os.rename(os.path.join(root, top), os.path.join(root, f"{top}.before-restore-{stamp}"))
            moved.add(top)
            dest = _safe_dest(root, rel)
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
            with open(full, "rb") as f:
                _write(dest, f.read())
    shutil.rmtree(staging, ignore_errors=True)


def _restore_db(path, locked):
    """The backup's store copied into the live one (sqlite's backup: every page, in one go), then
    the sessions and codes of now put back, and the last backup's result (it is about now)."""
    live = store._db if locked and store._db is not None else None
    own = live is None
    if own:
        live = sqlite3.connect(os.path.join(data_dir(), "labhud.db"))
    try:
        try:
            kept = [r for r in live.execute("SELECT name, value FROM kv") if _skip_key(r[0]) or r[0] == LAST_KEY]
        except sqlite3.Error:
            kept = []
        src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            src.backup(live)
        finally:
            src.close()
        live.executemany("INSERT INTO kv (name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", kept)
        live.commit()
    finally:
        if own:
            live.close()


# --- the routes ---

def _answer(fn):
    """Refused -> 400 with its text; ValueError from managed -> 409."""
    def wrapped(h, body):
        try:
            return fn(h, body)
        except Refused as e:
            return 400, {"ok": False, "error": str(e)}
        except ValueError as e:
            return 409, {"ok": False, "error": str(e)[:300]}
    wrapped.__name__ = fn.__name__
    return wrapped


@route("POST", "/api/backup/list")
@_answer
def list_route(h, body):
    last = store.load(LAST_KEY) if store.enabled() else None
    return 200, {"ok": True, "problem": problem(), "encryption": AESGCM is not None, "password_set": password_set(),
                 "settings": settings(), "last": last, "backups": listing() if not problem() else []}


@route("POST", "/api/backup/create")
@_answer
def create_route(h, body):
    with_secrets = body.get("secrets") is True
    typed = body.get("password") if isinstance(body.get("password"), str) else ""
    password = typed or (os.environ.get(PASSWORD_ENV) if with_secrets else "") or None
    try:
        name = create("manual", with_secrets, password)
    except Refused as e:
        _remember(False, "manual", error=str(e))
        raise
    _remember(True, "manual", name)
    print(f"backup: {name} made from {h._who()}" + (" (with the secrets)" if with_secrets else ""), flush=True)
    return 200, {"ok": True, "name": name, "encrypted": bool(password)}


@route("POST", "/api/backup/settings")
@_answer
def settings_route(h, body):
    if not store.enabled():
        raise Refused(problem() or "LABHUD_DATA is not usable")
    st = settings()
    if "daily" in body:
        if not isinstance(body["daily"], bool):
            raise Refused("daily: true or false")
        st["daily"] = body["daily"]
    for key, low, high in (("keep", 1, 90), ("at", 0, 23)):
        if key in body:
            v = body[key]
            if not isinstance(v, int) or isinstance(v, bool) or not low <= v <= high:
                raise Refused(f"{key}: a whole number from {low} to {high}")
            st[key] = v
    pw = body.get("password")
    if pw is not None and (not isinstance(pw, str) or (pw and len(pw) < MIN_PASSWORD)):
        raise Refused(f"a password of at least {MIN_PASSWORD} characters")
    if pw and AESGCM is None:
        raise Refused("encrypted backups need the Python package cryptography (it is in the Docker image)")
    if pw or body.get("forget_password") is True:
        managed.set_secrets({PASSWORD_ENV: pw or ""})
        if pw:
            os.environ[PASSWORD_ENV] = pw    # used from now on; secrets.env has it for the next start
        else:
            os.environ.pop(PASSWORD_ENV, None)
    store.save(SETTINGS_KEY, st)
    print(f"backup: settings {st} from {h._who()}" + (" (password changed)" if pw or body.get("forget_password") else ""), flush=True)
    return 200, {"ok": True, "settings": st, "password_set": password_set()}


@route("POST", "/api/backup/download")
@_answer
def download_route(h, body):
    path = _path(body.get("name"))
    with open(path, "rb") as f:
        data = f.read()
    print(f"backup: {body['name']} downloaded from {h._who()}", flush=True)
    return 200, {"ok": True, "name": body["name"], "size": len(data), "data": base64.b64encode(data).decode()}


@route("POST", "/api/backup/delete")
@_answer
def delete_route(h, body):
    os.unlink(_path(body.get("name")))
    print(f"backup: {body['name']} deleted from {h._who()}", flush=True)
    return 200, {"ok": True}


_uploads = {}   # id -> (path, bytes so far, next part, started)


@route("POST", "/api/backup/upload", limit=PART * 4 // 3 + 4096)
@_answer
def upload_route(h, body):
    why = problem()
    if why:
        raise Refused(why)
    part, last, uid = body.get("part"), body.get("last") is True, body.get("id")
    try:
        data = base64.b64decode(body.get("data") or "", validate=True)
    except (ValueError, TypeError):
        raise Refused("data: base64") from None
    if not isinstance(part, int) or isinstance(part, bool) or len(data) > PART:
        raise Refused(f"part: a number; data: at most {PART >> 10} KiB a part")
    now = time.monotonic()
    for k, (p, _, _, t) in list(_uploads.items()):   # an upload left halfway, an hour ago
        if now - t > 3600:
            _uploads.pop(k, None)
            _unlink(p)
    if part == 0:
        uid = secrets.token_hex(8)
        _uploads[uid] = (os.path.join(folder(), f".upload-{uid}"), 0, 0, now)
        _write(_uploads[uid][0], b"")
    if uid not in _uploads or _uploads[uid][2] != part:
        raise Refused("this upload is not going on (start again)")
    path, size, _, started = _uploads[uid]
    size += len(data)
    if size > MAX_FILE:
        _uploads.pop(uid, None)
        _unlink(path)
        raise Refused(f"more than {MAX_FILE >> 20} MiB")
    with open(path, "ab") as f:
        f.write(data)
    _uploads[uid] = (path, size, part + 1, started)
    if not last:
        return 200, {"ok": True, "id": uid}
    _uploads.pop(uid, None)
    with open(path, "rb") as f:
        head = f.read(len(MAGIC))
    encrypted = head == MAGIC
    if not encrypted and not head.startswith(b"\x1f\x8b"):
        _unlink(path)
        raise Refused("not a LabHUD backup (neither .tar.gz nor encrypted)")
    if not encrypted:   # what can be checked without the password is checked now
        with open(path, "rb") as f:
            blob = f.read()
        try:
            unpack(blob)
        except Refused:
            _unlink(path)
            raise
    name = _name("upload", encrypted)
    os.replace(path, os.path.join(folder(), name))
    prune()
    print(f"backup: {name} uploaded from {h._who()} ({size} bytes)", flush=True)
    return 200, {"ok": True, "name": name, "encrypted": encrypted}


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


@route("POST", "/api/backup/restore")
@_answer
def restore_route(h, body):
    if body.get("word") != "RESTORE":
        raise Refused("type RESTORE to confirm")
    name = body.get("name")
    password = body.get("password") if isinstance(body.get("password"), str) else None
    before = restore(name, password or None)
    print(f"restore: {name} from {h._who()}; what was there is in {before}; restarting", flush=True)
    return 200, {"ok": True, "before": before, "restart": True}
