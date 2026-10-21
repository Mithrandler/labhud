"""api/backup.py: what a backup holds and leaves out, the encryption, the checks before a restore,
and a whole backup -> restore cycle in a LABHUD_DATA of its own."""

import base64
import io
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from unittest import mock

import managed
import store
from api import backup

CONFIG = 'title = "Backed up"\n\n[[page]]\nid = "main"\ntitle = "MAIN"\n\n  [[page.group]]\n  id = "g"\n  title = "G"\n\n    [[page.group.card]]\n    id = "a"\n    name = "A"\n    ping = "192.0.2.1"\n'


def tar_of(entries):
    """A .tar.gz with (name, bytes | ("link", target) | ("dir",)) entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, what in entries:
            info = tarfile.TarInfo(name)
            if isinstance(what, tuple) and what[0] == "link":
                info.type, info.linkname = tarfile.SYMTYPE, what[1]
                tar.addfile(info)
            elif isinstance(what, tuple) and what[0] == "hard":
                info.type, info.linkname = tarfile.LNKTYPE, what[1]
                tar.addfile(info)
            elif isinstance(what, tuple) and what[0] == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(what)
                tar.addfile(info, io.BytesIO(what))
    return buf.getvalue()


MANIFEST = json.dumps({"format": 1}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        env = {"LABHUD_DATA": self.dir}
        self.patches = [mock.patch.dict(os.environ, env), mock.patch.object(store, "DIR", self.dir),
                        mock.patch.object(store, "_db", None), mock.patch.object(store, "PROBLEM", None),
                        mock.patch.object(backup, "SCRYPT", {"n": 2 ** 14, "r": 8, "p": 1})]
        for p in self.patches:
            p.start()
        os.environ.pop(backup.PASSWORD_ENV, None)
        with open(os.path.join(self.dir, "config.toml"), "w") as f:
            f.write(CONFIG)
        with open(os.path.join(self.dir, "secrets.env"), "w") as f:
            f.write("LABHUD_SONARR_KEY=sekret-key\n")
        store.save("events", [{"name": "kept", "t": 1, "bad": False}])
        store.save("admin-sessions", {"s1": {"user": "boss"}})
        store.save("pair_codes", {"x": {"until": 9e9}})
        store.save("devices", {"d1": {"name": "phone"}})
        os.makedirs(os.path.join(self.dir, "audit"))
        with open(os.path.join(self.dir, "audit", "2026-10.jsonl"), "w") as f:
            f.write('{"who":"boss"}\n')
        os.makedirs(os.path.join(self.dir, "tls"))
        with open(os.path.join(self.dir, "tls", "key.pem"), "w") as f:
            f.write("PRIVATE")
        for name in ("cli.json", "config.toml.bak"):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("no")

    def tearDown(self):
        if store._db is not None:
            store._db.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def read(self, name):
        with open(os.path.join(backup.folder(), name), "rb") as f:
            return f.read()


class Contents(Base):
    def test_plain_backup_has_the_data_but_no_sessions_or_secrets(self):
        name = backup.create("manual")
        self.assertRegex(name, r"^labhud-manual-\d{8}-\d{6}\.tar\.gz$")
        self.assertEqual(os.stat(os.path.join(backup.folder(), name)).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(backup.folder()).st_mode & 0o777, 0o700)
        files = backup.unpack(self.read(name))
        self.assertEqual(sorted(files), ["config.toml", "data/audit/2026-10.jsonl", "data/labhud.db", "manifest.json"])
        self.assertEqual(files["config.toml"].decode(), CONFIG)
        db = sqlite3.connect(":memory:")
        db.deserialize(files["data/labhud.db"])
        keys = {r[0] for r in db.execute("SELECT name FROM kv")}
        self.assertEqual(keys, {"events", "devices"})
        self.assertFalse(json.loads(files["manifest.json"])["secrets"])

    def test_secrets_need_a_password(self):
        with self.assertRaises(backup.Refused):
            backup.create("manual", with_secrets=True)
        with self.assertRaises(backup.Refused):
            backup.create("manual", password="short")

    def test_without_data_dir(self):
        with mock.patch.object(store, "DIR", ""), mock.patch.dict(os.environ, {"LABHUD_DATA": ""}):
            with self.assertRaises(backup.Refused):
                backup.create("manual")

    def test_prune_keeps_the_last_daily_ones(self):
        store.save(backup.SETTINGS_KEY, {"keep": 2})
        for i in range(4):
            with open(os.path.join(backup.folder(), f"labhud-auto-2026101{i}-030000.tar.gz"), "wb") as f:
                f.write(b"x")
            os.utime(os.path.join(backup.folder(), f"labhud-auto-2026101{i}-030000.tar.gz"), (1000 + i, 1000 + i))
        with open(os.path.join(backup.folder(), "labhud-manual-20260101-000000.tar.gz"), "wb") as f:
            f.write(b"x")
        backup.prune()
        names = sorted(b["name"] for b in backup.listing())
        self.assertEqual(names, ["labhud-auto-20261012-030000.tar.gz", "labhud-auto-20261013-030000.tar.gz",
                                 "labhud-manual-20260101-000000.tar.gz"])

    def test_daily_once_a_day_after_its_hour(self):
        import time
        t = time.localtime()
        at = lambda h: time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, 10, 0, 0, 0, -1))  # noqa: E731
        store.save(backup.SETTINGS_KEY, {"at": 3, "daily": True})
        self.assertFalse(backup.due(at(2)))
        self.assertTrue(backup.due(at(4)))
        self.assertIsNotNone(backup.daily(at(4)))
        self.assertFalse(backup.due(at(5)))
        store.save(backup.SETTINGS_KEY, {"daily": False})
        self.assertFalse(backup.due(at(23)))

    def test_daily_failure_is_news(self):
        said = []
        with mock.patch.object(backup, "on_event", lambda text, bad: said.append((text, bad))), \
                mock.patch.object(backup, "due", return_value=True), \
                mock.patch.object(backup, "archive", side_effect=OSError("disk full")):
            self.assertIsNone(backup.daily())
        self.assertEqual(said, [("Daily backup failed: disk full", True)])
        self.assertFalse(store.load(backup.LAST_KEY)["ok"])


@unittest.skipIf(backup.AESGCM is None, "the cryptography package is not installed")
class Encryption(Base):
    def test_round_trip_and_wrong_password(self):
        blob = backup.encrypt(b"\x1f\x8bhello", "correct horse")
        self.assertTrue(blob.startswith(backup.MAGIC))
        self.assertNotIn(b"hello", blob)
        self.assertEqual(backup.decrypt(blob, "correct horse"), b"\x1f\x8bhello")
        with self.assertRaisesRegex(backup.Refused, "wrong password"):
            backup.decrypt(blob, "wrong horse")
        with self.assertRaisesRegex(backup.Refused, "password"):
            backup.decrypt(blob, "")

    def test_the_header_is_authenticated_and_bounded(self):
        blob = backup.encrypt(b"data", "correct horse")
        head_end = blob.index(b"\n", len(backup.MAGIC))
        head = json.loads(blob[len(backup.MAGIC):head_end])
        tampered = dict(head, p=2)
        forged = backup.MAGIC + json.dumps(tampered, separators=(",", ":")).encode() + blob[head_end:]
        with self.assertRaises(backup.Refused):
            backup.decrypt(forged, "correct horse")
        huge = dict(head, n=2 ** 30)
        forged = backup.MAGIC + json.dumps(huge).encode() + blob[head_end:]
        with self.assertRaisesRegex(backup.Refused, "header"):
            backup.decrypt(forged, "correct horse")
        flipped = bytearray(blob)
        flipped[-1] ^= 1
        with self.assertRaises(backup.Refused):
            backup.decrypt(bytes(flipped), "correct horse")

    def test_with_secrets(self):
        name = backup.create("manual", with_secrets=True, password="correct horse")
        self.assertTrue(name.endswith(".tar.gz.enc"))
        blob = self.read(name)
        self.assertNotIn(b"sekret-key", blob)
        files = backup.unpack(blob, "correct horse")
        self.assertIn("secrets.env", files)
        self.assertIn("data/tls/key.pem", files)
        self.assertTrue(json.loads(files["manifest.json"])["secrets"])


class Checks(Base):
    def refused(self, entries, match):
        with self.assertRaisesRegex(backup.Refused, match):
            backup.unpack(tar_of(entries))

    def test_unsafe_paths(self):
        for bad in ("../etc/passwd", "/etc/passwd", "data/../../x", "data/a\\b", "data/.."):
            self.refused([("manifest.json", MANIFEST), (bad, b"x")], "unsafe path|not something")

    def test_links_and_devices(self):
        self.refused([("manifest.json", MANIFEST), ("data/x", ("link", "/etc/passwd"))], "link")
        self.refused([("manifest.json", MANIFEST), ("data/x", ("hard", "manifest.json"))], "link")

    def test_only_what_a_backup_holds(self):
        for bad in ("data/backups/x.tar.gz", "data/secrets.env", "data/cli.json", "other.txt", "data/.restore-staging/x"):
            self.refused([("manifest.json", MANIFEST), (bad, b"x")], "not something")

    def test_manifest_config_db_and_secrets_are_checked(self):
        self.refused([("config.toml", CONFIG.encode())], "manifest")
        self.refused([("manifest.json", MANIFEST), ("config.toml", b"title = 3\nnope = 1\n")], "config.toml")
        self.refused([("manifest.json", MANIFEST), ("data/labhud.db", b"SQLite format 3\x00" + b"\0" * 100)], "labhud.db")
        self.refused([("manifest.json", MANIFEST), ("data/labhud.db", b"not sqlite")], "labhud.db")
        self.refused([("manifest.json", MANIFEST), ("secrets.env", b"LABHUD_DATA=/elsewhere\n")], "secrets.env")
        self.refused([("manifest.json", MANIFEST), ("secrets.env", b"PATH=/x\n")], "secrets.env")

    def test_not_a_backup(self):
        with self.assertRaisesRegex(backup.Refused, "not a LabHUD backup"):
            backup.unpack(b"PK\x03\x04 a zip")
        with self.assertRaisesRegex(backup.Refused, "damaged"):
            backup.unpack(b"\x1f\x8b\x08\x00garbage")

    def test_a_name_cannot_leave_the_folder(self):
        for bad in ("../config.toml", "labhud-manual-20260101-000000.tar.gz/../../x", "x.tar.gz", None):
            with self.assertRaises(backup.Refused):
                backup._path(bad)


class Cycle(Base):
    def test_backup_then_restore(self):
        name = backup.create("manual")
        # things change after the backup
        store.save("events", [{"name": "new", "t": 2, "bad": True}])
        store.save("admin-sessions", {"s2": {"user": "boss"}})
        with open(os.path.join(self.dir, "config.toml"), "w") as f:
            f.write(CONFIG.replace("Backed up", "Changed"))
        with open(os.path.join(self.dir, "audit", "2026-10.jsonl"), "w") as f:
            f.write("changed\n")
        before = backup.restore(name, restart=False)
        self.assertTrue(before.startswith("labhud-before-restore-"))
        staging = os.path.join(self.dir, ".restore-staging")
        self.assertTrue(os.path.isdir(staging))
        backup.apply(staging)
        self.assertFalse(os.path.exists(staging))
        with open(os.path.join(self.dir, "config.toml")) as f:
            self.assertIn("Backed up", f.read())
        with open(os.path.join(self.dir, "audit", "2026-10.jsonl")) as f:
            self.assertEqual(f.read(), '{"who":"boss"}\n')
        self.assertEqual(store.load("events")[0]["name"], "kept")
        self.assertEqual(store.load("admin-sessions"), {"s2": {"user": "boss"}})   # who is signed in now stays
        with open(os.path.join(self.dir, "secrets.env")) as f:
            self.assertIn("sekret-key", f.read())   # a plain backup leaves secrets.env alone
        # the state before is a backup of its own
        files = backup.unpack(self.read(before))
        self.assertIn("Changed", files["config.toml"].decode())

    @unittest.skipIf(backup.AESGCM is None, "the cryptography package is not installed")
    def test_encrypted_restore_puts_the_secrets_back(self):
        name = backup.create("manual", with_secrets=True, password="correct horse")
        with open(os.path.join(self.dir, "secrets.env"), "w") as f:
            f.write("LABHUD_SONARR_KEY=other\n")
        with self.assertRaisesRegex(backup.Refused, "password"):
            backup.restore(name, restart=False)
        with self.assertRaisesRegex(backup.Refused, "wrong password"):
            backup.restore(name, "wrong horse", restart=False)
        before = backup.restore(name, "correct horse", restart=False)
        self.assertTrue(before.endswith(".enc"))   # the secrets of now, under the same password
        backup.apply(os.path.join(self.dir, ".restore-staging"))
        with open(os.path.join(self.dir, "secrets.env")) as f:
            self.assertIn("sekret-key", f.read())
        self.assertIn("LABHUD_SONARR_KEY=other", backup.unpack(self.read(before), "correct horse")["secrets.env"].decode())

    def test_a_link_already_in_the_data_folder_is_not_followed(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(outside))
        name = backup.create("manual")
        import shutil
        shutil.rmtree(os.path.join(self.dir, "audit"))
        os.symlink(outside, os.path.join(self.dir, "audit"))
        backup.restore(name, restart=False)
        with self.assertRaisesRegex(backup.Refused, "link"):
            backup.apply(os.path.join(self.dir, ".restore-staging"))
        self.assertEqual(os.listdir(outside), [])


class Routes(Base):
    def call(self, fn, body):
        h = mock.MagicMock()
        h._who.return_value = "test"
        return fn(h, body)

    def test_upload_in_parts_then_listed(self):
        blob = tar_of([("manifest.json", MANIFEST), ("config.toml", CONFIG.encode())])
        half = len(blob) // 2
        code, r = self.call(backup.upload_route, {"part": 0, "data": base64.b64encode(blob[:half]).decode()})
        self.assertEqual(code, 200, r)
        code, r = self.call(backup.upload_route, {"id": r["id"], "part": 1, "last": True,
                                                  "data": base64.b64encode(blob[half:]).decode()})
        self.assertEqual(code, 200, r)
        self.assertTrue(r["name"].startswith("labhud-upload-"))
        code, listed = self.call(backup.list_route, {})
        self.assertIn(r["name"], [b["name"] for b in listed["backups"]])
        code, got = self.call(backup.download_route, {"name": r["name"]})
        self.assertEqual(base64.b64decode(got["data"]), blob)

    def test_upload_refuses_a_bad_file_and_parts_out_of_order(self):
        code, r = self.call(backup.upload_route, {"part": 0, "last": True, "data": base64.b64encode(tar_of(
            [("manifest.json", MANIFEST), ("../x", b"x")])).decode()})
        self.assertEqual(code, 400)
        self.assertEqual([n for n in os.listdir(backup.folder()) if n.startswith(".upload")], [])
        code, r = self.call(backup.upload_route, {"part": 0, "data": ""})
        code, r = self.call(backup.upload_route, {"id": r["id"], "part": 5, "data": ""})
        self.assertEqual(code, 400)

    def test_restore_needs_the_word(self):
        name = backup.create("manual")
        code, r = self.call(backup.restore_route, {"name": name})
        self.assertEqual(code, 400)
        self.assertIn("RESTORE", r["error"])

    def test_settings(self):
        code, r = self.call(backup.settings_route, {"daily": False, "keep": 3, "at": 5})
        self.assertEqual((code, r["settings"]), (200, {"daily": False, "keep": 3, "at": 5}))
        code, r = self.call(backup.settings_route, {"keep": 0})
        self.assertEqual(code, 400)
        code, r = self.call(backup.settings_route, {"password": "short"})
        self.assertEqual(code, 400)

    def test_delete(self):
        name = backup.create("manual")
        self.assertEqual(self.call(backup.delete_route, {"name": name})[0], 200)
        self.assertEqual(self.call(backup.delete_route, {"name": name})[0], 400)


if __name__ == "__main__":
    unittest.main()
