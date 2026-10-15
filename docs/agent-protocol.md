# The agent protocol (v1)

How a [labhud agent](agents.md) and labhud talk. Two implementations follow it: `agentproto.py`
and `agents.py` in this repository, `internal/proto` in
[labhud-agent](https://github.com/Mithrandler/labhud-agent). Both are checked against the same
vectors, [agent-protocol-vectors.json](agent-protocol-vectors.json), so they cannot drift apart.

## In one picture

```
agent                                                    labhud
  │── GET /api/agent/ws  (WebSocket, ws:// or wss://) ────▶│
  │◀──────────────────────── welcome {session} (unsigned) │
  │                                                        │
  │  first time only:                                      │
  │── pair {code_id, agent_nonce, name} (unsigned) ───────▶│  code used up here
  │◀── challenge {agent_id, server_nonce, tls_fp, proof} ──│  labhud proves it knows the code
  │   both sides now hold the agent's key; it never travels │
  │                                                        │
  │── hello {version, host, caps} (signed) ───────────────▶│  the agent is saved only now
  │◀────────────────────── allow {caps, targets} (signed) ─│
  │── state {...}  every 30 s and on change ──────────────▶│
  │◀──────────────── command {cmd, cap, verb, target} ─────│
  │── result {cmd, ok, error} ────────────────────────────▶│
```

The agent always dials; nothing listens on the host. labhud sends a WebSocket ping every 25 s;
an agent that hears nothing for 60 s reconnects, waiting 1 s, 2 s, 4 s … up to 60 s between tries.

## Notation

- `HMAC(k, m)` is HMAC-SHA256, written as **lowercase hex**. A key that is itself such a hex
  string is used **as its ASCII text**, not decoded: `HMAC("0edc…", m)` keys with the 64 bytes
  `0`, `e`, `d`, `c` … This is what both implementations do; the vectors pin it.
- `V` is the text `labhud-agent/v1`. Every MAC'd message starts with `V|<purpose>|`, so a value
  computed for one purpose can never be replayed as another.
- Nonces and sessions are 16 random bytes in hex (32 characters); an agent id is 4 random bytes
  in hex (8 characters), chosen by labhud.

## Pairing

The admin makes a code in ⚙ > Agents: 18 random bytes, URL-safe base64 (24 characters, 144
bits), valid **5 minutes, once**. It reaches the agent in the install line, never through the
WebSocket. labhud keeps only

```
code_id     = SHA256("V|code-id|" + code)            (hex)   to find the code
pair_secret = HMAC(code, "V|pair-secret")                     to derive from
```

so its database never holds a code. The exchange:

1. labhud → `{"type":"welcome","v":1,"session":S}`.
2. agent → `{"type":"pair","code_id":…,"agent_nonce":A,"name":<hostname>}`. labhud looks the code
   up and **uses it up at once**, whatever happens next: a second try with the same code fails.
3. labhud picks the agent id `I` and `server_nonce` N, and answers
   `{"type":"challenge","agent_id":I,"server_nonce":N,"tls_fp":F,"proof":P}` with
   `P = HMAC(pair_secret, "V|server-proof|S|I|A|N|F")`. `F` is the SHA-256 of labhud's
   certificate (hex) when it serves `wss://`, else empty.
4. The agent checks `P`. A wrong proof means it is not talking to the labhud that made the code:
   it stops and says so. Over `wss://` it also checks that `F` is the certificate it actually
   sees, and from then on **pins** it (the first connection is made without verification; the
   proof is what vouches for the certificate, so a man in the middle cannot slip in his own).
5. Both sides compute the key:

   ```
   key = HMAC(pair_secret, "V|key|I|A|N")
   ```

   The agent stores `I`, `key` and `F` in `/etc/labhud-agent/key` (mode 600) and sends its first
   signed frame, `hello`. labhud saves the agent only when that frame checks out.

Someone who records the whole exchange gets `code_id`, the nonces and the proof: none of them
leads back to the code (144 bits) or the key. Someone who knows the code within its 5 minutes
can pair a fake agent — exactly like a phone's pairing code — and gets an agent that is allowed
nothing until the admin ticks it.

## Signed frames

After pairing, and at every later connection, every message in either direction is a frame:

```json
{"id":"a1b2c3d4","seq":1,"t":1760000000,"body":"{\"type\":\"hello\",…}","sig":"b5cc…"}
sig = HMAC(key, "V|frame|<dir>|S|<id>|<seq>|<t>|<body>")
```

- `body` is the message's JSON **as a string**: it is signed exactly as sent, so neither side
  needs a canonical JSON.
- `dir` is `a2l` (agent to labhud) or `l2a`: a frame cannot be reflected back to its sender.
- `S` is the session from this connection's `welcome`: a frame from another connection, even
  a minute old, does not verify.
- `seq` starts at 1 in each direction of each connection and must grow; a repeated or older
  `seq` is refused.
- `t` is Unix time in seconds; more than **30 s** from the receiver's clock is refused (the
  error names the offset: an agent with a wrong clock says so in its log).
- The frame has exactly these five keys; anything else is refused.

labhud closes a connection whose first frame does not arrive within 5 s or does not verify,
and any frame over 1 MiB. Before closing for a refusal it sends one unsigned
`{"type":"error","error":"<why>"}`, which the agent only logs (it proves nothing). One agent id
has one connection: a new one replaces the old.

The endpoint does not go through `LABHUD_HOSTS`, like `/api/push`: the signature is the check, so
an agent may know labhud by any address. It refuses any request with an `Origin` header instead:
a browser always sends one with a WebSocket and an agent never does, so no web page can reach it.

## Messages

### agent → labhud

`hello` — first frame of every connection.

```json
{"type":"hello","version":"0.1.0","name":"pve2",
 "host":{"hostname":"pve2","os":"Debian GNU/Linux 13","kernel":"6.12.43","arch":"amd64"},
 "caps":["sensors","docker","systemd","host","wol","pve"]}
```

`caps` is what the agent **found** (a Docker socket, D-Bus, the Proxmox API, a Pterodactyl key),
not what it may use.

`state` — after `hello`, on change and at least every 30 s. Every part is optional; a missing
part means "this capability is not here", an empty one "found, nothing in it".

```json
{"type":"state",
 "sensors":{"cpu":12.5,"mem":41.0,"mem_used_of":[6550000000,15900000000],"disk":63.2,
            "disk_used_of":[40000000000,63000000000],"load1":0.42,"uptime":277200,"cpu_temp":48.0,
            "gpu_count":2,"gpu0_util":3.0,"gpu0_mem":10.0,"gpu0_temp":41.0,"gpu0_power":9.5,
            "gpu0_mem_used_mib":1024.0,"gpu0_mem_total_mib":8192.0,
            "power_cpu":17.4,"power_gpu":19.0,"power_base":40,"power":76,"kwh_day":1.82,"cost_day":2.37},
 "guests":{"101":{"name":"pbs","type":"qemu","running":true,"cpu":1.2,"mem":34.0,
                  "mem_bytes":…,"mem_max":…,"uptime":86400,"tags":["infra"]}},
 "containers":{"jellyfin":{"state":"running","health":"healthy","image":"jellyfin/jellyfin"}},
 "units":{"nginx.service":{"active":"active","sub":"running"}},
 "servers":{"1a2b3c4d":{"name":"Valheim","state":"running"}},
 "neighbours":{"aa:bb:cc:dd:ee:ff":{"ip":"192.0.2.50"}}}
```

`sensors` uses the flat keys of the Python agent (`agents/labhud-agent.py`) and of
host-temp-api: the power estimate is the CPU package from RAPL + the GPUs + a fixed base the
agent is given (`LABHUD_AGENT_BASE_W`), and `cost_day` only with `LABHUD_AGENT_PRICE_KWH`. `guests` has the shape of the Proxmox source (`sources/proxmox.py`), so cards treat both
the same. `units` lists the loaded `.service` units, at most 200.
`neighbours` is the ARP table, to choose Wake-on-LAN targets from.

`result` — the end of a command: `{"type":"result","cmd":"<id>","ok":true}` or
`{"type":"result","cmd":"<id>","ok":false,"error":"<one line>"}`. A command that only starts
something long (a package update) answers `ok` at once and reports the end as an `event`.

`event` — `{"type":"event","text":"pve2: packages updated (12)","bad":false}`, for the timeline.

### labhud → agent

`allow` — after `hello` and whenever ⚙ changes. It replaces the previous one entirely.

```json
{"type":"allow","caps":["docker","pve"],
 "targets":{"docker":["jellyfin"],"pve":["101","113"]}}
```

`command` — `{"type":"command","cmd":"<16 hex>","cap":"docker","verb":"restart","target":"jellyfin"}`.

`bye` — `{"type":"bye","reason":"removed"}`: the agent was removed in ⚙. It deletes its key and
stops (`systemctl` will not restart it: it exits with status 0 and the unit has
`Restart=on-failure`). Any reason other than `removed` is only logged.

## Capabilities and verbs

| cap | targets | verbs | done through |
|---|---|---|---|
| `sensors` | — | — (state only) | `/proc`, hwmon, `nvidia-smi`, RAPL, `statvfs` |
| `docker` | container names | `start` `stop` `restart` | `/var/run/docker.sock` |
| `systemd` | unit names | `start` `stop` `restart` | D-Bus `org.freedesktop.systemd1` |
| `host` | `host` | `reboot` `shutdown` `update` | D-Bus `login1`; `update` = a transient unit running apt-get/dnf |
| `wol` | MAC addresses (`aa:bb:…`, lowercase) | `wake` | UDP broadcast to port 9 |
| `pve` | VMIDs | `start` `shutdown` `reboot` | `https://localhost:8006`, a local token |
| `ptero` | server identifiers | `start` `stop` `restart` | the panel's client API, a local key |

There is no `stop` for Proxmox guests and no `kill` for game servers on purpose: only the
graceful ones. There is no verb that runs a command.

## The two checks

A command is carried out only when **both** sides agree:

- **labhud** sends it only if the agent is connected, the capability is ticked, the target is
  ticked, and the verb is in the table above;
- **the agent** checks the same against the last `allow` it received, before touching any API.
  A command outside it gets `{"ok":false,"error":"not allowed"}` and nothing else.

So labhud cannot be made to send something the admin did not tick, and an agent cannot be
made to do it even if labhud were changed to send it. What a compromised labhud *can* do is
use the ticked targets — that is the price of the buttons.

## Versions

`hello.version` is shown in ⚙; labhud never sends code or updates to an agent. A future v2
changes `V`, so a v1 frame can never pass as v2.
