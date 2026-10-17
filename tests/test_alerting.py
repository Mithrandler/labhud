"""[[alert]] rules on hosts (alerting.py), their config, and the quiet hours of notify.py."""

import time
import unittest

import alerting
import config
import notify

HOSTS = [{"id": "host-pve", "name": "PVE", "agent": "PVE"},
         {"id": "nas", "name": "NAS", "agent": "pve2", "agent_target": "wol:aa:bb:cc:dd:ee:ff"},
         {"id": "web", "name": "web"}]
RULES = [dict(r) for r in alerting.DEFAULT_RULES]


def agents(online=True, **sensors):
    return {"PVE": {"online": online, "sensors": sensors}}


class Rules(unittest.TestCase):
    def run_seq(self, seq, rules=RULES, step=60, skip=(), status=None):
        r = alerting.Rules()
        return [r.check(HOSTS, rules, a, skip, status, now=1000 + i * step) for i, a in enumerate(seq)], r

    def test_star_means_hosts_with_an_agent_of_their_own(self):
        self.assertEqual([c["id"] for c in alerting.hosts(HOSTS)], ["host-pve"])
        self.assertEqual(alerting.covered(HOSTS, RULES), {"host-pve"})
        own = RULES + [{"on": "host-pve", "what": "cpu", "above": 50, "minutes": 0}]
        self.assertEqual(alerting.rules_for(HOSTS[0], own)["cpu"]["above"], 50)   # its own wins

    def test_cpu_held_for_its_minutes_then_once_then_back_under_the_margin(self):
        seq = [agents(cpu=95)] * 12 + [agents(cpu=88), agents(cpu=80)]
        got, r = self.run_seq(seq)
        flat = [e for es in got for e in es]
        self.assertEqual(flat, [("PVE CPU 95% for 10 min", True, ("rule", "host-pve"), False),
                                ("PVE CPU back to 80%", False, ("rule", "host-pve"), False)])
        self.assertEqual(got[10][0][0], "PVE CPU 95% for 10 min")   # at 600 s
        self.assertEqual(r.active(), [])

    def test_disk_at_once_and_temp_is_the_hottest_and_urgent(self):
        got, r = self.run_seq([agents(disk=93, cpu_temp=60, gpu1_temp=90)] * 6)
        self.assertEqual(got[0], [("PVE disk 93%", True, ("rule", "host-pve"), True)])
        self.assertEqual(got[5], [("PVE temperature 90°C for 5 min", True, ("rule", "host-pve"), True)])
        self.assertEqual([a["what"] for a in r.active()], ["disk", "temp"])

    def test_offline_after_its_minutes_and_back(self):
        seq = [agents(online=False)] * 6 + [agents()]
        flat = [e for es in self.run_seq(seq)[0] for e in es]
        self.assertEqual(flat, [("PVE offline for 5 min", True, ("rule", "host-pve"), True),
                                ("PVE back online", False, ("rule", "host-pve"), True)])
        # a short reboot says nothing
        self.assertEqual([e for es in self.run_seq([agents(online=False)] * 3 + [agents()])[0] for e in es], [])

    def test_failed_units_after_their_minutes_and_back(self):
        def failed(units):
            return {"PVE": {"online": True, "sensors": {}, "failed_units": units}}
        seq = [failed(["nginx.service"])] * 6 + [failed([])]
        flat = [e for es in self.run_seq(seq)[0] for e in es]
        self.assertEqual(flat, [("PVE: 1 failed unit for 5 min (nginx.service)", True, ("rule", "host-pve"), False),
                                ("PVE: no failed units", False, ("rule", "host-pve"), False)])
        # no systemd on the host (null): never judged
        self.assertEqual([e for es in self.run_seq([failed(None)] * 8)[0] for e in es], [])

    def test_not_judged_in_maintenance_on_demand_or_without_the_agent(self):
        hot = [agents(online=False, disk=99)] * 8
        self.assertEqual([e for es in self.run_seq(hot, skip={"host-pve"})[0] for e in es], [])
        self.assertEqual([e for es in self.run_seq(hot, status=lambda c: "on-demand")[0] for e in es], [])
        self.assertEqual([e for es in self.run_seq([{}] * 8)[0] for e in es], [])

    def test_no_rules_no_news(self):
        self.assertEqual([e for es in self.run_seq([agents(disk=99)] * 3, rules=[])[0] for e in es], [])


CFG = """
[[page]]
id = "main"
title = "MAIN"
[[page.group]]
id = "g"
title = "G"
[[page.group.card]]
id = "host-pve"
name = "PVE"
agent = "PVE"
[[page.group.card]]
id = "web"
name = "web"
"""


class Config(unittest.TestCase):
    def load(self, extra=""):
        return config.loads(CFG + extra, "test.toml")

    def test_search_and_bookmarks(self):
        t = self.load('[search]\nurl = "https://s.example/?q=%s"\n[[bookmark]]\nname = "Wiki"\nurl = "https://w.example"\n')
        self.assertEqual(t["search"], {"url": "https://s.example/?q=%s"})
        self.assertEqual(t["bookmarks"], [{"group": "Links", "name": "Wiki", "url": "https://w.example"}])
        self.assertEqual(config.public(t)["bookmarks"][0]["name"], "Wiki")
        for bad, why in (('[search]\nurl = "https://s.example/"', "%s"),
                         ('[[bookmark]]\nname = "x"\nurl = "javascript:alert(1)"', "http")):
            with self.assertRaises(config.ConfigError) as e:
                self.load("\n" + bad + "\n")
            self.assertIn(why, str(e.exception.problems))

    def test_defaults_without_any_rule(self):
        t = self.load()
        self.assertEqual(t["alert_rules"], alerting.DEFAULT_RULES)
        self.assertEqual(t["notify"], {"via": "both", "quiet": (23, 7)})
        self.assertEqual(config.loads("alert = []\n" + CFG, "test.toml")["alert_rules"], [])

    def test_rules_and_notify(self):
        t = self.load('[[alert]]\non = "host-pve"\nwhat = "disk"\nabove = 80\n'
                      '[[alert]]\non = "*"\nwhat = "offline"\nminutes = 2\n'
                      '[notify]\nvia = "app"\nquiet_from = 22\nquiet_to = 22\n')
        self.assertEqual(t["alert_rules"], [{"on": "host-pve", "what": "disk", "minutes": 0, "above": 80},
                                            {"on": "*", "what": "offline", "minutes": 2}])
        self.assertEqual(t["notify"], {"via": "app", "quiet": None})

    def test_wrong_rules_are_refused(self):
        for bad, said in (('on = "web"\nwhat = "cpu"\nabove = 90', "has no agent"),
                          ('on = "nope"\nwhat = "cpu"\nabove = 90', "does not exist"),
                          ('on = "*"\nwhat = "load"\nabove = 9', "what must be one of"),
                          ('on = "*"\nwhat = "cpu"', "missing 'above'"),
                          ('on = "*"\nwhat = "cpu"\nabove = 120', "from 1 to 100"),
                          ('on = "*"\nwhat = "offline"\nabove = 1', "offline takes minutes"),
                          ('on = "*"\nwhat = "failed"\nabove = 1', "failed takes minutes"),
                          ('on = "*"\nwhat = "cpu"\nabove = 90\nminutes = -1', "minutes must be")):
            with self.assertRaises(config.ConfigError) as e:
                self.load("[[alert]]\n" + bad + "\n")
            self.assertIn(said, "\n".join(e.exception.problems), bad)
        with self.assertRaises(config.ConfigError):
            self.load('[notify]\nvia = "email"\n')

    def test_a_host_gets_its_agent_charts(self):
        t = self.load()
        card = t["cards"]["g"][0]
        self.assertIn(["agents.PVE.sensors.cpu", "CPU", "percent"], card["charts"])
        self.assertIn("agents.PVE.sensors.cpu_temp", t["trends"])
        self.assertNotIn("charts", t["cards"]["g"][1])


class Quiet(unittest.TestCase):
    def setUp(self):
        self.saved = notify.URL, notify.QUIET, notify.VIA, notify.ALL
        notify.URL, notify.ALL = "", False   # only the outbox
        notify._held.clear()
        self.addCleanup(self.restore)

    def restore(self):
        notify.URL, notify.QUIET, notify.VIA, notify.ALL = self.saved
        notify._held.clear()

    def at(self, hour):
        t = time.localtime()
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, hour, 30, 0, 0, 0, -1))

    def test_the_window_crosses_midnight(self):
        notify.QUIET = (23, 7)
        self.assertEqual([notify.quiet_now(self.at(h)) for h in (22, 23, 2, 6, 7)], [False, True, True, True, False])
        notify.QUIET = (1, 5)
        self.assertEqual([notify.quiet_now(self.at(h)) for h in (0, 1, 4, 5)], [False, True, True, False])
        notify.QUIET = None
        self.assertFalse(notify.quiet_now(self.at(3)))

    def test_held_at_night_then_one_message_in_the_morning(self):
        notify.QUIET = (23, 7)
        start = notify.outbox()[-1]["id"] if notify.outbox() else 0
        msg = lambda text, urgent: (0, "LabHUD", text, True, None, "host-pve", urgent)  # noqa: E731
        notify.step([msg("PVE CPU 95% for 10 min", False), msg("PVE offline for 5 min", True),
                     msg("PVE RAM 93% for 10 min", False)], 1, now=self.at(2))
        night = notify.outbox(start)
        self.assertEqual([m["text"] for m in night], ["PVE offline for 5 min"])   # urgent: at once
        notify.step([], 1, now=self.at(7))
        morning = notify.outbox(night[-1]["id"])
        self.assertEqual(len(morning), 1)
        self.assertTrue(morning[0]["text"].startswith("2 while quiet:"), morning[0]["text"])
        self.assertIn("✕ PVE RAM 93% for 10 min", morning[0]["text"])
        self.assertEqual(notify._held, [])

    def test_the_outbox_ids_rise(self):
        notify.QUIET = None
        notify.deliver("T", "a", True, now=5000)
        notify.deliver("T", "b", True, now=5000)
        ids = [m["id"] for m in notify.outbox()[-2:]]
        self.assertLess(ids[0], ids[1])


if __name__ == "__main__":
    unittest.main()
