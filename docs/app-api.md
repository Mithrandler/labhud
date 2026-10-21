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

Notifications go through ntfy, the app, or both: `via` in `[notify]`, chosen in ⚙ > Alerts (or by
the app, signed in as the admin, with `POST /api/setup/alerts-save`). With ntfy,
`LABHUD_NOTIFY_FORMAT=ntfy` and `LABHUD_NOTIFY_CLICK=app` make a tap open the app on the card it is
about. The app's own come from `GET /api/v1/notifications` (below), the same messages after the
same quiet hours.

**Instant notifications without the app running** use [UnifiedPush](https://unifiedpush.org): the
app registers with a distributor on the phone (the ntfy app is one; its Play version rides on
Google's push) and leaves the endpoint it got with `POST /api/v1/push`. When a message enters the
outbox and `via` names the app, labhud POSTs `{"labhud":"news"}` to every endpoint: no title, no
text, so neither the push server nor Google learns what happened. The app wakes, reads
`/api/v1/notifications?after=` itself over the home network or VPN, and shows it.

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
When the phone is signed in as the admin, the page is the admin too (no second sign-in):
`/api/admin/check` answers for it with the id `phone:<name>`, and Sign out on that page signs the
phone out.

### Pairing

`POST /api/v1/pair` with `{"code": "<c from the link>", "name": "Pixel 6a"}` (JSON, under any
Host). Answers `{"ok": true, "token": "<id>.<secret>", "name": "Pixel 6a", "info": {…}}` once;
403 for a wrong, used or expired code. The name is the one given to `init.py pair` if any, made
unique ("Pixel 6a 2").

The link is `labhud://pair?u=<labhud's address>&c=<code>[&fp=<sha256>]`. `fp`, present when
labhud serves HTTPS itself (the default), is the SHA-256 of its certificate (DER, hex):
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
| `POST /api/v1/push` | `{"endpoint"}` | `{ok}`: this phone's UnifiedPush endpoint (https only; `""` forgets it). Any paired phone; 400 for an address that is not https |

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
| `GET /api/v1/uptime` | `{step, cards: {id: [values]}, percent: {id: 99.4}, coverage: {id: 87.5}}`: per card with a check, one value per 15 minutes, oldest first: the share of that time it was up (0..1), `-1` off on purpose (schedule, `on_demand`, `wake`, maintenance), `null` not known (labhud was off; show it grey). `?range=24h` (default) or `?range=7d`; `percent` is over the range, leaving out the off and unknown slices; `coverage` is the share of the range that is known at all: show it next to the percent when under 100 ("99.2% · 87% covered"). A guest off with `onboot` off is down unless the card has `on_demand`; a host whose agent has been quiet for 5 minutes is down |
| `GET /api/v1/sparks` | `{step, series: {key: [values]}}`: the last 30 minutes (one value a minute, oldest first) of every number with a trend, for the cards' sparklines in one request; a series with nothing known in that time is left out |
| `GET /api/v1/events` | `{events: [{t, name, bad, card?, state?}]}`, newest first |
| `GET /api/v1/devices` | `{pairing, devices: [{name, paired, seen, admin, push}]}`: the paired phones (`push`: it left a UnifiedPush endpoint) |
| `GET /api/v1/notifications` | `{via, quiet, notifications: [{id, time, title, text, bad, card}]}`: the last 100 messages LabHUD sent (or would send) as notifications, oldest first, after the quiet hours did their part; `?after=<id>` only the newer ones (ids rise). Show them when `via` is `app` or `both`. The outbox lives in memory: after a restart it starts empty |

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
    "icon": "proxmox", "size": "l",
    "metrics": [{"key": "proxmox.pve.cpu", "label": "CPU", "value": "20%", "sev": "", "trend": true,
                 "format": "percent", "pct": 20.0, "warn": 80, "crit": 92}],
    "charts": [{"key": "agents.pve.sensors.cpu_temp", "label": "CPU temp", "format": "celsius"}],
    "subtitle": "i5-12500 · 64 GB", "url": "https://192.0.2.10:8006",
    "actions": [], "maintenance_button": true
  }],
  "problems": ["vm-pve-106"],
  "alerts": [{"id": "demo-disk", "text": "Disk on nas-backup above 85% in 12 days"},
             {"id": "rule:vm-pve-100:disk:1760000000", "text": "docker disk 92%", "card": "vm-pve-100", "since": 1760000000}]
}
```

A card:

- `status`: `up`, `down`, `scheduled` (off on schedule), `on-demand` (off, started when needed),
  `maintenance`, `unknown` (not known yet), or `null` (the card has no status).
- `bad`: red on the display (down, or a number past its critical mark), `null` while nothing about
  it is known. `problems` lists the red ones in page order.
- `kind`: `guest` (a Proxmox guest, a small tile), `list` (rows, in `rows`) or `card`.
- `type`: what machine it is: `proxmox`, `nas`, `docker`, `vm`, `pc`, or `""` for a card that is
  not one (a list, a number). The card's own `kind` in the config, else a guess (⚙ > Pages >
  Organize by type uses the same).
- `metrics[].value` is formatted as the display shows it; `sev` is `""`, `warn` or `crit`;
  `trend` says the key has a history (`/api/v1/history`); `format` is the config's (`percent`,
  `percent1`, `celsius`, `used_of`, `bytes`, `rate`, `count`…). For `used_of` the history holds
  the percentage, not the pair.
- `metrics[].pct` (0 to 100) is there for a percentage (`percent`, `percent1`, `used_of`): the length of its bar. `warn` and `crit` are where the bar turns orange and red, there only when a high value is bad (CPU, RAM, disk: `[display] warn`/`crit` in config.toml, 80 and 92 unless set; or the metric's own `warn`/`crit`).
- `icon` names the card's icon: a file `/icons/svc/<icon>.png` (96 px; `.svg` for the web) when the server has one (it carries about 340 and fetches the others from the dashboard-icons pack on the first request, then keeps them; `[display] icons_online = false` turns that off), else one of labhud's line icons (`server`, `nas`, `vm`, `pc`, `app`, and the others in design/tokens.json). Its own `icon` in config.toml or the `labhud.icon` label, else its source's, else its kind's; missing when none fits.
- `size` is the card's place in the grid when the config sets one (`s` 1×1, `m` 2×1, `l` 2×2, `xl` 4×2 cells); missing means `s`.
- A card or a group the config marks `hidden` is not in `state` at all (not in `pages[].groups`, `cards` or `problems`); the alerts of its card (`alerts[]`) stay. A group whose cards are all hidden is left out too. A page may carry `columns` (1 to 6: at most that many columns of cells) and `density` (`compact` or `normal`); missing means the app's own choice.
- `theme` is the board's look, for every screen that has not picked its own: `base` (`oled` or `grey`, the colours behind the cards) and `accent` (a name: `blue`, `green`, `orange`, `red`, `violet`, `cyan`, `pink`, `yellow`, or `#rrggbb`). Set in ⚙ > Dashboard > Theme (`[display] base`, `accent`).
- `role` (`viewer`, `operator`, `admin`) is the role of the token or phone that asked, and `[roles.<role>] pages = [...]` in config.toml limits `pages` (and so `cards` and `problems`) to those pages for that role; a role with no table sees them all. It only decides what is shown: the alerts of a card a role does not see stay in `alerts[]`, and `/api/v1/card/<id>` answers 404 for it. A phone that is not signed in as the admin is a viewer.
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
- `containers` and `agent` (only from `/api/v1/card/<id>`, a card whose own agent has Docker):
  `[{name, up, status, update, updating, actions, image, icon}]`, stopped ones first, `actions` as above
  (UPDATE only with `update`, none while `updating`); `image` is the image it runs and `icon` the logo
  of its program (`/icons/svc/<icon>.png`), "" when none is known. The admin reads one's last lines with
  `POST /api/setup/logs {agent, target}` → `{ok, output}` (only ticked containers).
- `charts` (a card with an agent of its own): its agent's numbers that have a history, for the
  host's charts beside `metrics` with `trend` (leave out a label the metrics already chart).

`alerts`: the config's own list (`[alerts] path`) and what the `[[alert]]` rules find now, those
with the `card` they are about and `since` (Unix time).

A page with `games: true` has no fixed cards: its `servers` are the game servers, as the wall's
tiles show them (the same function, checked by a test): `{id, name, game, state, running, title,
line, players, max, address, ram_mb, ram_max_mb, uptime, map, version, actions}`. `title` and
`line` are the tile's two lines ("Minecraft", "3/20 players" or "stopped"); `actions` are like a
card's, from the Pterodactyl agent when it has the server ticked; `players`, `max`, `map` and
`version` are null when the game has no public query or did not answer. `icon` (labhud 0.1.x) is the
game's logo, "" when none is known.

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

## The logo

`/brand/mark.svg` (the mark alone, no background), `/brand/lockup.svg` (the mark and the name, for a dark background), `/brand/lockup-light.svg` (for a light one) and `/brand/mark-mono.svg` (one colour, for small sizes). `GET /api/brand/about` gives `{name, version, logo, mark, links}` for an About screen, and the update check (`updates.py`, every six hours, `[update] check = false` turns it off): `latest` (`{server, agent, app}`, the newest release tags, "" when not known), `update` (the server is behind its latest release), `outdated` (`{agent, app}`: the agent this server pins, and the app, which says its version in an `X-Labhud-App` header, are behind), `checked` (Unix time of the last read, 0 before the first) and `enabled`.

## API tokens

For scripts and other tools (Home Assistant, cron jobs): `Authorization: Bearer lh_…`. A token is
`lh_` followed by 43 URL-safe characters (32 random bytes); labhud keeps only its SHA-256 and
shows the token once, when it is made (⚙ > System > API & audit, or the app's Settings > API &
audit). Each has a name, one scope, an optional expiry, and the time and address of its last use.

| scope | may |
|---|---|
| `read` | the GETs of `/api/v1/` that show state (`state`, `card/<id>`, `history`, `sparks`, `uptime`, `events`…); the token stands in for the `LABHUD_HOSTS` check, like a phone's |
| `act` | + the cards' buttons (`POST /action/<name>`) and maintenance, without the screen's PIN |
| `admin` | + everything ⚙ does (`/api/setup/*`, `/api/agents/*`, the routes of `api/`) and `GET /api/v1/audit` |

A scope that does not cover a route gets the same 403 `{"ok": false, "error": "forbidden"}` as any
refusal, and the refusal is written to the audit log.

```sh
curl -H "Authorization: Bearer lh_…" https://labhud.example/api/v1/state
```

All three are `POST`, JSON, behind ⚙'s guard (the admin signed in and unlocked, an admin token,
or the `labhud` command):

| route | body | |
|---|---|---|
| `POST /api/tokens/list` | `{}` | `{tokens: [{id, name, scope, made, expires, last, ip, expired}], enabled, scopes}` (never the token or its hash) |
| `POST /api/tokens/create` | `{"name": "…", "scope": "read\|act\|admin", "days": n or null}` | `{token, id, …}`: the only time the token is sent; 400 with `error` for a bad name, scope or expiry, a name already used, or more than 32 |
| `POST /api/tokens/revoke` | `{"id": "…"}` | ends it at once; 404 for an unknown id |

## Audit log

Who did what: one JSON line per entry in `LABHUD_DATA/audit/<YYYY-MM-DD>.jsonl` (a file per UTC
day, kept 90 days):

```json
{"ts": "2026-10-18T13:01:36Z", "who": "admin", "action": "setup/save-text", "target": "", "ip": "192.168.1.20", "ua": "Firefox on Linux", "ok": true}
```

- `who`: `admin` (a browser signed in), `phone <name>`, `token <name>`, `cli` (the `labhud`
  command) or `anonymous`
- `action`: the path of a ⚙ call without `/api/` (`setup/save-text`, `agents/update`…),
  `action` (a card's button, `target` = the action's name), `sign-in` (`target` = `password`,
  `phone`, `pair` or `oidc`), `token-create`, `token-revoke`
- `ok`: false for a refusal (a wrong password, a scope too small, a locked screen) or an action
  that failed
- ⚙'s frequent reads (`setup/status`, `agents/list`, `tokens/list`…) are written only when
  refused. Never a password, ticket, token or code.

`GET /api/v1/audit` (the admin: a browser's ticket, a phone signed in as the admin, an admin
token, the `labhud` command) answers `{rows, more, offset, limit, enabled, days}`, newest first.

| parameter | |
|---|---|
| `who`, `action` | a part of it, any case |
| `ok` | `1` or `0` |
| `from`, `to` | a day (`2026-10-18`) or a moment (`2026-10-18T13:05`), UTC, both included |
| `offset`, `limit` | paging; `limit` at most 500 (default 100) |

A bad `from`/`to`/`offset`/`limit` is a 400 with `error`.

## Single sign-on and ⚙ > Security

### Config

In `config.toml`:

```toml
[admin]
session_hours = 720          # 1..720: how long a browser stays signed in without use (default 30 days)
trusted_proxies = ["172.18.0.0/16"]   # X-Forwarded-For is believed only from these peers
admin_networks = ["192.168.1.0/24"]   # the admin signs in and acts only from these (empty: anywhere)

[admin.oidc]
issuer = "https://auth.example/application/o/labhud/"
client_id = "…"
allow = ["demo"]               # user names (preferred_username), emails (verified) or subjects
allow_groups = ["labhud admins"]   # the `groups` claim; at least one of allow / allow_groups
redirect_uri = "https://hud.example/api/admin/oidc/callback"   # optional; default: the address the request came to
```

The client secret is `LABHUD_OIDC_CLIENT_SECRET` in secrets.env (set from ⚙ > Security), never in config.toml. `[lock] idle` (minutes, 1..120) is the PIN's idle time, also set from ⚙ > Security.

### `/api/config`

`admin` is now `{on, oidc}`; `oidc` is true when single sign-on is configured.

### The sign-in flow

Authorization code + PKCE S256, with state and nonce. The id_token is RS256, checked against the provider's JWKS: `iss`, `aud`, `azp`, `exp`, `iat`, `nbf`, `nonce`.

| route | body | answer |
|---|---|---|
| `POST /api/admin/oidc/start` | `{mode}` | `{ok, url, bind}` |
| `GET /api/admin/oidc/callback` | `?code&state` (the provider sends the browser here) | `303` to `/?oidc=<r>` (page) or `labhud://oidc?r=<r>` (app). On failure: `/?oidc_error=<why>` or `labhud://oidc?error=<why>` |
| `POST /api/admin/oidc/finish` | `{r, bind}` | page: `{ok, ticket, idle, user, sso}` (the same admin session as password+code; `ticket` goes in `X-Labhud-Admin`); app (with its Bearer token, the same phone that started): `{ok, device_admin: true, pin, unlocked}` — the phone is now signed in as the admin, exactly as after `/api/v1/admin-login` |

- `mode` is `"web"` (only from LabHUD's own page, Origin checked) or `"app"` (only with the phone's `Authorization: Bearer` token).
- The caller keeps `bind` secret (the page in sessionStorage, the app in memory) and opens `url` (the app in the browser / a Custom Tab).
- `r` works once, within 2 minutes; a started sign-in lasts 10 minutes.
- The `why` on failure is one of: `denied` (the user said no at the provider), `refused` (signed in at the provider but not in `allow` / `allow_groups`), `failed` (bad or expired state, bad token, …), `network` (outside `admin_networks`), `off`.
- Anything else: `403 {"ok": false, "error": "forbidden"}`.

### ⚙ > Security

All POST, JSON, admin + unlocked, from an admin network; same guard as every ⚙ call.

| route | body | answer |
|---|---|---|
| `POST /api/security/get` | `{}` | `{kept, session_hours, pin_minutes, pin_on, trusted_proxies, admin_networks, your_ip, peer, via_proxy, oidc: {issuer, client_id, allow, allow_groups, redirect_uri} or null, secret_set, callback}` |
| `POST /api/security/save` | any of `session_hours`, `pin_minutes`, `trusted_proxies`, `admin_networks`, `oidc` (`{issuer, client_id, secret, allow, allow_groups, redirect_uri}` or `null` to turn it off) | `{ok}`; `409` before the config is taken over; `400` with `error` for a bad value or a change that would lock the caller out (its address no longer in `admin_networks`) |
| `POST /api/security/oidc-test` | `{issuer?}` | `{ok, authorization_endpoint, keys}` or `{ok: false, error}` |
| `POST /api/security/remove-phones` | `{confirm: "REMOVE"}` | `{ok, phones}`: every paired phone's token ends at once |
| `POST /api/security/new-keys` | `{confirm: "NEW KEYS"}` | `{ok, agents, phones}`: every agent (and its webhooks) and every phone removed, the `labhud` command's key renewed; each agent is added again with a new code and each phone paired again |

### Networks

Outside `admin_networks`, the admin sign-ins (`/api/admin/login`, `/api/v1/admin-login`, `/api/v1/login-pair`, OIDC) answer 403, and a ticket or an admin phone acts as if not signed in.

The client address is the TCP peer, unless the peer is in `trusted_proxies`: then X-Forwarded-For is read from the right, skipping the trusted proxies. The `labhud` command on loopback is not concerned.
