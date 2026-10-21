"""Registries (api/registries.py) and what an agent's docker update carries
(agents.update_extras / Session.ask): a registry's login only to an agent that understands it,
only for an image of that registry, sealed, never in an answer; hold_critical only for labhud's
own scheduled updates on an agent that scans; [[registry]] in config.py."""

import json
import os
import tempfile
import unittest
from unittest import mock

import agentproto as P
import agents
import config
import managed
from api import registries
from tests.test_display_marks import CFG, FakeHandler
from tests.test_lock import FakeStore

AID = "a1b2c3d4"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.mkdir(self.data)
        self.cfg = os.path.join(self.tmp, "config.toml")
        with open(self.cfg, "w") as f:
            f.write(CFG)
        env = {k: v for k, v in os.environ.items() if not k.startswith("LABHUD_")}
        env.update(LABHUD_DATA=self.data, LABHUD_CONFIG=self.cfg)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        real, agents.store = agents.store, FakeStore()
        self.addCleanup(setattr, agents, "store", real)
        saved = agents.registries
        self.addCleanup(setattr, agents, "registries", saved)
        self.addCleanup(agents._state.pop, AID, None)
        self.addCleanup(agents._live.pop, AID, None)
        self.h = FakeHandler()

    def apply(self):
        """What server._apply does with the kept config."""
        agents.registries = config.load(managed.config_file())["registries"]


class Config(unittest.TestCase):
    def load(self, extra):
        return config.loads(CFG + extra)

    def test_normalised(self):
        t = self.load('\n[[registry]]\nhost = "https://GHCR.io/"\nuser = "demo"\n\n[[registry]]\nhost = "index.docker.io"\nuser = "me"\n')
        self.assertEqual(t["registries"], [{"host": "ghcr.io", "user": "demo"}, {"host": "docker.io", "user": "me"}])
        self.assertEqual(config.loads(CFG)["registries"], [])

    def test_refused(self):
        for extra in ('\n[[registry]]\nhost = "bad host"\nuser = "x"\n',
                      '\n[[registry]]\nhost = "ghcr.io"\n',
                      '\n[[registry]]\nhost = "ghcr.io"\nuser = "x"\ntoken = "never here"\n',
                      '\n[[registry]]\nhost = "ghcr.io"\nuser = "x"\n\n[[registry]]\nhost = "GHCR.IO"\nuser = "y"\n'):
            with self.subTest(extra=extra), self.assertRaises(config.ConfigError):
                self.load(extra)


class Routes(Base):
    def test_all_behind_the_admin_guard(self):
        import routes
        for op in ("list", "save", "remove", "hold"):
            self.assertEqual(routes.find("POST", f"/api/registries/{op}")[1], "manage")
        self.assertFalse([k for k in routes.ROUTES if k[1].startswith("/api/registries/") and k[0] != "POST"])

    def test_save_needs_take_over(self):
        code, r = registries.save(self.h, {"host": "ghcr.io", "user": "demo", "token": "ghp_x"})
        self.assertEqual(code, 409)

    def test_save_list_remove_and_the_token_never_comes_back(self):
        managed.take_over()
        code, r = registries.save(self.h, {"host": "https://ghcr.io", "user": "demo", "token": "ghp_SECRET"})
        self.assertEqual((code, r), (200, {"ok": True, "host": "ghcr.io", "token": True}))
        self.assertEqual(managed.read_config()["registry"], [{"host": "ghcr.io", "user": "demo"}])
        with open(managed.config_file()) as f:
            self.assertNotIn("ghp_SECRET", f.read())
        self.assertEqual(managed.read_secrets()["LABHUD_REGISTRY_GHCR_IO_TOKEN"], "ghp_SECRET")
        self.apply()
        code, r = registries.listing(self.h, {})
        self.assertEqual(code, 200)
        self.assertNotIn("ghp_SECRET", json.dumps(r))
        self.assertEqual(r["registries"][0]["host"], "ghcr.io")
        self.assertIs(r["registries"][0]["token"], True)
        # a new user without a token keeps the token
        registries.save(self.h, {"host": "ghcr.io", "user": "other"})
        self.assertEqual(managed.read_secrets()["LABHUD_REGISTRY_GHCR_IO_TOKEN"], "ghp_SECRET")
        self.assertEqual(managed.read_config()["registry"], [{"host": "ghcr.io", "user": "other"}])
        code, r = registries.remove(self.h, {"host": "ghcr.io"})
        self.assertEqual(code, 200)
        self.assertNotIn("registry", managed.read_config())
        self.assertNotIn("LABHUD_REGISTRY_GHCR_IO_TOKEN", managed.read_secrets())

    def test_bad_input(self):
        managed.take_over()
        for body in ({"host": "a b", "user": "x"}, {"host": "ghcr.io", "user": ""}, {"host": "ghcr.io", "user": "a\nb"},
                     {"host": "ghcr.io", "user": "x", "token": "a\nb"}, {"host": "ghcr.io", "user": "x", "token": 5}, {}):
            with self.subTest(body=body):
                self.assertEqual(registries.save(self.h, body)[0], 400)
        self.assertNotIn("registry", managed.read_config())

    def test_hold(self):
        self.assertEqual(registries.hold(self.h, {"on": "yes"})[0], 400)
        self.assertEqual(registries.hold(self.h, {"on": True}), (200, {"ok": True, "hold_critical": True}))
        self.assertTrue(agents.hold_critical())
        self.assertIs(registries.listing(self.h, {})[1]["hold_critical"], True)

    def test_scans_worst_first(self):
        rows = registries.scans({"n": {"containers": {
            "a": {"image": "x", "scan": {"critical": 0, "high": 4, "tool": "trivy", "t": 5}},
            "b": {"image": "y", "scan": {"critical": 2, "high": 1, "tool": "grype", "t": 6}},
            "c": {"image": "z"}, "d": {"scan": {"critical": "2"}}}}})
        self.assertEqual([(r["name"], r["critical"], r["high"]) for r in rows], [("b", 2, 1), ("a", 0, 4)])


class Logins(Base):
    def setUp(self):
        super().setUp()
        agents.registries = [{"host": "ghcr.io", "user": "demo"}, {"host": "docker.io", "user": "hub"}]
        managed.set_secrets({"LABHUD_REGISTRY_GHCR_IO_TOKEN": "ghp_SECRET"})

    def test_matching(self):
        self.assertEqual(registries.login_for("ghcr.io/demo/app:1"), ("ghcr.io", "demo", "ghp_SECRET"))
        self.assertIsNone(registries.login_for("lscr.io/linuxserver/sonarr"))   # not a registry of ours
        self.assertIsNone(registries.login_for("nginx"))                         # Docker Hub has no token
        self.assertIsNone(registries.login_for("sha256:" + "a" * 64))
        self.assertIsNone(registries.login_for(""))
        managed.set_secrets({"LABHUD_REGISTRY_DOCKER_IO_TOKEN": "dckr_x"})
        self.assertEqual(registries.login_for("linuxserver/sonarr"), ("docker.io", "hub", "dckr_x"))

    def agent(self, features=("registry-auth",), caps=("docker",), found=("docker",), image="ghcr.io/demo/app:1"):
        agents.store.save(agents.AGENTS_KEY, {AID: {"name": "n", "key": "k" * 64, "caps": list(caps), "found": list(found),
                                                    "features": list(features), "targets": {"docker": ["web"]}}})
        agents._state[AID] = {"containers": {"web": {"image": image, "update": True}}}

    def test_extras(self):
        self.agent()
        extra, seal = agents.update_extras(AID, "web")
        self.assertEqual(extra, {})
        self.assertEqual(seal[0], "ghcr.io")
        self.assertEqual(json.loads(seal[1]), {"username": "demo", "password": "ghp_SECRET"})

    def test_no_login_for_an_old_agent_or_another_registry(self):
        self.agent(features=())
        self.assertEqual(agents.update_extras(AID, "web"), ({}, None))
        self.agent(image="lscr.io/linuxserver/sonarr")
        self.assertEqual(agents.update_extras(AID, "web"), ({}, None))
        self.agent()
        self.assertEqual(agents.update_extras(AID, "other"), ({}, None))

    def test_hold_only_on_its_own_with_scan(self):
        agents.set_hold_critical(True)
        self.agent(image="nginx", caps=("docker", "scan"), found=("docker", "scan"))
        self.assertEqual(agents.update_extras(AID, "web", auto=True), ({"hold_critical": True}, None))
        self.assertEqual(agents.update_extras(AID, "web", auto=False), ({}, None))   # a button or a webhook
        self.agent(image="nginx", caps=("docker",), found=("docker", "scan"))       # scan not ticked
        self.assertEqual(agents.update_extras(AID, "web", auto=True), ({}, None))
        agents.set_hold_critical(False)
        self.agent(image="nginx", caps=("docker", "scan"), found=("docker", "scan"))
        self.assertEqual(agents.update_extras(AID, "web", auto=True), ({}, None))

    def test_the_frame_has_it_sealed_and_the_agent_opens_it(self):
        self.agent()
        sent = []

        class WS:
            def send(self, text):
                sent.append(text)

        s = agents.Session(WS(), "test")
        s.aid, s.key = AID, "k" * 64
        agents._live[AID] = s
        ok, error = agents.command(AID, "docker", "update", "web", wait=0.01)
        self.assertEqual(len(sent), 1)
        self.assertNotIn("ghp_SECRET", sent[0])
        frame = json.loads(sent[0])
        msg = json.loads(frame["body"])
        self.assertEqual((msg["type"], msg["verb"], msg["target"]), ("command", "update", "web"))
        auth = msg["auth"]
        self.assertEqual(auth["registry"], "ghcr.io")
        self.assertEqual(json.loads(P.unseal(s.key, s.session, msg["cmd"], auth["nonce"], auth["box"])),
                         {"username": "demo", "password": "ghp_SECRET"})

    def test_held_event(self):
        self.agent()
        heard = []
        saved = agents.on_held, agents.on_event
        agents.on_held = lambda name, target, text: heard.append((name, target, text))
        agents.on_event = lambda text, bad, event=None: None
        self.addCleanup(lambda: (setattr(agents, "on_held", saved[0]), setattr(agents, "on_event", saved[1]),
                                 agents.history.pop(AID, None)))
        s = agents.Session(None, "test")
        s.aid = AID
        s._handle({"type": "event", "text": "web: update held: 3 critical CVEs in the new image (1 now)", "bad": True,
                   "cap": "docker", "target": "web", "kind": "held"})
        self.assertEqual(heard, [("n", "web", "web: update held: 3 critical CVEs in the new image (1 now)")])
        self.assertEqual(agents.history[AID]["docker:web"][-1]["kind"], "held")


if __name__ == "__main__":
    unittest.main()
