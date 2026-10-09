"""Feeds, releases and the CalDAV agenda, on made-up answers."""

import time
import unittest
from unittest import mock

from sources import feeds
from tests.test_sources import FakeHTTP, lab_env

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Older</title><pubDate>Mon, 05 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title>Newer</title><pubDate>Thu, 08 Oct 2026 08:00:00 GMT</pubDate></item></channel></rss>"""
ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom one</title><updated>2026-10-07T10:00:00Z</updated></entry></feed>"""


def ical(summary, dtstart):
    return f"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:{summary}\r\nDTSTART{dtstart}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"


class Feeds(unittest.TestCase):
    def test_rss_and_atom(self):
        rows = feeds.parse_feed(RSS)
        self.assertEqual([r["name"] for r in rows], ["Newer", "Older"])
        self.assertEqual(feeds.parse_feed(ATOM)[0]["name"], "Atom one")

    def test_rss_source(self):
        def fake(url, headers=None, data=None, timeout=10, method=None, raw=False, public=False):
            if "broken" in url:
                raise OSError("timeout")
            return RSS
        with lab_env(RSS="news=https://news.example/feed,bad=https://broken.example/rss"), \
                mock.patch.object(feeds, "request", fake):
            got = feeds.rss()
        self.assertEqual(got["news"][0]["name"], "Newer")
        self.assertTrue(got["bad"][0]["bad"])

    def test_releases(self):
        routes = {"/repos/a/one/": {"tag_name": "v2.0", "published_at": "2026-10-08T00:00:00Z"},
                  "/repos/b/two/": OSError("HTTP Error 404: Not Found")}
        with lab_env(RELEASES="a/one, b/two"), mock.patch.object(feeds, "request", FakeHTTP(routes)):
            got = feeds.releases()["list"]
        self.assertEqual(got[0]["name"], "one v2.0")
        self.assertEqual(got[1], {"name": "b/two", "value": "no release"})

    def test_agenda(self):
        now = time.time()
        tomorrow = time.strftime("%Y%m%dT100000", time.localtime(now + 86400))
        today = time.strftime("%Y%m%d", time.localtime(now))
        xml = ("<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'><d:response><d:propstat><d:prop>"
               f"<c:calendar-data>{ical('Dentist', ':' + tomorrow)}</c:calendar-data></d:prop></d:propstat></d:response>"
               "<d:response><d:propstat><d:prop>"
               f"<c:calendar-data>{ical('Bin day', ';VALUE=DATE:' + today)}</c:calendar-data></d:prop></d:propstat></d:response></d:multistatus>")
        calls = []

        def fake(url, headers=None, data=None, timeout=10, method=None, raw=False, public=False):
            calls.append((method, headers.get("Depth"), b"time-range" in data))
            return xml
        with lab_env(CALDAV_URL="https://cloud.example/dav/cal/", CALDAV_USER="u", CALDAV_PASS="p"), \
                mock.patch.object(feeds, "request", fake):
            got = feeds.agenda()
        self.assertEqual(calls, [("REPORT", "1", True)])
        self.assertEqual(got["today"], 1)
        self.assertEqual([e["name"] for e in got["events"]], ["Bin day", "Dentist"])
        self.assertEqual(got["events"][0]["value"], "today")


if __name__ == "__main__":
    unittest.main()
