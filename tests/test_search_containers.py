"""Containers in the search box (static/search.js search_containers, run by Node; skipped without it)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parent / "js" / "search_containers.mjs"


def card(name, actions):
    return {"name": name, "card": {"actions": actions}, "actions": actions}


ROWS = [card("web", ["agent-box-restart"]), card("db", ["agent-box-restart", "agent-box-stop"]), card("plain", [])]


def found(act, signed, rows=ROWS):
    out = subprocess.run(["node", str(SCRIPT), json.dumps({"act": act, "signed": signed, "rows": rows})],
                         capture_output=True, text=True, check=True).stdout
    return [(x["label"], x["hint"], x["command"]) for x in json.loads(out)]


@unittest.skipUnless(shutil.which("node"), "node not found")
class SearchContainers(unittest.TestCase):
    def test_everyone_finds_a_container_by_name(self):
        got = found(False, False)
        self.assertEqual([g[0] for g in got], ["db", "plain"])   # "web" has a card of its own
        self.assertFalse(any(g[2] for g in got))

    def test_the_admin_gets_buttons_and_logs_only_where_the_row_has_buttons(self):
        got = [g[0] for g in found(True, True)]
        self.assertEqual(got, ["db", "restart · db", "stop · db", "logs · db", "plain"])

    def test_buttons_need_the_admin_and_may_act(self):
        self.assertEqual([g[0] for g in found(True, False)], ["db", "plain"])
        self.assertEqual([g[0] for g in found(False, True)], ["db", "plain"])

    def test_a_dangerous_one_asks_first(self):
        self.assertIn(("stop · db", "asks first", True), found(True, True))
