# Things to read rather than to watch, for a page of their own: RSS/Atom feeds, the latest
# releases of the software you run, and the next events of a CalDAV calendar (Nextcloud...).
#
#   LABHUD_RSS        name=url, comma separated (RSS 2.0 or Atom; Miniflux, FreshRSS and most
#                     sites have one): rss.<name> is a card list, newest first
#   LABHUD_RELEASES   owner/repo on GitHub, comma separated: releases.list, newest first
#   LABHUD_GITHUB_TOKEN  optional: a token without any scope, for 5000 requests an hour instead of 60
#   LABHUD_CALDAV_URL    the calendar's own URL (Nextcloud: .../remote.php/dav/calendars/<user>/<calendar>/)
#   LABHUD_CALDAV_USER / LABHUD_CALDAV_PASS   an app password, not your login password
#
# Data: rss.<name> [{name, value}], releases.list [{name, value}], agenda.{events, today}.

import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ._common import ago, basic, env, labelled_urls, request, source

ATOM = "{http://www.w3.org/2005/Atom}"


def _when(text):
    """Unix time from an RSS (RFC 822) or Atom (ISO 8601) date, or None."""
    if not text:
        return None
    try:
        return parsedate_to_datetime(text.strip()).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return time.mktime(time.strptime(text.strip()[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except ValueError:
        return None


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else ""


def parse_feed(xml_text, limit=8):
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        items.append(((it.findtext("title") or "").strip(), _when(it.findtext("pubDate"))))
    for it in root.iter(ATOM + "entry"):
        items.append(((it.findtext(ATOM + "title") or "").strip(),
                      _when(it.findtext(ATOM + "updated") or it.findtext(ATOM + "published"))))
    items.sort(key=lambda x: -(x[1] or 0))
    return [{"name": title or "(no title)", "value": ago(_iso(t)) if t else ""} for title, t in items[:limit]] \
        or [{"name": "nothing in this feed", "value": "—"}]


@source("rss", every=900, env=("RSS",),
        title="RSS / Atom feeds", about="The newest items of your feeds, one list per feed.",
        hints={"RSS": "name=https://example.org/feed.xml,other=..."})
def rss():
    out = {}
    for name, url in labelled_urls(env("RSS")).items():
        key = (name or "feed").lower()
        try:
            out[key] = parse_feed(request(url, timeout=15, raw=True, public=True))
        except (OSError, ValueError, ET.ParseError) as e:
            out[key] = [{"name": f"could not read: {str(e)[:60]}", "value": "", "bad": True}]
    return out


@source("releases", every=21600, env=("RELEASES",),
        title="GitHub releases", about="The latest release of each project you list.",
        hints={"RELEASES": "owner/repo,owner/repo"})
def releases():
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "labhud"}
    if env("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {env('GITHUB_TOKEN')}"
    rows = []
    for repo in (r.strip() for r in env("RELEASES").split(",") if r.strip()):
        try:
            r = request(f"https://api.github.com/repos/{repo}/releases/latest", headers, timeout=10, public=True)
            t = _when(r.get("published_at"))
            rows.append({"name": f"{repo.split('/')[-1]} {r.get('tag_name', '?')}",
                         "value": ago(_iso(t)) if t else "", "_t": t or 0})
        except (OSError, ValueError) as e:
            rows.append({"name": repo, "value": "no release" if "404" in str(e) else "?", "_t": -1})
    rows.sort(key=lambda r: -r["_t"])
    return {"list": [{k: v for k, v in r.items() if k != "_t"} for r in rows] or [{"name": "no projects", "value": "—"}]}


REPORT = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{start}" end="{end}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""


def parse_events(ical_texts):
    """[(start unix time, all day?, summary)] from VCALENDAR texts (unfolded, DTSTART + SUMMARY)."""
    out = []
    for text in ical_texts:
        text = text.replace("\r\n ", "").replace("\n ", "")
        for block in text.split("BEGIN:VEVENT")[1:]:
            block = block.split("END:VEVENT")[0]
            start = summary = None
            all_day = False
            for line in block.splitlines():
                key, _, value = line.partition(":")
                name = key.split(";")[0].upper()
                if name == "SUMMARY":
                    summary = value.replace("\\,", ",").replace("\\;", ";").strip()
                elif name == "DTSTART":
                    v = value.strip()
                    try:
                        if len(v) == 8:  # a date: all day
                            start, all_day = time.mktime(time.strptime(v, "%Y%m%d")), True
                        elif v.endswith("Z"):
                            start = time.mktime(time.strptime(v, "%Y%m%dT%H%M%SZ")) - time.timezone
                        else:  # local time (a TZID is taken as this machine's zone)
                            start = time.mktime(time.strptime(v[:15], "%Y%m%dT%H%M%S"))
                    except ValueError:
                        start = None
            if start is not None:
                out.append((start, all_day, summary or "(no title)"))
    return sorted(out)


@source("agenda", every=900, env=("CALDAV_URL", "CALDAV_USER", "CALDAV_PASS"),
        title="Calendar (CalDAV)", about="The next events of one calendar (Nextcloud, Radicale, Baïkal...).",
        hints={"CALDAV_URL": "https://cloud.example.org/remote.php/dav/calendars/<user>/<calendar>/",
               "CALDAV_PASS": "an app password (Nextcloud: Settings > Security)"})
def agenda():
    now = time.time()
    fmt = "%Y%m%dT%H%M%SZ"
    body = REPORT.format(start=time.strftime(fmt, time.gmtime(now - 86400)),
                         end=time.strftime(fmt, time.gmtime(now + 14 * 86400))).encode()
    xml_text = request(env("CALDAV_URL"), {"Authorization": basic(env("CALDAV_USER"), env("CALDAV_PASS")),
                                           "Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
                       data=body, method="REPORT", timeout=15, raw=True)
    root = ET.fromstring(xml_text)
    texts = [e.text or "" for e in root.iter("{urn:ietf:params:xml:ns:caldav}calendar-data")]
    today = time.strftime("%Y%m%d", time.localtime(now))
    rows, count_today = [], 0
    for start, all_day, summary in parse_events(texts):
        end_of_day = start + 86400 if all_day else start + 3600
        if end_of_day < now:
            continue
        day = time.strftime("%Y%m%d", time.localtime(start))
        count_today += day == today
        when = ("today" if day == today else time.strftime("%a %d %b", time.localtime(start))) \
            + ("" if all_day else time.strftime(" %H:%M", time.localtime(start)))
        rows.append({"name": summary, "value": when})
    return {"events": rows[:10] or [{"name": "nothing in the next two weeks", "value": "—"}], "today": count_today}

