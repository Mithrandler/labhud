"""The agent protocol's cryptography (agentproto.py) against the vectors shared with the Go agent
(docs/agent-protocol-vectors.json), and the reasons a frame is refused."""

import hashlib
import json
import os
import unittest

import agentproto as p
from tests.test_smoke import ROOT

with open(os.path.join(ROOT, "docs", "agent-protocol-vectors.json"), encoding="utf-8") as f:
    V = json.load(f)


# The agent keeps its own copy (internal/proto/testdata/vectors.json). Changing the vectors here means
# changing it there, and this sum with them: the two sides cannot drift apart unseen.
VECTORS_SHA256 = "02cc650cb18cc4bd2e68ed7187ac2eb0ffc61314eb77afb31bda672cd74baf91"
AGENT_COPY = os.path.join(os.environ.get("LABHUD_AGENT_DIR", os.path.join(ROOT, "..", "labhud-agent")),
                          "internal", "proto", "testdata", "vectors.json")


class SharedWithTheAgent(unittest.TestCase):
    def test_pinned_sum(self):
        with open(os.path.join(ROOT, "docs", "agent-protocol-vectors.json"), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), VECTORS_SHA256)

    @unittest.skipUnless(os.path.exists(AGENT_COPY), "no checkout of labhud-agent (set LABHUD_AGENT_DIR)")
    def test_agents_copy_is_the_same_file(self):
        with open(AGENT_COPY, "rb") as a, open(os.path.join(ROOT, "docs", "agent-protocol-vectors.json"), "rb") as b:
            self.assertEqual(a.read(), b.read())


class Vectors(unittest.TestCase):
    def test_pairing(self):
        self.assertEqual(p.code_id(V["code"]), V["code_id"])
        secret = p.pair_secret(V["code"])
        self.assertEqual(secret, V["pair_secret"])
        args = (V["session"], V["agent_id"], V["agent_nonce"], V["server_nonce"])
        self.assertEqual(p.server_proof(secret, *args, V["tls_fp"]), V["server_proof"])
        self.assertEqual(p.server_proof(secret, *args), V["server_proof_no_tls"])
        self.assertEqual(p.agent_key(secret, *args[1:]), V["key"])

    def test_frames(self):
        last = {p.TO_LABHUD: 0, p.TO_AGENT: 0}
        for fr in V["frames"]:
            made = p.make_frame(V["key"], fr["direction"], V["session"], V["agent_id"],
                                fr["seq"], fr["t"], fr["message"])
            self.assertEqual(made, fr["frame"])
            aid, seq, msg = p.read_frame(fr["frame"], {V["agent_id"]: V["key"]}.get, fr["direction"],
                                         V["session"], last[fr["direction"]], fr["t"] + 5)
            last[fr["direction"]] = seq
            self.assertEqual((aid, msg), (V["agent_id"], fr["message"]))


class Sealed(unittest.TestCase):
    """A registry's login inside a command: the same bytes as the Go agent's proto.Seal/Unseal."""

    def args(self):
        return V["key"], V["session"], V["seal"]["cmd"], V["seal"]["nonce"]

    def test_vectors(self):
        s = V["seal"]
        self.assertEqual(p.seal(*self.args(), s["text"]), s["box"])
        self.assertEqual(p.unseal(*self.args(), s["box"]), s["text"])
        self.assertEqual(p.seal(*self.args(), s["short"]["text"]), s["short"]["box"])
        self.assertEqual(len(s["box"]), 2 * len(s["text"].encode()))
        self.assertNotIn(b"ghp_", bytes.fromhex(s["box"]))

    def test_the_frame_carries_it_sealed(self):
        fr = next(f for f in V["frames"] if f["message"].get("auth"))
        self.assertNotIn("ghp_", fr["frame"])
        auth = fr["message"]["auth"]
        self.assertEqual(p.unseal(V["key"], V["session"], fr["message"]["cmd"], auth["nonce"], auth["box"]),
                         V["seal"]["text"])

    def test_bound(self):
        """Another connection, command, nonce or key opens it to noise, never to the text."""
        s = V["seal"]
        key, session, cmd, nonce = self.args()
        for other in ((key, "ff" * 16, cmd, nonce), (key, session, "f" * 16, nonce),
                      (key, session, cmd, "00" * 16), ("0" * 64, session, cmd, nonce)):
            try:
                self.assertNotEqual(p.unseal(*other, s["box"]), s["text"])
            except UnicodeDecodeError:
                pass

    def test_refused(self):
        with self.assertRaises(ValueError):
            p.seal(*self.args()[:3], "xyz", "text")
        with self.assertRaises(ValueError):
            p.seal(*self.args(), "x" * (p.MAX_SEALED + 1))
        for bad in ("abc", "ZZ", 7):
            with self.assertRaises(ValueError):
                p.unseal(*self.args(), bad)


class Registries(unittest.TestCase):
    def test_vectors(self):
        for ref, host in V["registries"]:
            self.assertEqual(p.registry_of(ref), host, ref)

    def test_not_text(self):
        self.assertEqual(p.registry_of(None), "")


class Refused(unittest.TestCase):
    def setUp(self):
        self.fr = V["frames"][0]
        self.keys = {V["agent_id"]: V["key"]}.get

    def read(self, text=None, direction=None, session=None, last=0, now=None, keys=None):
        return p.read_frame(text or self.fr["frame"], keys or self.keys, direction or self.fr["direction"],
                            session or V["session"], last, self.fr["t"] if now is None else now)

    def refused(self, reason, **kw):
        with self.assertRaises(ValueError) as e:
            self.read(**kw)
        self.assertIn(reason, str(e.exception))

    def test_good(self):
        self.assertEqual(self.read()[2]["type"], "hello")

    def test_other_session(self):        # a frame copied from another connection
        self.refused("bad signature", session="ff" * 16)

    def test_reflected(self):            # the agent's own frame sent back to it
        self.refused("bad signature", direction=p.TO_AGENT)

    def test_replay(self):
        self.refused("replayed", last=1)

    def test_clock(self):
        self.refused("clock off", now=self.fr["t"] + p.MAX_SKEW + 1)
        self.read(now=self.fr["t"] - p.MAX_SKEW)

    def test_unknown_agent(self):
        self.refused("bad signature", keys={}.get)

    def test_tampered(self):
        f = json.loads(self.fr["frame"])
        f["body"] = f["body"].replace("docker", "pve")
        self.refused("bad signature", text=json.dumps(f))
        f = json.loads(self.fr["frame"])
        f["seq"] = 7
        self.refused("bad signature", text=json.dumps(f))

    def test_shapes(self):
        self.refused("not JSON", text="{")
        self.refused("not a frame", text="[]")
        f = json.loads(self.fr["frame"])
        self.refused("not a frame", text=json.dumps(dict(f, extra=1)))
        self.refused("not a frame", text=json.dumps(dict(f, seq="1")))
        self.refused("not a frame", text=json.dumps(dict(f, seq=True)))
        self.refused("not a frame", text=json.dumps(dict(f, id="../etc")))

    def test_body_without_type(self):
        text = p.make_frame(V["key"], p.TO_LABHUD, V["session"], V["agent_id"], 1, 100, {"x": 1})
        with self.assertRaises(ValueError):
            p.read_frame(text, self.keys, p.TO_LABHUD, V["session"], 0, 100)


if __name__ == "__main__":
    unittest.main()
