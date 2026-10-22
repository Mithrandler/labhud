# Security model

The connections are drawn in [architecture.md](architecture.md); who might attack them and what
is left open, in [threat-model.md](threat-model.md).

labhud is built for one screen on a trusted network. It has **no login**, by design: a wall
display cannot type a password.

## What the page reveals

The snapshot sent to the browser holds what you configured: host names, internal addresses,
guest names, storage use, and whatever your own JSON documents carry (logins, alerts, DNS
statistics). Treat the page as you would your network map.

The keys never leave the server. The browser only receives the results.
`/status` and `/api/status` add which sources run and their last error messages (keys and
passwords blanked out, addresses not); they sit behind the same host check as the page.

## The layers to put in front of it

1. **A firewall rule** that lets only the display (and, if you like, your VPN) reach port 8095.
   This is the protection that matters.
2. **`LABHUD_HOSTS`**: labhud answers only to the `host:port` names you list, and returns 421 to
   anything else. Without it, a web page opened in the display's browser could point a domain it
   controls at your server (DNS rebinding) and read the snapshot.
3. If you want it outside your LAN: **a VPN** (WireGuard, Tailscale), or a reverse proxy **with
   authentication** in front (Authelia, Authentik, oauth2-proxy). Never a plain port forward.

The container runs as an unprivileged user (uid 10001), on a read-only filesystem, with every
capability dropped except `NET_RAW` for ping (see `compose.example.yaml`).

## Keys: the least each source needs

labhud only reads. Give every source a key that can do nothing else, so a leaked `.env` cannot
stop a VM or delete a backup.

| source | what to give it | checked by labhud |
|---|---|---|
| Proxmox VE | an API token with privilege separation, role `PVEAuditor` on `/` | yes |
| Proxmox Backup Server | an API token, role `Audit` on `/` | yes |
| OPNsense | a user of its own with only `page-diagnostics-system-activity` and `page-status-trafficgraph` | no |
| Synology DSM | a user of its own, not in `administrators` | no |
| Sonarr, Radarr, Prowlarr, Bazarr, Jellyfin, Seerr | their API key: these keys have full rights, there is no read-only kind | no |
| qBittorrent, Navidrome | a user of its own where the service allows it | no |

For Proxmox VE and PBS, labhud asks the service at start (and after a config reload) what the
token may do. Anything beyond reading (`VM.PowerMgmt`, `Sys.Modify`, `Datastore.Modify`...) is
logged as a warning and shown on `/status`. (The agent's own token is another matter: for Servers > VMs & LXC the admin may add `VM.Snapshot` and `Datastore.Audit` to it, on purpose; see agents.md. Rolling a guest back and deleting a snapshot need the admin level and the guest's or snapshot's name typed, and the snapshot must be in the host's own list. Rebooting or shutting a host down and stopping a unit need the admin level and the host's or unit's name typed; updating its packages and starting or restarting a ticked unit are act level.) The guest agent's disk usage needs `VM.Monitor`
(Proxmox 8) or `VM.GuestAgent.Audit` (Proxmox 9), which count as reading.

## Certificates

Most homelab services present a self-signed certificate, so labhud cannot check them the usual
way, and by default it does not check them at all. The traffic is encrypted, but a device that
can get between labhud and a service (ARP spoofing on the LAN, a forged DNS answer) can pose as
the service and receive its key with the next poll. The startup log and `/status` list every
host whose certificate is not checked.

Pin them. For each service:

```sh
python3 init.py fingerprint https://192.0.2.10:8006 https://192.0.2.20:8007
# or, from the image:
docker run --rm ghcr.io/mithrandler/labhud python3 /app/init.py fingerprint https://192.0.2.10:8006
```

Compare the fingerprint with the one the service shows itself (Proxmox VE: *Node > System >
Certificates*; PBS: *Dashboard > Show Fingerprint*), then put the printed line in `.env`:

```sh
LABHUD_PINS=192.0.2.10:8006=<sha256>,192.0.2.20:8007=<sha256>
```

labhud then compares the certificate right after the handshake and drops the connection, before
sending anything, if it is not that one. A pin without a port applies to every port of the host.
When a certificate is renewed, the source fails with "not the pinned one" until you update the pin.

If your services have certificates from a CA (Let's Encrypt, or your own), use `LABHUD_VERIFY=on`
instead, with `LABHUD_CA=/path/ca.pem` for an internal CA. Pins still win for the hosts they name.

## HTTPS for labhud itself

**labhud answers HTTPS by default**, on its own port. Over plain HTTP everything a phone sends it
(the admin's password and code, the PIN, the app's token) would cross the network readable by
anything else on it; HTTPS closes that, even on a LAN that is only yours.

- At the first start labhud makes **its own certificate** (ECDSA P-256, self-signed, 10 years)
  into `LABHUD_DATA/tls/`, the key readable only by labhud. It is never made again on its own: the
  app and the agents learn its SHA-256 when they pair (the QR code, the agent's code) and accept
  that certificate and no other, so a new one means pairing them again. To start over, delete the
  folder and restart.
- **A browser warns once** ("Advanced → Proceed"): no authority vouches for a certificate labhud
  made itself. The app and the agents need no warning, they pinned it.
- **Your own certificate** instead (a real one, from your reverse proxy or your own CA):
  `LABHUD_TLS_CERT` and `LABHUD_TLS_KEY` (PEM files).
- **Plain HTTP** only when you say so, `LABHUD_HTTP=on` (behind a reverse proxy that does TLS),
  or when labhud cannot keep a certificate (no `LABHUD_DATA`, no `openssl`): the log and `/status`
  then say why.
- A plain `http://` request on the HTTPS port (an old bookmark) gets a redirect to `https://`,
  only for a name in `LABHUD_HOSTS`. `LABHUD_HOSTS` does not change: it lists `host:port` names,
  whatever the scheme.

The older agents that labhud polls pin it with `python3 init.py fingerprint https://192.0.2.50:8095`
(`LABHUD_AGENT_PUSH_PIN` on each agent).

## Agents

**labhud-agent** ([agents.md](agents.md)) dials labhud on `/api/agent/ws`:

- Its key is **derived from the pairing code on both sides and never sent**; labhud proves it
  knows the code too, so an agent cannot be paired to an impostor. labhud keeps only a hash and
  a derived secret of each code, never the code.
- **Every frame is signed** (HMAC-SHA256) in both directions, bound to its connection, numbered,
  and refused when older than 30 s: a captured frame cannot be replayed, here or on another
  connection.
- Over HTTPS (the default) labhud vouches for its certificate at pairing and the agent pins it.
  Over plain HTTP (`LABHUD_HTTP=on`) the content is not encrypted: the names of your
  containers, guests and services, and the sensors, cross the LAN in clear; nothing in them is a
  secret, and nothing can be changed or forged in transit.
- The endpoint answers under any name (the signature is the check) and **refuses any request
  with an `Origin`**, which every browser sends with a WebSocket: no web page can reach it.
- What it may do is ticked in ⚙ and checked **twice**, by labhud and by the agent. The Proxmox
  token and the Pterodactyl key stay on the host, in `/etc/labhud-agent/config` (mode 600).
- labhud never sends an agent code or an update.

The older agents:

- **Push** (recommended): the agent sends its data to labhud, signed with a key of its own
  (`LABHUD_PUSH_<NAME>_KEY`), and listens on nothing. A forged or replayed push is refused. See
  [live-agent.md](live-agent.md#pushing-instead-of-being-polled).
- **Polled**: give it a read key (`LABHUD_AGENT_READ_KEY` / `LABHUD_JSON_<NAME>_AUTH`), and
  HTTPS with a pinned certificate if the network between them is not yours.

## Secrets in files

Every `LABHUD_*` variable can be set as `LABHUD_*_FILE`, a path whose content is the value. With
Compose secrets the keys stay out of `docker inspect` and out of the process environment:

```yaml
services:
  labhud:
    environment:
      LABHUD_PBS_TOKEN_SECRET_FILE: /run/secrets/pbs
    secrets: [pbs]
secrets:
  pbs:
    file: ./secrets/pbs   # chmod 600; the container reads it as uid 10001
```

A value set directly wins over a file. A file that is missing or empty stops labhud at start,
naming the variable.

## Notifications

Off unless `LABHUD_NOTIFY_URL` is set. Each message carries a line from the history: card and
guest names, a source's error, an alert's text. If the webhook is a public service (ntfy.sh,
Discord), that text leaves your network: use a topic nobody can guess, or your own server.

## Actions

Off unless both `LABHUD_ACTION_URL` and `LABHUD_ACTION_SECRET` are set. The display (or a paired
app) posts to labhud, which checks the request comes from its own page (or carries a phone's
token) and the action is offered in `config.toml`, then forwards it with an HMAC signature that
the agent checks (fresh, never reused, from labhud's IP only). The agent runs only commands
listed in its own file. Only a display or a phone signed in as the admin shows and accepts the
buttons (and, with a PIN, only while unlocked). See [actions.md](actions.md).

## The lock (PIN)

A display on a wall is used by whoever walks by, and it usually stays signed in as the admin. The
PIN is optional, 4 digits, and exists only with an admin, who alone sets or changes it (⚙ >
Security > Lock, or `init.py pin`). With a PIN, **every signed-in screen goes back to only showing
after 2 minutes without a touch** (`[lock] idle`), or at once with the lock in the top bar: no ⚙,
no buttons, as if signed out, while the 30-day session stays. The lock, ⚙ and the panel's UNLOCK
ask for the PIN. A screen that is not signed in has no lock: it only shows anyway. The same PIN
holds for the phones signed in as the admin (`/api/v1/unlock`).

- Only a scrypt hash of the PIN is kept: in `LABHUD_DATA` (`labhud.db`), or in
  `LABHUD_LOCK_PIN_HASH` when there is no data folder (`init.py pin --hash` prints the line; it
  can come from a file, `LABHUD_LOCK_PIN_HASH_FILE`). Without a data folder the PIN cannot be
  changed from the screen.
- labhud checks the PIN, only for a screen or phone signed in as the admin, and answers with a
  ticket, kept in memory (a restart locks every screen). With a PIN set, labhud refuses the
  maintenance buttons, actions and ⚙ without a live ticket.
- Four digits are 10,000 PINs. After five wrong ones labhud checks nothing for a minute, then
  2, 4… up to an hour; each such pause goes to the log and the day's timeline.
- Forgotten PIN: `docker exec labhud python3 /app/init.py pin --clear`.

With `LABHUD_HTTP=on` the PIN crosses the LAN in clear text, like everything else the page shows.

## The admin

One account, with a password and a second factor (TOTP: Aegis, 2FAS, Bitwarden, Google
Authenticator...). It is made by the setup page, or by `init.py admin` on an older install; the
same command replaces it (a forgotten password, a lost authenticator) and `--remove` deletes it.

A display that is not signed in **only shows**: no ⚙, no buttons on the cards, no maintenance.
**Sign in** is at the top right; signed in, the admin's name there opens Settings, Edit config and
Sign out. Everything that changes something needs it: ⚙ (this screen's look as well as phones,
agents and the PIN), the cards' buttons and maintenance, the config editor. The app pairs by
signing in with the same three (or from a QR code shown in ⚙) and then acts with its own token.
**Without an admin every display only shows**; `init.py admin` makes one.

- Only a scrypt hash of the password is kept, in `LABHUD_DATA` or in `LABHUD_ADMIN` (`init.py
  admin --env` prints the line). The TOTP secret must be kept as it is, like any key in `.env`.
- A code works once, even within its 30 seconds. After five wrong sign-ins nothing is checked for a
  minute, then 2, 4... up to an hour, for every sign-in at once; each pause goes to the log and the
  day's timeline.
- A sign-in is a session for that browser (kept in its localStorage). It lasts 30 days from its
  last use, so a wall used every day stays signed in; it survives a restart of labhud, which keeps
  only each session's SHA-256 with when and from where (IP, browser) it was used. ⚙ > Security >
  **Signed in** lists them: sign one out, or every one at once. A new password or authenticator
  (`init.py admin`) signs every browser out.
- A signed-in display is the admin's to whoever stands at it. On a wall in a shared place, set a
  PIN as well: buttons and ⚙ then also need the screen unlocked.
- With `LABHUD_HTTP=on` the password and the code cross the network in clear text, like the PIN.
  Sign in from the display, the host, or over the VPN then.

## Paired phones (the LabHUD app)

The app is not labhud's own page, so it cannot pass the Host and Origin checks. It is paired once
instead (`init.py pair`, or ⚙ > Devices > Phones > Pair a phone) and then sends a token of its own.

- The pairing code is random (144 bits), works once, and only for 5 minutes. It travels in the QR
  code and in the link; whoever scans or copies it first gets the token.
- A valid token stands in for `LABHUD_HOSTS` and the Origin check, but **a QR code alone only
  looks**. The phone acts (buttons, maintenance, phones, agents, the PIN) only once signed in as
  the admin (`/api/v1/admin-login`, or pairing by signing in), and with a PIN only after
  `/api/v1/unlock`, for 2 minutes at a time. The sign-in is tied to the admin: a new password or
  authenticator puts every phone back to looking. The app keeps the token in Android's encrypted
  storage.
- Only SHA-256 hashes of codes and tokens are kept (in `LABHUD_DATA`); every phone has its own
  token, listed with when it was last seen on `/status` and by `init.py devices`.
- A lost phone: ⚙ > Devices > Phones > **Remove** on the display, or
  `docker exec labhud python3 /app/init.py unpair "<name>"`. Its token stops working at once. A
  phone that only looks cannot remove the others; a phone signed in as the admin can (after the
  PIN, when there is one).
- The app's wall mode loads the wall page with the token as a cookie: the page is the admin of a
  phone signed in as the admin (no second sign-in) and, with a PIN, asks for it again after 2
  minutes without use, like any display. A phone that only looks stays only looking. The PIN
  guards a screen left open, not a stolen phone with the app unlocked.
- **Pair a phone** and **Remove** are in ⚙, so they need the admin signed in.
- Over HTTPS (the default) the QR code carries the certificate's fingerprint and the app accepts
  only that certificate. With `LABHUD_HTTP=on` the token crosses the network in clear text.
- The app reaches labhud the same way a browser would: keep it on the LAN or behind a VPN. Pairing
  does not make it safe to expose the port to the internet.

## Servers (`/servers` and the app's Servers tab)

The hosts' Docker, with live logs and a shell. It is the part of labhud that can stop a container, so it has more
rules than the rest:

- **HTTPS only.** Over plain HTTP the page and its API answer 403, whoever asks; the app refuses to use Servers on a
  server added with `http://`. (`LABHUD_SERVERS_HTTP=on` lifts this for tests and demos: do not set it.)
- **Three levels on the token scopes.** `read` lists, reads logs, inspects, shows stats and follows a log; `act` also
  starts, stops, restarts and updates; `admin` opens a shell, starts the session and checks the audit chain. A browser
  or a phone signed in as the admin has them all, but acting needs the PIN typed (when there is one).
- **A session of its own**, started with the PIN and ended after 15 minutes without a call; the next call says to
  start it again.
- **Stop and update ask for the container's name typed.** A shell needs the admin, a PIN set and typed, and the
  container ticked for Terminal in Hosts and agents.
- **The host's images, volumes and networks** need "Unused images" ticked for the host. Removing one is admin level, asks for its name typed, is
  checked against the host's own list, and is never forced. A volume's files are read only after the admin opens that volume (one by one,
  audited); files that look like secrets show nothing.
- **Refusals are counted.** 8 in a minute from one address block it for 60 s, then longer each time.
- **`[admin] servers_networks`** (a list of networks, optional): act and admin calls only from those addresses.
- **Stacks** (compose files kept by the agent): reading or editing the files, restoring, down, the git source and its
  webhook are admin level; redeploy and restart are `act`. Save has the host check the file (`compose config`) and never
  deploys. The `.env` shows its values as `********` and they stay on the host. A webhook only syncs its own stack from
  a source that needs no token (a private repository's token is used once, never kept). Ticking `stacks` is as strong as
  ticking the Docker socket: a compose file can mount `/` or run privileged.
- **Strict page**: no inline script or style, `frame-ancestors 'none'`, `no-referrer`, `no-store`.
- **The audit log is chained**: each entry holds the hash of the previous one; Servers' *check* tells whether the chain
  holds. It does not show that the newest entries were removed or the whole log deleted: keep a copy elsewhere.

What the agent may do is still what the admin ticked, checked again by the agent. See the threat model for what each
rule is for.

## Reporting a problem

Open an issue on GitHub. For anything that should not be public, use GitHub's private
vulnerability reporting (the repository's *Security* tab).
