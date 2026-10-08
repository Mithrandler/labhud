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

## Security, integrations and docs (2026-10-08, all done, not yet in a release)

Security between machines:

8. ~~**Push agents.**~~ An agent POSTs its JSON to `/api/push/<name>`, signed with a key of its
   own (HMAC-SHA256 and a timestamp), and listens on no port ([live-agent.md](live-agent.md)).
9. ~~**TLS to agents.**~~ HTTPS for labhud (`LABHUD_TLS_CERT`) and the agent
   (`LABHUD_AGENT_TLS_CERT`), each pinning the other's certificate; a read key for a polled agent.
10. ~~**Secrets from files.**~~ Any `LABHUD_*` as `LABHUD_*_FILE` (Compose secrets, systemd).
11. ~~**Hardened container.**~~ uid 10001, read-only, only `NET_RAW`; CI now starts the image
    that way and fails if it runs as root.
12. ~~**Verified certificates.**~~ `LABHUD_PINS` (the exact certificate, checked before the key
    is sent) or `LABHUD_VERIFY`; unchecked hosts are named in the log and on `/status`.

Integrations ([integrations.md](integrations.md)):

13. ~~**Healthchecks**~~ and ~~**Scrutiny**~~ as sources.
14. ~~**Uptime Kuma**~~ as a source.
15. ~~**MQTT / Home Assistant.**~~ Card states and history events, with discovery.

Features:

16. ~~**Intervals and stale state.**~~ `LABHUD_<SOURCE>_EVERY`; a missed poll keeps the last
    answer, marked stale, for `LABHUD_SOURCE_STALE` seconds (back-off was already there).
17. ~~**Actions with a question.**~~ `choices` under `[action.<name>]`.
18. ~~**Maintenance.**~~ `[[maintenance.window]]` and a MAINTENANCE button: grey, silent, no focus.
19. ~~**History on disk.**~~ `LABHUD_DATA`: events, sparklines and maintenance in SQLite.

Documentation:

20. ~~**Architecture**~~ ([architecture.md](architecture.md)) and ~~**threat model**~~
    ([threat-model.md](threat-model.md)).
21. ~~**Per-source recipes**~~ ([sources.md](sources.md)).
22. ~~**Documentation site**~~: MkDocs on GitHub Pages, live once Pages is set to
    "GitHub Actions" in the repository settings.
