# Architecture

labhud is one Python process (standard library only) that polls your services, keeps the latest
answer of each in memory, and streams changes to the displays. This page shows every connection
it makes or accepts: direction, port, protocol and the key it carries. For what can go wrong on
each of them, see [threat-model.md](threat-model.md).

## The picture

```
                         ┌───────────────────────── your LAN ─────────────────────────┐
                         │                                                            │
  display (browser) ─────┼─ GET  /, /api/stream (SSE), /api/config ──▶ ┌──────────┐   │
   wall tablet, phone    │  POST /action/<name>                        │          │   │
                         │                       :8095, HTTP           │  labhud  │   │
                         │                                             │          │   │
                         │   ┌─────────────────── polls (pull) ◀───────┤          │   │
                         │   │                                         └────┬─────┘   │
                         │   ▼                                              │         │
                         │  Proxmox VE / PBS ......... HTTPS, API token      │         │
                         │  OPNsense, Synology, OMV .. HTTPS, key or user    │         │
                         │  *arr, Jellyfin, Seerr .... HTTP(S), API key      │         │
                         │  your JSON (live agent) ... HTTP(S), no key       │         │
                         │  hosts .................... ICMP ping / TCP connect│         │
                         │                                                  │         │
                         │  action agent :9189 ◀── POST + HMAC ─────────────┘         │
                         │                                                            │
  phone app ─────────────┼─ /api/v1/* with its token (LAN or VPN) ──▶ labhud          │
                         └────────────────────────────────────────────────────────────┘
                                                       │
                          outbound, optional: weather API, notification webhook, MQTT broker
```

Everything flows **from labhud outward**, except the displays and the phone apps, which connect
to labhud, and agents that push their numbers. Neither a display nor an app ever talks to a
service or an agent itself.

## Connections labhud accepts

| from | to | what | protection |
|---|---|---|---|
| display | `:8095` `GET /`, static files | the page | firewall + `LABHUD_HOSTS` (421 for any other name) |
| display | `GET /api/stream` | Server-Sent Events: one full snapshot, then only the changes (`delta`), a keep-alive comment after 20 s of silence | same |
| display | `GET /api/config`, `/api/snapshot`, `/api/history`, `/api/status`, `/status` | layout, current data, sparklines, source health | same |
| display | `POST /action/<name>` | the press of a button | `Origin` must be labhud's own page, the display's lock must be open, action must be on a card in `config.toml` |
| display | `POST /api/maintenance/<card>` | a MAINTENANCE press: `{"minutes": n}`, 0 ends it | `[maintenance] buttons = true`, `Origin` must be labhud's own page, the lock open, card must exist, at most 7 days |
| display | `/api/lock/*`, `/api/admin/*`, ⚙ | the display's PIN, the admin sign-in, phone pairing | `Origin`, PIN / password + TOTP, slowed after wrong tries |
| phone app | `/api/v1/*` (state, card, history, uptime, events, stream, actions) | everything the app shows and does, see [app-api.md](app-api.md) | a token per paired phone (`Authorization: Bearer`), any Host |
| agent | `POST /api/push/<name>` | a pushed source's JSON | HMAC-SHA256 with that source's own key, 30-second window; not behind `LABHUD_HOSTS` |

All of it is HTTPS by default, with labhud's own certificate or yours (`LABHUD_TLS_CERT`); plain
HTTP only with `LABHUD_HTTP=on`.

Looking needs no login: anyone who passes the firewall and uses an allowed host name sees what
the display sees. Pressing needs the display's PIN when one is set, or a paired phone's token;
changing the config or pairing a phone needs the admin when there is one
(see [security.md](security.md)).

## Connections labhud makes

| to | protocol | key | how often |
|---|---|---|---|
| Proxmox VE (each node) | HTTPS REST | API token (`PVEAuditor`) | 10 s; backups every 30 min |
| Proxmox Backup Server | HTTPS REST | API token (`Audit`) | 60 s |
| OPNsense | HTTPS REST | key + secret | 5 s |
| Synology DSM | HTTPS | user + password, session | 15 s |
| qBittorrent | HTTP(S) | user + password, cookie | 10 s |
| Sonarr, Radarr, Prowlarr, Bazarr, Jellyfin, Seerr, Navidrome, OMV | HTTP(S) | API key or user | 5–10 min |
| `LABHUD_JSON_<NAME>_URL` | HTTP(S) GET | `LABHUD_JSON_<NAME>_AUTH`, if set | `LABHUD_JSON_<NAME>_EVERY`, default 10 s |
| hosts with `ping` / `tcp` on their card | ICMP echo / TCP connect | none | 30 s |
| weather (`[weather]`) | HTTPS | none | 15 min |
| Healthchecks, Uptime Kuma | HTTP(S) | read-only API key | 60 s, 30 s |
| Scrutiny | HTTP(S) | none (its API has no key) | 5 min |
| `LABHUD_NOTIFY_URL` | HTTP(S) POST | `LABHUD_NOTIFY_AUTH` | on events only |
| `LABHUD_MQTT_URL` | MQTT 3.1.1 (or over TLS) | user + password | on changes only |
| `LABHUD_ACTION_URL` | HTTP POST | HMAC-SHA256 of time, nonce and action name | on a button press only |

Certificates are checked per host: pinned (`LABHUD_PINS`), verified against CAs
(`LABHUD_VERIFY`), or, by default, **not at all**: encrypted, but not authenticated. The log and
`/status` name every unchecked host. See [security.md](security.md#certificates).

## The action agent

[`agents/labhud-agent.py`](../agents/labhud-agent.py) listens on `:9189` (plain HTTP):

- `GET /`: the host's sensors (temperatures, GPUs) as JSON, for a `LABHUD_JSON_*_URL` source.
  With `LABHUD_AGENT_READ_KEY`, only to that bearer key; without it, to anyone who reaches the port.
- Or it **pushes** them to labhud (`LABHUD_AGENT_PUSH_URL`) and, with `LABHUD_AGENT_PORT=0`,
  listens on nothing.
- HTTPS with `LABHUD_AGENT_TLS_CERT` / `LABHUD_AGENT_TLS_KEY`.
- `POST /action/<name>`: runs a command written in advance in its TOML file, never anything
  taken from the request. Off unless configured; accepts only labhud's IP and a fresh, unused
  HMAC. Details: [actions.md](actions.md).

## Inside labhud

```
 one thread per source ──▶ latest result ──▶ compare with the previous ──▶ delta ──▶ every SSE client
   (its own interval)         in memory            │
                                                   ├──▶ events.recent (last changes, in memory)
                                                   ├──▶ history (sparklines 6 h, 15-min averages 7 days)
                                                   ├──▶ uptime (per checked card, 15-min slices, 7 days)
                                                   └──▶ notifications (if a webhook is set)
```

- A source that misses a poll keeps its last answer, marked stale, for `LABHUD_SOURCE_STALE`
  (60 s, at least two of its intervals); after that it becomes `{"unavailable": "<why>"}` and its
  cards show the error, not old numbers. After `LABHUD_SOURCE_GRACE` (300 s) of failing, the
  display shows an orange mark next to the clock.
- `config.toml` is read again a few seconds after it changes; a broken file is refused and the
  display shows why, while the old config keeps running.
- Nothing is written to disk unless `LABHUD_DATA` names a folder: then the events, the
  sparklines, the uptime, maintenance set from the display, the paired phones and the admin are
  kept in `labhud.db` (SQLite) there.
- Maintenance (from `config.toml` windows or the display) is checked every 10 s and sent as
  the `maintenance` entry of the snapshot; cards in it are grey, not red, and do not notify.
- Keys are read from the environment and never sent to the browser. Error messages are scrubbed
  of keys and passwords before they reach `/status`.
- A new part of labhud is a file, not an edit to the dispatch: a module in `api/` adds its routes
  with `@route` (`routes.py`; the guard `manage` is ⚙'s check, `public` the display's), and a
  script calls `register_section()` (`static/settings/registry.js`) for its page in ⚙.
- The look comes from `design/tokens.json` (colours, sizes, line icons) through `design/build.py`;
  the service icons are files in `static/icons/svc` (`design/icons.py`), the logo is in
  `static/brand`.

## Ports at a glance

| port | who listens | who should reach it |
|---|---|---|
| 8095/tcp | labhud | the displays, and your VPN if you use one |
| 9189/tcp | labhud-agent (optional; none when it only pushes) | labhud's IP (sensors, actions) |
| your services' API ports | the services | labhud's IP |
