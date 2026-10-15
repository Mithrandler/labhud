"""The agent protocol's cryptography (agentproto.py) against the vectors shared with the Go agent
(docs/agent-protocol-vectors.json), and the reasons a frame is refused."""

import json
import os
import unittest

import agentproto as p
from tests.test_smoke import ROOT

with open(os.path.join(ROOT, "docs", "agent-protocol-vectors.json"), encoding="utf-8") as f:
    V = json.load(f)


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
