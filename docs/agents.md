# labhud agents (plan)

> Not built yet. This is the design agreed for the next version; the action agent that exists
> today is described in [actions.md](actions.md).

## What it is

One small agent on each host you want to control: a Proxmox node, a Docker host, a plain Linux
box, the machine running a Pterodactyl panel. It does its work through the local APIs the host
already has, never through scripts you have to write:

| on the host | the agent uses | labhud can |
|---|---|---|
| Proxmox VE | the local API (`https://localhost:8006`, a token it makes at pairing) | start, shut down, reboot VMs and LXCs; see their state |
| Docker | the socket (`/var/run/docker.sock`) | start, stop, restart containers; see their state and health |
| any Linux with systemd | D-Bus (`org.freedesktop.systemd1`, `login1`) | reboot or shut down the host, restart units, update packages |
| the same LAN | a raw socket | wake other machines (Wake-on-LAN), from the agent nearest to them |
| Pterodactyl | the panel's client API (a key given at pairing) | start, stop, restart game servers; see players and state |

Each of these is a **capability** the agent finds by itself at start and reports. labhud shows
it, and it stays **off until you tick it** in ⚙ > Agents, one by one.

## Shape

- **Go**, one static binary for linux/amd64 and linux/arm64, in its own repository
  (`labhud-agent`), with its own CI, releases and versions. The protocol lives in this repo.
- Shipped two ways: a **Docker image** (with the socket and, for systemd, `/run/dbus` mounted)
  and a **systemd service** installed by a one-line script from labhud (`/agent/install.sh`, as
  today).
- Runs as **root**, like the Beszel and Dockhand agents: it reboots hosts, updates packages and
  talks to Docker. The limits are therefore in what it accepts (below), not in its rights.

## Connection

The agent dials labhud; nothing listens on the host.

```
agent ──wss://labhud:8095/api/agent/ws──▶ labhud
        hello {id, version, capabilities, host facts}
        ◀── command {id, action, target, ts, nonce, sig}
        ──▶ result {id, ok, error?}         state {guests, containers, units, servers}
```

- **WebSocket** on labhud's own port, over TLS with the certificate pinned at pairing (the
  agent refuses any other). labhud gets a small WebSocket endpoint of its own (standard library
  only, like the rest).
- The agent reconnects by itself with back-off; while it is away its cards show "agent offline",
  not "down".
- State is pushed on change and every 30 s; it feeds cards the way sources do today, so the
  wall, the app and the uptime work unchanged.

## Pairing

Like a phone, and like Beszel or Dockhand:

1. ⚙ > Agents > **Add an agent** (admin only) shows a one-time code and the install line:
   `curl -fsSL http://labhud:8095/agent/install.sh | sh -s -- <code>` or the `docker run` with
   `LABHUD_AGENT_CODE=<code>`.
2. The agent sends the code once; labhud answers with a **secret for that agent alone** and
   labhud's certificate fingerprint. The code is then dead (5 minutes, single use).
3. The agent shows up in ⚙ > Agents with its host name and what it found. Nothing is allowed
   yet.
4. **Remove** ends its secret at once; the agent stops and says so in its log.

## What an agent accepts

- Only commands signed by labhud with **its own** secret (HMAC-SHA256 of id, time, nonce,
  action and target), less than 30 seconds old, never twice.
- Only actions of a **ticked capability**, and only on **targets labhud lists for it**: the VMs,
  containers, units or game servers you chose in ⚙, kept by labhud and sent to the agent as an
  allowlist it checks too. A compromised labhud can therefore act only on what you allowed.
- Never a shell: each action maps to one API call written in the agent.
- Package updates: `apt-get update && apt-get -y upgrade` (or `dnf`), started as a transient
  systemd unit so it survives the agent restarting; the result comes back as an event.

## In labhud

- `agents.py`: pairing, secrets (in `LABHUD_DATA`), the WebSocket endpoint, the allowlists.
- ⚙ > Agents: list, add, remove, the capabilities and targets per agent.
- Cards: `agent = "<name>"` on a card (or `guests` / `containers` that follow an agent, like
  `guests` follows Proxmox today) gives it the agent's state and buttons.
- `/api/v1`: the app gets the agents' buttons through the actions it already knows; nothing new
  to learn for it.

## Moving over

The current pieces keep working until an agent covers them: `agents/labhud-agent.py` (sensors
and TOML actions) and any agent behind `LABHUD_ACTION_URL`. A host moves when its new agent
shows the same buttons; then its old agent is switched off.

## Still to decide

- Whether labhud may push an **update of the agent itself** (convenient; also the most powerful
  thing a compromised labhud could do).
- Sensors (temperatures, GPUs, power): move into the Go agent at once, or keep the Python agent
  for them for a while.
- Pterodactyl: one agent on the panel host for all servers (the client API), or one per node
  through Wings.
