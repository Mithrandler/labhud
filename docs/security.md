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
logged as a warning and shown on `/status`. The guest agent's disk usage needs `VM.Monitor`
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

`LABHUD_TLS_CERT` and `LABHUD_TLS_KEY` (PEM files) make labhud answer HTTPS on its port. A
self-signed certificate is fine for agents, which pin it:

```sh
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
  -subj /CN=labhud -keyout key.pem -out cert.pem
python3 init.py fingerprint https://192.0.2.50:8095   # -> LABHUD_AGENT_PUSH_PIN on each agent
```

A browser warns about a self-signed certificate; for the display itself, a reverse proxy with a
real certificate is often simpler. `LABHUD_HOSTS` does not change: it lists `host:port` names,
whatever the scheme.

## Agents

**labhud-agent** ([agents.md](agents.md)) dials labhud on `/api/agent/ws`:

- Its key is **derived from the pairing code on both sides and never sent**; labhud proves it
  knows the code too, so an agent cannot be paired to an impostor. labhud keeps only a hash and
  a derived secret of each code, never the code.
- **Every frame is signed** (HMAC-SHA256) in both directions, bound to its connection, numbered,
  and refused when older than 30 s: a captured frame cannot be replayed, here or on another
  connection.
- **Over plain HTTP the content is not encrypted**: the names of your containers, guests and
  services, and the sensors, cross the LAN in clear. Nothing in them is a secret, and nothing
  can be changed or forged in transit; if that is still too much, give labhud a certificate
  (`LABHUD_TLS_CERT`) and use `https://`. labhud then vouches for it at pairing and the agent
  pins it.
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
listed in its own file. Anyone who opens the page can press the buttons unless the display has a
PIN (see the lock, below). See [actions.md](actions.md).

## The lock (PIN)

A display on a wall is used by whoever walks by. With a PIN set (⚙ > Security > Lock, or `init.py pin`),
the screen is only for looking until someone types it; open, it locks again after `[lock] idle`
minutes without a touch, or at once with the lock left of ⚙.

- Only a scrypt hash of the PIN is kept: in `LABHUD_DATA` (`labhud.db`), or in
  `LABHUD_LOCK_PIN_HASH` when there is no data folder (`init.py pin --hash` prints the line; it
  can come from a file, `LABHUD_LOCK_PIN_HASH_FILE`). Without a data folder the PIN cannot be
  changed from the screen.
- labhud checks the PIN and answers with a ticket, kept in memory (a restart locks every screen).
  With a PIN set, labhud refuses the maintenance buttons and actions without a live ticket.
- Four digits are 10,000 PINs. After five wrong ones labhud checks nothing for a minute, then
  2, 4… up to an hour; each such pause goes to the log and the day's timeline.
- Forgotten PIN: `docker exec labhud python3 /app/init.py pin --clear`.

What it does not do: over plain HTTP the PIN crosses the LAN in clear text, like everything else
the page shows. Use `LABHUD_TLS_CERT` / `LABHUD_TLS_KEY` or a VPN where that matters.

## The admin

One account, with a password and a second factor (TOTP: Aegis, 2FAS, Bitwarden, Google
Authenticator...). It is made by the setup page, or by `init.py admin` on an older install; the
same command replaces it (a forgotten password, a lost authenticator) and `--remove` deletes it.

Once there is an admin, these need it signed in: pairing and removing phones in ⚙ > Phones,
opening the config editor from ⚙, and pairing from the app without a QR code (the app signs in
with the same three). Without an admin they work as before: on the display's own page, behind its
PIN when one is set.

- Only a scrypt hash of the password is kept, in `LABHUD_DATA` or in `LABHUD_ADMIN` (`init.py
  admin --env` prints the line). The TOTP secret must be kept as it is, like any key in `.env`.
- A code works once, even within its 30 seconds. After five wrong sign-ins nothing is checked for a
  minute, then 2, 4... up to an hour, for every sign-in at once; each pause goes to the log and the
  day's timeline.
- A sign-in on the display lasts while it is used and ends after 15 minutes without use, or at a
  restart, or when the admin is replaced.
- Over plain HTTP the password and the code cross the network in clear text, like the PIN. Sign in
  from the display, the host, or over the VPN.

## Paired phones (the LabHUD app)

The app is not labhud's own page, so it cannot pass the Host and Origin checks. It is paired once
instead (`init.py pair`, or ⚙ > Phones > Pair a phone) and then sends a token of its own.

- The pairing code is random (144 bits), works once, and only for 5 minutes. It travels in the QR
  code and in the link; whoever scans or copies it first gets the token.
- A valid token stands in for `LABHUD_HOSTS`, the Origin check and the lock's PIN: **a token is as
  good as standing at an unlocked display**, including the actions and maintenance buttons. The
  app keeps it in Android's encrypted storage and asks for the phone's fingerprint or PIN when it
  opens.
- Only SHA-256 hashes of codes and tokens are kept (in `LABHUD_DATA`); every phone has its own
  token, listed with when it was last seen on `/status` and by `init.py devices`.
- A lost phone: ⚙ > Phones > **Remove** on the display, or
  `docker exec labhud python3 /app/init.py unpair "<name>"`. Its token stops working at once. A
  phone cannot remove the others with its own token.
- The app's wall mode loads the wall page with the token as a cookie: that page is a display like
  any other, so the display's PIN still guards its buttons, pairing and removing.
- With an admin, **Pair a phone** and **Remove** need it signed in. Without one: with a PIN on the
  display they need the screen unlocked; without a PIN, anyone at the display can pair a phone, as
  they can already press its buttons.
- Over plain HTTP the token crosses the network in clear text, like everything else. Over a VPN
  that is fine; on a network you do not trust, give labhud HTTPS (`LABHUD_TLS_CERT`): the QR code
  then carries the certificate's fingerprint and the app accepts only that certificate.
- The app reaches labhud the same way a browser would: keep it on the LAN or behind a VPN. Pairing
  does not make it safe to expose the port to the internet.

## Reporting a problem

Open an issue on GitHub. For anything that should not be public, use GitHub's private
vulnerability reporting (the repository's *Security* tab).
