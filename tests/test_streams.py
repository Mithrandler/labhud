"""Live logs and the terminal (agents.stream_*, POST /api/setup/stream-*): the output is handed on
once and forgotten, the shell needs the admin's PIN, and what goes through is not kept."""

import base64
import http.client
import json
import shutil
import tempfile
import threading
import time
import unittest

import agents
from tests import signin, test_agents
from tests.test_smoke import DemoServer


class StreamBuffer(unittest.TestCase):
    def test_read_forgets_what_was_fetched(self):
        st = agents.Stream("s1", "a", "logs", "web", None, "t")
        st.put(b"one ")
        st.put(b"two")
        data, nxt, end = st.read(0, 0)
        self.assertEqual((data, nxt, end), (b"one two", 7, None))
        st.put(b" three")
        data, nxt, end = st.read(nxt, 0)
        self.assertEqual((data, nxt), (b" three", 13))
        self.assertEqual(len(st.data), 6)      # what was fetched first is gone
        self.assertEqual(st.read(nxt, 0), (b"", 13, None))

    def test_end_comes_after_the_last_bytes(self):
        st = agents.Stream("s1", "a", "logs", "web", None, "t")
        st.put(b"bye")
        st.finish("ended")
        self.assertEqual(st.read(0, 0), (b"bye", 3, None))
        self.assertEqual(st.read(3, 0), (b"", 3, "ended"))

    def test_a_slow_page_loses_the_oldest(self):
        st = agents.Stream("s1", "a", "logs", "web", None, "t")
        st.put(b"a" * agents.STREAM_BUFFER)
        st.put(b"b" * 10)
        data, nxt, _ = st.read(0, 0)
        self.assertEqual(len(data), agents.STREAM_BUFFER)
        self.assertTrue(data.endswith(b"b" * 10))
        self.assertEqual(nxt, agents.STREAM_BUFFER + 10)

    def test_read_waits_for_something(self):
        st = agents.Stream("s1", "a", "logs", "web", None, "t")
        threading.Timer(0.2, lambda: st.put(b"late")).start()
        t = time.monotonic()
        data, _, _ = st.read(0, 5)
        self.assertEqual(data, b"late")
        self.assertLess(time.monotonic() - t, 3)


def b64(text):
    return base64.b64encode(text.encode()).decode()


class Base(DemoServer):
    """test_agents.Server without its inherited tests: a server that has a signed-in admin, and its pair() and manage()."""
    manage = test_agents.Server.manage
    pair = test_agents.Server.pair

    @classmethod
    def setUpClass(cls):
        cls.data = tempfile.mkdtemp()
        cls.extra_env = {"LABHUD_DATA": cls.data, **signin.ENV}
        super().setUpClass()
        cls.ticket = signin.sign_in(cls.port, cls.host)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.data, ignore_errors=True)


class Streams(Base):
    def agent(self, name, caps, cap, targets):
        c, session, aid, key = self.pair(name=name)
        self.manage("caps", {"id": aid, "caps": caps})
        self.manage("targets", {"id": aid, "cap": cap, "targets": targets})
        c.read_signed(key, session), c.read_signed(key, session)
        return c, session, aid, key

    def setup(self, op, body, extra=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Host": self.host, "Content-Type": "application/json", "Origin": f"http://{self.host}",
                   "X-Labhud-Admin": self.ticket, **(extra or {})}
        conn.request("POST", "/api/setup/" + op, body=json.dumps(body), headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data)

    def opened(self, c, key, session):
        while True:
            m = c.read_signed(key, session)
            if m.get("type") == "open":
                return m

    def test_live_log(self):
        c, session, aid, key = self.agent("logbox", ["docker"], "docker", ["web"])
        self.assertEqual(self.setup("stream-open", {"agent": "logbox", "kind": "logs", "target": "db"})[0], 502)   # not ticked
        status, r = self.setup("stream-open", {"agent": "logbox", "kind": "logs", "target": "web"})
        self.assertEqual(status, 200)
        sid = r["id"]
        m = self.opened(c, key, session)
        self.assertEqual((m["cap"], m["verb"], m["target"], m["cmd"]), ("docker", "follow", "web", sid))
        c.send_signed(key, session, aid, {"type": "chunk", "cmd": sid, "data": b64("line 1\n")})
        status, r = self.setup("stream-read", {"id": sid, "after": 0})
        self.assertEqual((base64.b64decode(r["data"]), r["next"], r["end"]), (b"line 1\n", 7, None))
        # nothing new: the read waits; an `end` from the agent ends it
        threading.Timer(0.3, lambda: c.send_signed(key, session, aid, {"type": "end", "cmd": sid, "reason": "time"})).start()
        status, r = self.setup("stream-read", {"id": sid, "after": 7})
        self.assertEqual((r["data"], r["end"]), ("", "time"))
        # a log takes no input, a bad id is nothing
        self.assertEqual(self.setup("stream-input", {"id": sid, "data": b64("x")})[0], 409)
        self.assertEqual(self.setup("stream-read", {"id": "0" * 16})[0], 404)
        self.assertEqual(self.setup("stream-close", {"id": sid})[0], 200)
        self.assertEqual(self.setup("stream-read", {"id": sid})[0], 404)
        c.close()

    def test_two_at_most_per_agent(self):
        c, session, aid, key = self.agent("twobox", ["docker"], "docker", ["a", "b", "c"])
        ids = [self.setup("stream-open", {"agent": "twobox", "kind": "logs", "target": t}) for t in ("a", "b", "c")]
        self.assertEqual([i[0] for i in ids], [200, 200, 502])
        self.assertIn("too many", ids[2][1]["error"])
        for i in ids[:2]:
            self.setup("stream-close", {"id": i[1]["id"]})
        c.close()

    def test_shell_without_a_pin_is_refused(self):
        c, session, aid, key = self.agent("shellbox", ["terminal"], "terminal", ["web"])
        status, r = self.setup("stream-open", {"agent": "shellbox", "kind": "shell", "target": "web"})
        self.assertEqual(status, 403)
        self.assertIn("PIN", r["error"])
        c.close()


class ShellWithPin(Base):
    def test_shell(self):
        c, session, aid, key = self.pair(name="pinbox")
        self.manage("caps", {"id": aid, "caps": ["terminal"]})
        self.manage("targets", {"id": aid, "cap": "terminal", "targets": ["web"]})
        c.read_signed(key, session), c.read_signed(key, session)
        origin = f"http://{self.host}"

        def post(path, body, ticket=None):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
            headers = {"Host": self.host, "Content-Type": "application/json", "Origin": origin, "X-Labhud-Admin": self.ticket}
            if ticket:
                headers["X-Labhud-Unlock"] = ticket
            conn.request("POST", path, body=json.dumps(body), headers=headers)
            r = conn.getresponse()
            data = r.read()
            conn.close()
            return r.status, json.loads(data)

        status, r = post("/api/lock/pin", {"new": "2468"})
        self.assertEqual(status, 200)
        ticket = r["ticket"]
        opened = {"agent": "pinbox", "kind": "shell", "target": "web", "cols": 90, "rows": 30}
        self.assertEqual(post("/api/setup/stream-open", opened)[0], 403)                    # the PIN not typed
        self.assertEqual(post("/api/setup/stream-open", opened, "made-up")[0], 403)
        status, r = post("/api/setup/stream-open", opened, ticket)
        self.assertEqual(status, 200)
        sid = r["id"]
        while True:
            m = c.read_signed(key, session)
            if m.get("type") == "open":
                break
        self.assertEqual((m["cap"], m["verb"], m["target"], m["cols"], m["rows"]), ("terminal", "shell", "web", 90, 30))
        # keys go to the agent as they are, a resize too
        self.assertEqual(post("/api/setup/stream-input", {"id": sid, "data": b64("ls\n")}, ticket)[0], 200)
        m = c.read_signed(key, session)
        self.assertEqual((m["type"], base64.b64decode(m["data"])), ("input", b"ls\n"))
        self.assertEqual(post("/api/setup/stream-resize", {"id": sid, "cols": 100, "rows": 40}, ticket)[0], 200)
        m = c.read_signed(key, session)
        self.assertEqual((m["type"], m["cols"], m["rows"]), ("resize", 100, 40))
        self.assertEqual(post("/api/setup/stream-resize", {"id": sid, "cols": 0, "rows": 40}, ticket)[0], 400)
        self.assertEqual(post("/api/setup/stream-input", {"id": sid, "data": "***"}, ticket)[0], 400)
        # the PIN gone (a locked screen): the stream is not served any more
        self.assertEqual(post("/api/setup/stream-read", {"id": sid}, "made-up")[0], 403)
        c.send_signed(key, session, aid, {"type": "chunk", "cmd": sid, "data": b64("$ ")})
        status, r = post("/api/setup/stream-read", {"id": sid, "after": 0}, ticket)
        self.assertEqual(base64.b64decode(r["data"]), b"$ ")
        self.assertEqual(post("/api/setup/stream-close", {"id": sid}, ticket)[0], 200)
        m = c.read_signed(key, session)
        self.assertEqual(m["type"], "close")
        c.close()


def b64(text):   # noqa: F811
    return base64.b64encode(text.encode()).decode()


if __name__ == "__main__":
    unittest.main()
