"""updates.py: is there a newer LabHUD, with GitHub's answers made up."""

import json
import os
import tempfile
import unittest

import updates
from tests.test_lock import FakeStore
from tests.test_smoke import ROOT


def github(tags, fail=()):
    """A request() that answers releases/latest from `tags` ({repo: tag}); `fail`: repos that cannot be reached."""
    asked = []

    def request(url, headers=None, timeout=10, public=False, **_):
        repo = url.split("/repos/")[1].split("/releases")[0]
        asked.append(repo)
        if repo in fail:
            raise OSError("network is unreachable")
        if repo not in tags:
            raise OSError("HTTP Error 404: Not Found")
        return {"tag_name": tags[repo]}
    request.asked = asked
    return request


class Versions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(updates.parse("v0.1.2"), (0, 1, 2, 1))
        self.assertEqual(updates.parse("0.1.2-rc1"), (0, 1, 2, 0))
        for bad in ("dev", "", None, "latest", "1.2", "1.2.3.4", "v1.x.3"):
            self.assertIsNone(updates.parse(bad), msg=bad)

    def test_newer(self):
        for latest, current, want in (("v0.1.1", "0.1.0", True), ("0.1.0", "0.1.0", False), ("0.1.0", "0.1.1", False),
                                      ("0.2.0", "0.1.9", True), ("1.0.0", "0.99.99", True), ("0.10.0", "0.9.0", True),
                                      ("0.1.1-rc1", "0.1.1", False), ("0.1.1", "0.1.1-rc1", True),
                                      ("0.1.1", "dev", False), ("", "0.1.0", False), ("0.1.1", None, False),
                                      # the series wraps: 0.1.19, then 0.1.2, ..., 0.1.29, then 0.1.3
                                      ("0.1.19", "0.1.2", False), ("0.1.2", "0.1.19", True), ("0.1.21", "0.1.2", True),
                                      ("0.1.3", "0.1.29", True), ("0.1.29", "0.1.3", False), ("0.1.2", "0.1.2", False)):
            with self.subTest(latest=latest, current=current):
                self.assertEqual(updates.newer(latest, current), want)


class Checking(unittest.TestCase):
    def setUp(self):
        updates.CHECK = True
        updates._state.update(latest={k: "" for k in updates.REPOS}, checked=0, error="")

    def tearDown(self):
        updates.CHECK = True

    TAGS = {"Mithrandler/labhud": "v0.1.1", "Mithrandler/labhud-agent": "v0.1.0", "Mithrandler/labhud-android": "v0.1.3"}

    def test_newer_server(self):
        s = updates.check(github(self.TAGS), now=1000)
        self.assertEqual(s["latest"], {"server": "v0.1.1", "agent": "v0.1.0", "app": "v0.1.3"})
        a = updates.about("0.1.0", app="0.1.2")
        self.assertTrue(a["update"])
        self.assertEqual(a["outdated"], {"agent": False, "app": True})
        self.assertEqual(a["checked"], 1000)

    def test_the_same_version_is_no_update(self):
        updates.check(github(self.TAGS))
        a = updates.about("0.1.1")
        self.assertFalse(a["update"])
        self.assertEqual(a["outdated"]["app"], False)     # no app asked

    def test_network_error_keeps_what_was_known(self):
        updates.check(github(self.TAGS), now=1000)
        s = updates.check(github(self.TAGS, fail=set(self.TAGS)), now=2000)
        self.assertEqual(s["latest"]["server"], "v0.1.1")
        self.assertEqual(s["checked"], 1000)
        self.assertIn("unreachable", s["error"])

    def test_a_repository_without_a_release(self):
        s = updates.check(github({"Mithrandler/labhud": "v0.1.1"}))
        self.assertEqual((s["latest"]["server"], s["latest"]["agent"]), ("v0.1.1", ""))
        self.assertEqual(s["error"], "")

    def test_a_tag_that_is_no_version_is_ignored(self):
        s = updates.check(github({"Mithrandler/labhud": "nightly"}))
        self.assertEqual(s["latest"]["server"], "")

    def test_check_false_says_nothing(self):
        updates.check(github(self.TAGS))
        updates.configure({"check": False})
        a = updates.about("0.1.0")
        self.assertEqual((a["enabled"], a["update"], a["latest"]["server"]), (False, False, ""))
        self.assertIsNone(updates.news("0.1.0", FakeStore()))

    def test_the_news_goes_out_once_per_version(self):
        updates.check(github(self.TAGS))
        st = FakeStore()
        self.assertIn("0.1.1 is out", updates.news("0.1.0", st))
        self.assertIsNone(updates.news("0.1.0", st))
        updates._state["latest"]["server"] = "v0.1.2"
        self.assertIn("0.1.2 is out", updates.news("0.1.0", st))

    def test_no_news_when_up_to_date_or_dev(self):
        updates.check(github(self.TAGS))
        self.assertIsNone(updates.news("0.1.1", FakeStore()))
        self.assertIsNone(updates.news("dev", FakeStore()))

    def test_the_pinned_agent_is_read(self):
        with open(os.path.join(ROOT, "agents", "agent-release.json"), encoding="utf-8") as f:
            self.assertEqual(updates.pinned_agent(), json.load(f)["version"])


class Config(unittest.TestCase):
    def load(self, extra=""):
        import config
        with open(os.path.join(ROOT, "config.example.toml"), encoding="utf-8") as f:
            text = f.read() + "\n" + extra
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return config.load(path)

    def test_checks_by_default(self):
        self.assertEqual(self.load()["update"], {"check": True})

    def test_check_can_be_turned_off(self):
        self.assertEqual(self.load("[update]\ncheck = false\n")["update"], {"check": False})

    def test_mistakes_are_named(self):
        import config
        with self.assertRaises(config.ConfigError) as e:
            self.load('[update]\ncheck = "yes"\n')
        self.assertIn("check must be true or false", str(e.exception))
        with self.assertRaises(config.ConfigError) as e:
            self.load("[update]\nevery = 5\n")
        self.assertIn("every", str(e.exception))


if __name__ == "__main__":
    unittest.main()
