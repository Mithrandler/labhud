"""⚙'s sections (static/settings/registry.js): registered, grouped, hidden from a screen not signed in, found by the search."""

import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(ROOT, "static", "settings", "registry.js")
NODE = shutil.which("node")


def run(script):
    with open(REGISTRY, encoding="utf-8") as f:
        src = f.read()
    program = (
        "let ADMIN = false;\n"
        "function signed_in() { return ADMIN; }\n"
        + src
        + "\n"
        + script
    )
    result = subprocess.run(
        [NODE, "-e", program],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class Registry(unittest.TestCase):
    def setUp(self):
        if not NODE:
            self.skipTest("node not found")

    def test_grouped_in_order(self):
        script = (
            'register_section({id:"b", group:"system", title:"B"});\n'
            'register_section({id:"a", group:"display", title:"A"});\n'
            'register_section({id:"c", group:"display", title:"C"});\n'
            'console.log(JSON.stringify(settings_groups().map(([t, l]) => [t, l.map(s => s.id)])));'
        )
        self.assertEqual(run(script), [["Display", ["a", "c"]], ["System", ["b"]]])

    def test_admin_only_hidden(self):
        script = (
            'register_section({id:"x", group:"system", title:"X", admin:true});\n'
            'register_section({id:"y", group:"system", title:"Y"});\n'
            "console.log(JSON.stringify(["
            "settings_groups()[0][1].map(s=>s.id),"
            "(ADMIN = true, settings_groups()[0][1].map(s=>s.id))"
            "]));"
        )
        self.assertEqual(run(script), [["y"], ["x", "y"]])

    def test_register_again_replaces(self):
        script = (
            'register_section({id:"a", group:"display", title:"Old"});\n'
            'register_section({id:"a", group:"display", title:"New"});\n'
            'console.log(JSON.stringify([SETTINGS_SECTIONS.length, settings_section("a").title, settings_section("a").icon]));'
        )
        self.assertEqual(run(script), [1, "New", "a"])

    def test_bad_group_throws(self):
        script = (
            'let msg = "";'
            'try { register_section({id:"z", group:"nope", title:"Z"}); }'
            'catch (e) { msg = e.message; }'
            'console.log(JSON.stringify(msg));'
        )
        self.assertIn("no group nope", run(script))

    def test_bad_id_throws(self):
        script = (
            'let msg = "";'
            'try { register_section({id:"Bad Id", group:"display", title:"Z"}); }'
            'catch (e) { msg = e.message; }'
            'console.log(JSON.stringify(msg));'
        )
        self.assertIn("an id like", run(script))

    def test_search(self):
        script = (
            'register_section({id:"look", group:"display", title:"Look", keywords:"theme font"});\n'
            'register_section({id:"alerts", group:"display", title:"Alerts", keywords:"rules ntfy"});\n'
            'console.log(JSON.stringify(['
            'find_settings("font").map(s=>s.id),'
            'find_settings("ALERTS ntfy").map(s=>s.id),'
            'find_settings("").length,'
            'find_settings("theme ntfy").length'
            "]));"
        )
        self.assertEqual(run(script), [["look"], ["alerts"], 0, 0])

    def test_groups_order(self):
        script = (
            'settings_groups_order([["system", "System"], ["display", "Display"]]);\n'
            'register_section({id:"s", group:"system", title:"S"});\n'
            'register_section({id:"d", group:"display", title:"D"});\n'
            'console.log(JSON.stringify(settings_groups().map(([t]) => t)));'
        )
        self.assertEqual(run(script), ["System", "Display"])


if __name__ == "__main__":
    unittest.main()
