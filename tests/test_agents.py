"""Agents (agents.py, websock.py, /api/agent/ws, /api/agents/*): pairing from a one-time code,
signed frames, what an agent may do, and removal. A minimal WebSocket client written here plays
the agent against the real demo server; commands go through a fake connection in-process."""

import base64
import hashlib
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

    # the demo's made-up agents in ⚙ > Devices > Agents: listed first, ticks kept in memory
    def test_demo_agents_listed_and_ticked(self):
        rows = {r["name"]: r for r in self.manage("list")[1]["agents"]}
        pve = rows["pve"]
        self.assertTrue(pve["online"] and "wol" in pve["caps"])
        self.assertEqual(pve["labels"]["pve"]["106"], "windows")
        self.assertEqual(self.manage("targets", {"id": pve["id"], "cap": "wol", "targets": pve["targets"]["wol"] + ["aa:bb:cc:dd:ee:01"]})[0], 200)
        self.assertEqual(self.manage("targets", {"id": pve["id"], "cap": "pve", "targets": ["999"]})[0], 400)   # no such guest
        self.assertEqual(self.manage("caps", {"id": pve["id"], "caps": ["docker"]})[0], 400)                   # not found there
        self.assertEqual(self.manage("remove", {"id": pve["id"]})[0], 400)                                     # the demo's stay
        pve = {r["name"]: r for r in self.manage("list")[1]["agents"]}["pve"]
        self.assertIn("aa:bb:cc:dd:ee:01", pve["targets"]["wol"])
        self.assertIn("aa:bb:cc:dd:ee:01", pve["choices"]["wol"])

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

    # --- updates ---

    def test_update_asked_and_answered(self):
        c, session, aid, key = self.pair(name="old1")
        c.close()
        pinned = agents.release()
        if not pinned["@AGENT_SHA256_AMD64@"]:
            self.skipTest("no agent release pinned")
        c, session = WSClient(self.port), None
        session = c.recv_json()["session"]
        c.send_signed(key, session, aid, dict(HELLO, version="0.0.9", install="service",
                                              host={"hostname": "old1", "arch": "amd64"}))
        self.assertEqual(c.read_signed(key, session)["type"], "allow")
        self.assertTrue(self.wait_for(lambda: self.listed(aid)["update"] == pinned["@AGENT_VERSION@"]))
        got = {}

        def agent_side():   # the agent: the update, then its result
            msg = c.read_signed(key, session)
            got.update(msg)
            c.send_signed(key, session, aid, {"type": "result", "cmd": msg["cmd"], "ok": True})
        t = threading.Thread(target=agent_side)
        t.start()
        status, r = self.manage("update", {"id": aid})
        t.join(5)
        self.assertEqual((status, r["ok"]), (200, True), r)
        self.assertEqual((got["type"], got["version"], got["sha256"]),
                         ("update", pinned["@AGENT_VERSION@"], pinned["@AGENT_SHA256_AMD64@"]))
        c.close()

    def test_update_refused_without_a_newer_release_or_in_a_container(self):
        c, session, aid, key = self.pair(name="box9")
        c.close()
        c = WSClient(self.port)
        session = c.recv_json()["session"]
        c.send_signed(key, session, aid, dict(HELLO, version="0.1.0", install="container", host={"arch": "amd64"}))
        c.read_signed(key, session)
        self.assertTrue(self.wait_for(lambda: self.listed(aid)["install"] == "container"))
        self.assertEqual(self.listed(aid)["update"], "")
        status, r = self.manage("update", {"id": aid})
        self.assertEqual(status, 409)
        self.assertIn("image", r["error"])
        c.close()

    def test_auto_update_flag(self):
        self.assertEqual(self.manage("auto", {"on": True})[1]["auto_update"], True)
        self.assertTrue(self.manage("list")[1]["auto_update"])
        self.assertEqual(self.manage("auto", {"on": False})[1]["auto_update"], False)
        self.assertEqual(self.manage("auto", {"on": True}, origin=False)[0], 403)

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
        with open(agents.RELEASE_FILE) as f:
            self.assertIn(f'VERSION="{json.load(f)["version"]}"', text)
        self.assertIn("releases/download", text)
        import subprocess
        self.assertEqual(subprocess.run(["sh", "-n"], input=body).returncode, 0)

    def test_code_lines(self):
        r = self.manage("code")[1]
        pinned = any(agents.release()[f"@AGENT_SHA256_{a}@"] for a in ("AMD64", "ARM64"))
        self.assertEqual(r["released"], pinned)
        self.assertIn(r["code"], r["install"])
        if pinned:   # one line downloads and checks; the image is there too
            self.assertIn("/agent/install-agent.sh | " + agents.SUDO + " sh -s -- --url", r["install"])
            self.assertIn("--network host", r["docker"])
            self.assertIn(r["code"], r["docker"])
        else:        # nothing to download: a binary of your own, checked against your checksum
            self.assertIn("--binary ./labhud-agent --sha256 YOUR_SHA256", r["install"])
            self.assertEqual(r["docker"], "")

    def test_code_lines_once_released(self):
        real = agents.release
        agents.release = lambda releases="": dict(real(releases), **{"@AGENT_SHA256_AMD64@": "a" * 64, "@AGENT_SHA256_ARM64@": ""})
        try:
            r = agents.install_lines("http://wall:8095", "CODE1")
        finally:
            agents.release = real
        self.assertEqual((r["released"], r["builds"]), (True, ["amd64"]))
        self.assertEqual(r["install"], "curl -fsSL http://wall:8095/agent/install-agent.sh | " + agents.SUDO + " sh -s -- "
                                       "--url http://wall:8095 --code CODE1")
        self.assertTrue(r["docker"].endswith("ghcr.io/mithrandler/labhud-agent:latest"))

    def test_service_may_replace_its_own_binary(self):
        """The unit keeps /usr read-only (ProtectSystem=full): the binary lives in a folder the
        service may write, or an update fails with "read-only file system"."""
        script = agents.install_script().decode()
        self.assertIn("BIN=$BINDIR/labhud-agent", script)
        self.assertIn("ReadWritePaths=$DIR $BINDIR", script)
        self.assertIn("ProtectSystem=full", script)

    def test_code_lines_with_labhud_own_certificate(self):
        """curl does not trust labhud's own certificate: -k, and the script's SHA-256 in the line."""
        r = agents.install_lines("https://wall:8095", "CODE1", own_cert=True)
        sha = hashlib.sha256(agents.install_script()).hexdigest()
        self.assertTrue(r["install"].startswith("curl -fsSLk https://wall:8095/agent/install-agent.sh -o install-agent.sh "
                                                f"&& echo '{sha}  install-agent.sh' | sha256sum -c && " + agents.SUDO + " sh install-agent.sh "
                                                "--url https://wall:8095 --code CODE1"), r["install"])

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
        self.assertEqual(self.listed(aid)["choices"]["docker"], ["jellyfin", "images"])
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

    def test_rename(self):
        c, session, aid, key = self.pair(name="before")
        self.assertEqual(self.manage("rename", {"id": aid, "name": 5})[0], 400)
        status, data = self.manage("rename", {"id": aid, "name": "  pve2.lan "})
        self.assertEqual((status, data["name"]), (200, "pve2-lan"))   # trimmed, no dots
        self.assertEqual(self.listed(aid)["name"], "pve2-lan")
        self.assertEqual(self.manage("rename", {"id": "nope", "name": "x"})[0], 404)
        self.manage("remove", {"id": aid})
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


class Logs(Server):
    """POST /api/setup/logs: a ticked container's last lines, for the admin only."""

    def test_logs(self):
        c, session, aid, key = self.pair(name="logbox")
        origin = f"http://{self.host}"
        self.manage("caps", {"id": aid, "caps": ["docker"]})
        self.manage("targets", {"id": aid, "cap": "docker", "targets": ["jellyfin"]})
        c.read_signed(key, session), c.read_signed(key, session)
        self.assertEqual(self.post_json("/api/setup/logs", {"agent": "logbox", "target": "plex"}, origin)[0], 502)
        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/logs", {"agent": "logbox", "target": "jellyfin"}, origin)))
        t.start()
        cmd = c.read_signed(key, session)
        self.assertEqual((cmd["verb"], cmd["target"]), ("logs", "jellyfin"))
        c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": True, "output": "line 1\nline 2\n"})
        t.join(5)
        self.assertEqual(answer["r"][0], 200)
        self.assertEqual(json.loads(answer["r"][1]), {"ok": True, "output": "line 1\nline 2\n"})
        self.assertEqual(self.post_json("/api/setup/logs", {"agent": "nobody", "target": "jellyfin"}, origin)[0], 404)
        self.assertEqual(self.post_json(f"/action/agent-{aid}-docker-jellyfin-logs", {}, origin)[0], 403)  # not an action
        ticket, self.ticket = self.ticket, None
        try:
            self.assertEqual(self.post_json("/api/setup/logs", {"agent": "logbox", "target": "jellyfin"}, origin)[0], 403)
        finally:
            self.ticket = ticket
        c.close()


class PruneAndHistory(Server):
    """Cleaning a host's unused images (the target "images", the admin only) and what each
    container went through (events with a kind)."""

    def test_prune(self):
        c, session, aid, key = self.pair(name="imgbox")
        origin = f"http://{self.host}"
        self.manage("caps", {"id": aid, "caps": ["docker"]})
        self.assertEqual(self.post_json("/api/setup/prune", {"agent": "imgbox"}, origin)[0], 502)   # not ticked
        self.manage("targets", {"id": aid, "cap": "docker", "targets": ["jellyfin", "images"]})
        c.read_signed(key, session), c.read_signed(key, session)
        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/prune", {"agent": "imgbox"}, origin)))
        t.start()
        cmd = c.read_signed(key, session)
        self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("docker", "prune", "images"))
        c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": True, "output": "freed 1.2 GB (14 image layers)"})
        t.join(5)
        self.assertEqual(json.loads(answer["r"][1]), {"ok": True, "output": "freed 1.2 GB (14 image layers)"})
        # "images" is no container: no buttons for it, and no action on it
        b = json.loads(self.get("/api/snapshot")[2])["agents"]["imgbox"]["buttons"]["docker"]
        self.assertEqual(sorted(b), ["jellyfin"])
        self.assertEqual(self.post_json(f"/action/agent-{aid}-docker-images-stop", {}, origin)[0], 403)
        self.assertEqual(self.listed(aid)["prune"], True)
        c.close()

    def test_inventory_and_selective_cleaning(self):
        c, session, aid, key = self.pair(name="hostbox")
        origin = f"http://{self.host}"
        self.manage("caps", {"id": aid, "caps": ["docker"]})
        self.manage("targets", {"id": aid, "cap": "docker", "targets": ["images"]})
        c.read_signed(key, session), c.read_signed(key, session)
        # an unknown kind of cleaning is refused before the agent hears of it
        self.assertEqual(self.post_json("/api/setup/prune", {"agent": "hostbox", "what": "everything"}, origin)[0], 400)
        text = json.dumps({"images": [{"id": "abc", "tags": ["nginx:1"], "size": 100, "created": 5, "used": 0, "dangling": False,
                                       "evil": "<script>"}],
                           "volumes": [{"name": "v", "driver": "local", "size": -1, "used": 1, "anonymous": False}],
                           "networks": [{"name": "bridge", "driver": "bridge", "scope": "local", "used": 0, "builtin": True}],
                           "more": {"images": 3, "other": 9}})
        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/inventory", {"agent": "hostbox"}, origin)))
        t.start()
        cmd = c.read_signed(key, session)
        self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("docker", "inventory", "images"))
        c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": True, "output": text})
        t.join(5)
        got = json.loads(answer["r"][1])
        self.assertTrue(got["ok"])
        self.assertEqual(got["images"][0], {"id": "abc", "tags": ["nginx:1"], "size": 100, "created": 5, "used": 0, "dangling": False})
        self.assertEqual(got["more"], {"images": 3})
        self.assertEqual(got["totals"], {"images": 100, "unused_images": 100, "volumes": 0})
        # the second look is served from the cache: no command goes to the agent
        self.assertTrue(json.loads(self.post_json("/api/setup/inventory", {"agent": "hostbox"}, origin)[1])["ok"])
        # each cleaning is its own verb, on "images"
        for what, verb in (("dangling", "prune-dangling"), ("networks", "prune-networks"), ("volumes", "prune-volumes")):
            answer.clear()
            t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/prune", {"agent": "hostbox", "what": what}, origin)))
            t.start()
            cmd = c.read_signed(key, session)
            self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("docker", verb, "images"))
            c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": True, "output": "done"})
            t.join(5)
            self.assertEqual(json.loads(answer["r"][1]), {"ok": True, "output": "done"})
        c.close()

    def test_stacks(self):
        c, session, aid, key = self.pair(name="stackbox")
        origin = f"http://{self.host}"
        self.manage("caps", {"id": aid, "caps": ["stacks"]})
        c.read_signed(key, session)

        def ask(body, reply):
            answer = {}
            t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/stack-run", dict(body, agent="stackbox"), origin)))
            t.start()
            cmd = c.read_signed(key, session)
            while cmd.get("type") != "command":   # an `allow` goes first when the stack gets ticked
                cmd = c.read_signed(key, session)
            c.send_signed(key, session, aid, dict({"type": "result", "cmd": cmd["cmd"]}, **reply))
            t.join(5)
            return cmd, json.loads(answer["r"][1])

        # a deploy: the files arrive sealed (not in the clear), the name gets ticked
        cmd, got = ask({"verb": "deploy", "name": "web", "compose": "services: {}\n", "env": "TOKEN=hunter2\n"},
                       {"ok": True, "output": "up"})
        self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("stacks", "deploy", "web"))
        self.assertEqual(got, {"ok": True, "output": "up"})
        self.assertNotIn("hunter2", json.dumps(cmd))
        self.assertNotIn("services", json.dumps(cmd))
        opened = json.loads(P.unseal(key, session, cmd["cmd"], cmd["files"]["nonce"], cmd["files"]["box"], P.MAX_SEALED_FILES))
        self.assertEqual(opened, {"compose": "services: {}\n", "env": "TOKEN=hunter2\n"})
        self.assertEqual(self.listed(aid)["targets"]["stacks"], ["web"])
        # no env key at all: the host keeps its .env
        cmd, _ = ask({"verb": "deploy", "name": "web", "compose": "services: {}\n"}, {"ok": True, "output": ""})
        self.assertEqual(json.loads(P.unseal(key, session, cmd["cmd"], cmd["files"]["nonce"], cmd["files"]["box"],
                                                      P.MAX_SEALED_FILES)), {"compose": "services: {}\n"})
        # the other verbs carry no files
        cmd, got = ask({"verb": "down", "name": "web"}, {"ok": False, "error": "compose down: boom"})
        self.assertNotIn("files", cmd)
        self.assertEqual(got["error"], "compose down: boom")
        # refused before the agent hears of it
        post = lambda body: self.post_json("/api/setup/stack-run", dict(body, agent="stackbox"), origin)   # noqa: E731
        self.assertEqual(post({"verb": "deploy", "name": "Bad Name", "compose": "x"})[0], 502)
        self.assertEqual(post({"verb": "deploy", "name": "web", "compose": ""})[0], 502)
        self.assertEqual(post({"verb": "deploy", "name": "web", "compose": "x" * (49 << 10)})[0], 502)
        self.assertEqual(post({"verb": "exec", "name": "web"})[0], 502)
        # a stack is no button on a card
        self.assertEqual(self.post_json(f"/action/agent-{aid}-stacks-web-down", {}, origin)[0], 403)
        c.close()

    def test_stack_from_git(self):
        """The server fetches the compose file from the repository and seals it: the token is not in the agent's frame, the answer or the audit."""
        import http.server

        seen = {}

        class Repo(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["path"], seen["auth"] = self.path, self.headers.get("Authorization")
                body = b"services:\n  web:\n    image: nginx\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        repo = http.server.HTTPServer(("127.0.0.1", 0), Repo)
        threading.Thread(target=repo.serve_forever, daemon=True).start()
        self.addCleanup(repo.server_close)
        self.addCleanup(repo.shutdown)
        c, session, aid, key = self.pair(name="gitbox")
        origin = f"http://{self.host}"
        self.manage("caps", {"id": aid, "caps": ["stacks"]})
        c.read_signed(key, session)
        git = {"repo": f"http://127.0.0.1:{repo.server_port}/o/r", "branch": "main", "path": "web/compose.yaml", "token": "tok-secret-1234"}
        answer = {}
        t = threading.Thread(target=lambda: answer.update(r=self.post_json("/api/setup/stack-run", {"agent": "gitbox", "verb": "deploy", "name": "web", "git": git}, origin)))
        t.start()
        cmd = c.read_signed(key, session)
        while cmd.get("type") != "command":
            cmd = c.read_signed(key, session)
        c.send_signed(key, session, aid, {"type": "result", "cmd": cmd["cmd"], "ok": True, "output": "up"})
        t.join(5)
        self.assertEqual(seen, {"path": "/o/r/raw/branch/main/web/compose.yaml", "auth": "Bearer tok-secret-1234"})
        opened = json.loads(P.unseal(key, session, cmd["cmd"], cmd["files"]["nonce"], cmd["files"]["box"], P.MAX_SEALED_FILES))
        self.assertEqual(opened, {"compose": "services:\n  web:\n    image: nginx\n"})
        self.assertNotIn("tok-secret", json.dumps(cmd) + str(answer["r"][1]))
        # a refused address is a 400 before anything goes to the agent
        bad = self.post_json("/api/setup/stack-run", {"agent": "gitbox", "verb": "deploy", "name": "web", "git": dict(git, path="../x")}, origin)
        self.assertEqual(bad[0], 400)
        self.assertNotIn("tok-secret", str(bad[1]))
        c.close()

    def test_history(self):
        c, session, aid, key = self.pair(name="histbox")
        for ev in ({"text": "web updated (1.2 → 1.3)", "bad": False, "cap": "docker", "target": "web",
                    "kind": "update", "from": "1.2", "to": "1.3"},
                   {"text": "web stopped by itself (exit code 1)", "bad": True, "cap": "docker", "target": "web", "kind": "crash"},
                   {"text": "no kind", "bad": False},
                   {"text": "bad target", "bad": True, "cap": "docker", "target": "../x", "kind": "crash"}):
            c.send_signed(key, session, aid, dict(ev, type="event"))
        hist = lambda: json.loads(self.get("/api/snapshot")[2]).get("agents", {}).get("histbox", {}).get("history", {})  # noqa: E731
        self.assertTrue(self.wait_for(lambda: len(hist().get("docker:web", [])) == 2))
        rows = hist()
        self.assertEqual(list(rows), ["docker:web"])
        self.assertEqual([(r["kind"], r["ok"], r.get("from"), r.get("to")) for r in rows["docker:web"]],
                         [("update", True, "1.2", "1.3"), ("crash", False, None, None)])
        c.close()


class PruneRules(unittest.TestCase):
    def setUp(self):
        self.real = agents.store
        agents.store = FakeStore()
        agents.store.save(agents.AGENTS_KEY, {"a1b2c3d4": {"name": "n", "key": "k", "caps": ["docker"],
                                                          "targets": {"docker": ["jellyfin", "images"]}}})
        agents.history.clear()

    def tearDown(self):
        agents.store = self.real

    def test_host_verbs_belong_to_images(self):
        for verb in agents.HOST_VERBS:
            self.assertTrue(agents.allowed("a1b2c3d4", "docker", verb, "images"), verb)
            self.assertFalse(agents.allowed("a1b2c3d4", "docker", verb, "jellyfin"), verb)
        self.assertEqual(set(agents.HOST_VERBS), set(agents.CLEAN.values()) | {"inventory"})

    def test_images_takes_prune_only(self):
        self.assertTrue(agents.allowed("a1b2c3d4", "docker", "prune", "images"))
        self.assertFalse(agents.allowed("a1b2c3d4", "docker", "prune", "jellyfin"))
        self.assertFalse(agents.allowed("a1b2c3d4", "docker", "start", "images"))
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-images-stop"))
        self.assertEqual(sorted(agents.buttons("a1b2c3d4", agents.get("a1b2c3d4"))["docker"]), ["jellyfin"])

    def test_history_kept_and_dropped_with_the_agent(self):
        for i in range(agents.HISTORY_PER_TARGET + 3):
            agents._remember("a1b2c3d4", {"cap": "docker", "target": "web", "kind": "update", "text": str(i)}, 1000 + i)
        rows = agents.history["a1b2c3d4"]["docker:web"]
        self.assertEqual(len(rows), agents.HISTORY_PER_TARGET)
        self.assertEqual(rows[-1]["text"], str(agents.HISTORY_PER_TARGET + 2))
        agents._remember("a1b2c3d4", {"cap": "docker", "target": "web", "kind": "exec"}, 1)   # not a kind
        self.assertEqual(len(rows), agents.HISTORY_PER_TARGET)
        agents.remove("a1b2c3d4")
        self.assertNotIn("a1b2c3d4", agents.history)


class Schedule(unittest.TestCase):
    """Containers labhud updates on its own, in the window (agents.due, run_auto)."""

    def setUp(self):
        self.real = agents.store
        agents.store = FakeStore()
        agents.store.save(agents.AGENTS_KEY, {"a1b2c3d4": {"name": "n", "key": "k", "caps": ["docker"],
                                                          "targets": {"docker": ["web", "db", "images"]}}})
        agents._state.clear()

    def tearDown(self):
        agents.store = self.real
        agents._live.pop("a1b2c3d4", None)

    def test_auto_only_ticked_and_sent_with_allow(self):
        self.assertTrue(agents.set_auto("a1b2c3d4", ["web", "gone"]))
        self.assertFalse(agents.set_auto("a1b2c3d4", ["images"]))
        self.assertEqual(agents.allowance("a1b2c3d4")["auto"], {"docker": ["web"]})   # "gone" is not ticked

    def test_window(self):
        at = lambda y, mo, d, h, mi: time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))   # noqa: E731
        self.assertTrue(agents.set_window([6], "04:00"))   # Sundays
        self.assertFalse(agents.set_window([7], "04:00"))
        self.assertFalse(agents.set_window([1], "4am"))
        sunday = at(2026, 10, 18, 4, 0)
        self.assertEqual(agents.due(sunday), "2026-10-18")
        self.assertIsNone(agents.due(sunday, last="2026-10-18"))      # once a day
        self.assertEqual(agents.due(at(2026, 10, 18, 4, 40)), "2026-10-18")   # a restart within the hour
        self.assertIsNone(agents.due(at(2026, 10, 18, 5, 1)))        # later: the next window
        self.assertIsNone(agents.due(at(2026, 10, 18, 3, 59)))
        self.assertIsNone(agents.due(at(2026, 10, 19, 4, 0)))        # Monday

    def test_run_auto_only_with_an_update_waiting(self):
        agents.set_auto("a1b2c3d4", ["web", "db"])
        sent = []

        class Live:
            def command(self, cap, verb, target, wait):
                sent.append((cap, verb, target))
                return True, "", ""
        agents._live["a1b2c3d4"] = Live()
        agents._state["a1b2c3d4"] = {"containers": {"web": {"update": True}, "db": {"update": True, "updating": True}}}
        self.assertEqual(agents.run_auto(), [("n", "web", True, "")])
        self.assertEqual(sent, [("docker", "update", "web")])   # db is being updated already


class Hooks(unittest.TestCase):
    def setUp(self):
        self.real = agents.store
        agents.store = FakeStore()
        agents.store.save(agents.AGENTS_KEY, {"a1b2c3d4": {"name": "n", "key": "k", "caps": ["docker"],
                                                          "targets": {"docker": ["web", "images"]}}})
        agents._hook_last.clear()

    def tearDown(self):
        agents.store = self.real
        agents._live.pop("a1b2c3d4", None)

    def test_one_container_once_in_30s_and_only_its_hash_kept(self):
        self.assertIsNone(agents.new_hook("a1b2c3d4", "images"))
        self.assertIsNone(agents.new_hook("a1b2c3d4", "db"))   # not ticked
        token = agents.new_hook("a1b2c3d4", "web")
        self.assertNotIn(token, json.dumps(agents.store.load(agents.AGENTS_KEY)))
        sent = []

        class Live:
            def command(self, cap, verb, target, wait):
                sent.append((cap, verb, target))
                return True, "", ""
        agents._live["a1b2c3d4"] = Live()
        self.assertEqual(agents.hook(token, now=100)[0], True)
        self.assertEqual(agents.hook(token, now=110)[1], "too soon: once in 30 s")
        self.assertEqual(agents.hook(token, now=131)[0], True)
        self.assertEqual(sent, [("docker", "update", "web")] * 2)
        self.assertEqual(agents.hook("x" * 43)[1], "forbidden")
        newer = agents.new_hook("a1b2c3d4", "web")   # a new one stops the old one
        self.assertEqual(agents.hook(token, now=500)[1], "forbidden")
        self.assertTrue(agents.remove_hook("a1b2c3d4", "web"))
        self.assertEqual(agents.hook(newer, now=900)[1], "forbidden")


class Versions(unittest.TestCase):
    def test_newer(self):
        for v, own, want in [("0.1.1", "0.1.0", True), ("0.1.0", "0.1.0", False), ("0.1.0", "0.1.1", False),
                             ("0.1.0", "0.1.0-dev.a0e2b4d", True), ("0.1.0", "", True), ("0.1.0-rc1", "0.0.1", False),
                             ("1.0.0", "0.9.99", True)]:
            self.assertEqual(agents.newer(v, own), want, (v, own))

    def test_update_for(self):
        rel = agents.release()
        if not rel["@AGENT_SHA256_AMD64@"]:
            self.skipTest("no agent release pinned")
        v = rel["@AGENT_VERSION@"]
        self.assertEqual(agents.update_for({"install": "service", "version": "0.0.1", "host": {"arch": "amd64"}}), v)
        self.assertEqual(agents.update_for({"install": "container", "version": "0.0.1", "host": {"arch": "amd64"}}), "")
        self.assertEqual(agents.update_for({"install": "", "version": "0.0.1", "host": {"arch": "amd64"}}), "")   # too old to say
        self.assertEqual(agents.update_for({"install": "service", "version": v, "host": {"arch": "amd64"}}), "")
        self.assertEqual(agents.update_for({"install": "service", "version": "0.0.1", "host": {"arch": "riscv64"}}), "")


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
            for verb in agents.acts(cap):
                name = agents.action_name("a1b2c3d4", cap, target, verb)
                self.assertLessEqual(len(name), 64)
                self.assertRegex(name, r"^[a-z0-9][a-z0-9-]{0,63}$")
                self.assertEqual(agents.resolve(name), ("a1b2c3d4", cap, target, verb))

    def test_resolve_refuses(self):
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-plex-restart"))       # not ticked
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-jellyfin-exec"))      # no such verb
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-docker-jellyfin-logs"))      # a read, not a button
        self.assertIsNone(agents.resolve("agent-a1b2c3d4-pve-101-start"))             # cap not ticked
        self.assertIsNone(agents.resolve("agent-ffffffff-docker-jellyfin-restart"))   # no such agent
        self.assertIsNone(agents.resolve("vm-pve-101-start"))

    def test_card_actions(self):
        host = agents.card_actions({"id": "n", "agent": "n100"})
        self.assertEqual([n.rsplit("-", 1)[1] for n in host], ["reboot", "shutdown", "update"])
        one = agents.card_actions({"id": "j", "agent": "n100", "agent_target": "docker:jellyfin"})
        self.assertEqual(one, [f"agent-a1b2c3d4-docker-jellyfin-{v}" for v in ("start", "stop", "restart", "update")])
        self.assertEqual(agents.card_actions({"id": "p", "agent": "n100", "agent_target": "docker:plex"}), [])
        self.assertEqual(agents.card_actions({"id": "x", "agent": "nobody"}), [])

    def test_guest_card_gets_only_update(self):
        names = agents.card_actions({"id": "v", "agent": "n100", "check": ["proxmox", "pve2", 210]})
        self.assertEqual([n.rsplit("-", 1)[1] for n in names], ["update"])

    def test_snapshot_buttons(self):
        b = agents.snapshot()["n100"]["buttons"]
        self.assertEqual(sorted(b), ["docker", "systemd"])          # wol and host are not listed by pages
        self.assertEqual(b["docker"]["jellyfin"], [f"agent-a1b2c3d4-docker-jellyfin-{v}" for v in ("start", "stop", "restart", "update")])
        self.assertNotIn("plex", b["docker"])                         # only what is ticked
        for names in b["docker"].values():
            for n in names:
                self.assertIsNotNone(agents.resolve(n))

    def test_wings_containers_hidden(self):
        u = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
        a = agents.store.load(agents.AGENTS_KEY)
        a["a1b2c3d4"]["targets"]["docker"].append(u)
        agents.store.save(agents.AGENTS_KEY, a)
        agents._state["a1b2c3d4"] = {"containers": {"jellyfin": {}, u: {}}}
        # without the game servers a UUID is just a container's name
        self.assertIn(u, agents.snapshot()["n100"]["buttons"]["docker"])
        a["a1b2c3d4"]["found"] = ["docker", "ptero"]
        agents.store.save(agents.AGENTS_KEY, a)
        got = agents.listing()[0]
        self.assertEqual(got["choices"]["docker"], ["jellyfin", "images"])
        self.assertNotIn(u, got["targets"]["docker"])
        self.assertNotIn(u, agents.snapshot()["n100"]["buttons"]["docker"])

    def test_card_wake(self):
        wake = agents.action_name("a1b2c3d4", "wol", "aa:bb:cc:dd:ee:ff", "wake")
        # WAKE from one agent, the host's buttons from another (here the same, for the store)
        both = agents.card_actions({"id": "n", "agent": "n100", "wake": "n100:aa:bb:cc:dd:ee:ff"})
        self.assertEqual(both[0], wake)
        self.assertEqual([n.rsplit("-", 1)[1] for n in both[1:]], ["reboot", "shutdown", "update"])
        self.assertEqual(agents.card_actions({"id": "w", "wake": "n100:aa:bb:cc:dd:ee:ff"}), [wake])
        self.assertEqual(agents.card_actions({"id": "w", "wake": "n100:aa:bb:cc:dd:ee:00"}), [])   # not ticked
        self.assertEqual(agents.card_actions({"id": "w", "wake": "nobody:aa:bb:cc:dd:ee:ff"}), [])
        # the same MAC as the card's own target: one button, not two
        self.assertEqual(agents.card_actions({"id": "w", "agent": "n100", "agent_target": "wol:aa:bb:cc:dd:ee:ff",
                                              "wake": "n100:aa:bb:cc:dd:ee:ff"}), [wake])

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
        c = self.parse('wake = "pve2:aa:bb:cc:dd:ee:ff"\nping = "10.0.0.1"\n')
        self.assertEqual((c["wake"], c["check"][0]), ("pve2:aa:bb:cc:dd:ee:ff", "ping"))
        for bad in ('wake = "pve2"\n', 'wake = "pve2:AA:BB:CC:DD:EE:FF"\n', 'wake = "a.b:aa:bb:cc:dd:ee:ff"\n',
                    'agent_target = "docker:jellyfin"\n', 'agent = "a.b"\n',
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
        # on_event takes the event's kind too (notify.py's, for the kinds in EVENT_OF)
        agents.on_change, agents.on_event = (lambda: None), (lambda t, b, e=None: self.events.append((t, b)))
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

    def test_read_gives_the_output(self):
        self.allow()

        def answer():
            cmd = self.agent_read()
            self.assertEqual((cmd["cap"], cmd["verb"], cmd["target"]), ("docker", "logs", "jellyfin"))
            self.agent_send({"type": "result", "cmd": cmd["cmd"], "ok": True, "output": "x" * 10 + "y" * agents.MAX_OUTPUT})
        t = threading.Thread(target=answer)
        t.start()
        self.assertEqual(agents.read(self.aid, "docker", "logs", "jellyfin"), (True, "", "y" * agents.MAX_OUTPUT))
        t.join()
        self.assertEqual(agents.read(self.aid, "docker", "logs", "plex"), (False, "not allowed", ""))

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
