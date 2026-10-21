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
- **0.3 (October 2026): agents, setup from the screen, one version for all three.**
  [labhud-agent](https://github.com/Mithrandler/labhud-agent) (Go, binary or container) paired
  from ⚙ > Agents: sensors and charts, Docker (start, stop, update, logs), systemd, Proxmox
  guests, Pterodactyl, Wake-on-LAN, the host. ⚙ on seven tabs: sources (tried, or found by a
  network scan), pages and groups by drag and drop and by kind, agents, alert rules with quiet
  hours, phones, the PIN, the config file kept by labhud. The `labhud` command does the same from
  a terminal. Only the admin acts; a PIN sends a signed-in screen back to looking after 2
  minutes. HTTPS by default. The app 0.3: Status / Devices / Activity / Settings, the bell,
  games, containers, agents' ticks, the admin's sign-in with the PIN or a fingerprint, instant
  notifications through UnifiedPush with nothing of the news in the push. Audited with bandit,
  gosec, govulncheck, Android lint and a history scan ([threat-model.md](threat-model.md)).

## Not done yet

Nothing planned is left from 0.3. Next ideas come from using it; open an issue for one.
