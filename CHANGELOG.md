# Changelog

All notable changes to labhud. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/). Before 1.0.0 the config format may
still change in a minor version; such changes are listed under **Changed** with what to edit.

## [Unreleased]

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

[Unreleased]: https://github.com/Mithrandler/labhud/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/Mithrandler/labhud/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Mithrandler/labhud/releases/tag/v0.1.1
