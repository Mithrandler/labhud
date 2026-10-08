# Changelog

All notable changes to labhud. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Before 1.0.0 the config format may
still change in a minor version; such changes are listed under **Changed** with what to edit.

## [Unreleased]

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

[Unreleased]: https://github.com/Mithrandler/labhud/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/Mithrandler/labhud/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/Mithrandler/labhud/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/Mithrandler/labhud/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Mithrandler/labhud/releases/tag/v0.1.1
