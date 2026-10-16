"""Agents (agents.py, websock.py, /api/agent/ws, /api/agents/*): pairing from a one-time code,
signed frames, what an agent may do, and removal. A minimal WebSocket client written here plays
the agent against the real demo server; commands go through a fake connection in-process."""

import base64
import json
import os
import queue
import shutil
import socket
import struct
import tempfile
import threading
import time
import unittest

import agentproto as P
import agents
import websock
from tests import signin
from tests.test_lock import FakeStore
from tests.test_smoke import DemoServer


class WSClient:
    """Just enough of a WebSocket client: masked text frames, close."""

    def __init__(self, port, origin=None):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /api/agent/ws HTTP/1.1\r\nHost: 10.0.0.9:{port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n")
        if origin:
            req += f"Origin: {origin}\r\n"
        self.s.sendall((req + "\r\n").encode())
        self.f = self.s.makefile("rb")
        self.status = int(self.f.readline().split()[1])
        while self.f.readline() not in (b"\r\n", b""):
            pass
        self.seq, self.seq_in = 0, 0

    def send_raw(self, text, op=1):
        data = text.encode() if isinstance(text, str) else text
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack("!H", n)
                                     if n < 65536 else bytes([0x80 | 127]) + struct.pack("!Q", n))
        self.s.sendall(head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))

    def recv(self):
        """The next text message, or None when the server closed."""
        while True:
            h = self.f.read(2)
            if len(h) < 2:
                return None
            n = h[1] & 0x7F
            if n == 126:
                n = struct.unpack("!H", self.f.read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self.f.read(8))[0]
            data = self.f.read(n)
            op = h[0] & 0x0F
            if op == 0x8:
                return None
            if op == 0x9:
                self.send_raw(data, op=0xA)
                continue
            if op == 0x1:
                return data.decode()

    def recv_json(self):
        text = self.recv()
        return json.loads(text) if text else None

    def send_signed(self, key, session, aid, message, seq=None, t=None):
        self.seq = self.seq + 1 if seq is None else seq
        self.send_raw(P.make_frame(key, P.TO_LABHUD, session, aid, self.seq, int(t or time.time()), message))

    def read_signed(self, key, session):
        text = self.recv()
        if text is None:
            return None
        aid, self.seq_in, msg = P.read_frame(text, lambda a: key, P.TO_AGENT, session, self.seq_in, int(time.time()))
        return msg

    def close(self):
        try:
            self.s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.f.close()
        self.s.close()


HELLO = {"type": "hello", "version": "0.1.0", "host": {"hostname": "box1", "arch": "amd64"},
         "caps": ["sensors", "docker", "nonsense"]}


class Server(DemoServer):
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

    def manage(self, op, body=None, origin=True):
        status, data = self.post_json(f"/api/agents/{op}", body or {}, f"http://{self.host}" if origin else None)
        return status, json.loads(data)

    def pair(self, code=None, name="box1"):
        """(client, session, agent id, key) after a full pairing with a fresh code."""
        code = code or self.manage("code")[1]["code"]
        c = WSClient(self.port)
        self.assertEqual(c.status, 101)
        welcome = c.recv_json()
        self.assertEqual(welcome["type"], "welcome")
        an = os.urandom(16).hex()
        c.send_raw(json.dumps({"type": "pair", "code_id": P.code_id(code), "agent_nonce": an, "name": name}))
        ch = c.recv_json()
        self.assertEqual(ch["type"], "challenge", ch)
        secret = P.pair_secret(code)
        self.assertEqual(ch["proof"], P.server_proof(secret, welcome["session"], ch["agent_id"], an,
                                                     ch["server_nonce"], ch["tls_fp"]))
        key = P.agent_key(secret, ch["agent_id"], an, ch["server_nonce"])
        c.send_signed(key, welcome["session"], ch["agent_id"], dict(HELLO, name=name))
        allow = c.read_signed(key, welcome["session"])
        self.assertEqual(allow, {"type": "allow", "caps": [], "targets": {}})
        return c, welcome["session"], ch["agent_id"], key

    def connect(self, aid, key):
        c = WSClient(self.port)
        session = c.recv_json()["session"]
        c.send_signed(key, session, aid, HELLO)
        return c, session

    def listed(self, aid):
        return next((a for a in self.manage("list")[1]["agents"] if a["id"] == aid), None)

    def wait_for(self, check, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return True
            time.sleep(0.05)
        return False

    # --- pairing ---

    def test_pair_and_list(self):
        c, session, aid, key = self.pair(name="box1")
        a = self.listed(aid)
        self.assertTrue(a["online"])
        self.assertEqual((a["version"], a["host"]["hostname"], a["caps"]), ("0.1.0", "box1", []))
        self.assertEqual(a["found"], ["sensors", "docker"])        # unknown capabilities dropped
        self.assertNotIn("key", json.dumps(self.manage("list")[1]))
        c.close()
        self.assertTrue(self.wait_for(lambda: not self.listed(aid)["online"]))

    def test_code_once(self):
        code = self.manage("code")[1]["code"]
        c, *_ = self.pair(code)
        c.close()
        again = WSClient(self.port)
        again.recv_json()
        again.send_raw(json.dumps({"type": "pair", "code_id": P.code_id(code), "agent_nonce": "0" * 32}))
        self.assertIn("wrong or expired", again.recv_json()["error"])
        self.assertIsNone(again.recv())
        again.close()

    def test_wrong_code(self):
        c = WSClient(self.port)
        c.recv_json()
        c.send_raw(json.dumps({"type": "pair", "code_id": P.code_id("guessed"), "agent_nonce": "0" * 32}))
        self.assertIn("wrong or expired", c.recv_json()["error"])
        c.close()

    def test_code_needs_the_page(self):
        self.assertEqual(self.manage("code", origin=False)[0], 403)
        self.assertEqual(self.manage("list", origin=False)[0], 403)

    def test_browser_refused(self):
        c = WSClient(self.port, origin=f"http://{self.host}")
        self.assertEqual(c.status, 403)
        c.close()

    def test_hello_from_stranger(self):
        c = WSClient(self.port)
        session = c.recv_json()["session"]
        c.send_signed("f" * 64, session, "deadbeef", HELLO)
        self.assertIn("bad signature", c.recv_json()["error"])
        self.assertIsNone(c.recv())
        c.close()

    def test_silent_agent_dropped(self):
        c = WSClient(self.port)
        c.recv_json()
        started = time.monotonic()
        self.assertIsNone(c.recv())
        self.assertLess(time.monotonic() - started, agents.FIRST_FRAME + 3)
        c.close()

    def test_installer_served(self):
        status, _, body = self.get("/agent/install-agent.sh", host="any-name:1")   # under any name
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertNotIn("@AGENT_", text)
        self.assertIn('VERSION="0.1.0"', text)
        self.assertIn("releases/download", text)
        import subprocess
        self.assertEqual(subprocess.run(["sh", "-n"], input=body).returncode, 0)

    def test_code_lines(self):
        r = self.manage("code")[1]
        pinned = any(agents.release()[f"@AGENT_SHA256_{a}@"] for a in ("AMD64", "ARM64"))
        self.assertEqual(r["released"], pinned)
        self.assertIn(r["code"], r["install"])
        if pinned:   # one line downloads and checks; the image is there too
            self.assertIn("/agent/install-agent.sh | sudo sh -s -- --url", r["install"])
            self.assertIn("--network host", r["docker"])
            self.assertIn(r["code"], r["docker"])
        else:        # nothing to download: a binary of your own, checked against your checksum
            self.assertIn("--binary ./labhud-agent --sha256 YOUR_SHA256", r["install"])
            self.assertEqual(r["docker"], "")

    def test_code_lines_once_released(self):
        real = agents.release
        agents.release = lambda releases="": dict(real(releases), **{"@AGENT_SHA256_AMD64@": "a" * 64})
        try:
            r = agents.install_lines("http://wall:8095", "CODE1")
        finally:
            agents.release = real
        self.assertEqual((r["released"], r["builds"]), (True, ["amd64"]))
        self.assertEqual(r["install"], "curl -fsSL http://wall:8095/agent/install-agent.sh | sudo sh -s -- "
                                       "--url http://wall:8095 --code CODE1")
        self.assertTrue(r["docker"].endswith("ghcr.io/mithrandler/labhud-agent:latest"))

    # --- after pairing ---

    def test_reconnect_and_state(self):
        c, session, aid, key = self.pair(name="statebox")
        c.close()
        c, session = self.connect(aid, key)
        self.assertEqual(c.read_signed(key, session)["type"], "allow")
        c.send_signed(key, session, aid, {"type": "state", "sensors": {"cpu": 12.5, "cpu_temp": 48},
                                          "containers": {"jellyfin": {"state": "running"}}})
        self.assertTrue(self.wait_for(lambda: json.loads(self.get("/api/snapshot")[2])
                                      .get("agents", {}).get("statebox", {}).get("sensors", {}).get("cpu") == 12.5))
        self.assertEqual(self.listed(aid)["choices"]["docker"], ["jellyfin"])
        c.close()

    def test_replay_closes(self):
        c, session, aid, key = self.pair(name="replay")
        c.send_signed(key, session, aid, {"type": "event", "text": "one"}, seq=5)
        c.send_signed(key, session, aid, {"type": "event", "text": "again"}, seq=5)
        self.assertIn("replayed", c.recv_json()["error"])
        self.assertIsNone(c.recv())
        c.close()

    def test_old_session_refused(self):
        c, session, aid, key = self.pair(name="oldsession")
        c.close()
        c = WSClient(self.port)
        c.recv_json()
        c.send_signed(key, session, aid, HELLO)          # signed for the previous connection
        self.assertIn("bad signature", c.recv_json()["error"])
        c.close()

    def test_caps_targets_and_remove(self):
        c, session, aid, key = self.pair(name="removeme")
        self.assertEqual(self.manage("caps", {"id": aid, "caps": ["docker", "bogus"]})[0], 400)
        self.assertEqual(self.manage("caps", {"id": aid, "caps": ["docker"]})[0], 200)
        self.assertEqual(c.read_signed(key, session), {"type": "allow", "caps": ["docker"], "targets": {"docker": []}})
        self.assertEqual(self.manage("targets", {"id": aid, "cap": "docker", "targets": ["a;rm -rf /"]})[0], 400)
        self.assertEqual(self.manage("targets", {"id": aid, "cap": "docker", "targets": ["jellyfin"]})[0], 200)
        self.assertEqual(c.read_signed(key, session)["targets"], {"docker": ["jellyfin"]})
        self.assertEqual(self.manage("remove", {"id": aid})[0], 200)
        self.assertEqual(c.read_signed(key, session), {"type": "bye", "reason": "removed"})
        self.assertIsNone(c.recv())
        c.close()
        self.assertIsNone(self.listed(aid))
        again, s2 = self.connect(aid, key)                # its key is gone
        self.assertIn("bad signature", again.recv_json()["error"])
        again.close()


class Buttons(Server):
    """The whole way: a ticked guest gets a button on its Proxmox card, the press reaches the
    agent as a signed command, and its result comes back to the display."""

    def test_guest_button_and_press(self):
        c, session, aid, key = self.pair(name="pvebox")
        c.send_signed(key, session, aid, {"type": "state", "node": "pve",
                                          "guests": {"101": {"name": "web01", "running": True}}})
        name = f"agent-{aid}-pve-101-shutdown"
        origin = f"http://{self.host}"
        self.assertEqual(self.post_json(f"/action/{name}", {}, origin)[0], 403)   # nothing ticked yet
        self.manage("caps", {"id": aid, "caps": ["pve"]})
        self.manage("targets", {"id": aid, "cap": "pve", "targets": ["101"]})
        c.read_signed(key, session), c.read_signed(key, session)                  # the two allows

        self.assertTrue(self.wait_for(lambda: any(name in [a["name"] for a in x.get("actions", [])]
                                                  for x in json.loads(self.get("/api/v1/state")[2])["cards"])))
        self.assertNotIn(f"agent-{aid}-pve-101-start", json.dumps(json.loads(self.get("/api/v1/state")[2])))  # it runs

        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=self.post_json(f"/action/{name}", {}, origin)))
        t.start()
        cmd = c.read_signed(key, session)
        self.assertEqual((cmd["type"], cmd["cap"], cmd["verb"], cmd["target"]), ("command", "pve", "shutdown", "101"))
        c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": False, "error": "VM is locked"})
        t.join(5)
        self.assertEqual(answer["r"][0], 502)
        self.assertEqual(json.loads(answer["r"][1])["error"], "VM is locked")
        self.assertEqual(self.post_json(f"/action/{name}", {}, None)[0], 403)        # not from the page
        ticket, self.ticket = self.ticket, None
        try:
            self.assertEqual(self.post_json(f"/action/{name}", {}, origin)[0], 403)  # not signed in
            self.assertEqual(self.manage("list")[0], 403)
        finally:
            self.ticket = ticket
        self.assertEqual(self.post_json(f"/action/agent-{aid}-pve-102-shutdown", {}, origin)[0], 403)  # not ticked
        c.close()


class Names(unittest.TestCase):
    def setUp(self):
        self.real = agents.store
        agents.store = FakeStore()
        agents._state.clear()
        agents.store.save(agents.AGENTS_KEY, {"a1b2c3d4": {
            "name": "n100", "key": "k", "caps": ["docker", "systemd", "wol", "host"],
            "targets": {"docker": ["jellyfin", "Plex_1"], "systemd": ["nginx.service"],
                        "wol": ["aa:bb:cc:dd:ee:ff"], "host": ["host"]}}})

    def tearDown(self):
        agents.store = self.real

    def test_round_trip(self):
        for cap, target in (("docker", "jellyfin"), ("docker", "Plex_1"), ("systemd", "nginx.service"),
                            ("wol", "aa:bb:cc:dd:ee:ff"), ("host", "host")):
            for verb in agents.VERBS[cap]:
                name = agents.action_name("a1b2c3d4", cap, target, verb)
                self.assertLessEqual(len(name), 64)
                self.assertRegex(name, r"^[a-z0-9][a-z0-9-]{0,63}$")
                self.assertEqual(agents.resolve(name), ("a1b2c3d4", cap, target, verb))

    def test_resolve_refuses(self):
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-plex-restart"))       # not ticked
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-jellyfin-exec"))      # no such verb
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-pve-101-start"))             # cap not ticked
        self.assertIsNone(agents.resolve("agent-ffffffff-docker-jellyfin-restart"))   # no such agent
        self.assertIsNone(agents.resolve("vm-pve-101-start"))

    def test_card_actions(self):
        host = agents.card_actions({"id": "n", "agent": "n100"})
        self.assertEqual([n.rsplit("-", 1)[1] for n in host], ["reboot", "shutdown", "update"])
        one = agents.card_actions({"id": "j", "agent": "n100", "agent_target": "docker:jellyfin"})
        self.assertEqual(one, [f"agent-a1b2c3d4-docker-jellyfin-{v}" for v in ("start", "stop", "restart")])
        self.assertEqual(agents.card_actions({"id": "p", "agent": "n100", "agent_target": "docker:plex"}), [])
        self.assertEqual(agents.card_actions({"id": "x", "agent": "nobody"}), [])

    def test_text(self):
        self.assertEqual(agents.action_text(agents.action_name("a1b2c3d4", "host", "host", "update")), "n100 · UPDATE")
        self.assertEqual(agents.action_text(agents.action_name("a1b2c3d4", "docker", "jellyfin", "restart")),
                         "jellyfin · RESTART")


class Cards(unittest.TestCase):
    """config.py's agent keys and cardstate's view of an agent's target."""

    def parse(self, card):
        import config
        text = ('[[page]]\nid = "p"\ntitle = "P"\n[[page.group]]\nid = "g"\ntitle = "G"\n'
                '[[page.group.card]]\nid = "c"\nname = "C"\n' + card)
        return config.loads(text)["cards"]["g"][0]

    def test_config_keys(self):
        import config
        c = self.parse('agent = "n100"\n')
        self.assertEqual((c["sensors"], c["check"]), ("agents.n100.sensors", ["agent", "n100", "", ""]))
        c = self.parse('agent = "n100"\nagent_target = "docker:jellyfin"\nping = "10.0.0.1"\n')
        self.assertEqual(c["check"][0], "ping")             # a card's own check wins
        self.assertNotIn("sensors", c)
        for bad in ('agent_target = "docker:jellyfin"\n', 'agent = "a.b"\n',
                    'agent = "n"\nagent_target = "shell:ls"\n', 'agent = "n"\nagent_target = "docker:a;b"\n'):
            with self.assertRaises(config.ConfigError, msg=bad):
                self.parse(bad)

    def test_status(self):
        import cardstate
        a = {"online": True, "containers": {"jellyfin": {"state": "running"}, "old": {"state": "exited"}},
             "units": {"x.service": {"active": "activating"}}, "guests": {"101": {"running": False}}}
        self.assertIs(cardstate.agent_status(a, "", ""), True)
        self.assertIs(cardstate.agent_status(a, "docker", "jellyfin"), True)
        self.assertIs(cardstate.agent_status(a, "docker", "old"), False)
        self.assertIsNone(cardstate.agent_status(a, "systemd", "x.service"))
        self.assertIs(cardstate.agent_status(a, "pve", "101"), False)
        self.assertIsNone(cardstate.agent_status(dict(a, online=False), "docker", "jellyfin"))   # offline: unknown
        self.assertIsNone(cardstate.agent_status(None, "", ""))


class FakeWS:
    """A websock.Conn stand-in: what the server sends lands in `out`, recv() takes from `inbox`."""

    def __init__(self):
        self.inbox, self.out, self.closed = queue.Queue(), queue.Queue(), False

    def send(self, text):
        if self.closed:
            raise websock.Closed("closed")
        self.out.put(text)

    def recv(self):
        item = self.inbox.get(timeout=5)
        if item is None:
            raise websock.Closed("closed by the peer")
        return item

    def ping(self):
        pass

    def close(self, code=1000, reason=""):
        if not self.closed:
            self.closed = True
            self.inbox.put(None)


class Commands(unittest.TestCase):
    """A paired agent in-process: commands, the allowlist, results, events."""

    def setUp(self):
        self.real = agents.store
        agents.store = FakeStore()
        agents._live.clear()
        agents._state.clear()
        self.events = []
        self.hooks = agents.on_change, agents.on_event
        agents.on_change, agents.on_event = (lambda: None), (lambda t, b: self.events.append((t, b)))
        code = agents.new_code()
        self.ws = FakeWS()
        self.session = agents.Session(self.ws, "test")
        self.thread = threading.Thread(target=self.session.run, daemon=True)
        self.thread.start()
        sid = json.loads(self.ws.out.get(timeout=2))["session"]
        an = "1" * 32
        self.ws.inbox.put(json.dumps({"type": "pair", "code_id": P.code_id(code), "agent_nonce": an, "name": "n1"}))
        ch = json.loads(self.ws.out.get(timeout=2))
        self.aid, self.sid = ch["agent_id"], sid
        self.key = P.agent_key(P.pair_secret(code), self.aid, an, ch["server_nonce"])
        self.seq = self.seq_in = 0
        self.agent_send(HELLO)
        self.assertEqual(self.agent_read()["type"], "allow")

    def tearDown(self):
        self.ws.close()
        self.thread.join(2)
        agents.store = self.real
        agents.on_change, agents.on_event = self.hooks

    def agent_send(self, msg):
        self.seq += 1
        self.ws.inbox.put(P.make_frame(self.key, P.TO_LABHUD, self.sid, self.aid, self.seq, int(time.time()), msg))

    def agent_read(self):
        _, self.seq_in, msg = P.read_frame(self.ws.out.get(timeout=2), lambda a: self.key, P.TO_AGENT,
                                           self.sid, self.seq_in, int(time.time()))
        return msg

    def allow(self):
        agents.set_caps(self.aid, ["docker"])
        agents.set_targets(self.aid, "docker", ["jellyfin"])
        self.agent_read(), self.agent_read()

    def answer(self, ok, error=None):
        cmd = self.agent_read()
        self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("docker", "restart", "jellyfin"))
        self.agent_send({"type": "result", "cmd": cmd["cmd"], "ok": ok, **({"error": error} if error else {})})

    def test_refused_outside_the_allowlist(self):
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin"), (False, "not allowed"))
        self.allow()
        self.assertEqual(agents.command(self.aid, "docker", "restart", "plex"), (False, "not allowed"))
        self.assertEqual(agents.command(self.aid, "docker", "exec", "jellyfin"), (False, "not allowed"))
        self.assertEqual(agents.command(self.aid, "pve", "start", "101"), (False, "not allowed"))
        self.assertTrue(self.ws.out.empty())             # nothing was sent

    def test_command_ok_and_failed(self):
        self.allow()
        t = threading.Thread(target=self.answer, args=(True,))
        t.start()
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin"), (True, ""))
        t.join()
        t = threading.Thread(target=self.answer, args=(False, "no such container"))
        t.start()
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin"), (False, "no such container"))
        t.join()

    def test_no_answer(self):
        self.allow()
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin", wait=0.2),
                         (False, "no answer from the agent"))

    def test_offline_during_command(self):
        self.allow()
        threading.Timer(0.2, self.ws.close).start()
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin"), (False, "the agent went offline"))
        self.thread.join(2)
        self.assertFalse(agents.online(self.aid))
        self.assertEqual(agents.command(self.aid, "docker", "restart", "jellyfin"), (False, "the agent is offline"))

    def test_events_limited(self):
        for i in range(agents.EVENTS_PER_MINUTE + 5):
            self.agent_send({"type": "event", "text": f"e{i}", "bad": i == 0})
        self.agent_send({"type": "state"})     # a frame after them: the events were all handled
        deadline = time.monotonic() + 2
        while "_t" not in agents._state.get(self.aid, {}) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(len([e for e in self.events if e[0].startswith("n1: ")]), agents.EVENTS_PER_MINUTE)
        self.assertEqual(self.events[1], ("n1: e0", True))      # [0] is "Agent paired"

    def test_snapshot_and_rename(self):
        self.agent_send({"type": "state", "sensors": {"cpu": 1}})
        deadline = time.monotonic() + 2
        while not agents._state.get(self.aid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(agents.snapshot()["n1"]["sensors"], {"cpu": 1})
        self.assertTrue(agents.snapshot()["n1"]["online"])
        self.assertEqual(agents.rename(self.aid, "  new   name "), "new name")
        self.assertIn("new name", agents.snapshot())


class Handshake(unittest.TestCase):
    def test_rfc_example(self):           # RFC 6455, section 1.3
        self.assertEqual(websock.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_needs_upgrade(self):
        good = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Upgrade": "websocket",
                "Connection": "keep-alive, Upgrade", "Sec-WebSocket-Version": "13"}
        self.assertTrue(websock.handshake_ok(good))
        for k, v in (("Upgrade", "h2c"), ("Sec-WebSocket-Version", "8"), ("Sec-WebSocket-Key", "short"),
                     ("Connection", "close")):
            self.assertIsNone(websock.handshake_ok(dict(good, **{k: v})))


if __name__ == "__main__":
    unittest.main()
