# Changelog

All notable changes to labhud. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Before 1.0.0 the config format may
still change in a minor version; such changes are listed under **Changed** with what to edit.

## [Unreleased]

### Added
- `onboard.py`: what setting labhud up needs, shared by `init.py` and the coming setup page.
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
- **Docker** source (`LABHUD_DOCKER_URL`, one engine or several, through a docker-socket-proxy):
  running, unhealthy, restarting and crashed containers, problems first. Containers can list
  themselves with labels (`labhud.enable`, `labhud.name`, `labhud.group`, `labhud.url`), so a new
  container appears on the display without editing `config.toml`.
- labhud-agent pushes CPU, RAM, disk, load and uptime too (`cpu`, `mem`, `mem_used_of`, `disk`,
  `disk_used_of`, `load1`, `uptime`), so a machine without Proxmox gets a full card.

## [0.1.5] - 2026-10-08

### Added
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

### Changed
- A source that misses a poll keeps its last good answer, marked `stale`, for
  `LABHUD_SOURCE_STALE` seconds (60) before its cards show the error. A stopped VM no longer
  turns "unknown" for one cycle when Proxmox answers slowly.

### Fixed
- A card already red (a VM stopped all along) no longer takes the screen as a new problem when
  its source misses one poll or answers after the first minute: only a card seen fine and then
  red counts as new.

## [0.1.4] - 2026-10-08

### Added
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

### Changed
- A page whose groups need fewer columns than the screen has now spreads them over the whole
  width (wider columns) instead of leaving the right side empty.
- New demo screenshots with the current layout.

## [0.1.3] - 2026-10-08

### Changed
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

### Fixed
- CI: a version tag in a repository without the `REGISTRY` variable built an invalid image name
  (`/owner/labhud:X.Y.Z`) and failed; it now builds the image without pushing it.

## [0.1.2] - 2026-10-08

### Added
- The LabHUD name in the middle of the top bar, in portrait (where that space was empty).
- `panel = "list:<path>"`: a tap opens a list made for the panel (flat rows, or `{title, rows}`
  sections) instead of repeating the card. See `docs/live-agent.md`.

### Fixed
- Sizes always use a decimal point (`1.5T`); the server side wrote `1,5T` while the page wrote `1.5T`.
- Backup ages are floored like every other "ago": a backup 5.5 days old shows `5d ago`, not `6d ago`.

## [0.1.1] - 2026-10-08

First public release.

### Added
- MIT license; the Roboto fonts' SIL Open Font License is in `static/fonts/OFL.txt`.
- `docs/install.md`, `docs/security.md` and `compose.example.yaml`.
- Image labels `org.opencontainers.image.licenses` and `.source`.
- CI can publish the same tags to a second registry (`EXTRA_IMAGE`).

### Changed
- A request under a name missing from `LABHUD_HOSTS` gets a plain-text 421 that names the host
  and the variable to set, instead of `{"error":"unknown host"}`; the accepted names are logged
  at start.

## 0.1.0 - 2026-10-08

First tagged version, not published: the display as it runs in production, generalized.

### Added
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

[Unreleased]: https://github.com/Mithrandler/labhud/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/Mithrandler/labhud/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/Mithrandler/labhud/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Mithrandler/labhud/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Mithrandler/labhud/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Mithrandler/labhud/releases/tag/v0.1.1
