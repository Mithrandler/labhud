# labhud agents

One small agent on each host you want to control: a Proxmox node, a Docker host, a plain Linux
box, the machine next to a Pterodactyl panel. It dials labhud (nothing listens on the host),
reports the host's sensors and what it runs, and does what you allow through the host's own
APIs, never through a shell or scripts you write. The agent is
[labhud-agent](https://github.com/Mithrandler/labhud-agent), in Go; the protocol is in
[agent-protocol.md](agent-protocol.md).

| capability | on the host it uses | labhud can |
|---|---|---|
| `sensors` | `/proc`, hwmon, `nvidia-smi`, RAPL | show CPU, memory, disk, load, uptime, CPU temperature, GPUs, power |
| `docker` | the socket (`/var/run/docker.sock`) | start, stop, restart, update containers; read their logs (the admin); show their CPU, RAM and network; say when one stops by itself, runs out of memory or keeps restarting; clean the unused images (the target `images`, the admin) |
| `systemd` | D-Bus (`org.freedesktop.systemd1`) | start, stop, restart services; name the failed units (all of them, for an alert) |
| `host` | D-Bus (`login1`, a transient unit) | reboot, shut down, update packages |
| `wol` | a UDP broadcast | wake other machines on its network |
| `pve` | the local API (`https://localhost:8006`, a token made at install) | start, shut down, reboot VMs and LXCs |
| `ptero` | the panel's client API (a key given at install) | start, stop, restart game servers |

Each capability is found by the agent itself and stays **off until you tick it** in ⚙ > Devices > Agents,
and then only on the **targets you tick**: each container, service, guest, server or MAC. The
agent checks the same allowlist again before it touches anything. There is no hard stop for
guests and no kill for game servers, on purpose.

The older pieces keep working next to it: the Python agent that pushes sensors
([live-agent.md](live-agent.md)) and the action agent behind `LABHUD_ACTION_URL`
([actions.md](actions.md)). A host moves over when its new agent shows the same buttons; then
the old one is switched off.

## Adding one

`LABHUD_DATA` is needed (labhud keeps the agents and their keys there), and the admin signed in:
⚙ is the admin's.

1. ⚙ > Devices > Agents > **Add an agent** shows a one-time code (5 minutes, once) and the command to run
   on the host, as root. Which command depends on whether this labhud pins a release of the
   agent (`agents/agent-release.json`, filled in when labhud is released):

   **A release is pinned** (the normal case once labhud-agent is published). One line downloads
   the agent for the host's architecture, checks it against the SHA-256 labhud pins, and
   installs it:

   ```
   curl -fsSL http://labhud:8095/agent/install-agent.sh | sudo sh -s -- --url http://labhud:8095 --code <code>
   ```

   **No release is pinned** (a labhud built from `main`, or before the agent's first release).
   The installer refuses to download anything it cannot check, so you bring the binary: build it
   (`CGO_ENABLED=0 go build ./cmd/labhud-agent` in labhud-agent, see its README) or copy one,
   put it on the host as `./labhud-agent`, and give the SHA-256 you got **where you built it**
   (one worked out on the host from the same file proves nothing):

   ```
   curl -fsSL http://labhud:8095/agent/install-agent.sh -o install-agent.sh
   sudo sh install-agent.sh --url http://labhud:8095 --code <code> --binary ./labhud-agent --sha256 <sha256>
   ```

   ⚙ shows the right one, with the code already in it (over labhud's own certificate, which curl
   does not trust, the line fetches the installer with `-k` and checks its SHA-256 instead). Either way the installer writes
   `/etc/labhud-agent/config` (mode 600) and a systemd unit and starts it. On Proxmox VE it also
   makes a user `labhud@pve` with only `VM.PowerMgmt`, `VM.Audit` and `Sys.Audit`, and a token for
   it that stays on the host. For a Pterodactyl panel add `--ptero-url https://panel…`: the client
   API key is asked for, not given on the command line. `--base-w` and `--price-kwh` set the power
   estimate. From agent 0.3.12 on it **updates itself**: ⚙ > Agents shows *Update to vX* when
   labhud pins a newer release (also in the app and `labhud agent update`), and *Update agents
   automatically* does it as each one dials in. The agent downloads the release from its own
   releases address and checks it twice (see agent-protocol.md, *Updates*). An older agent: run
   the line again (no `--code` needed: the agent keeps its key). `--uninstall` removes everything.
2. **As a container** instead: with a release pinned (or `LABHUD_AGENT_IMAGE` set), ⚙ also shows
   a `docker run` with the host's network (Wake-on-LAN, the ARP table), `/` read-only at `/host`
   (sensors, disk, name), and the Docker and D-Bus sockets. Leave out a socket and that
   capability is simply not there. Update it from your container tool like any image.
3. The agent appears in ⚙ > Devices > Agents with what it found. Tick what it may do, then on what.

labhud never sends an agent code or updates: a new version comes from your package or image
tooling, and ⚙ only shows which version each agent runs.

## On the cards

- A **Proxmox guest card** (`proxmox = "node/vmid"`, or one made by `guests = "node"`) gets
  START, SHUT DOWN and REBOOT from the agent on that node as soon as the guest is ticked. They
  replace the guest's `vm-…` actions, if it had any.
- `agent = "<name>"` on a card: that host's REBOOT, SHUT DOWN and UPDATE (when ticked), its
  sensors in the panel (`agents.<name>.sensors`, unless the card has `sensors`), and, without a
  check of its own, its dot follows the agent: green while connected, grey ("unknown", not
  down) while it is away.
- `agent_target = "<capability>:<target>"` next to `agent`: the card controls one target
  instead, and its dot follows that target's state.
- A **container** ticked under `docker`, on its row in a guest's panel (the guest card has
  `agent`) or on a card of its own (`agent_target = "docker:<name>"`): **update available**
  when its registry has a newer image than the one it runs (the agent checks 2 minutes after it
  starts, then every 6 hours), and then **UPDATE**: the agent pulls the image and makes the
  container again with the same settings, keeping the old one until the new one runs (and
  putting it back if it does not). "updating…" until the agent says how it went, in the
  timeline. The admin also gets **LOGS**: its last 200 lines under the row, with Refresh.
- In a guest's panel the containers are **grouped by compose project**, each with its CPU and RAM
  (the ticked ones that run) and its latest news of the last 7 days: "updated 10.10.1 → 10.10.3 ·
  2d ago", "out of memory · 5h ago". A project whose running containers are all ticked with RESTART
  has **RESTART STACK**: each of them restarted, as their own buttons would. A container's own card
  shows its whole **history** (the last 10 updates and crashes).
- A container that **stops by itself** (with an error, not stopped by someone), **runs out of
  memory** or **keeps stopping** (3 times in 10 minutes) is an event on the timeline and a
  notification, like a host going down.
- **Clean unused images**: tick `images` under Docker (it is no container, and "Tick all" leaves it
  out), then ⚙ > Devices > Agents > *Clean unused images*, or `labhud agent prune <agent>`: the
  images no container uses, unused for a day at least, go, and the timeline says what that freed.
- **Update on their own**: under Docker in ⚙ > Devices > Agents, the ticked containers labhud
  updates by itself when their registry has a newer image, in a window the same for every agent
  (every day at 04:00 unless changed; labhud's local time, `TZ`), one after the other, as UPDATE
  does; the timeline says what started. `labhud agent schedule <agent> <container> on|off`,
  `labhud agent window sun 04:00`. When another updater (Dockhand, Watchtower) runs too, give the
  agent `LABHUD_AGENT_AUTO_LABELS=dockhand.update=false`: the containers labhud updates on their own
  get that label when they are made again, and lose it when they are no longer labhud's, so each
  updater keeps to its own.
- **The fleet's updates**: a card with `list = "fleet.updates"` (and `fleet.update_count` as a
  metric) lists every container with a newer image on every host; its panel has UPDATE on each
  ticked one and UPDATE ALL.
- **DETAILS** (the admin), beside LOGS: how a container is made (ports, volumes, networks,
  restart policy, labels), its environment by name only: values are where passwords live.
- **Failed units**: the `failed` alert rule (on by default, after 5 minutes) watches every systemd
  unit of a host in the failed state, ticked or not.
- `wake = "<agent>:<mac>"`: WAKE from another agent, one that is up while this machine is off
  (the MAC ticked under its Wake-on-LAN). It adds to whatever the card's own `agent` gives, so a
  Proxmox host can have its sensors and SHUT DOWN from its own agent and WAKE from a neighbour's.

```toml
[[page.group.card]]
id = "n100"
name = "n100docker"
ping = "192.0.2.21"
agent = "n100docker"

[[page.group.card]]
id = "jellyfin"
name = "Jellyfin"
agent = "n100docker"
agent_target = "docker:jellyfin"      # also systemd:<unit>, pve:<vmid>, ptero:<id>, wol:<mac>

[[page.group.card]]
id = "pve"
name = "PVE"
ping = "192.0.2.22"
agent = "pve"                         # its sensors, SHUT DOWN, UPDATE
wake = "n100docker:aa:bb:cc:dd:ee:ff" # WAKE from the agent on n100docker
```

The data is in the snapshot under `agents.<name>`, like any source: `agents.pve2.sensors.cpu_temp`
can be a card's metric. The buttons reach the app through `/api/v1` as well; nothing new for it.

## Removing one

⚙ > Devices > Agents > **Remove** deletes its key in labhud at once and tells the agent, which deletes its
own and stops. To bring it back, pair it again with a new code.
