"""What the display says about a source that stopped answering (server.where_shown)."""

import os
import unittest
from unittest import mock

os.environ.setdefault("LABHUD_DEMO", "1")  # the server module loads a config when imported
import server  # noqa: E402


class WhereShown(unittest.TestCase):
    def test_cards_pages_and_host(self):
        topo = {"pages": [{"id": "general", "title": "GENERAL", "groups": [{"id": "storage", "title": "Storage"}]}],
                "cards": {"storage": [{"id": "nas", "name": "NAS", "metrics": [["synology.used", "Used", "percent"]]},
                                      {"id": "other", "name": "Other", "metrics": [["omv.used", "Used", "percent"]]}]}}
        with mock.patch.object(server, "TOPOLOGY", topo), \
                mock.patch.dict(server.SOURCE_HOST, {"synology": "nas"}, clear=True), \
                mock.patch.dict(server.CARD_NAMES, {"nas": "NAS"}), \
                mock.patch.dict(server._data, {"status": {"nas": False}}):
            got = server.where_shown("synology")
        self.assertEqual(got["shown"], ["GENERAL › Storage › NAS"])
        self.assertEqual((got["host"], got["host_state"]), ("NAS", "down"))

    def test_a_live_part(self):
        topo = {"pages": [{"id": "health", "title": "HEALTH", "groups": [{"id": "fleet", "title": "Fleet"}]}],
                "cards": {"fleet": [{"id": "upd", "name": "UPDATES", "metrics": [["live.fleet_health.packages", "P", "count"]]}]}}
        with mock.patch.object(server, "TOPOLOGY", topo), mock.patch.dict(server.SOURCE_HOST, {}, clear=True):
            self.assertEqual(server.where_shown("live")["shown"], ["HEALTH › Fleet › UPDATES"])
            self.assertIsNone(server.where_shown("live")["host"])


if __name__ == "__main__":
    unittest.main()
