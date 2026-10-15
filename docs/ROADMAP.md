# Roadmap

## Done

Everything below is in a release; [CHANGELOG.md](../CHANGELOG.md) has the details, version by
version.

- **The product:** config in `config.toml` (validated, reloaded on change, `include`), sources as
  plugins enabled by `LABHUD_*` variables, any JSON API without code, a demo mode, Docker image
  for amd64 and arm64 on GHCR, tests and CI, a setup page, a config editor and `init.py`.
- **Sources:** Proxmox VE and PBS (guests that follow Proxmox, snapshots, storage forecast),
  OPNsense, Synology, OMV, the *arr apps, Jellyfin, Seerr, qBittorrent, Docker (with labels),
  Beszel, NUT, Healthchecks, Uptime Kuma, Scrutiny, DNS filters, Traefik, Speedtest Tracker,
  Immich, Home Assistant, game servers, RSS, releases, CalDAV, certificates, pushed agents.
- **The display:** pages in newspaper columns, panels, sparklines and 7-day charts, the day's
  timeline, maintenance, night and calm modes, ⚙ settings on the screen (Display, Screen,
  Phones, Security), a list view, a public status page, Fully Kiosk control.
- **Security:** pinned or verified certificates, keys from files, a hardened container, signed
  actions only (HMAC, nonce, 30 s), a PIN lock on the display, one admin with TOTP.
- **Apps:** `/api/v1` for the [Android app](https://github.com/Mithrandler/labhud-android):
  pairing by QR code or admin sign-in, a token per phone, each card's state worked out on the
  server, history and per-card uptime, notifications that open the app.

## Next: labhud agents

One agent per host, added from ⚙ with a pairing code, that controls what runs there through
local APIs (Proxmox, Docker, systemd, Pterodactyl) instead of scripts. The design and the open
questions are in [agents.md](agents.md).

Smaller:

- Demo data and screenshots for the newer sources.
- Per-card uptime on the display too (the app shows it first).
