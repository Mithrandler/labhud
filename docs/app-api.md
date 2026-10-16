# The phone app and its API

The LabHUD Android app ([labhud-android](https://github.com/Mithrandler/labhud-android)) shows
your labhud on a phone: what is red, every card, its panel and charts, the day's timeline, the
actions, and the whole wall page when you want it. It reads everything from labhud, the same way
the display does, through the API below. This page is for setting it up and for anyone writing
another client.

## Pairing a phone

Pairing needs `LABHUD_DATA` (the paired devices are kept there).

1. On the display: ⚙ > **Phones** > **Pair a phone** shows a QR code. Or, on the machine:

   ```sh
   docker exec -it labhud python3 /app/init.py pair "Pixel 6a"
   ```

   which prints the same QR code in the terminal, and the link under it.
2. In the app: **Add server**, then scan the code (or paste the link).

The code works once and only for 5 minutes. The phone must reach labhud at the address in the
link: the display's own address for ⚙, the first name in `LABHUD_HOSTS` for `init.py pair`
(or give another: `init.py pair "Pixel 6a" http://10.8.0.1:8095` for a VPN address).

⚙ > **Phones** lists the paired phones, with when each was last seen, and **Remove** ends one at
once (the display's PIN is asked first when one is set). From the machine: `init.py devices` and
`init.py unpair "Pixel 6a"`. What a token allows, and why the port must still stay off the internet:
[security.md](security.md#paired-phones-the-labhud-app).

Notifications stay with ntfy: with `LABHUD_NOTIFY_FORMAT=ntfy` and `LABHUD_NOTIFY_CLICK=app`, a
tap on one opens the app on the card it is about.

## The API

Version 1. `GET /api/v1/info` says `"api": 1`; a later incompatible change raises the number and
keeps the old routes for a while. Fields may be added at any time: ignore what you do not know.

### Authentication

Every request carries `Authorization: Bearer <token>`. The token stands in for `LABHUD_HOSTS` and
the Origin check. Without a token the read routes answer as `/api/snapshot` does: only under a
name in `LABHUD_HOSTS` (421 otherwise).

A token only **looks** until the phone signs in as the admin (below): `info` and `state` say
`device_admin`, `pin` (the display has a PIN) and `unlocked`. Buttons, maintenance and managing
phones and agents need `device_admin`, and with a PIN, a live unlock ticket too.

For the wall page inside a WebView, set the token as the cookie `labhud_device` for labhud's
address instead: the page then loads under any name. It stays a wall: its buttons need their
Origin to be the page's own, and the display's PIN, when one is set, as on any other screen.

### Pairing

`POST /api/v1/pair` with `{"code": "<c from the link>", "name": "Pixel 6a"}` (JSON, under any
Host). Answers `{"ok": true, "token": "<id>.<secret>", "name": "Pixel 6a", "info": {…}}` once;
403 for a wrong, used or expired code. The name is the one given to `init.py pair` if any, made
unique ("Pixel 6a 2").

The link is `labhud://pair?u=<labhud's address>&c=<code>[&fp=<sha256>]`. `fp`, present when
labhud serves HTTPS itself (`LABHUD_TLS_CERT`), is the SHA-256 of its certificate (DER, hex):
accept that certificate and no other.

### Pairing by signing in as the admin

When labhud has an admin (`info.admin`), the app can pair without a QR code:
`POST /api/v1/login-pair` with `{"user", "password", "code", "name"}` (the code from the admin's
authenticator app), under any Host. The answer is the same as `/api/v1/pair`; 403 for a wrong
sign-in, with `wait` (seconds) once wrong tries are being slowed down. A code works once. A phone
paired this way is signed in as the admin already.

### Signing in as the admin, and the PIN

| route | body | answer |
|---|---|---|
| `POST /api/v1/admin-login` | `{"user", "password", "code"}` | `{ok, device_admin, pin, unlocked}`; 403 with `wait` for a wrong sign-in |
| `POST /api/v1/admin-logout` | `{}` | `{ok, device_admin: false, …}` |
| `POST /api/v1/unlock` | `{"pin"}` | `{ok, ticket, idle}`: send the ticket as `X-Labhud-Unlock` with every button; `ticket` is null without a PIN |

The sign-in belongs to the admin who signed in: a new admin (`init.py admin`) puts every phone
back to looking. The unlock ticket ends after `idle` seconds (120) without use, as on a display;
wrong PINs are slowed down with the display's own counter. The display's ⚙ > Security > Signed in
lists the phones signed in as the admin, and Remove ends a phone at once.

### Reading

| route | answer |
|---|---|
| `GET /api/v1/info` | `api`, `version`, `title`, `demo`, `actions` (signed actions on), `maintenance_buttons`, `pairing`, `admin` (signing in to pair works), `trends` (the keys that have history), `uptime` (`/api/v1/uptime` answers) |
| `GET /api/v1/state` | the whole state, below |
| `GET /api/v1/card/<id>` | one card as in `state`, plus `sections` (its panel) |
| `GET /api/v1/game/<id>` | one game server as in a games page's `servers`, plus `who` (the players' names, when the game gives them) |
| `GET /api/v1/stream` | SSE: `event: state` with the same document as `/api/v1/state`, at once and on every change (at most once a second), `: ping` every 20 s |
| `GET /api/v1/history` | `{step, series: {key: [values]}}`: the last hours of every trend, one point per `step` seconds; `?long`: 15-minute points over 7 days |
| `GET /api/v1/uptime` | `{step, cards: {id: [values]}, percent: {id: 99.4}}`: per card with a check, one value per 15 minutes, oldest first: the share of that time it was up (0..1), `-1` off on purpose (schedule, on demand, maintenance), `null` not known. `?range=24h` (default) or `?range=7d`; `percent` is over the range, leaving out the off and unknown slices |
| `GET /api/v1/events` | `{events: [{t, name, bad, card?, state?}]}`, newest first |
| `GET /api/v1/devices` | `{pairing, devices: [{name, paired, seen}]}`: the paired phones |

`state`:

```json
{
  "title": "LabHUD", "now": 1760000000,
  "pages": [{"id": "general", "title": "GENERAL",
             "groups": [{"id": "pve", "title": "Proxmox", "cards": ["host-pve", "vm-pve-100"]}]}],
  "cards": [{
    "id": "host-pve", "name": "PVE", "page": "general", "group": "pve", "kind": "card",
    "status": "up", "bad": false, "summary": "VM 4 · LXC 3 · CPU 20%",
    "frozen": false, "note": "", "note_kind": "",
    "metrics": [{"key": "proxmox.pve.cpu", "label": "CPU", "value": "20%", "sev": "", "trend": true}],
    "subtitle": "i5-12500 · 64 GB", "url": "https://192.0.2.10:8006",
    "actions": [], "maintenance_button": true
  }],
  "problems": ["vm-pve-106"],
  "alerts": [{"id": "demo-disk", "text": "Disk on nas-backup above 85% in 12 days"}]
}
```

A card:

- `status`: `up`, `down`, `scheduled` (off on schedule), `on-demand` (off, started when needed),
  `maintenance`, `unknown` (not known yet), or `null` (the card has no status).
- `bad`: red on the display (down, or a number past its critical mark), `null` while nothing about
  it is known. `problems` lists the red ones in page order.
- `kind`: `guest` (a Proxmox guest, a small tile), `list` (rows, in `rows`) or `card`.
- `metrics[].value` is formatted as the display shows it; `sev` is `""`, `warn` or `crit`;
  `trend` says the key has a history (`/api/v1/history`); `format` is the config's (`percent`,
  `percent1`, `celsius`, `used_of`, `bytes`, `rate`, `count`…). For `used_of` the history holds
  the percentage, not the pair.
- `frozen`: the numbers are not live (host down or off, or the source kept its last answer); dim
  them. `note` says why, `note_kind` is `stale`, `off`, `not_configured`, `error` or `""`.
- `rows` (lists): `[name, value, bad]`, a fourth `true` for a row grey on purpose (a host off by
  design); at most 50.
- Optional: `subtitle`, `url` (the service's own page), `links` (`[{label, url}]`), `as_of` (Unix
  time of the data), `maintenance_until`.
- `actions`: the buttons that make sense now (no START for what runs): `{name, label, confirm,
  danger, choices?}`. Ask `confirm` before sending; with `choices`, offer those instead
  (`{label, action, danger}`), each sent as its own action.
- `sections` (only from `/api/v1/card/<id>`): the panel, `[{title, rows: [[name, value, bad]]}]`.

A page with `games: true` has no fixed cards: its `servers` are the game servers, as the wall's
tiles show them (the same function, checked by a test): `{id, name, game, state, running, title,
line, players, max, address, ram_mb, ram_max_mb, uptime, map, version, actions}`. `title` and
`line` are the tile's two lines ("Minecraft", "3/20 players" or "stopped"); `actions` are like a
card's, from the Pterodactyl agent when it has the server ticked; `players`, `max`, `map` and
`version` are null when the game has no public query or did not answer.

Times in the texts (`note`, rows) are written in labhud's own time zone.

### Acting

With the token of a phone signed in as the admin (and `X-Labhud-Unlock`, with a PIN), the
display's own routes work from the app:

| route | body | |
|---|---|---|
| `POST /action/<name>` | `{}` | a signed action (needs `LABHUD_ACTION_SECRET`); the answer is the agent's |
| `POST /api/action-log/<name>` | `{"result": "accepted\|done\|failed\|refused", "took": seconds}` | how it went, for the timeline |
| `POST /api/maintenance/<card>` | `{"minutes": n}` (0 ends it) | needs `[maintenance] buttons = true` |

Each is JSON (`Content-Type: application/json`); 403 `{"ok": false, "error": "forbidden"}` for
every refusal. Actions need `LABHUD_ACTION_URL` and `LABHUD_ACTION_SECRET` on the server
(`info.actions`).
