# Roadmap

1. ~~**Config out of code.**~~ Done: `config.toml`, read and validated by `config.py`
   (`tomllib`, zero dependencies). Every problem is reported at once, with its location;
   `python3 config.py config.toml` checks a file. See `config.example.toml`.
2. ~~**Sources as plugins.**~~ Done: `sources/`, one module per service registered with
   `@source`; a source runs only when its `LABHUD_*` variables are set (see `.env.example`),
   otherwise its card says "not configured". Your own JSON endpoints need no code
   (`LABHUD_JSON_<NAME>_URL`). `server.py` and the frontend (`static/`) are here too; what
   used to be hard-coded in the frontend (tab title, action labels and confirmations, the host
   panel's node and sensors, the games page, groups kept in one column) is now in the config.
3. ~~**Lab-specific parts become optional.**~~ Done: your own data comes from one JSON document
   ([docs/live-agent.md](live-agent.md)) that also feeds the built-in panels and the alert banner
   (`[alerts]` in the config). Actions are off unless `LABHUD_ACTION_URL` is set, and are run by
   an agent you control, never by labhud ([docs/actions.md](actions.md)). `agents/labhud-agent.py`
   is a complete example: sensors, plus actions from a TOML file behind Origin and IP checks.
4. ~~**Demo mode**~~ Done: `LABHUD_DEMO=1` runs `demo/config.toml` on made-up data from
   `demo/__init__.py`, with no network, keys, ping or actions. The screenshots in
   `docs/screenshots/` come from it.
5. ~~**English everywhere**~~, including identifiers and comments. Done.
6. **Packaging.** Done: tests (config loader, every source parser on saved answers, a demo-mode
   smoke test), CI that runs them and builds a linux/amd64 + linux/arm64 image, semver tags and
   `CHANGELOG.md`. Install docs: [install.md](install.md). Still to come: publishing on GHCR.
7. ~~**Security notes.**~~ Done: [security.md](security.md). No built-in auth: host allowlist
   required, firewall in front, VPN or an authenticating reverse proxy for remote access.

## Next (chosen 2026-10-08)

Security between machines:

8. ~~**Push agents.**~~ Done. The agent sends its data to labhud over HTTPS with a key of its own, and
   listens on no port. Hosts no longer need an open port for the dashboard.
9. ~~**TLS to agents**~~ Done (both ways, pinned), with the agent's certificate pinned in the config, so nobody on the LAN can
   read or forge the data.
10. ~~**Secrets from files.**~~ Done. Every `LABHUD_*` secret can also be `LABHUD_*_FILE` (Docker secrets),
    so tokens stay out of `docker inspect` and the process environment.
11. **Hardened container.** Already there (uid 10001, read-only, only `NET_RAW`, see
    `compose.example.yaml`); left: a CI check that the image still runs that way.
11a. ~~**Verified certificates.**~~ Done: `LABHUD_PINS`, `LABHUD_VERIFY`. HTTPS to the services is not verified today (self-signed
    certificates). Per source: a CA file or a pinned certificate fingerprint, with "no check" as an
    explicit, logged choice. First on the list in [threat-model.md](threat-model.md).

Integrations:

12. **Healthchecks** (missed cron jobs) and **Scrutiny** (disk health) as sources.
13. **Uptime Kuma** as a source, so monitors are not defined twice.
14. **MQTT / Home Assistant.** State changes published on MQTT, for automations.

Features:

15. ~~**Back-off and configurable intervals.**~~ Done (back-off was already there). Each source already has its own interval, fixed in
    code; make it settable, retry a failing source less and less often, and keep its last known
    state as "stale" for a while instead of dropping it at the first failed poll.
16. ~~**Actions with a question.**~~ Done. An action can ask for a choice before it runs (now / in 5 min /
    tonight), with the choices defined in `config.toml`.
17. ~~**Maintenance windows.**~~ Done. A host marked "in maintenance" until a given time is not red, does
    not notify and does not take the screen.
18. ~~**History on disk.**~~ Done. Events and sparklines survive a container restart (small SQLite file).

Documentation:

19. ~~**Architecture**~~ Done: [architecture.md](architecture.md), every connection with its
    direction, port and key.
20. ~~**Threat model**~~ Done: [threat-model.md](threat-model.md), a stolen key, someone on
    the LAN, someone at the display; what labhud stops and what is left.
21. **Per-source recipes**: which token to create, the least rights it needs, the config block.
22. **Documentation site** on GitHub Pages, built from `docs/`.
