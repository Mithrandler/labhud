"""The agent protocol's cryptography: pairing and signed frames. Pure functions, no I/O, so the
same test vectors (docs/agent-protocol-vectors.json) check this file and the Go agent
(labhud-agent, internal/proto). docs/agent-protocol.md describes the whole exchange.

Every MAC is HMAC-SHA256, written as lowercase hex. Every message a MAC covers starts with
"labhud-agent/v1|" and names what it is for, so one value can never stand in for another.
"""

import hashlib
import hmac
import json
import re

V = "labhud-agent/v1"
MAX_SKEW = 30                  # seconds a frame's time may differ from ours
HEX16 = re.compile(r"[0-9a-f]{32}")
HEX32 = re.compile(r"[0-9a-f]{64}")
AGENT_ID = re.compile(r"[0-9a-f]{8}")
TO_LABHUD, TO_AGENT = "a2l", "l2a"


def _mac(key, msg):
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()


def code_id(code):
    """What the agent sends instead of the code: finds the code in labhud, reveals nothing of it."""
    return hashlib.sha256(f"{V}|code-id|{code}".encode()).hexdigest()


def pair_secret(code):
    """What labhud keeps for a live code (never the code itself) and both sides derive from."""
    return _mac(code, f"{V}|pair-secret")


def server_proof(secret, session, agent_id, agent_nonce, server_nonce, tls_fp=""):
    """labhud's answer to a pairing: proves it knows the code, and vouches for its certificate."""
    return _mac(secret, f"{V}|server-proof|{session}|{agent_id}|{agent_nonce}|{server_nonce}|{tls_fp}")


def agent_key(secret, agent_id, agent_nonce, server_nonce):
    """The agent's own key, the same on both sides, never sent: 64 hex characters."""
    return _mac(secret, f"{V}|key|{agent_id}|{agent_nonce}|{server_nonce}")


def frame_sig(key, direction, session, agent_id, seq, t, body):
    return _mac(key, f"{V}|frame|{direction}|{session}|{agent_id}|{seq}|{t}|{body}")


def make_frame(key, direction, session, agent_id, seq, t, message):
    """A signed frame as text. `message` is a dict with a "type"; its JSON is signed as it is sent."""
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({"id": agent_id, "seq": seq, "t": t, "body": body,
                       "sig": frame_sig(key, direction, session, agent_id, seq, t, body)},
                      ensure_ascii=False, separators=(",", ":"))


def read_frame(text, key_for, direction, session, last_seq, now):
    """(agent id, seq, message) of a valid frame. Raises ValueError with the reason otherwise.

    key_for(agent_id) gives the key or None; last_seq is the highest seq seen on this connection
    in this direction (0 at the start); the new one must be higher."""
    try:
        f = json.loads(text)
    except ValueError:
        raise ValueError("not JSON") from None
    if not isinstance(f, dict) or set(f) != {"id", "seq", "t", "body", "sig"}:
        raise ValueError("not a frame")
    aid, seq, t, body, sig = f["id"], f["seq"], f["t"], f["body"], f["sig"]
    if not (isinstance(aid, str) and AGENT_ID.fullmatch(aid) and type(seq) is int and type(t) is int
            and isinstance(body, str) and isinstance(sig, str) and HEX32.fullmatch(sig)):
        raise ValueError("not a frame")
    key = key_for(aid)
    if not key or not hmac.compare_digest(sig, frame_sig(key, direction, session, aid, seq, t, body)):
        raise ValueError("bad signature")
    if seq <= last_seq:
        raise ValueError("replayed frame")
    if abs(t - now) > MAX_SKEW:
        raise ValueError(f"clock off by {t - now:+d} s")
    try:
        message = json.loads(body)
    except ValueError:
        raise ValueError("body not JSON") from None
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("body without a type")
    return aid, seq, message


# --- sealed values: a registry's login in a command (labhud → agent) ---
#
# A frame is signed, not encrypted, and labhud may speak plain ws://. So a secret inside a command
# (a registry's user and token, for one pull) travels sealed: XORed with a keystream that only the
# two holders of the agent's key can make, bound to this connection, this command and a fresh
# nonce. The frame's signature already covers the sealed text, so it cannot be changed unseen.
#
#     k        = HMAC(key, "V|seal|<session>|<cmd>|<nonce>")
#     stream_i = HMAC(k, "V|seal-stream|<i>")        (raw 32 bytes, i = 0, 1, 2 …)
#     box      = hex(text XOR stream_0 || stream_1 || …)

MAX_SEALED = 4096   # bytes of text
MAX_SEALED_FILES = 64 << 10   # a stack deploy's files (the agent's proto.MaxSealedFiles)


def _keystream(key, session, cmd, nonce, n):
    k = _mac(key, f"{V}|seal|{session}|{cmd}|{nonce}").encode()
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hmac.new(k, f"{V}|seal-stream|{i}".encode(), hashlib.sha256).digest()
        i += 1
    return bytes(out[:n])


def seal(key, session, cmd, nonce, text, limit=MAX_SEALED):
    """`text` (str) sealed for one command of one connection: lowercase hex, twice its UTF-8 length."""
    raw = text.encode()
    if len(raw) > limit:
        raise ValueError("too long to seal")
    if not HEX16.fullmatch(nonce):
        raise ValueError("the nonce is 16 bytes of hex")
    return bytes(a ^ b for a, b in zip(raw, _keystream(key, session, cmd, nonce, len(raw)))).hex()


def unseal(key, session, cmd, nonce, box, limit=MAX_SEALED):
    """seal()'s text back (the agent's side; labhud only seals)."""
    if not (isinstance(box, str) and re.fullmatch(r"(?:[0-9a-f]{2}){0,%d}" % limit, box)) or not HEX16.fullmatch(nonce):
        raise ValueError("not a sealed value")
    raw = bytes.fromhex(box)
    return bytes(a ^ b for a, b in zip(raw, _keystream(key, session, cmd, nonce, len(raw)))).decode()


# --- which registry an image comes from (both sides must agree: a login goes only there) ---

DOCKER_HUB = "docker.io"
_HUB_NAMES = ("docker.io", "index.docker.io", "registry-1.docker.io")


def registry_of(ref):
    """The registry an image reference is pulled from, as Docker reads it: the part before the
    first "/" when it has a "." or a ":" or is "localhost", else Docker Hub ("docker.io"). Lower
    case; "" for an image id or anything that is not a name."""
    if not isinstance(ref, str):
        return ""
    ref = ref.strip()
    if not ref or ref.startswith("sha256:") or re.fullmatch(r"[0-9a-f]{64}", ref) or any(c.isspace() for c in ref):
        return ""
    first, slash, _ = ref.partition("/")
    host = first.lower() if slash and ("." in first or ":" in first or first == "localhost") else DOCKER_HUB
    return DOCKER_HUB if host in _HUB_NAMES else host
