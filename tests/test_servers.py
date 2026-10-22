"""api/servers.py: the zone Servers. The gate (HTTPS, level, session, address, blocking), the hosts and
containers it lists, the actions it sends, and what it makes of what an agent answers."""

import base64
import http.client
import json
import shutil
import tempfile
import threading
import unittest
import unittest.mock

import admin
import agents
import routes
from api import audit, servers
from tests import signin, test_agents
from tests.test_smoke import DemoServer

def audit_text(data):
    """Everything the audit log of that LABHUD_DATA holds, as text."""
    import glob
    import os
    out = []
    for f in sorted(glob.glob(os.path.join(data, "audit", "*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            out.append(fh.read())
    return "".join(out)


STATE = {"type": "state",
         "sensors": {"cpu": 12.5, "mem": 41.0},
         "containers": {
             "web": {"state": "running", "health": "healthy", "image": "nginx:1", "project": "site", "cpu": 4.2,
                     "mem": 845000000, "mem_pct": 10.6, "net_rx": 2100, "net_tx": 480, "restarts": 1},
             "db": {"state": "exited", "image": "postgres:16"},
             "bad name!": {"state": "running"}}}


class Base(DemoServer):
    """A server with a signed-in admin; Servers allowed over plain HTTP (tests only)."""
    manage = test_agents.Server.manage
    pair = test_agents.Server.pair
    plain = "on"

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp()
        cls.extra_env = {"LABHUD_DATA": cls.data, **signin.ENV}
        if cls.plain:
            cls.extra_env["LABHUD_SERVERS_HTTP"] = cls.plain
        super().setUpClass()
        cls.ticket = signin.sign_in(cls.port, cls.host)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.data, ignore_errors=True)

    def api(self, op, body=None, ticket=True, origin=True, token=None, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": self.host, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = f"http://{self.host}"
        if ticket is True:
            headers["X-Labhud-Admin"] = self.ticket
        elif ticket:
            headers["X-Labhud-Admin"] = ticket
        if token:
            headers["Authorization"] = "Bearer " + token
        headers.update(extra or {})
        conn.request("POST", "/api/servers/" + op, body=json.dumps(body or {}), headers=headers)
        r = conn.getresponse()
        data = r.read()
        out = (r.status, json.loads(data), dict(r.getheaders()))
        conn.close()
        return out

    def session(self):
        self.assertEqual(self.api("session")[0], 200)

    def make_host(self, name, containers=True, caps=("docker",), docker=("web", "db"), terminal=("web",)):
        c, session, aid, key = self.pair(name=name)
        self.manage("caps", {"id": aid, "caps": list(caps)})
        if "docker" in caps:
            self.manage("targets", {"id": aid, "cap": "docker", "targets": list(docker)})
        if "terminal" in caps:
            self.manage("targets", {"id": aid, "cap": "terminal", "targets": list(terminal)})
        c.read_signed(key, session), c.read_signed(key, session)
        if containers:
            c.send_signed(key, session, aid, STATE)
            self.assertTrue(self.wait_for(lambda: self.manage("list")[1] and
                                          any(a["name"] == name and a["online"] for a in self.manage("list")[1]["agents"])))
            self.assertTrue(self.wait_for(lambda: self.api("containers", {"agent": name})[1].get("containers")))
        return c, session, aid, key

    wait_for = test_agents.Server.wait_for

    def answer(self, c, key, session, aid, **reply):
        """The agent's side: takes the next command and answers it; returns the command."""
        got = {}

        def side():
            msg = c.read_signed(key, session)
            got.update(msg)
            c.send_signed(key, session, aid, dict({"type": "result", "cmd": msg["cmd"], "ok": True}, **reply))
        t = threading.Thread(target=side)
        t.start()
        return got, t


class Gate(Base):
    def audit_text(self):
        return audit_text(self.data)

    def test_a_call_without_the_admin_is_refused_and_written_to_the_audit_log(self):
        status, r, _ = self.api("hosts", ticket=False)
        self.assertEqual((status, r["ok"]), (403, False))
        self.assertIn('"action":"servers/hosts"', self.audit_text())

    def test_a_call_from_another_page_is_refused(self):
        self.session()
        self.assertEqual(self.api("hosts", origin=False)[0], 403)
        self.assertEqual(self.api("hosts", extra={"Origin": "http://evil.example"})[0], 403)

    def test_answers_carry_the_strict_headers(self):
        self.session()
        status, r, headers = self.api("hosts")
        self.assertEqual(status, 200)
        csp = headers["Content-Security-Policy"]
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


class Page(Base):
    def fetch(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("GET", path, headers={"Host": self.host})
        r = conn.getresponse()
        data = r.read()
        out = (r.status, data.decode("utf-8", "replace"), dict(r.getheaders()))
        conn.close()
        return out

    def test_the_page_and_its_files_come_with_the_strict_policy(self):
        for path in ("/servers", "/servers.js", "/servers.css"):
            status, text, headers = self.fetch(path)
            self.assertEqual(status, 200, path)
            self.assertNotIn("unsafe-inline", headers["Content-Security-Policy"], path)
            self.assertEqual(headers["Cache-Control"], "no-store", path)

    def test_nothing_is_inline(self):
        import re
        _, html, _ = self.fetch("/servers")
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)[^>]*>", "an inline script")
        self.assertNotRegex(html, r"<style", "an inline style")
        self.assertNotRegex(html, r"\sstyle\s*=", "a style attribute")
        self.assertNotRegex(html, r"\son[a-z]+\s*=", "an inline handler")
        _, js, _ = self.fetch("/servers.js")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("outerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertNotIn("document.write", js)
        self.assertNotIn("eval(", js)
        self.assertNotIn("setAttribute(\"style\"", js)


class NoSessionYet(Base):
    def test_the_session_is_started_with_the_pin_step_and_not_before(self):
        status, r, _ = self.api("hosts")
        self.assertEqual((status, r.get("resume")), (401, True))
        self.assertEqual(self.api("session")[0], 200)
        self.assertEqual(self.api("hosts")[0], 200)


class OnlyHTTPS(Base):
    plain = ""

    def test_plain_http_is_refused_whoever_asks(self):
        status, r, _ = self.api("session")
        self.assertEqual(status, 403)
        self.assertIn("HTTPS", r["error"])
        self.assertEqual(self.api("hosts")[0], 403)
        self.assertEqual(self.api("hosts", ticket=False)[0], 403)

    def test_the_page_is_not_served_over_plain_http_either(self):
        for path in ("/servers", "/servers.js", "/servers.css"):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
            conn.request("GET", path, headers={"Host": self.host})
            r = conn.getresponse()
            body = r.read()
            conn.close()
            self.assertEqual(r.status, 403, path)
            self.assertIn(b"HTTPS", body)


class Listing(Base):
    def test_hosts_and_containers(self):
        self.session()
        c, session, aid, key = self.make_host("lister")
        status, r, _ = self.api("hosts")
        self.assertEqual(status, 200)
        mine = next(h for h in r["hosts"] if h["name"] == "lister")
        self.assertTrue(mine["online"])
        self.assertEqual(mine["containers"], {"running": 1, "total": 2})   # the name that is no container name is not counted
        self.assertEqual(mine["cpu"], 12.5)
        status, r, _ = self.api("containers", {"agent": "lister"})
        rows = {x["name"]: x for x in r["containers"]}
        self.assertEqual(sorted(rows), ["db", "web"])         # the name that is no container name is left out
        web = rows["web"]
        self.assertEqual((web["state"], web["health"], web["image"], web["restarts"]), ("running", "healthy", "nginx:1", 1))
        self.assertEqual(web["can"], list(servers.ACTIONS))
        self.assertEqual(web["cpu"], 4.2)
        self.assertEqual(self.api("containers", {"agent": "nobody"})[0], 404)
        c.close()

    def test_a_container_that_is_not_ticked_may_be_seen_not_acted_on(self):
        self.session()
        c, session, aid, key = self.make_host("tickless", docker=("web",))
        rows = {x["name"]: x for x in self.api("containers", {"agent": "tickless"})[1]["containers"]}
        self.assertEqual(rows["web"]["can"], list(servers.ACTIONS))
        self.assertEqual(rows["db"]["can"], [])
        self.assertEqual(self.api("action", {"agent": "tickless", "name": "db", "verb": "restart"})[0], 502)
        c.close()


class Actions(Base):
    def test_an_action_is_sent_and_written_to_the_audit_log(self):
        self.session()
        c, session, aid, key = self.make_host("acter")
        got, t = self.answer(c, key, session, aid)
        status, r, _ = self.api("action", {"agent": "acter", "name": "web", "verb": "restart"})
        t.join(5)
        self.assertEqual((status, r["ok"]), (200, True), r)
        self.assertEqual((got["cap"], got["verb"], got["target"]), ("docker", "restart", "web"))
        lines = audit_text(self.data)
        self.assertIn('"action":"servers/restart"', lines)
        self.assertIn("acter/web", lines)
        c.close()

    def test_the_agents_refusal_comes_back(self):
        self.session()
        c, session, aid, key = self.make_host("refuser")
        got, t = self.answer(c, key, session, aid, ok=False, error="No such container: web")
        status, r, _ = self.api("action", {"agent": "refuser", "name": "web", "verb": "stop"})
        t.join(5)
        self.assertEqual(status, 502)
        self.assertIn("No such container", r["error"])
        c.close()

    def test_bad_input(self):
        self.session()
        c, session, aid, key = self.make_host("badin")
        for body in ({"agent": "badin", "name": "web", "verb": "rm"}, {"agent": "badin", "name": "../etc", "verb": "stop"},
                     {"agent": "badin", "name": "images", "verb": "stop"}, {"agent": "badin", "verb": "stop"}, {}):
            self.assertEqual(self.api("action", body)[0], 400, body)
        c.close()


class Reads(Base):
    def test_inspect_is_rebuilt_and_secrets_stay_masked(self):
        self.session()
        c, session, aid, key = self.make_host("inspector")
        raw = {"image": "nginx:1", "created": "2026-10-01T00:00:00Z", "restart": "always", "restarts": 2,
               "ports": ["80/tcp -> 8080"], "mounts": ["/data -> /var/lib/x"], "networks": ["bridge"],
               "env": ["DB_PASSWORD=hunter2", "TZ=UTC"],
               "labels": {"com.x.name": "web", "traefik.api.token": "s3cret"}, "evil": "<script>", "extra": {"x": 1}}
        got, t = self.answer(c, key, session, aid, output=json.dumps(raw))
        status, r, _ = self.api("inspect", {"agent": "inspector", "name": "web"})
        t.join(5)
        self.assertEqual(status, 200, r)
        v = r["inspect"]
        self.assertEqual(got["verb"], "inspect")
        self.assertEqual(v["env"], ["DB_PASSWORD", "TZ"])                       # names, never values
        self.assertEqual(v["labels"]["traefik.api.token"], "••••")
        self.assertEqual(v["labels"]["com.x.name"], "web")
        self.assertNotIn("evil", v)
        self.assertNotIn("hunter2", json.dumps(v))
        c.close()

    def test_an_answer_that_is_no_inspect_is_not_shown(self):
        self.session()
        c, session, aid, key = self.make_host("junker")
        got, t = self.answer(c, key, session, aid, output="not json at all")
        status, r, _ = self.api("inspect", {"agent": "junker", "name": "web"})
        t.join(5)
        self.assertEqual(status, 502)
        c.close()

    def test_stats_numbers_only(self):
        self.session()
        c, session, aid, key = self.make_host("statter")
        out = {"cpu": 80, "mem": 800, "mem_limit": 4000, "mem_pct": 20, "net_rx": 11, "net_tx": 22, "block_read": 6,
               "block_write": 9, "pids": 7, "name": "<b>x</b>", "mem_pct_text": "lots"}
        got, t = self.answer(c, key, session, aid, output=json.dumps(out))
        status, r, _ = self.api("stats", {"agent": "statter", "name": "web"})
        t.join(5)
        self.assertEqual(status, 200)
        self.assertEqual(r["stats"], {"cpu": 80, "mem": 800, "mem_limit": 4000, "mem_pct": 20, "net_rx": 11, "net_tx": 22,
                                      "block_read": 6, "block_write": 9, "pids": 7})
        c.close()

    def test_stats_with_text_instead_of_numbers_gives_zeros(self):
        self.session()
        c, session, aid, key = self.make_host("textstat")
        got, t = self.answer(c, key, session, aid, output=json.dumps({"cpu": "80%", "mem": [1], "pids": True}))
        status, r, _ = self.api("stats", {"agent": "textstat", "name": "web"})
        t.join(5)
        self.assertEqual(status, 200)
        self.assertTrue(all(v == 0 for v in r["stats"].values()))
        c.close()

    def test_logs(self):
        self.session()
        c, session, aid, key = self.make_host("logger")
        got, t = self.answer(c, key, session, aid, output="line 1\nline 2\n")
        status, r, _ = self.api("logs", {"agent": "logger", "name": "web"})
        t.join(5)
        self.assertEqual((status, r["text"]), (200, "line 1\nline 2\n"))
        c.close()


class Streams(Base):
    def test_a_live_log_opens_through_servers_and_a_shell_needs_the_admin_level_and_a_pin(self):
        self.session()
        c, session, aid, key = self.make_host("streamer", caps=("docker", "terminal"))
        status, r, _ = self.api("stream-open", {"agent": "streamer", "kind": "logs", "name": "web"})
        self.assertEqual(status, 200, r)
        sid = r["id"]
        while True:
            m = c.read_signed(key, session)
            if m.get("type") == "open":
                break
        self.assertEqual((m["verb"], m["target"]), ("follow", "web"))
        c.send_signed(key, session, aid, {"type": "chunk", "cmd": sid, "data": base64.b64encode(b"hello\n").decode()})
        status, r, _ = self.api("stream-read", {"id": sid, "after": 0})
        self.assertEqual((status, base64.b64decode(r["data"])), (200, b"hello\n"))
        self.assertEqual(self.api("stream-close", {"id": sid})[0], 200)
        # the shell: admin level, and the PIN (no PIN is set on this server, so labhud refuses it)
        status, r, _ = self.api("stream-open", {"agent": "streamer", "kind": "shell", "name": "web"})
        self.assertEqual(status, 403)
        c.close()


class Scopes(Base):
    def token(self, scope):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        conn.request("POST", "/api/tokens/create", body=json.dumps({"name": f"t-{scope}", "scope": scope}),
                     headers={"Host": self.host, "Origin": f"http://{self.host}", "Content-Type": "application/json",
                              "X-Labhud-Admin": self.ticket})
        out = json.loads(conn.getresponse().read())
        conn.close()
        self.assertTrue(out.get("token"), out)
        return out["token"]

    def test_a_read_token_reads_and_nothing_more(self):
        self.session()
        c, session, aid, key = self.make_host("scoper")
        tok = self.token("read")
        self.assertEqual(self.api("hosts", ticket=False, origin=False, token=tok)[0], 200)
        self.assertEqual(self.api("containers", {"agent": "scoper"}, ticket=False, origin=False, token=tok)[0], 200)
        self.assertEqual(self.api("action", {"agent": "scoper", "name": "web", "verb": "stop"}, ticket=False, origin=False, token=tok)[0], 403)
        self.assertEqual(self.api("session", ticket=False, origin=False, token=tok)[0], 403)
        self.assertEqual(self.api("audit-verify", ticket=False, origin=False, token=tok)[0], 403)
        self.assertEqual(self.api("stream-open", {"agent": "scoper", "kind": "shell", "name": "web"}, ticket=False, origin=False, token=tok)[0], 403)
        c.close()

    def test_an_act_token_acts_but_does_not_administer(self):
        self.session()
        c, session, aid, key = self.make_host("actor")
        tok = self.token("act")
        got, t = self.answer(c, key, session, aid)
        self.assertEqual(self.api("action", {"agent": "actor", "name": "web", "verb": "restart"}, ticket=False, origin=False, token=tok)[0], 200)
        t.join(5)
        self.assertEqual(self.api("audit-verify", ticket=False, origin=False, token=tok)[0], 403)
        c.close()

    def test_an_admin_token_verifies_the_audit_chain(self):
        tok = self.token("admin")
        status, r, _ = self.api("audit-verify", ticket=False, origin=False, token=tok)
        self.assertEqual(status, 200)
        self.assertTrue(r["intact"])
        self.assertGreater(r["checked"], 0)


class FakeHandler:
    """What _gate reads of a request."""

    def __init__(self, ip="192.0.2.9", headers=None, https=True, cli=False, token=None, same_page=True,
                 may=("read", "act", "admin")):
        self.client_address = (ip, 1)
        self.headers = headers or {"X-Labhud-Admin": "t1"}
        self.path = "/api/servers/hosts"
        self._https, self._cli_, self._tok, self._same, self._may = https, cli, token, same_page, may

    def _ip(self):
        return self.client_address[0]

    def _cli(self):
        return self._cli_

    def _token(self):
        return self._tok

    def _same_page(self):
        return self._same

    def _token_may(self, needed):
        return False

    def _admin_any(self):
        return "read" in self._may

    def _operator(self):
        return "act" in self._may

    def _may_manage_now(self):
        return "admin" in self._may

    def _who(self):
        return "admin"


class GateUnits(unittest.TestCase):
    def setUp(self):
        servers.reset()
        self.clock = [1000.0]
        self.patches = [unittest.mock.patch.object(servers, "_now", lambda: self.clock[0]),
                        unittest.mock.patch.object(servers, "_https", lambda h: h._https),
                        unittest.mock.patch.object(audit, "record", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(servers.reset)

    def started(self, h):
        servers._start_session(h)
        return h

    def test_every_route_has_a_level_and_every_level_is_known(self):
        found = {k[1].removeprefix("/api/servers/") for k in routes.ROUTES if k[1].startswith("/api/servers/")}
        self.assertEqual(found, set(servers.NEEDS))
        self.assertTrue(set(servers.NEEDS.values()) <= set(servers.LEVELS))

    def test_destructive_and_administrative_routes_need_admin_and_nothing_acts_with_read(self):
        for op in ("session", "stream-input", "stream-resize", "audit-verify"):
            self.assertEqual(servers.NEEDS[op], "admin", op)
        self.assertEqual(servers.NEEDS["action"], "act")
        for op in ("hosts", "containers", "logs", "inspect", "stats"):
            self.assertEqual(servers.NEEDS[op], "read", op)

    def test_plain_http_is_refused_before_anything_else(self):
        code, r = servers._gate(FakeHandler(https=False), "hosts")
        self.assertEqual(code, 403)
        self.assertIn("HTTPS", r["error"])

    def test_a_level_below_the_one_needed_is_refused(self):
        h = self.started(FakeHandler(may=("read",)))
        self.assertIsNone(servers._gate(h, "hosts"))
        self.assertEqual(servers._gate(h, "action")[0], 403)
        self.assertEqual(servers._gate(h, "audit-verify")[0], 403)

    def test_a_page_that_is_not_labhuds_own_is_refused(self):
        self.assertEqual(servers._gate(FakeHandler(same_page=False), "hosts")[0], 403)

    def test_no_session_means_start_again_with_the_pin_and_15_idle_minutes_end_it(self):
        h = FakeHandler()
        code, r = servers._gate(h, "hosts")
        self.assertEqual((code, r.get("resume")), (401, True))
        servers._start_session(h)
        self.assertIsNone(servers._gate(h, "hosts"))
        self.clock[0] += servers.SESSION_IDLE - 1
        self.assertIsNone(servers._gate(h, "hosts"))       # every call keeps it alive
        self.clock[0] += servers.SESSION_IDLE + 1
        self.assertEqual(servers._gate(h, "hosts")[0], 401)
        self.assertEqual(servers._gate(h, "hosts")[0], 401)   # and it stays ended

    def test_a_session_belongs_to_its_credentials(self):
        a = self.started(FakeHandler(headers={"X-Labhud-Admin": "ticket-a"}))
        b = FakeHandler(headers={"X-Labhud-Admin": "ticket-b"})
        self.assertIsNone(servers._gate(a, "hosts"))
        self.assertEqual(servers._gate(b, "hosts")[0], 401)

    def test_the_cli_and_tokens_need_no_session(self):
        self.assertIsNone(servers._gate(FakeHandler(cli=True), "hosts"))
        self.assertIsNone(servers._gate(FakeHandler(token=("id", {"scope": "admin"})), "hosts"))

    def test_too_many_refusals_block_the_address_for_longer_each_time(self):
        h = FakeHandler(may=())
        for _ in range(servers.FAILS):
            self.assertEqual(servers._gate(h, "hosts")[0], 403)
        code, r = servers._gate(h, "hosts")
        self.assertEqual(code, 429)
        self.assertEqual(r["retry_after"], servers.FIRST_BLOCK)
        # another address is not blocked
        self.assertEqual(servers._gate(FakeHandler(ip="192.0.2.77", may=()), "hosts")[0], 403)
        self.clock[0] += servers.FIRST_BLOCK + 1
        for _ in range(servers.FAILS):
            self.assertEqual(servers._gate(h, "hosts")[0], 403)
        code, r = servers._gate(h, "hosts")
        self.assertEqual((code, r["retry_after"]), (429, servers.FIRST_BLOCK * 2))

    def test_a_good_call_while_blocked_is_still_refused(self):
        bad = FakeHandler(may=())
        for _ in range(servers.FAILS):
            servers._gate(bad, "hosts")
        good = self.started(FakeHandler())
        self.assertEqual(servers._gate(good, "hosts")[0], 429)

    def test_an_ended_session_is_no_failed_attempt(self):
        h = FakeHandler()
        for _ in range(servers.FAILS * 2):
            self.assertEqual(servers._gate(h, "hosts")[0], 401)

    def test_only_the_networks_set_may_act(self):
        admin.configure({"servers_networks": ["192.0.2.0/24"]})
        self.addCleanup(admin.configure, None)
        inside = self.started(FakeHandler(ip="192.0.2.9"))
        outside = self.started(FakeHandler(ip="198.51.100.4"))
        self.assertIsNone(servers._gate(inside, "action"))
        self.assertEqual(servers._gate(outside, "action")[0], 403)
        self.assertIsNone(servers._gate(outside, "hosts"))      # reading is not limited by it
        cli = FakeHandler(ip="198.51.100.4", cli=True)
        self.assertIsNone(servers._gate(cli, "action"))         # the command beside the server is not concerned


if __name__ == "__main__":
    unittest.main()
