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
| **The actions themselves** | the agent's TOML file, the ticks in ⚙ > Agents | a shutdown pressed by the wrong person is an outage |
| **The admin** (password, authenticator secret) | `LABHUD_DATA` (scrypt hash; the TOTP secret as it is) | whoever is the admin presses every button and changes every setting |
| **Phone tokens and agent keys** | the phone's Keystore; `/etc/labhud-agent/key`; only hashes in `LABHUD_DATA` | a token is a phone; a key is an agent |
| **The `labhud` command's key** | `LABHUD_DATA/cli.json` (mode 600, new at every start) | it does what ⚙ does, from labhud's own machine |

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
| Someone sniffs the display's traffic | HTTPS by default: labhud's own certificate (`LABHUD_DATA/tls`), or yours (`LABHUD_TLS_CERT`) | `LABHUD_HTTP=on` turns it off; a browser warns once about labhud's own certificate |
| Someone holds the display | a display not signed in as the admin only shows (no buttons, no ⚙); with a PIN, a signed-in display goes back to only showing after 2 minutes without a touch | they see what the wall shows |

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
| Keys sniffed while typed | HTTPS on the port, by default | with `LABHUD_HTTP=on` the Proxmox secret crosses the network once: run setup from the host itself (`localhost`) or a trusted segment, or use `init.py` |
| Making labhud ask an address of the attacker's choice ("Try") | the code | with the code, labhud can be made to request any URL once per try, like any monitoring tool |
| "Find services" used to scan a network | the code; at most 32 hosts typed one by one, no ranges, only the listed ports | with the code, a few requests to each host listed |
| The page reopened later | it is not served once `config.toml` exists; `LABHUD_SETUP=off` disables it entirely | deleting `config.toml` brings it back, with a new code |

### The config editor

| attack | stopped by | left over |
|---|---|---|
| Someone at the display, or on the network, rewrites config.toml | a code from `init.py edit`, run where labhud's files are; it expires (15 min); 10 wrong codes lock for a minute | whoever can run commands in labhud's container can edit, as they could edit the file |
| A web page in the display's browser posts to the editor | LABHUD_HOSTS, Origin = Host, JSON only, the code in a header | none known |
| A broken or hostile config | every save is checked like a start; the old file is kept as config.toml.bak | a valid config can still point cards at other hosts: it is the same power as editing the file |

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
| A LAN device posts to the agent | HMAC with a secret only labhud and the agent know, plus the agent's IP allowlist | nothing, while the secret stays secret |
| Replaying a captured request | 30-second window, nonce, each signature accepted once | nothing |
| A stolen phone presses buttons | a phone only looks until it signs in as the admin; with a PIN, a signed-in phone needs it again after 2 minutes (or the fingerprint that stands in for it); the app's own lock if chosen; Remove in ⚙ > Phones ends its token at once | a signed-in, unlocked phone without the app lock, taken within those 2 minutes |
| Injecting a command | the agent runs only commands written in its TOML file; the name only selects one | nothing |
| Someone at the display presses SHUT DOWN | the admin's sign-in (a display not signed in has no buttons); the PIN, if set; a confirmation dialog | a display left signed in without a PIN |
| Someone at the display guesses the PIN | 5 wrong PINs stop the checks for 1 min, doubling up to 1 h; each pause is in the timeline | 10,000 PINs: a patient guesser gets through in weeks, and it shows in the history long before |
| Someone at the display bypasses the keypad (developer tools) | labhud refuses an action, and maintenance, without a live ticket | nothing |
| Someone at the display puts a host in maintenance to hide an outage | off unless `[maintenance] buttons = true`; at most 7 days; logged and in the history | the host stays grey and silent until it ends |

### The admin, the phones and the `labhud` command

| attack | stopped by | left over |
|---|---|---|
| Guessing the admin's password | scrypt; the authenticator code too; 5 wrong tries stop the checks for 1 min, doubling up to 1 h; a code works once | none known |
| Stealing a display's admin session | it lives in that browser only; it ends after 30 days unused, at Sign out, from ⚙ > Security on any screen, or with a new admin (`init.py admin`) | whoever copies the browser's storage has the session until one of those |
| Pairing a phone without being asked | a one-time code (5 minutes) shown by the admin, or the admin's own sign-in | a phone paired by QR code only looks |
| labhud makes the agents (root) run a binary of its choosing | an `update` names only a version and a SHA-256; the agent downloads from its own releases address (not labhud's), checks the release's SHA256SUMS too, and takes only a newer release | a compromised labhud can move the agents forward to a genuine newer release, nothing else |
| A visitor at a wall that runs the app's Wall mode | the page is the admin only while its phone is; with a PIN, every button and ⚙ need it, and the wall goes back to only showing after 2 minutes | a wall phone signed in as the admin on a display without a PIN: anyone at the wall has ⚙ |
| A phone's token copied out of the phone | sealed with a Keystore key that never leaves the phone; only a hash on the server | a rooted phone that is unlocked |
| The wall's PIN read from a phone | kept only behind a fingerprint-bound Keystore key, voided when a fingerprint is added | none known |
| The first sign-in without a QR code over HTTPS | the certificate seen then is pinned for good | that first connection trusts what answers: do it on your own network |
| Another process on labhud's host uses the `labhud` command's key | `cli.json` is mode 600, owned by labhud's user, accepted only from loopback, new at every start | root on that host, which has everything anyway |
| A paired phone makes labhud post somewhere (its UnifiedPush endpoint) | https addresses only; the body is fixed (`{"labhud":"news"}`), nothing of the news; an endpoint that answers 404/410 is forgotten | any paired phone can make labhud send that one fixed POST to an https address of its choice, once per notification |
| Reading a source's address through ⚙ ("Test") | admin only; only `http://` and `https://` are opened, never `file:` | the admin can make labhud request an address on the network, as any monitoring tool |
| Reading a container's logs | admin only, ticked containers only, the last 200 lines | logs can hold what the program printed: tokens some programs log |

### Notifications

| attack | stopped by | left over |
|---|---|---|
| The push service (Google, ntfy.sh) reads the news | with the app's Instant notifications, only a content-free wake-up goes through it; the app reads the news from labhud over your network | with ntfy, the text goes to the ntfy server you set (yours, or ntfy.sh) |
| Someone reads the outbox | `/api/v1/notifications` needs a paired phone or a name in `LABHUD_HOSTS` | it holds the last 100 messages, as the wall's history does |

### Agents (labhud-agent)

| attack | stopped by | left over |
|---|---|---|
| Pairing a fake agent | a one-time code, 144 bits, 5 minutes, admin only | someone who reads the code in those minutes pairs an agent that is allowed nothing until ticked |
| A fake labhud collects agents | labhud must prove it knows the code; an agent refuses a wrong proof | nothing |
| Recording the pairing | only a hash of the code and nonces travel; the key is derived, never sent | nothing |
| Replaying or reflecting a frame | each frame is signed for its connection, its direction and its number, at most 30 s old | nothing |
| Someone on the LAN reads the traffic | `https://` with a pinned certificate (the default) | with `LABHUD_HTTP=on`: names of containers, guests, services, and the sensors |
| A web page opens the WebSocket | refused when the request has an `Origin` | nothing |
| labhud itself is taken over | the agent acts only on the ticked targets, with the ticked verbs, and accepts no code | the ticked buttons can be pressed: that is their price |
| A shell in a container, opened by someone who is not the admin | the `terminal` capability is off in the agent unless `LABHUD_AGENT_TERMINAL=on`, ticked per container, the server refuses it for API tokens and the command line and without a typed PIN, closes it after 15 minutes or 3 idle, keeps no output, audits each open and close | what the admin does in it; a container with the Docker socket mounted is the host |
| Cleaning images, networks or volumes by mistake or on purpose | the host verbs (`prune*`, `inventory`) only on the target `images`, ticked on its own (never by "Tick all"), the admin only; only what no container uses (images unused for a day, networks a day old, volumes with no name: a named volume is never removed) | an image to pull again |
| A webhook URL leaks (a CI log) | it updates one ticked container, as its UPDATE would, at most once in 30 s; a new URL in ⚙ ends it; labhud keeps only its hash | that container pulled and made again |
| Command injection through a target | targets have a strict shape, checked in labhud and in the agent; each verb is one API call | nothing |

### The zone Servers (`/servers`, `/api/servers/*`)

The hosts' Docker, in the app and in the browser. Every route goes through one gate (`api/servers.py` `_gate`) and
declares the level it needs in one table (`NEEDS`); a test fails if a route is missing from it.

| attack | stopped by | left over |
|---|---|---|
| Reading or acting over plain HTTP, from someone on the LAN | Servers answers 403 to anything not over TLS (the page, its script and style too), also to a signed-in admin; the app refuses an `http://` server for it | `LABHUD_SERVERS_HTTP=on` (tests, a demo) lifts it: never in use |
| A web page of another site calls it with the admin's ticket | the page's own origin is required (`_same_page`), except for a token or the command line, which carry their own secret | nothing |
| A read token, or a stolen phone that only looks, restarts a container | three levels on the token scopes: `read` (lists, logs, inspect, stats, live log), `act` (start, stop, restart, update), `admin` (shell, the session, the audit check); a refusal is written to the audit log | an `act` token can restart what is ticked: that is its price |
| Guessing the admin or a token against it | 8 refusals in a minute block that address for 60 s, then 120 s, up to 15 minutes (429 with the wait); a session that ended is not counted | an attacker with many addresses |
| A session left open on a screen | with a browser or a phone the session starts only after the PIN, and ends after 15 minutes without a call (401, start again with the PIN); the PIN's own ticket ends after 2 | what an open session does in those minutes |
| Acting from an address that should not | `[admin] servers_networks` (optional): act and admin calls only from those networks; the command beside the server is not concerned | reading is not limited by it |
| A shell opened by a token, or without the PIN | `stream-open` for a shell needs the admin level and `_may_terminal` (a browser or a phone signed in as the admin, a PIN set and typed); input and resize need the admin level; nothing the shell says is kept | what the admin does in it |
| A host's answer carries markup, giant text or a secret | the answers are rebuilt field by field with limits (`inspect`, `stats`, the lists); a label whose name looks like a secret loses its value whatever the agent sent, and variables give names only; the pages put everything in with `textContent` | nothing |
| Script injection into the page | the CSP has no inline script or style and no `eval`, `frame-ancestors 'none'`, `no-referrer`, `no-store`; a test fails on an inline script, style, handler or `innerHTML` in the page's files | the terminal's own library may style itself less than it would |
| A name picked wrongly stops or updates the wrong container | stop and update ask for the container's name typed; the server only takes a name of the right shape that the agent has ticked, and the agent checks again | nothing |
| The audit log is edited afterwards | each entry carries the hash of the one before (`prev`, `h`); `POST /api/servers/audit-verify` walks the chain and names the first line that does not hold | cutting off the newest entries, or deleting the whole log, is not seen |

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
4. **Keep HTTPS on `:8095`** (the default); `LABHUD_HTTP=on` only behind a proxy that does TLS.
5. **Set the PIN** with an admin: a signed-in display or phone then goes back to only showing
   after 2 minutes without a touch.
