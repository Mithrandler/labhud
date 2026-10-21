"""/status's setup checklist: from states only, never values."""

import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


def items(result, **patches):
    with mock.patch.multiple(server, **patches) if patches else mock.patch.object(server, "DEMO", True):
        return {c["item"]: c for c in server.checklist(result)}


class Checklist(unittest.TestCase):
    def test_nothing_set_up(self):
        got = items({"jellyfin": {"state": "not_configured", "needs": ["LABHUD_JELLYFIN_URL"]}},
                    KEY_CHECKS={}, ALLOWED_HOSTS={f"localhost:{server.PORT}"}, ACTION_URL="")
        self.assertFalse(got["At least one source set up"]["ok"])
        self.assertFalse(got["LABHUD_HOSTS names the display"]["ok"])
        self.assertIsNone(got["Proxmox/PBS keys can only read"]["ok"])
        self.assertNotIn("Actions signed", got)

    def test_problems_are_named(self):
        got = items({"proxmox": {"state": "ok"}, "pbs": {"state": "failing"}},
                    KEY_CHECKS={"proxmox": {"extra": [("/", ["VM.PowerMgmt"])]}, "pbs": {"error": "down"}},
                    ALLOWED_HOSTS={"192.0.2.50:8095"}, ACTION_URL="http://192.0.2.10:9189/action/", ACTION_SECRET=b"")
        self.assertFalse(got["Every source set up answers"]["ok"])
        self.assertIn("pbs", got["Every source set up answers"]["how"])
        self.assertFalse(got["Proxmox/PBS keys can only read"]["ok"])
        self.assertTrue(got["LABHUD_HOSTS names the display"]["ok"])
        self.assertFalse(got["Actions signed"]["ok"])


if __name__ == "__main__":
    unittest.main()
