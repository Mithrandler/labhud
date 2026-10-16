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
| `docker` | the socket (`/var/run/docker.sock`) | start, stop, restart containers |
| `systemd` | D-Bus (`org.freedesktop.systemd1`) | start, stop, restart services |
| `host` | D-Bus (`login1`, a transient unit) | reboot, shut down, update packages |
| `wol` | a UDP broadcast | wake other machines on its network |
| `pve` | the local API (`https://localhost:8006`, a token made at install) | start, shut down, reboot VMs and LXCs |
| `ptero` | the panel's client API (a key given at install) | start, stop, restart game servers |

Each capability is found by the agent itself and stays **off until you tick it** in ⚙ > Agents,
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

1. ⚙ > Agents > **Add an agent** shows a one-time code (5 minutes, once) and the command to run
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

   ⚙ shows the right one, with the code already in it. Either way the installer writes
   `/etc/labhud-agent/config` (mode 600) and a systemd unit and starts it. On Proxmox VE it also
   makes a user `labhud@pve` with only `VM.PowerMgmt`, `VM.Audit` and `Sys.Audit`, and a token for
   it that stays on the host. For a Pterodactyl panel add `--ptero-url https://panel…`: the client
   API key is asked for, not given on the command line. `--base-w` and `--price-kwh` set the power
   estimate. Run it again to upgrade (no `--code` needed: the agent keeps its key); `--uninstall`
   removes everything.
2. **As a container** instead: with a release pinned (or `LABHUD_AGENT_IMAGE` set), ⚙ also shows
   a `docker run` with the host's network (Wake-on-LAN, the ARP table), `/` read-only at `/host`
   (sensors, disk, name), and the Docker and D-Bus sockets. Leave out a socket and that
   capability is simply not there. Update it from your container tool like any image.
3. The agent appears in ⚙ > Agents with what it found. Tick what it may do, then on what.

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

⚙ > Agents > **Remove** deletes its key in labhud at once and tells the agent, which deletes its
own and stops. To bring it back, pair it again with a new code.
