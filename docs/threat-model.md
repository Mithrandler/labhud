# Threat model

What labhud protects, against whom, and where it stops. The connections themselves are drawn in
[architecture.md](architecture.md); the settings that close each gap are in
[security.md](security.md).

## What is worth protecting

| asset | where it lives | why it matters |
|---|---|---|
| **Service keys** (Proxmox, PBS, OPNsense, *arr...) | labhud's environment (`.env`) | the most valuable thing here: some services have no read-only keys |
| **Action secret** (`LABHUD_ACTION_SECRET`) | labhud and the agent | whoever has it can start and stop what the agent's file allows |
| **The snapshot** | labhud's memory, every display | a map of your network: host names, addresses, guests, alerts, logins |
| **The actions themselves** | the agent's TOML file | a shutdown pressed by the wrong person is an outage |

## Who might try

1. **Someone on your LAN or Wi-Fi**: a guest, a compromised IoT device or container.
2. **A web page opened on the display**, trying to read labhud through the browser (DNS
   rebinding, cross-site requests).
3. **Someone holding the display**: a stolen or borrowed phone, a visitor at the wall.
4. **Someone who gets a copy of the config**: a leaked `.env`, a backup, a screenshot of `/status`.
5. **The internet**: only if labhud is published, which it should not be without a VPN or an
   authenticating proxy.

Out of scope: someone with root on labhud's host or on the agent's host. They already have
everything.

## Scenarios

Each line: what happens, what stops it today, and what is left.

### Reading the dashboard

| attack | stopped by | left over |
|---|---|---|
| A LAN device opens `:8095` | your firewall rule (labhud itself has no login) | without that rule, the whole snapshot is readable |
| A web page uses DNS rebinding to read the snapshot | `LABHUD_HOSTS`: any other `Host` gets 421 | nothing, if `LABHUD_HOSTS` is set |
| Someone sniffs the display's traffic | `LABHUD_TLS_CERT` / `LABHUD_TLS_KEY`, or a reverse proxy with TLS | plain HTTP by default |
| Someone holds the display | nothing | they see what the wall shows, and can press the buttons |

### Stealing keys

| attack | stopped by | left over |
|---|---|---|
| Reading keys through the page or `/status` | keys never go to the browser; error texts are scrubbed | addresses in error texts are not scrubbed |
| A leaked `.env` | least-privilege keys: labhud warns at start when a Proxmox VE or PBS token can do more than read | *arr, Jellyfin and Seerr keys have full rights, there is no other kind |
| `docker inspect` or `/proc/<pid>/environ` on labhud's host | `LABHUD_*_FILE`: keys read from files (Compose secrets) | keys set directly in `.env` are still in the environment |
| **A machine in the middle on the LAN** (ARP spoofing, a rogue DNS answer) | `LABHUD_PINS` (the exact certificate) or `LABHUD_VERIFY=on`: the connection is dropped before the key is sent | **by default certificates are not checked**: it can pose as Proxmox or OPNsense and receive the key. The log and `/status` name every unchecked host |

### The setup page

Served only while there is no `config.toml`, and the only place where labhud receives keys over
HTTP (`setupmode.py`).

| attack | stopped by | left over |
|---|---|---|
| Someone on the network sets labhud up first, with their own sources | every call needs the setup code, printed only in labhud's log; 10 wrong codes lock it for a minute | whoever reads the log (the Docker host) can do it, as they could edit the files |
| A web page in the same browser posts to the setup page | Origin must equal Host, the body must be JSON, the code goes in a header a page cannot guess | none known |
| Keys read back from the setup page | it never returns a key: not from the environment, not one typed earlier (fields come back empty, "kept") | the config preview shows addresses |
| Keys sniffed while typed | HTTPS on the port (`LABHUD_TLS_CERT`) | over plain HTTP the Proxmox secret crosses the network once: run setup from the host itself (`localhost`) or a trusted segment, or use `init.py` |
| Making labhud ask an address of the attacker's choice ("Try") | the code | with the code, labhud can be made to request any URL once per try, like any monitoring tool |
| The page reopened later | it is not served once `config.toml` exists; `LABHUD_SETUP=off` disables it entirely | deleting `config.toml` brings it back, with a new code |

### Forging data

| attack | stopped by | left over |
|---|---|---|
| Faking a service's answer to hide a problem or raise a false one | pinned or verified certificates | hosts left unchecked |
| Faking an agent's data | pushed: HMAC with that agent's own key, 30-second window. Polled: a pinned HTTPS certificate on the agent | a polled agent over plain HTTP can still be impersonated |
| Reading the agent's sensors | pushed: the agent listens on nothing. Polled: `LABHUD_AGENT_READ_KEY` | an agent without a read key answers anyone who reaches it |
| Pushing to a source with another's key | each key opens its own source only | nothing |

### Running actions

| attack | stopped by | left over |
|---|---|---|
| A LAN device posts to the agent | signed mode: HMAC with a secret only labhud and the agent know, plus the agent's IP allowlist | nothing, while the secret stays secret |
| Replaying a captured request | signed mode: 30-second window, nonce, each signature accepted once | in direct mode: only the IP allowlist |
| Faking `Origin` in direct mode | only the display's IP | a device that can take the display's IP (same Wi-Fi, no DHCP reservation) can run actions. Use signed mode |
| Injecting a command | the agent runs only commands written in its TOML file; the name only selects one | nothing |
| Someone at the display presses SHUT DOWN | a confirmation dialog | nobody is identified. Keep destructive actions to what a guest may press |
| Someone at the display puts a host in maintenance to hide an outage | off unless `[maintenance] buttons = true`; at most 7 days; logged and in the history | the host stays grey and silent until it ends |

### Availability

| attack | stopped by | left over |
|---|---|---|
| Many browsers open the stream | a cap on open streams (503 above it) | a LAN device can use up the slots and blank the real display |
| A service hangs | a timeout per request, one thread per source | none for the other sources |
| A broken `config.toml` | the new file is refused, the old one keeps running, the display shows why | nothing |
| A source fails | its cards show the error, `/status` and the orange mark tell which | the cards show "unknown" instead of their last known state |

## The short version

The firewall in front of `:8095` and `:9189` carries most of the weight. Inside that, the gaps
worth closing next, in order:

1. **Pin the certificates** of the services labhud polls (`LABHUD_PINS`). Unpinned, a LAN
   attacker can collect keys.
2. **Push from the agents** (`LABHUD_PUSH_<NAME>_KEY`), so the screen can trust what it shows
   and the hosts need no open port.
3. **Keys in files** (`LABHUD_*_FILE`), so they are not in the container's environment.
4. **HTTPS on `:8095`** (`LABHUD_TLS_CERT`) if the display is not on a segment you trust.
