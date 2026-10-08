# The live agent: your own data on the display

Most of what a lab wants on the wall has no API that labhud could know about: firewall alerts,
DNS stats, logins, which containers run where, game servers. For all of these, labhud reads
**one JSON document that you produce**. You can write it from a cron job, a small service, or a
static file on a web server. labhud polls it and treats it like any other source.

```sh
LABHUD_JSON_LIVE_URL=http://192.0.2.10:9189/live   # any URL that returns a JSON object
LABHUD_JSON_LIVE_EVERY=5                           # seconds between polls (optional)
LABHUD_JSON_LIVE_TIMEOUT=20                        # seconds (optional)
```

The source name is the part between `LABHUD_JSON_` and `_URL`, lowercased. Name it `live` to
get the built-in panels described below. Any other name works the same way for card metrics
and lists, but not for the built-in panels. Several sources can share one URL variable:
`LABHUD_JSON_TEMP_URL="pve=http://a:9189/,node2=http://b:9189/"` gives `temp.pve` and
`temp.node2`.

Every key below is **optional**. A key that is missing shows as "—" or an empty panel section.
It never breaks the display.

## Anything, from the config

Each value in the document can be addressed by its dotted path:

```toml
metrics = [{ key = "live.dns.total", label = "Queries", format = "count" }]
list = "live.display.top_clients"   # a list of {name, value, bad?}
```

`list` rows are `{"name": "...", "value": "..."}`, with `"bad": true` to show a row in red. Rows
written as `{text, label}` also work.

**Errors.** If one part of your agent fails, put `"unavailable": "<why>"` in that part, for example
`live.dns.unavailable`. A card whose first metric is `live.dns.*` shows that text instead of stale
numbers. Outside `live`, a card shows the error of the whole source.

## Built-in panels and features (source `live`)

| Where | Path | Shape |
|---|---|---|
| alert banner | set by `[alerts] path` in the config, for example `live.alerts` | `[{id, text}]`. `id` must stay the same while it is the same alert, because OK hides it by `id`. |
| Proxmox guest panel, "docker containers" | `live.containers.<vmid>` | `[{name, state, status}]` (`state` = `"running"` …), or `{unavailable}` |
| `panel = "dns:details"` | `live.display.dns_blocked`, `live.display.dns_clients` | `[{name, value}]` |
| `panel = "security:log"` | `live.log` | `[{text, ts}]`, newest first, `ts` in Unix seconds |
| `panel = "security:suricata"` | `live.suricata.signatures` | `[{name, n}]` |
| `panel = "security:sources"` | `live.display.sources`, `live.display.countries` | `[{name, value}]` |
| `panel = "security:tripwire"` | `live.display.tripwire` | `[{name, value}]` |
| `panel = "security:logins"` | `live.logins.recent` | `[{app, user, ip, ts}]` |
| `panel = "network:devices"` | `live.devices.list` | `[{name, mac, ip}]` |
| a games page (`games = "<path>"` on a `[[page]]`) | any path | see below |

### A panel from your own list (`panel = "list:<path>"`)

Works with any source, not only `live`. Without it, a tap shows the card's own metrics and list
again, whole. With it, the panel shows the list at `<path>` instead, so the card can stay short
while the panel explains. Two shapes are accepted:

- flat rows, shown under "details": `[{name, value, bad}]`
- sections: `[{title, rows: [{name, value, bad}]}]`

`bad: true` colours a row red. For example, a card that lists `live.display.ports` can open
`list:live.display.ports_detail`, with sections like "how it works" and "forwarded ports".

### Game servers

A page with `games = "live.games.servers"` has no fixed cards. It shows one tile per server, up
to 15:

```json
{"id": "a1b2c3", "name": "mc", "game": "Minecraft", "state": "running",
 "address": "play.example.com:25565", "ram_mb": 2900, "ram_max_mb": 6144, "uptime_s": 3600,
 "query": {"players": 3, "max": 20, "map": null, "version": "1.21", "names": ["alex", "sam"]}}
```

`query` is `null` when the game has no query protocol, or did not answer. A tile's buttons are
the actions `game-start-<id>`, `game-stop-<id>` and `game-restart-<id>`, sent to your action
agent (see [actions.md](actions.md)).

## Sensors for host panels

A Proxmox host card can show its GPUs in its panel. Set `sensors = "<path>"` on the card, pointing at
an object with flat keys `gpu0_util`, `gpu0_mem`, `gpu0_temp`, `gpu1_util` and so on. That is
exactly what [`agents/labhud-agent.py`](../agents/labhud-agent.py) serves at `/`, together with
`cpu_temp`.
