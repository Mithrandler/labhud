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
