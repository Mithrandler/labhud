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
   wall tablet, phone    │  POST /action/<name>  (signed mode only)    │          │   │
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
                         │  action agent :9189 ◀── POST + HMAC (signed) ────┘         │
                         │       ▲                                                    │
                         │       └── POST from the display's browser (direct mode)    │
                         └────────────────────────────────────────────────────────────┘
                                                       │
                                      outbound, optional: weather API, notification webhook
```

Everything flows **from labhud outward**, except the displays, which connect to labhud, and the
direct action mode, where the display's browser talks to the agent itself.

## Connections labhud accepts

| from | to | what | protection |
|---|---|---|---|
| display | `:8095` `GET /`, static files | the page | firewall + `LABHUD_HOSTS` (421 for any other name) |
| display | `GET /api/stream` | Server-Sent Events: one full snapshot, then only the changes (`delta`), a keep-alive comment after 20 s of silence | same |
| display | `GET /api/config`, `/api/snapshot`, `/api/history`, `/api/status`, `/status` | layout, current data, sparklines, source health | same |
| display | `POST /action/<name>` | only in signed mode: the press of a button | `Origin` must be labhud's own page, action must be on a card in `config.toml` |

labhud has no login (see [security.md](security.md)): anyone who passes the firewall and uses an
allowed host name sees everything the display sees.

## Connections labhud makes

| to | protocol | key | how often |
|---|---|---|---|
| Proxmox VE (each node) | HTTPS REST | API token (`PVEAuditor`) | 10 s; backups every 30 min |
| Proxmox Backup Server | HTTPS REST | API token (`Audit`) | 60 s |
| OPNsense | HTTPS REST | key + secret | 5 s |
| Synology DSM | HTTPS | user + password, session | 15 s |
| qBittorrent | HTTP(S) | user + password, cookie | 10 s |
| Sonarr, Radarr, Prowlarr, Bazarr, Jellyfin, Seerr, Navidrome, OMV | HTTP(S) | API key or user | 5–10 min |
| `LABHUD_JSON_<NAME>_URL` | HTTP(S) GET | none | `LABHUD_JSON_<NAME>_EVERY`, default 10 s |
| hosts with `ping` / `tcp` on their card | ICMP echo / TCP connect | none | 30 s |
| weather (`[weather]`) | HTTPS | none | 15 min |
| `LABHUD_NOTIFY_URL` | HTTP(S) POST | `LABHUD_NOTIFY_AUTH` | on events only |
| `LABHUD_ACTION_URL` (signed mode) | HTTP POST | HMAC-SHA256 of time, nonce and action name | on a button press only |

Certificates are checked per host: pinned (`LABHUD_PINS`), verified against CAs
(`LABHUD_VERIFY`), or, by default, **not at all**: encrypted, but not authenticated. The log and
`/status` name every unchecked host. See [security.md](security.md#certificates).

## The action agent

[`agents/labhud-agent.py`](../agents/labhud-agent.py) listens on `:9189` (plain HTTP):

- `GET /`: the host's sensors (temperatures, fans) as JSON, for a `LABHUD_JSON_*_URL` source.
  **No check at all**: anyone who reaches the port can read them.
- `POST /action/<name>`: runs a command written in advance in its TOML file, never anything
  taken from the request. Off unless configured. Signed mode accepts only labhud's IP and a fresh,
  unused HMAC; direct mode accepts only the display's IP with the display's `Origin`.
  Details: [actions.md](actions.md).

## Inside labhud

```
 one thread per source ──▶ latest result ──▶ compare with the previous ──▶ delta ──▶ every SSE client
   (its own interval)         in memory            │
                                                   ├──▶ events.recent (last changes, in memory)
                                                   ├──▶ history (sparklines, in memory)
                                                   └──▶ notifications (if a webhook is set)
```

- A source that fails is replaced by `{"unavailable": "<why>"}` until it answers again: its
  cards show the error, not old numbers. After `LABHUD_SOURCE_GRACE` (300 s) of failing, the
  display shows an orange mark next to the clock.
- `config.toml` is read again a few seconds after it changes; a broken file is refused and the
  display shows why, while the old config keeps running.
- Nothing is written to disk: a restart starts with an empty history.
- Keys are read from the environment and never sent to the browser. Error messages are scrubbed
  of keys and passwords before they reach `/status`.

## Ports at a glance

| port | who listens | who should reach it |
|---|---|---|
| 8095/tcp | labhud | the displays, and your VPN if you use one |
| 9189/tcp | labhud-agent (optional) | labhud's IP (sensors, signed actions) or the display's IP (direct actions) |
| your services' API ports | the services | labhud's IP |
