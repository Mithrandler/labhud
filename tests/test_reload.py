"""config.toml edited while the server runs: a broken file is reported and kept out, a good one
is applied without a restart. Uses a copy of the demo config in a temporary directory."""

import json
import os
import shutil
import tempfile
import time
import unittest

from tests.test_smoke import DemoServer, ROOT


class Reload(DemoServer):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.config = os.path.join(cls.tmp, "config.toml")
        shutil.copy(os.path.join(ROOT, "demo", "config.toml"), cls.config)
        os.environ["LABHUD_TEST_CONFIG"] = cls.config
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        os.environ.pop("LABHUD_TEST_CONFIG", None)
        shutil.rmtree(cls.tmp)

    def wait_for(self, check, seconds=12):
        deadline = time.monotonic() + seconds
        while True:
            status = json.loads(self.get("/api/status")[2])
            if check(status) or time.monotonic() > deadline:
                return status
            time.sleep(0.5)

    def test_reload(self):
        with open(self.config) as f:
            good = f.read()
        with open(self.config, "w") as f:
            f.write(good + "\n[[page]]\nid = \"Bad Id\"\n")
        status = self.wait_for(lambda s: s["config_errors"])
        self.assertTrue(any("Bad Id" in p for p in status["config_errors"]), status["config_errors"])
        self.assertEqual(json.loads(self.get("/api/config")[2])["title"], "LabHUD DEMO")  # still the old one

        with open(self.config, "w") as f:
            f.write(good.replace('title = "LabHUD DEMO"', 'title = "RELOADED"', 1))
        status = self.wait_for(lambda s: not s["config_errors"])
        self.assertEqual(status["config_errors"], [])
        self.assertEqual(json.loads(self.get("/api/config")[2])["title"], "RELOADED")

    # the smoke tests are not run a second time here
    test_page = test_config = test_unknown_host_and_path = test_stream = None
    test_status = test_every_config_path_has_data = test_history = None


if __name__ == "__main__":
    unittest.main()


NODE = shutil.which("node")
DEFER = os.path.join(ROOT, "tests", "js", "reload_defer.mjs")


@unittest.skipUnless(NODE, "node not found")
class ReloadWaits(unittest.TestCase):
    """The display's reload (static/app.js, run in Node) waits while ⚙, a panel or a confirmation
    is open: a tick in ⚙ > Agents used to close ⚙ and drop the screen on the first page."""

    def run_steps(self, steps):
        import subprocess
        out = subprocess.run([NODE, DEFER], input=json.dumps(steps), capture_output=True, text=True, check=True)
        return json.loads(out.stdout)

    def test_nothing_open_reloads_at_once(self):
        self.assertEqual(self.run_steps(["reload"]), ["reload@0"])

    def test_settings_open_waits_until_closed(self):
        steps = [["#settings", False], "reload", "tick", "reload", "tick", ["#settings", True], "tick", "tick"]
        self.assertEqual(self.run_steps(steps), ["reload@3"])

    def test_panel_then_confirm(self):
        steps = [["#panel", False], "reload", "tick", ["#panel", True], ["#confirm", False], "tick",
                 ["#confirm", True], "tick"]
        self.assertEqual(self.run_steps(steps), ["reload@3"])
