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
| Someone sniffs the display's traffic | nothing: `:8095` is plain HTTP | put TLS in front (reverse proxy) or keep the display on a trusted segment or VPN |
| Someone holds the display | nothing | they see what the wall shows, and can press the buttons |

### Stealing keys

| attack | stopped by | left over |
|---|---|---|
| Reading keys through the page or `/status` | keys never go to the browser; error texts are scrubbed | addresses in error texts are not scrubbed |
| A leaked `.env` | least-privilege keys: labhud warns at start when a Proxmox VE or PBS token can do more than read | *arr, Jellyfin and Seerr keys have full rights, there is no other kind |
| `docker inspect` or `/proc/<pid>/environ` on labhud's host | nothing | secrets read from files: roadmap item 10 |
| **A machine in the middle on the LAN** (ARP spoofing, a rogue DNS answer) | nothing: **HTTPS certificates are not verified** | it can pose as Proxmox or OPNsense and receive the key with labhud's next poll. Certificate pinning or a CA bundle is next on the roadmap |

### Forging data

| attack | stopped by | left over |
|---|---|---|
| Faking a service's answer to hide a problem or raise a false one | nothing, for the same reason as above | same fix: verified certificates |
| Faking the live agent's JSON | nothing: it is plain HTTP with no key | push agents with a key of their own, TLS to agents: roadmap 8 and 9 |
| Reading the agent's sensors | nothing: `GET /` on the agent is open | firewall `:9189` to labhud's IP |

### Running actions

| attack | stopped by | left over |
|---|---|---|
| A LAN device posts to the agent | signed mode: HMAC with a secret only labhud and the agent know, plus the agent's IP allowlist | nothing, while the secret stays secret |
| Replaying a captured request | signed mode: 30-second window, nonce, each signature accepted once | in direct mode: only the IP allowlist |
| Faking `Origin` in direct mode | only the display's IP | a device that can take the display's IP (same Wi-Fi, no DHCP reservation) can run actions. Use signed mode |
| Injecting a command | the agent runs only commands written in its TOML file; the name only selects one | nothing |
| Someone at the display presses SHUT DOWN | a confirmation dialog | nobody is identified. Keep destructive actions to what a guest may press |

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

1. **Verify certificates** of the services labhud polls. Today a LAN attacker can collect keys.
2. **Authenticate the agents' data** (push with a key, TLS), so the screen can trust what it shows.
3. **Secrets from files**, so they are not in the container's environment.
4. **TLS in front of `:8095`** if the display is not on a segment you trust.
