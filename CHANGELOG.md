# Changelog

All notable changes to labhud. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Before 1.0.0 the config format may
still change in a minor version; such changes are listed under **Changed** with what to edit.

## [Unreleased]

## [0.1.1] - 2026-10-22

Needs labhud-agent 0.1.1 for the live log, the terminal, stacks and the host's Docker.

### Added

- **Live log and terminal** (agent 0.1.1+): **LIVE** in a container's panel follows its log, **SHELL** opens a
  terminal (xterm.js, vendored) in it. The terminal is off by default in three places (the agent's
  `LABHUD_AGENT_TERMINAL=on`, the *Terminal* tick, the container's tick), only for the admin on a page or phone
  with a typed PIN, 15 minutes at most, 3 idle, nothing stored, every open and close in the audit log.
  `POST /api/setup/stream-open|read|input|resize|close` (the page polls; each poll is held up to 20 s).
- **Stacks (docker compose)** (agent 0.1.1+, off unless the agent has `LABHUD_AGENT_STACKS`): ⚙ > Containers lists an agent's
  stacks with EDIT / REDEPLOY / RESTART / DOWN and a *New stack* editor (compose file + `.env`); the files travel
  sealed, a `.env`'s values are never read back, every action is audited. `labhud agent stack …`.
- **Icons on rows and pages** in `/api/v1/state`: a list row is `[name, value, bad, quiet, icon]` (the last two only
  when there is something to say) and a page carries the `icon` the config gave it.
- **The host's Docker, whole** (agent 0.1.1+): ⚙ > Devices > Agents > *Docker host* lists the images, volumes and
  networks with sizes and what uses them, and cleans each kind on its own: unused images, untagged layers,
  unused networks, anonymous volumes (never a named one). `labhud agent inventory <agent>` and
  `labhud agent prune <agent> [images|dangling|networks|volumes]`; `POST /api/setup/inventory` and
  `/api/setup/prune {agent, what}`. The agent's answer is rebuilt field by field before it is shown.
- **Small widgets**: a card can be a `widget = "clock"` (own `timezone`), a `widget = "calendar"`, or show a
  page with `frame = "https://…"`. The browser draws them alone; the CSP lets through only the frame's origin.
- **Command palette**: the search box (🔍 or `/`) also finds pages, the sections of ⚙ and the cards'
  buttons (`RESTART · web`), best match first; `>` looks only among the commands. The buttons and ⚙
  appear only to who may act (signed in as the admin and unlocked; the admin hands that on with an
  operator token or role), and a button asks as in the card's panel.
- **Four more services**: Portainer, Paperless-ngx, Tautulli and Mealie are sources, with a form and a
  connection test in ⚙ > Integrations, a first card, and a place in the network scan. Read-only keys.
- **Discovered, to approve**: ⚙ > Dashboard > Discovered lists the Proxmox guests and the containers
  (and, after a network scan, the services) that have no card, to add one by one or all at once
  (into a page "Discovered", group "Found") or dismiss; nothing is added by itself
  (`/api/discover/list`, `approve`, `dismiss`, `restore`; admin, audited).
- **Theme per board and pages per role**: `[display] base` (`oled`, `grey`) and `accent` set the theme
  of every screen that has not picked its own (⚙ > Look has a new "Board" choice; a screen that picked
  one keeps it), from ⚙ > Dashboard > Theme (`POST /api/boards/theme`), and `/api/v1/state` carries
  it as `theme`. `[roles.viewer|operator|admin] pages = [...]` (⚙ > Dashboard > Pages per role,
  `POST /api/boards/roles`) limits what each role sees in `/api/v1/state`; the alerts of a card it does
  not see stay active. Both need the admin and are heard by the audit log.
- **Hidden cards and groups, columns and density per page**: `hidden = true` on a card or a group
  keeps it off every screen (it is not in `/api/v1/state`; its alerts stay active); a page takes
  `columns` (1 to 6) and `density` (`compact` or `normal`), both in `/api/v1/state`. All three are
  set from ⚙ > Dashboard (a card's, group's and page's ⋯ menu), kept by Export and Import.
- **Boards as files**: ⚙ > Dashboard > Export and import (`POST /api/boards/export`, `/api/boards/import`)
  writes the pages, groups and the cards' places, kinds, icons and sizes to a JSON file and reads
  one back, showing first what it drops (cards this labhud does not have) and what it keeps in a
  group "Unplaced" (cards the file leaves out). No address or key leaves labhud.
- **Roles for API tokens**: ⚙ calls the scopes viewer (`read`), operator (`act`) and admin; the API
  takes either name and lists both (`role`, `roles`).
- **Audit retention**: `[audit] keep_days` (7 to 730, default 90), set from ⚙ > System > API & audit
  (`POST /api/audit/settings`, written to the kept config, no restart) and read back as `keep_days`.
- `GET /api/v1/history` (and `/api/history`) take `?keys=a&keys=b`: only those series, so a card
  draws its own without the whole lab's history. An older labhud ignores it.
- **Icons for machines and containers**: about 340 shipped (distros, Windows, the usual homelab
  apps), and any other icon of the dashboard-icons pack fetched once on the first request and kept
  in `LABHUD_DATA/icons` (`[display] icons_online = false` never asks). A Proxmox guest gets its
  OS's icon (or a `icon-<name>` tag's), a container its image's, a game its logo when the pack has
  it, a card named after a service the service's; `/api/v1/card` containers carry `image` and `icon`.
- **Update available**: every six hours labhud reads the latest releases of itself, its agent and
  its Android app from GitHub. ⚙ > System > About says "Update available: v0.1.1 · release notes",
  the account button and its Settings entry get a dot, and `/api/brand/about` carries `latest`,
  `update` and `outdated` for the app. The same news goes out once per version as `update_available`
  (tick it in ⚙ > Notifications). `[update] check = false` turns it all off.

### Fixed

- ⚙ > Dashboard: the icon next to a card's or a page's name (`lay_icon`) was a plain `<img>` of
  `/icons/svc/<name>.svg`, so a name of a line icon (`vm`, `nas`…) gave a 404; it is the card's
  own icon now.

### Changed

- Inside: one `util.py` for the hash, the clean name, the free name and the pairing code that the
  phones, the agents, the API tokens and the single sign-in each had their own copy of; three
  functions nothing called and one wrapper are gone, and so are the "package F/G/H/I" markers.

## [0.1.0] - 2026-10-21

First public release: the display as it runs in production, generalized, with what Portainer,
Dockhand, Homarr, Homepage and Beszel had and labhud did not (much of it through the agent, whose
release is 0.1.0 too).

### Added

- **Single sign-on (OIDC)** in ⚙ > Security: sign in with Authentik (or any OpenID Connect
  provider); the password and the authenticator stay as the way back in. `[admin.oidc]` in
  `config.toml`, the client secret in `secrets.env`. The app can sign in the same way.
- **The session's length, the PIN's idle time and the admin's networks** in ⚙ > Security:
  `[admin] session_hours`, `trusted_proxies` and `admin_networks`; a change that would leave the
  browser outside is refused.
- **API tokens** (`read`, `act` or `admin`, with an optional expiry) and **the audit log** (who did
  what, when and from where) in ⚙ > System > API & audit; a token is shown once, never a secret.
- **Private registries and image scans** in ⚙ > Containers: the logins labhud gives the agents when
  they pull (`[[registry]]` in `config.toml`, the token in `secrets.env`), and the CVEs of each
  image (trivy or grype on the host), with an update held back when the new image has more
  critical CVEs.
- **Five channels of notifications** (email, webhook, Discord, Gotify, the app) and the matrix of
  which channel gets what, in ⚙ > Notifications > Events.
- **Backup and restore** in ⚙ > System: `config.toml` and LabHUD's data in one file, the secrets
  only encrypted, with a password; a daily backup keeps the last few.
- **CrowdSec: several instances** on one card (`CROWDSEC_URL = "router=http://…,vps=http://…"`, the keys
  named the same); `crowdsec.by` per instance, a ban on your own address says where, and an instance
  that does not answer is a red row while the others are still read.
- **The groundwork for the new cards and ⚙** (nothing on screen changes yet):
  - a card's `icon` and `size` (`s`, `m`, `l`, `xl`; `large = true` is `l`), a page's `icon`, the
    Docker labels `labhud.icon` and `labhud.size`; every card gets an icon (its own, its source's,
    its kind's), from 60 service icons kept in `static/icons/svc` (dashboard-icons, Apache-2.0) or
    five new line icons;
  - `[display] warn` / `crit` (80 / 92) for CPU, RAM and disk, and a metric's own `warn` / `crit`,
    the same on the wall, in the app and in the notifications; a metric's `pct`, `warn`, `crit`
    in `/api/v1` for its bar;
  - `GET /api/v1/sparks`: every trend's last 30 minutes in one request;
  - the logo in `static/brand` (the mark, the mark with the name for dark and light, one colour)
    and `GET /api/brand/about`;
  - sizes in `design/tokens.json` (gaps, corners, type), ⚙'s styles in `settings.css`, ⚙'s
    sections in one list (`static/settings/registry.js`), routes added by modules in `api/`.
- **Containers updated on a schedule**: ⚙ > Devices > Agents > Docker > *Update on their own*:
  ticked containers labhud updates by itself when their image has a newer one, in a window (days,
  time; every day at 04:00 by default); `labhud agent schedule` and `labhud agent window`. With
  agent 0.1.0, `LABHUD_AGENT_AUTO_LABELS` (e.g. `dockhand.update=false`) marks them for another
  updater to leave alone.
- **Search**: 🔍 in the top bar (or "/"): every card on every page and the bookmarks; Enter opens
  the card's Web UI or its panel; what matches nothing goes to `[search] url`.
- **Bookmarks**: `[[bookmark]]` (group, name, url) on a card with `bookmarks = true` and in the
  search.
- **Notes**: a card with `notes = true` shows a text written in its panel (the admin) or in the
  app, kept across restarts.
- **Cards from Docker labels**, as Homepage does: `labhud.name` on a container (agent 0.1.0) gives
  it a card, on `labhud.page` / `labhud.group` or a DISCOVERED page, with `labhud.url` and
  `labhud.description`; config.toml stays as it is.
- **Webhooks for CI**: a secret URL per container (⚙ > Devices > Agents > Docker > Webhooks); a POST
  to it updates that container, as UPDATE does, at most once in 30 s. labhud keeps only its hash.
- **The fleet's updates**: `list = "fleet.updates"`, every container with a newer image on every
  host, with UPDATE and UPDATE ALL in the card's panel; `fleet.update_count`.
- **A container's DETAILS** (the admin): ports, volumes, networks, restart policy, labels, the
  environment by name only (agent 0.1.0).
- **Four integrations**: CrowdSec (its own bans, and a ban on your own address in red), Gitea
  Actions (the latest CI runs, failed first), Nextcloud (version, updates, users, space), authentik
  (failed logins of the day, outposts that stopped checking in). Read-only keys; docs/integrations.md.
- **Containers that stop by themselves**: one that stops with an error (no one stopped it), runs
  out of memory, or keeps stopping (3 times in 10 minutes) is an event on the timeline and a
  notification, like a host going down.
- **Each container's history**: its updates (from which version to which) and its crashes, the last
  10, on its card's panel; the latest of the last 7 days under its row in a guest's panel.
- **Compose stacks**: a guest's containers grouped by compose project, with **RESTART STACK** when
  every running container of it is ticked with RESTART.
- **CPU and RAM per container** (the ticked ones that run), on their rows and their cards; also
  `agents.<name>.containers.<c>.cpu` / `mem` / `mem_pct` / `net_rx` / `net_tx` for metrics.
- **Clean unused images**: the docker target `images` (ticked on its own, never by "Tick all"),
  then ⚙ > Devices > Agents > *Clean unused images* or `labhud agent prune <agent>`: the images no
  container uses, unused for a day at least, and the timeline says what that freed.
- **Failed systemd units**: the alert rule `failed` (on by default, after 5 minutes), for every
  unit of a host in the failed state.
- **A year of history**: the charts in a card's panel have 30d and 1y, from a third series (an
  average every 2 hours, kept like the others; `/api/history?year`).
- **Agents update themselves.** ⚙ > Agents shows *Update to vX* on an agent older than the release
  labhud pins (also `labhud agent update <agent>|--all`), and *Update agents automatically* does
  it as each one dials in. labhud only names the version and its SHA-256: the agent (0.1.0 or
  newer, installed as a service) downloads it from its own releases, checks it against the
  release's SHA256SUMS too, and takes only a newer one. A container agent is updated by its image.
- **Uptime in a card's panel**: the last 24 hours or 7 days as slices, and the share of time up,
  as the app's tiles show it (`/api/uptime`).
- **The demo has agents** in ⚙ > Devices: the docker VM's and the Proxmox node's, with what they
  found; their ticks can be tried (kept until the demo restarts), and one has an update waiting.
- **Alert rules on hosts, set in ⚙ > Alerts.** `[[alert]]` rules (`alerting.py`): a host's CPU,
  RAM, disk or temperature (the hottest of CPU and GPUs) past a mark for some minutes, or its agent
  gone for some minutes, on one card or on every card with an agent of its own (`on = "*"`). The
  defaults, without any rule: CPU and RAM above 90% for 10 min, disk above 90%, temperature above
  85 °C for 5 min, offline for 5 min. A problem clears only under the mark minus a margin; cards
  in maintenance, off on schedule or started on demand are skipped, and the fixed marks of
  `thresholds.py` leave the cards a rule watches alone. What is wrong now is in the bell (and in
  `/api/v1/state`'s `alerts`); every change is in the history.
- **`[notify]`: ntfy, the app, or both, and quiet hours.** `via` says where notifications go;
  `quiet_from` / `quiet_to` (23 to 7 by default): in them only urgent news goes out (offline,
  disk, temperature, a host down), the rest waits and goes out as one message when they end. ⚙ >
  Alerts sets both, and the ntfy topic and token (Test, Save to `secrets.env`, Forget).
  `GET /api/v1/notifications?after=<id>`: the messages, for the app to show itself.
- **Instant notifications in the app, through UnifiedPush.** `POST /api/v1/push {endpoint}`: a
  phone leaves the endpoint its distributor gave it (the ntfy app is one). When a message is due
  and `via` names the app, labhud POSTs `{"labhud":"news"}` there, with nothing of the news in it,
  and the app reads the message itself over the home network or VPN. An endpoint its push server
  calls gone (404, 410) is forgotten; `/api/v1/devices` says which phones have one.
- **`type` on every card in `/api/v1/state`**: proxmox, nas, docker, vm, pc or "" (the card's
  `kind`, else the same guess as Organize by type), for the app's Devices.
- **A host's charts from its agent**: a card with `agent = "<name>"` charts the agent's CPU, RAM,
  disk, temperatures and network (what the agent sends) in its panel, beside its own numbers, and
  `/api/v1` gives them as the card's `charts`. The panel's charts gain **1h** (1h, 6h, 24h, 7d).
- **The `labhud` command** ([docs/cli.md](docs/cli.md)): `docker exec -it labhud labhud …` does
  what ⚙ does, through the same routes. It works on the admin, PIN, sources (list, test, add,
  remove), agents (list, code, allow, deny, logs), phones (pair, list, remove), pages (list,
  move), organize, config (show, edit, take-over), scan, alert rules (list, add, remove) and
  the ntfy topic (set, test, forget). It proves who it is with a key the running server writes at each start into
  `LABHUD_DATA/cli.json` (mode 600), taken only from loopback. In ⚙, the **?** beside a
  section's title shows its commands.
- **Containers: update available, UPDATE, LOGS.** A container ticked on an agent shows **update
  available** when its registry has a newer image (the agent checks 2 minutes after it starts,
  then every 6 hours), then **UPDATE**: the agent pulls it and makes the container again with
  the same settings, the old one kept until the new one runs. The press is followed until the
  agent's own word on it ("updated", or why not); meanwhile the row keeps its buttons, greyed,
  with UPDATING… in place of UPDATE. The admin gets
  **LOGS** on each ticked container: its last 200 lines under the row, with Refresh. On a
  guest's rows and on a card with `agent_target = "docker:<name>"`; in `/api/v1/card/<id>` as
  `containers`, and `POST /api/setup/logs {agent, target}` for the admin. On a narrow screen a
  container's buttons go under its name.
- The demo's docker VM has a made-up agent (charts, a disk alert, containers with an update and
  logs).
- ⚙ > Sources names your own JSON sources: "Live agent (your own JSON)" with what it feeds, not
  just "live"; `LABHUD_JSON_<NAME>_TITLE` names any of them.
- **⚙ > Advanced: the config file, edited in ⚙.** The editor opens inside ⚙ (no new tab, which a
  kiosk browser does not open), checks as you type and saves only a config that loads. **Take
  over** copies a mounted config (read-only on most installs, so Edit config could not save) into
  `LABHUD_DATA/config.toml`, with its included files merged in; labhud restarts in place (same
  PID) and from then on keeps that copy, which ⚙ writes. Keys typed into ⚙ go to
  `LABHUD_DATA/secrets.env` (mode 600) and win over the environment, except the settings that say
  how labhud starts (`LABHUD_DATA`, `LABHUD_HOSTS`, the port, TLS, actions). Routes:
  `POST /api/setup/{status,text,save-text,config,save,secrets,take-over,catalog,try}`, for the
  admin only (like ⚙ > Devices). `init.py edit` and `/edit` stay, for the terminal.
- **Game servers in the app's API.** A games page in `/api/v1/state` carries `servers`: each game
  server as the wall's tile shows it (title, line, players, address, RAM, uptime, buttons), from the
  same function as the wall's (`game_card()`, checked against `cardstate.py` by the parity test);
  `GET /api/v1/game/<id>` adds who is playing.
- **Agents** ([docs/agents.md](docs/agents.md)): labhud-agent, one per host, dials labhud on
  `/api/agent/ws` and acts only through the host's own APIs: Docker, systemd, reboot and package
  updates, Wake-on-LAN, Proxmox guests, Pterodactyl servers. Paired from a one-time code in the
  new **⚙ > Agents**, where each capability and each target is ticked; nothing is allowed before.
  The protocol ([docs/agent-protocol.md](docs/agent-protocol.md)): the key is derived from the
  code on both sides, every frame is signed, test vectors shared with the Go agent.
- Proxmox guest cards get the agent's START / SHUT DOWN / REBOOT when the guest is ticked; new card
  keys `agent` and `agent_target` for a host's or a target's buttons, sensors and state.
- `/agent/install-agent.sh` installs the Go agent as a service, checking it against the SHA-256
  labhud pins (`agents/agent-release.json`); `LABHUD_AGENT_RELEASES` and `LABHUD_AGENT_IMAGE`
  change where it and the image come from.
- `?static&settings=<tab>` opens ⚙ on a tab, for screenshots.
- `install-agent.sh --ptero-pin <sha256>`: a Pterodactyl panel with a self-signed certificate.
- A Proxmox guest card with `agent = "<name>"` (the agent inside the guest) lists that agent's
  containers in its panel, with START / STOP / RESTART on each one ticked in ⚙ > Agents; from its
  own agent such a card gets only UPDATE (Proxmox already gives START, SHUT DOWN and REBOOT).
- A games page takes a server's buttons from the agent that has it ticked (Pterodactyl), so the
  config's `game-*` actions and the action agent are no longer needed for it.
- The snapshot's `agents.<name>.buttons`: the ticked targets' action names, for pages that list
  containers, units or game servers themselves.
- **Copy** buttons beside everything there is to copy: the agent's code and install commands, a
  phone's pairing link, the admin command, the setup page's commands, key and files. They work on
  plain http too (where the browser has no clipboard API).
- Card key `wake = "<agent>:<mac>"`: WAKE from another agent, next to the buttons of the card's own
  `agent` (a host that is off cannot wake itself).
- ⚙ > Agents: **Tick all / Untick all** for a capability's targets (a host with 28 containers).
- ⚙ > Agents > Wake-on-LAN takes a MAC typed in: a machine that is off is not in the agent's ARP
  table, so it could not be ticked.
- **Sign in at the top right.** Signed in, the admin's name there opens a menu: Settings, Edit
  config, Sign out. A sign-in lasts 30 days from its last use on that browser and survives a
  restart (labhud keeps only each session's SHA-256); ⚙ > Security > **Signed in** lists the
  browsers, with Sign out for one or for all.
- **Uptime per card**: every card with a check is sampled each minute (up, down, off on purpose)
  and kept as 15-minute slices over 7 days, with `LABHUD_DATA` across restarts. The app reads it
  from `GET /api/v1/uptime?range=24h|7d`; `info.uptime` says the server has it.
- `metrics[].format` in `/api/v1/state` and `/api/v1/card`, so an app can tell a temperature from
  a share without guessing from the label.
- **The admin**: one account with a password and a code from an authenticator app (TOTP). The
  setup page has a new Admin step (it ends only when a code is right); an existing install gets
  one with `init.py admin` (which also replaces it; `--remove` deletes it). Once there is an
  admin, ⚙ > Admin signs in, and pairing or removing phones and opening the config editor
  (**Edit config** in ⚙, no `init.py edit` needed) need that sign-in. A code works once; wrong
  sign-ins are slowed down like the PIN's. See docs/security.md.
- `POST /api/v1/login-pair`: the app can pair by signing in as the admin, without a QR code.
- ⚙ > Phone app lists the paired phones, with when each was last seen, and removes one
  (asks first; the display's PIN when one is set). `GET /api/v1/devices`, `POST /api/v1/unpair`.
- Pairing for the LabHUD Android app: `init.py pair [name]` (a QR code in the terminal) or
  **Pair a phone** in ⚙ (a QR code on the display) makes a one-time code, valid 5 minutes. The
  app trades it for a token of its own. `init.py devices` lists the paired phones (also on
  `/status`), `init.py unpair <name>` ends one at once. Needs `LABHUD_DATA`.
- A paired device's token (`Authorization: Bearer`) stands in for `LABHUD_HOSTS`, the Origin
  check and the lock's PIN: the app asks for the phone's fingerprint or PIN when it opens. The
  `labhud_device` cookie does the same for the wall page inside the app, Origin still checked.
- `/api/v1/info`, `/api/v1/state`, `/api/v1/card/<id>`, `/api/v1/stream`, `/api/v1/history`,
  `/api/v1/events`: each card's status, red or not, summary, coloured numbers, note, actions and
  panel, worked out on the server exactly as the display does (a test runs both on the demo).
  See [docs/app-api.md](docs/app-api.md).
- `LABHUD_NOTIFY_CLICK=app` (ntfy): a tap on a notification opens the app on that card.
- A lock for a display anyone can walk up to: with a 4-digit PIN set, the screen is only for
  looking. Pages, rotation, night and calm go on, but any touch opens a keypad. The right PIN opens
  the screen until `[lock] idle` minutes (10 by default) pass without a touch; the lock left of ⚙
  closes it at once. The PIN is set, changed or removed in ⚙ (the current PIN is asked first) or
  with `init.py pin`; `init.py pin --clear` removes a forgotten one.
- `[lock] idle = <minutes>` (1–120).
- An action is followed to the end: a bar under the top bar, on every page, says "sending…",
  then "✓ accepted · starting… 0:42" with a filling bar, then "✓ Valheim running" — or, if the
  state has not changed after 5 minutes, "no change after 5 min" in red. The result stays a few
  seconds (a failure 30 s, or until tapped). Game servers, Proxmox guests, Wake-on-LAN and shut
  downs are watched; other actions end at "accepted".
- Every action step also goes to the day's timeline (`POST /api/action-log/<action>`: only
  actions the config offers, from labhud's own page; the text is written by the server).
- The last 7 days under TODAY: for each host that went down, how many times, for how long, and its
  uptime over the history kept. Host events now carry their card and state.
- A card's `links = [{ label, url }]`: buttons at the top of its panel (its UI, its logs...); a
  card's `url` shows there as "Web UI" too.
- The mark next to the clock says exactly what is wrong: for each source that stopped answering,
  its name, every card and page it shows on, its host and whether that host is up, the problem in
  plain words, since when, its last good answer and the next try (`where` in `/api/status`).
- A card's `age = "<path>"` shows how old its numbers are ("as of 40m ago"), for data refreshed
  rarely, such as an hourly check.
- Charts in a card's panel for every number with a history, over 6 hours, 24 hours or 7 days. The
  server now also keeps a 15-minute average for 7 days (`/api/history?long`, kept in
  `LABHUD_DATA` like the rest).
- TODAY: a tap on the clock lists every change since midnight. Up to 400 events are kept (30 still
  travel in the snapshot); all of them at `/api/events`.
- A 3-day forecast: a tap on the weather opens it, with the chance of rain.
- Under the calm clock, chosen per screen in ⚙: the forecast and the first line of any card's list
  (the next film, the latest download, who is playing). The clock stays whole on screen.
- More in ⚙: hide groups or single cards on that screen (a hidden card's problem neither takes
  the screen nor signals), compact / normal / airy spacing, keep the screen on (Wake Lock over
  HTTPS, NoSleep.js's silent video over plain HTTP), and a red flash and/or a beep for a new
  problem, silent at night unless asked.
- Installable: a web app manifest and icons ("Add to Home screen" opens it full screen over HTTPS).
- Settings of a screen, behind a ⚙ next to the clock and kept in that browser only: OLED black or
  dark grey, eight accent colours or any other, the font (Roboto, Inter, JetBrains Mono, the
  system's) and text size (85–130%; a page that does not fit still shrinks), page rotation (on or
  off, 10–60 s, which pages), and night and calm hours that override `[night]`/`[calm]` for that
  screen — including whether a new problem lights a dimmed screen. Inter and JetBrains Mono are
  bundled (OFL 1.1) and downloaded only by a screen that picks them.
- `onboard.py`: what setting labhud up needs, shared by `init.py` and the setup page.
  `catalog()` lists every source with a form (title, one line, and per variable a label, a hint
  and whether it is secret; never a value), `try_source()` runs a source once with proposed
  values in place of the environment, for the calling thread only, and `merge_env()` sets values
  in an existing `.env` without disturbing the rest. `build_config()` takes the guests to show.
- `@source(..., title=, about=, hints=)`: every built-in source now describes itself.
- **Setup page.** With no `config.toml`, labhud serves a setup page instead of stopping, opened
  with a one-time code from its log (`http://host:8095/#ABCD-EFGH`): Proxmox VE (read-only check,
  certificate pin, which guests get a card, or "only running ones"), a form for every other
  source with where its key comes from, tried before it is kept and given a first card, the
  weather and `LABHUD_HOSTS`. It writes `config.toml` and `.env` (600) into the config folder and
  the same process starts again as the display. Code required on every call, Origin = Host, JSON
  only, ten wrong codes lock it for a minute, no key is ever sent back; `LABHUD_SETUP=off`
  disables it. See docs/install.md and docs/threat-model.md.
- Setup page, **Find services**: on the hosts you list (at most 32, no ranges), labhud tries the
  usual ports of the services it knows and recognises each by its answer, not by an open port;
  "Use" fills that service's form.
- labhud reads a `.env` itself: `LABHUD_ENV_FILE`, or `.env` next to the config file. Its values
  only fill variables the environment leaves unset.
- `config.loads(text)`: validation of a config not written yet.
- `/status` starts with a setup checklist: sources set up and answering, Proxmox/PBS keys
  read-only, certificates checked, `LABHUD_HOSTS` naming the display, actions signed, keys in
  files, history kept, pushing agents heard from. Also in `/api/status` as `checklist`.
- **A machine in one line:** `init.py agent <name>` adds a push key and a card while labhud runs
  (no restart: new `LABHUD_PUSH_*_KEY` lines in `.env` are read when the config changes) and
  prints `curl -fsSL http://labhud:8095/agent/install.sh | sudo sh -s -- <name> <url>`. labhud
  serves the installer and the agent at `/agent/`; the installer checks the agent's checksum,
  asks for the key, and sets up a sandboxed systemd service that pushes and opens no port.
- **Guest cards that follow Proxmox:** `guests = "<node>"` on a group adds a card for every
  guest of that node that has none, and drops it when the guest is deleted (a node that does not
  answer keeps its cards); `guests_tag = "labhud"` takes only tagged guests. `/status` lists
  guests with no card (with the TOML to paste) and cards whose guest is gone.
- **Thresholds with hysteresis.** On the display, a number keeps its warn/crit colour until it is
  5 points (3 °C) under the mark, so it no longer blinks between colours. On the server
  (`thresholds.py`), a number past its critical mark for `LABHUD_ALERT_HOLD` seconds (300) is an
  event and a notification ("PVE Disk 93%"), and "back to 85%" when it clears; maintenance
  silences it.
- **Upkeep:** Proxmox VE snapshots older than 14 days (`backups.snapshots`), "full in N days"
  on storages filling within 60 days, and on PBS: datastores with PBS's own full-date estimate,
  failed verify jobs and days since the last garbage collection.
- **New sources:** UPS through NUT (`nut`, on battery / charge / minutes left), certificate expiry
  of your public names (`certs`), DNS filters (`technitium`, `pihole`, `adguard`, same numbers for
  all three), Beszel (`beszel`), container updates from What's Up Docker (`updates`), Traefik
  routers and errors (`traefik`), Speedtest Tracker (`speedtest`), Immich (`immich`), Home
  Assistant entities (`homeassistant`) and game servers asked with Steam A2S or Minecraft's
  status ping (`games`). Each has a form on the setup page and a first card. See
  docs/integrations.md.
- **`[calm]`:** after a while with nothing red, the screen shows only a big clock and "ALL
  GOOD"; a problem, an alert or a touch brings the cards back. The clock jumps every 5 minutes
  (OLED burn-in). `?static&calm` shows it for screenshots.
- **Screen control** through Fully Kiosk Browser (`LABHUD_FULLY_URL`/`_PASS`): off or dimmed during
  `[night]`, on in the morning, lit for three minutes by a new problem (`screen.py`).
- **Public status page** (`LABHUD_PUBLIC_PORT` + `[public] groups`): names and states only, on a
  port of its own that serves nothing else.
- **Feeds, releases and a calendar:** `rss`, `releases` (GitHub) and `agenda` (CalDAV) sources.
- **Quick search** in the list view (`?view=list`): type part of a card's, group's or subtitle's
  name; Enter opens the only match's `url` (or its panel), Escape clears.
- **First-run tour:** four short notes the first time a screen opens labhud (kept in that
  browser's storage; never in screenshots or the list view; `?static&tour` shows it).
- **Config editor in the browser:** `init.py edit` prints a one-time link (`/edit#CODE`, 15
  minutes); the page checks config.toml as you type and saves only a valid file, in place,
  keeping `config.toml.bak`. Same protections as the setup page (`editmode.py`).
- **Any JSON API without code:** `LABHUD_JSON_<NAME>_PICK` (key=path, with list indexes),
  `_LIST` / `_LIST_NAME` / `_LIST_VALUE` (a card list from a list in the answer), `_HEADERS`,
  `_PUBLIC`. A top-level JSON list is accepted when picked from.
- **`include = ["pages/*.toml"]`:** pages, strip entries, actions and quiet hours from other files;
  an edit to any of them reloads.
- **Docker** source (`LABHUD_DOCKER_URL`, one engine or several, through a docker-socket-proxy):
  running, unhealthy, restarting and crashed containers, problems first. Containers can list
  themselves with labels (`labhud.enable`, `labhud.name`, `labhud.group`, `labhud.url`), so a new
  container appears on the display without editing `config.toml`.
- New agent readings: `cpu`, `mem`, `mem_used_of`, `disk`, `disk_used_of`, `load1`, `uptime`, so
  a machine without Proxmox gets a full card.
- Certificate pinning: `LABHUD_PINS=host[:port]=<sha256>,...`. The certificate is compared right
  after the handshake and the connection dropped, before the key is sent, if it is not that one.
  `LABHUD_VERIFY=on` (+ `LABHUD_CA`) checks certificates the normal way instead.
  `python3 init.py fingerprint https://host:port` prints the line, and `init.py` offers to pin
  the Proxmox certificate it connects to. The log and `/status` name every unchecked host.
- `LABHUD_*_FILE`: any setting read from a file (Compose secrets, systemd credentials).
- `LABHUD_<SOURCE>_EVERY`: any source's poll interval.
- Pushed sources: `LABHUD_PUSH_<NAME>_KEY`. An agent POSTs its JSON to `/api/push/<name>`, signed
  with HMAC-SHA256 and a timestamp, and the host it runs on needs no open port. The bundled agent
  does it with `LABHUD_AGENT_PUSH_URL` / `_KEY` (and `LABHUD_AGENT_PORT=0`).
- HTTPS: `LABHUD_TLS_CERT` / `LABHUD_TLS_KEY` for labhud, `LABHUD_AGENT_TLS_CERT` / `_KEY` for
  the agent, which pins labhud's certificate with `LABHUD_AGENT_PUSH_PIN`.
- `LABHUD_AGENT_READ_KEY` + `LABHUD_JSON_<NAME>_AUTH`: the agent's sensors only to labhud.
- The weather (a public API) always has its certificate checked.
- Maintenance: `[[maintenance.window]]` in config.toml, and with `[maintenance] buttons = true` a
  MAINTENANCE button in each card's panel (1 hour, 4 hours, 1 day, end). A card in maintenance
  is grey, does not notify and does not take the screen.
- Actions with a question: `choices = [{label, action}]` under `[action.<name>]`.
- `LABHUD_DATA`: the history, the sparklines and maintenance kept across restarts (SQLite).
- Sources: Healthchecks, Scrutiny, Uptime Kuma (docs/integrations.md).
- MQTT: card states (retained) and history events, with Home Assistant discovery
  (`LABHUD_MQTT_URL`).
- Docs: architecture, threat model, per-source recipes (`docs/sources.md`), integrations; a
  documentation site (MkDocs, GitHub Pages).
- CI starts the image the hardened way (read-only, only `NET_RAW`) and fails if it runs as root.
- The server drops a client that stalls for 60 s.
- `/status`: a page with every source's state, last answer, poll time, next try and last error,
  plus the version, uptime, connected displays and whether actions are on. `/api/status` gives
  the same as JSON. No data and no setting values are shown.
- A small orange mark next to the clock when a configured source has been failing for longer
  than `LABHUD_SOURCE_GRACE` seconds (default 300); a tap lists those sources and their errors.
- `config.toml` is read again a few seconds after it changes, without a restart. A good file is
  applied and every display reloads; a broken one is kept out, its problems are logged and shown
  in a banner on the displays and on `/status`, and the last good version keeps running.
- A card that turns red while the screen is on (host down, or a number past its critical mark)
  takes the screen: the rotation goes to its page and stays there for 3 minutes, and the card
  pulses with a red ring. What is already red when the page loads does not move it.
- `[night]` in the config: between two hours of the display's clock the screen dims to `dim`
  percent. A touch lights it up for a minute, a new problem for as long as it holds its page.
- `events.recent`: a history of what changed (hosts up and down, guests started and stopped,
  sources that stopped answering and came back, new alerts, config reloads), newest first, for
  any `list` card. Kept in memory, last 50. The demo has a RECENT card with it.
- Notifications: with `LABHUD_NOTIFY_URL` set, the history's bad news is sent to a webhook in
  ntfy, Gotify, Discord or plain JSON shape (`LABHUD_NOTIFY_FORMAT`). A host down is sent only
  if it is still down `LABHUD_NOTIFY_DELAY` seconds later (default 120); `LABHUD_NOTIFY_ALL=1`
  adds the good news. `/status` shows how many went out and the last error.
- Sparklines: every number shown as a percentage, a temperature or a rate gets a faint shade of
  its last 6 hours behind it (sampled once a minute, in memory, from `/api/history`).
  `sparklines = false` turns them off. The demo starts with six made-up hours.
- `?view=list`: a view for a phone in the hand. One scrolling list, red cards first, then every
  card as a row with its first numbers; a tap opens the panel. A card's new `url` key adds an
  OPEN button to the panel in this view (never on the wall).
- The Proxmox VE and PBS tokens are checked at start and after a reload: any privilege beyond
  reading is logged as a warning and shown on `/status`. docs/security.md lists the least each
  source needs.
- Signed actions: with `LABHUD_ACTION_SECRET`, the display posts actions to labhud, which checks
  they come from its own page and are offered in the config, then forwards them with an
  HMAC-SHA256 signature (time, nonce, name). `labhud-agent.py` checks it with
  `LABHUD_AGENT_SECRET`: at most 30 seconds old, never reused, from labhud's IP. Without a
  secret, actions still go straight from the browser to the agent, as before.
- `init.py` (`labhud init`): asks for a Proxmox VE address and token, checks the token works and
  can only read, finds the nodes and guests, and writes a first `config.toml` and `.env` (never
  over existing files). In the image: `python3 /app/init.py /out`.
- List rows may carry a time (`t`, Unix seconds) instead of a value: shown as the time today,
  as the date before.
- The LabHUD name in the middle of the top bar, in portrait (where that space was empty).
- `panel = "list:<path>"`: a tap opens a list made for the panel (flat rows, or `{title, rows}`
  sections) instead of repeating the card. See `docs/live-agent.md`.
- MIT license; the Roboto fonts' SIL Open Font License is in `static/fonts/OFL.txt`.
- `docs/install.md`, `docs/security.md` and `compose.example.yaml`.
- Image labels `org.opencontainers.image.licenses` and `.source`.
- CI can publish the same tags to a second registry (`EXTRA_IMAGE`).
- `config.toml` with validation that reports every problem at once, with its location
  (`python3 config.py config.toml` checks a file).
- Data sources as plugins (`sources/`), each enabled by its `LABHUD_*` variables: Proxmox VE
  (plus backups), PBS, OPNsense, Synology, OpenMediaVault, qBittorrent, Sonarr, Radarr,
  Prowlarr, Bazarr, Jellyfin, Navidrome, Seerr, Open-Meteo weather, and any JSON endpoint
  (`LABHUD_JSON_<NAME>_URL`).
- The frontend, with the tab title, action labels, host panel sensors, games page and
  column grouping set from the config.
- An optional live agent contract (`docs/live-agent.md`) and actions sent to an agent you
  run (`docs/actions.md`, `agents/labhud-agent.py`); actions are off by default.
- Demo mode (`LABHUD_DEMO=1`) on made-up data, with no network, keys or ping.
- Multi-arch image (linux/amd64, linux/arm64), built by CI; the version is in the
  `LABHUD_VERSION` environment variable and the image labels.
- Tests: the config loader, every source parser on saved API answers, and a smoke test of the
  server in demo mode.

### Changed

- **The agent's release is 0.1.0** (`agents/agent-release.json`): *Update* in ⚙ takes the
  agents installed as a service to it.
- **One design for the wall, ⚙ and the app**: the colours and icons are in `design/tokens.json`,
  and `design/build.py` writes them into the pages, `static/icons.js` and the app's `Tokens.kt`
  (a test fails when they drift). Nothing changes on the wall; its icons now come from there.
- **The agent's release is 0.1.0** (`agents/agent-release.json`): the install line installs it,
  and *Update* takes the agents to it.
- **Wall mode needs no second sign-in.** The wall page in the app's Wall mode is the admin when
  its phone is signed in as the admin: ⚙ and the cards' buttons are there at once, behind the
  display's PIN when one is set (and only showing again after 2 minutes, as on any screen). Sign
  out on that page signs the phone out.
- README for 0.3, with the five steps to a working wall; the roadmap says what 0.3 did and what is left.
- **Pending security updates are orange, not red.** A card's `security` count above 0 is a
  warning: the card stands out, but it is no longer a problem (no red bar in the app, no bell,
  no red on the wall). The fleet's update run takes care of them.
- **The same order of buttons on every card**: what starts or wakes something, UPDATE, the
  config's own, MAINTENANCE, then after a thin line, in red, RESTART / REBOOT and STOP / SHUT DOWN
  (on a narrow screen the red ones get a row of their own). The verb comes from the action's name,
  so a container called "stopwatch" is not red; YES in the confirmation is red only for those.
  `/api/v1` gives the app the same order and `danger` (parity-tested).
- **⚙ > Sources: set a source up from the page.** Every source a form can set up, as a tile
  (with ✓ when it is set up) you can search; its form, **Test** (empty secret fields are tried with
  the ones already set, which the page never sees), **Save** (tested first; the keys go to
  `secrets.env`, its first card to the Services group of the page you choose, then labhud restarts
  in place) and **Forget**. **Scan my network** looks for the known services (now also Technitium,
  Beszel, Immich, AdGuard Home, Pi-hole, Traefik) on a private subnet of at most a /22 (the
  display's /24 by default) plus the agents' addresses, slowly: 16 connections at most, a silent
  host left after one round; a service counts only when its page names it, and **Set up** fills
  the form with its address. Routes `/api/setup/{source-save,source-forget,scan,scan-status}`.
- **⚙ > Devices > + Add system**: first the kind of host (Proxmox node, Docker host, Linux server,
  Pterodactyl node, phone), then its command (the container first for a Docker host) and
  "Waiting for the agent…"; once it dials in, the capabilities that kind uses are ticked (its
  targets still are not). A phone gets the QR code there too.
- **⚙ > Pages: pages and groups for every screen, from the page.** Hold a card or a group's title
  and drag it (a finger too: the hold tells a drag from a scroll), or use its ⋯ menu (up, down, to
  another group or page, rename, kind); + Group, + Page; nothing changes until Save, and the
  screens take it when ⚙ closes. **Organize by type** proposes one page's groups as Proxmox, NAS &
  backup, Docker hosts, VMs & LXC, PCs & other, guessed from each card (a host panel, a NAS or
  backup by name, an agent with Docker, a guest); cards that are not machines stay in their groups,
  and a card's new `kind` key corrects the guess. A layout that would lose or repeat a card is
  refused. Needs the config kept by labhud (⚙ > Advanced > Take over). Routes
  `/api/setup/{layout,layout-save,organize}`. A group holding guests of several Proxmox nodes
  writes the node beside each guest's name.
- **A bell instead of the bars over the cards.** What was pressed and the alerts are in one list
  under a bell beside the account, with a count on it (red with an alert or a failure not yet
  seen). A short toast at the bottom says it for 4 seconds (pressed, done, failed, a new alert,
  ⚙'s messages) and can be closed. The bar of running actions (which stayed up to 5 minutes and
  could not be closed) and the alert banner are gone; OK on an alert in the bell dismisses it as
  before.
- A game server started or stopped from its tile is followed by its id: "starting…" no longer
  waits the full 5 minutes when the list is sorted again under it (the running ones go first, so
  the tile then shows another server).
- ⚙'s tabs: **Look** (now with Night and Calm), **Pages**, **Alerts** (what a screen does when
  something goes wrong) under Display; Devices, Security, Advanced under LabHUD.
- The menu under the admin's name is now Settings and Sign out; Edit config moved to ⚙ > Advanced.
- **HTTPS by default.** At the first start labhud makes its own certificate (ECDSA P-256,
  self-signed, 10 years) into `LABHUD_DATA/tls/` with `openssl` (now in the image) and answers
  HTTPS on its port. The app and the agents pin it when they pair (the QR code and the agent's code
  already carry the fingerprint); a browser warns once. `LABHUD_TLS_CERT` / `LABHUD_TLS_KEY` still
  give your own certificate; **plain HTTP needs `LABHUD_HTTP=on`** (or happens without
  `LABHUD_DATA` or `openssl`, said in the log and on `/status`). A plain `http://` request on the
  port gets a redirect to `https://`, for a name in `LABHUD_HOSTS` only. `init.py` prints `https://`
  links with that certificate. **Upgrading:** an install with `LABHUD_DATA` switches to HTTPS, so
  its phones and agents must be paired again, or set `LABHUD_HTTP=on` to stay on HTTP. The compose
  example now mounts `./data` as `LABHUD_DATA`.
- **A paired phone only looks until it signs in as the admin.** Its token carries an `admin` flag,
  tied to the admin who signed in (a new admin puts every phone back to looking): `POST
  /api/v1/admin-login` and `admin-logout`; pairing by signing in (`login-pair`) gives it at once.
  Tokens paired before only look. A phone signed in as the admin can also manage phones, agents and
  the PIN. `info` and `state` say `device_admin`, `pin` and `unlocked`; ⚙ > Security > Signed in
  lists the phones signed in as the admin.
- **The PIN only goes with the admin, and a locked screen only shows.** With a PIN set, every
  signed-in screen goes back to only showing after **2 minutes** without a touch (was 10; `[lock]
  idle`): the pages and panels stay usable, ⚙ and the buttons go, and the lock, ⚙ or the panel's
  UNLOCK ask for the PIN. The keypad no longer covers the whole screen at the first touch. The
  PIN unlocks only a screen signed in as the admin; a phone signed in as the admin unlocks with
  `POST /api/v1/unlock {pin}` and sends the ticket as `X-Labhud-Unlock`, like a display.
- An agent's buttons need no `LABHUD_ACTION_URL`; the config's own actions still do.
- **A display that is not signed in only shows**: no ⚙, no buttons on the cards, no maintenance,
  and labhud refuses them. Without an admin every display only shows (`init.py admin` makes one).
  The app is unchanged: it acts with its own token.
- **⚙ rearranged to use the whole screen.** A slim bar of icons (with the names beside them on a wide
  screen, at the bottom on a portrait one) and five tabs: **Look** (theme, text, density), **Pages**
  (the rotation, and one column per page with its groups and cards to show or hide), **Night &
  alerts**, **Devices** (agents and phones) and **Security**. The sections fill a fluid grid instead
  of fixed columns. Agents are a list with the open agent beside it: what it may do, then its
  targets under a tab per capability, with a filter and Tick all. The old tab names still open the
  right tab (`?static&settings=agents`).
- Setting or changing the PIN needs the admin.
- ⚙ > Agents > Add an agent shows the command that works with this labhud: the one-line `curl`
  (and the `docker run`) once a release of the agent is pinned, otherwise the installer with
  `--binary` and `--sha256`, and why. With `--binary`, a `--sha256` given wins over the pinned one.
- On an agent that also has the game servers (a Pterodactyl node), the Docker lists leave out the
  Wings containers (named by a server's UUID): they are the same servers, without a readable name.
- ⚙ on the display has four tabs instead of one long list: **Display** (theme, text, layout,
  rotation), **Screen** (night, calm, flash and beep), **Phones** (pair, remove) and **Security**
  (the PIN, the admin). The tab last open is remembered with the screen's settings.
- The wall page inside the app's wall mode (the `labhud_device` cookie) now asks for the display's
  PIN like any other screen; only the app's own token skips it.
- The name shows as **LabHUD** everywhere on screen: the top bar (now all in the accent colour, so
  it no longer reads "labHUD"), the default title, `/status`, the setup page, the config editor and
  the day's timeline ("LabHUD started"). Commands, the image and the container stay `labhud`.
- Numbers that are not live are dimmed: the card's host is off or down, or its source kept the
  last answer after a failed poll — then the card also says "not updated since 14:32".
- The ⚙ is a thin outline in the theme's accent colour.
- A machine that is off on purpose is grey everywhere, not red: a stopped Proxmox guest that does
  not start at boot (read from its config, only for stopped guests, cached 10 minutes; the token
  needs VM.Audit, which PVEAuditor has), an `on_demand` card on the bottom strip, and the same
  host in another source's list (Beszel's "down" becomes "off").
- In a card's metrics the boxes of a row share its width, so one left alone on the last row no
  longer looks forgotten.
- A guest's RAM stops at 100% (the balloon can report 101%).
- Beszel's list shows each system's fullest of CPU, RAM and disk ("disk 79%") instead of all
  three, which left no room for the name on a wall's column.
- labhud now reads a `.env` next to `config.toml` by itself, when there is one. Its values only
  fill variables the environment leaves unset, so a running Compose setup sees no difference;
  `LABHUD_ENV_FILE` names another file.
- labhud-agent pushes CPU, RAM, disk, load and uptime besides the temperatures.
- Proxmox guests carry their `tags` and `template` in the data.
- A source that misses a poll keeps its last good answer, marked `stale`, for
  `LABHUD_SOURCE_STALE` seconds (60) before its cards show the error. A stopped VM no longer
  turns "unknown" for one cycle when Proxmox answers slowly.
- A page whose groups need fewer columns than the screen has now spreads them over the whole
  width (wider columns) instead of leaving the right side empty.
- New demo screenshots with the current layout.
- Every group is now a framed box with a large heading; card names inside are smaller.
- A card named like its group (PVE in group PVE, LOG in group LOG) no longer repeats the name: its
  status dot moves into the group's heading. A long group title shrinks to fit its frame.
- Groups are laid out by the page, not by CSS columns, and are never split between two columns
  (before, a title could end up at the bottom of one column with its cards in the next). Columns
  are balanced; a group taller than the screen spans two columns, with its cards in two columns
  inside; a page that still does not fit is zoomed out a little (down to 60%) instead of cut.
- Proxmox guest rows in landscape show only the numbers, with "CPU RAM" once above the rows, so
  the guest names have room; the panel and portrait keep the labels on every row.
- `keep_together` on a group has no effect any more (no group splits now); it is still accepted.
- New demo screenshots, plus one in portrait.
- A request under a name missing from `LABHUD_HOSTS` gets a plain-text 421 that names the host
  and the variable to set, instead of `{"error":"unknown host"}`; the accepted names are logged
  at start.

### Removed

- **Direct actions.** Actions now always go through labhud, signed for the agent: both
  `LABHUD_ACTION_URL` and `LABHUD_ACTION_SECRET` are needed, and with the URL alone actions stay
  off (the startup log and `/status` say so). The agent's `LABHUD_AGENT_ORIGIN` is gone; set
  `LABHUD_AGENT_SECRET` and allow only labhud's IP. See docs/actions.md.

### Fixed

- **The demo's made-up news could reach a real channel**: the flag that keeps it in the app's outbox
  only is now cleared when a real config is loaded, so the channels work again after the demo.
- **Uptime said 100% for a host that was off**: a host whose agent has been quiet for 5 minutes is
  down (unless `on_demand`, `wake` or maintenance), a guest off with `onboot` off is down unless the
  card says `on_demand`, the running 15 minutes survive a restart, and `coverage` says how much of
  the range is known ("99.2% · 87% covered" in the panel; the gaps stay grey).
- **⚙ needs the PIN on a browser too**, like on a phone; `GET /api/v1/devices` answers only a
  paired phone, the admin or the `labhud` command. Gone, unused: `/api/agents/rename`,
  `/api/setup/config`, `/api/setup/save`, `/api/admin/edit` (`init.py edit` stays).
- An offline agent's last numbers are dimmed and "not updated since", no longer shown as live.
- A wall whose stream was dropped (too slow, or a proxy's 502 during a restart) froze with old
  numbers: the stream is closed and the page opens it again, waiting longer each time.
- Alerts no longer stay on the bell after their agent is removed or renamed, nor a host's CPU or
  disk alert while it is offline.
- `labhud source add crowdsec|gitea|nextcloud|authentik --page …` makes their card too.
- Gitea Actions: a run's time from its own end or start, with its offset (it showed "20743d ago"),
  and a repository whose runs no runner takes is one quiet row instead of a queue of waiting ones.
- A source's optional settings (`CROWDSEC_WATCH`, `GITEA_REPOS`) can be set in ⚙ > Sources and with
  `labhud source add`: they were refused as unknown.
- Times given in UTC by a service ("3h ago", the feeds' dates) were an hour off in summer.
- The page menu no longer runs under the bell and the account button when there are many pages:
  it keeps clear of them and scrolls, the open page in sight.
- **Update in ⚙ failed on agents installed as a service** ("read-only file system"): the unit
  keeps `/usr` read-only, and the binary was in `/usr/local/bin`. The installer now puts it in
  `/usr/local/lib/labhud-agent`, the one folder besides its settings the service may write, with a
  link in `/usr/local/bin`. An agent installed before needs its install line once more (without the
  code); then Update works.
- The agent's install line works as root without `sudo` (a Proxmox node has none): it uses `sudo`
  only when it is not root.
- **The agent's install line in ⚙ works over labhud's own HTTPS.** curl refused labhud's own
  certificate, so the line failed at once; it now fetches the installer with `-k` and checks it
  against its SHA-256, which the line carries.
- A tick in ⚙ > Agents that changed a card's buttons reloaded the display at once, closing ⚙
  and dropping the screen on the first page. A reload now waits while ⚙, a card's panel or a
  confirmation is open, and happens once they close.
- ⚙ on a wide screen left a hole under a short section when the one beside it was tall (Screen:
  Night next to Calm); the sections now flow in columns like the wall's groups.
- A display opened before the admin was made said "Unlock the screen first" when pairing a
  phone, on a screen that was unlocked: it still believed there was no admin. A refusal now reads
  the admin flag again and says to sign in; ⚙ reads it again each time it opens.
- A test that compared the server's card colours with the display's depended on the demo's random
  numbers and failed now and then (it held back the older images on the other forge).
- On a screen with a PIN that had never seen the first-run tour, the tour covered the keypad and
  the lock refused every tap on the tour: neither could be used. The tour now works above the lock.
- A slow source that fails once no longer leaves its corner empty for ten minutes: a failed
  source is retried within a minute (then backing off as before), and a slow one keeps its last
  good answer for two of its own intervals. The weather vanished after one passing 503 from
  Open-Meteo right after a restart.
- A list that cannot show every row no longer hides problems without a trace: when a hidden row
  is bad, the last row says "+N more · M bad". Top-N lists that simply end stay as they were.
- The log no longer prints a traceback for every client that drops its connection, and lines
  from two threads no longer end up glued together.
- A card already red (a VM stopped all along) no longer takes the screen as a new problem when
  its source misses one poll or answers after the first minute: only a card seen fine and then
  red counts as new.
- CI: a version tag in a repository without the `REGISTRY` variable built an invalid image name
  (`/owner/labhud:X.Y.Z`) and failed; it now builds the image without pushing it.
- Sizes always use a decimal point (`1.5T`); the server side wrote `1,5T` while the page wrote `1.5T`.
- Backup ages are floored like every other "ago": a backup 5.5 days old shows `5d ago`, not `6d ago`.

### Security

- A source, or a test of one in ⚙ or the `labhud` command, only ever opens `http://` and
  `https://` addresses (never `file:` or another scheme). The threat model covers 0.3: the admin,
  phones, the PIN, the `labhud` command, UnifiedPush and container logs.
- Only the PIN's scrypt hash is kept: in `LABHUD_DATA`, or in `LABHUD_LOCK_PIN_HASH` (printed by
  `init.py pin --hash`) without it. labhud checks the PIN and gives the page a ticket held in memory.
  With a PIN set, the maintenance buttons and signed actions (`/action/`) are refused without a
  live ticket. Five wrong PINs stop the checks for a minute, then 2, 4… up to an hour, and go to
  the day's timeline.
- Direct actions (no `LABHUD_ACTION_SECRET`) go from the browser to the agent, so for them the
  lock is in the page only; use signed mode to have labhud enforce it.
- The display's pages now send a Content-Security-Policy (own scripts, styles, fonts and media
  only, no frames, connections only to labhud and the action agent), `nosniff`,
  `Referrer-Policy: no-referrer` and a Permissions-Policy. The status page's script moved to
  `status.js` for that.

### Docs

- Brought up to date and trimmed: actions, architecture, threat model, security and the app API
  describe only what exists now; the roadmap keeps the done work as one summary.
- docs/agents.md: the plan for labhud's own agents.
- docs/actions.md: moving an install from direct to signed actions, which the phone app needs.

[Unreleased]: https://github.com/Mithrandler/labhud/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Mithrandler/labhud/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Mithrandler/labhud/releases/tag/v0.1.0
