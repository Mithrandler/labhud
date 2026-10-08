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

Off unless `LABHUD_ACTION_URL` is set. With `LABHUD_ACTION_SECRET` (recommended), the display
posts to labhud, which checks the request comes from its own page and the action is offered in
`config.toml`, then forwards it with an HMAC signature that the agent checks (fresh, never
reused, from labhud's IP only). Without it, the browser posts straight to the agent, which must
check the `Origin` header and the display's IP. Either way the agent runs only commands listed
in its own file. With no login, anyone who opens the page can press the buttons. See
[actions.md](actions.md).

## Reporting a problem

Open an issue on GitHub. For anything that should not be public, use GitHub's private
vulnerability reporting (the repository's *Security* tab).
