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

If your endpoint wants a key, `LABHUD_JSON_LIVE_AUTH="Bearer <key>"` sends it as the
`Authorization` header.

The source name is the part between `LABHUD_JSON_` and `_URL`, lowercased. Name it `live` to
get the built-in panels described below. Any other name works the same way for card metrics
and lists, but not for the built-in panels. Several sources can share one URL variable:
`LABHUD_JSON_TEMP_URL="pve=http://a:9189/,node2=http://b:9189/"` gives `temp.pve` and
`temp.node2`.

Every key below is **optional**. A key that is missing shows as "—" or an empty panel section.
It never breaks the display.

## Any JSON API, without code

The endpoint does not have to be made for labhud. Pick values out of any JSON answer and give
them your own names, and turn a list in it into a card list:

```sh
LABHUD_JSON_GITEA_URL=https://git.example.org/api/v1/repos/me/app/issues?state=open
LABHUD_JSON_GITEA_HEADERS=Authorization: token <key>
LABHUD_JSON_GITEA_PICK=first=0.title
LABHUD_JSON_GITEA_LIST=.               # the answer itself is the list
LABHUD_JSON_GITEA_LIST_NAME=title
LABHUD_JSON_GITEA_LIST_VALUE=user.login
```

Paths are dotted, with list indexes (`data.0.stats.total`, `-1` for the last). `_PICK` keeps
only the values named, `_LIST` adds `<name>.list`, `_HEADERS` adds headers (`Name: value; ...`),
`_PUBLIC=on` checks the certificate of a service on the internet. Then `gitea.first` and
`list = "gitea.list"` in the config.

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

## Adding a machine in one line

For any Linux machine with systemd and Python 3.11+ (a NAS, a VPS, a Raspberry Pi), on labhud's
side:

```sh
docker exec labhud python3 /app/init.py agent nas      # or python3 init.py agent nas
```

This makes a key, adds `LABHUD_PUSH_NAS_KEY` to the `.env` next to `config.toml` (the setup
page's folder) and a NAS card with CPU, RAM, disk and temperature to `config.toml`. labhud picks
both up a few seconds later, with no restart. It prints the line to run on the machine:

```sh
curl -fsSL http://192.0.2.50:8095/agent/install.sh | sudo sh -s -- nas http://192.0.2.50:8095
```

The installer, served by labhud, downloads the agent from it, checks the agent's SHA-256, asks
for the key (so it is not in the shell history), and installs a systemd service that pushes every
10 s, listens on no port and runs as a throwaway user on a read-only system. The card is live
within seconds; `/status` shows the agent under the setup checklist until its first push.

Over plain HTTP the script can be changed in transit: run it on a network you trust, or serve
labhud over HTTPS. Needs `.env` and `config.toml` writable by labhud's user (the setup page's
`/config` folder); with a read-only `config.toml`, copy the lines it would add by hand.

## Pushing instead of being polled

labhud can also **receive** a source: the agent sends its JSON whenever it likes, and the host it
runs on needs no open port at all. Each pushed source has its own key, known only to labhud and
that one agent.

On labhud:

```sh
LABHUD_PUSH_PVE_KEY=<python3 -c "import secrets; print(secrets.token_hex(32))">
LABHUD_PUSH_PVE_STALE=60   # seconds without a push before its cards show an error (optional)
```

On the agent (`agents/labhud-agent.py`):

```sh
LABHUD_AGENT_PUSH_URL=http://192.0.2.50:8095/api/push/pve   # the name after /push/ = PVE, lowercased
LABHUD_AGENT_PUSH_KEY=<the same key>
LABHUD_AGENT_PORT=0          # listen on nothing (only if it runs no actions)
```

The data is then `pve.cpu_temp`, `pve.gpu0_util` and so on. A push is a `POST /api/push/<name>`
with the JSON object as the body (1 MiB at most) and two headers, which your own agent can make
as easily:

- `X-Labhud-Time`: Unix seconds, within 30 seconds of labhud's clock;
- `X-Labhud-Signature`: the hex HMAC-SHA256, keyed with the push key, of
  `<time>.<name>.` followed by the body, byte for byte.

```sh
body='{"cpu_temp": 48.5}'; t=$(date +%s)
sig=$(printf '%s' "$t.pve.$body" | openssl dgst -sha256 -hmac "$KEY" -r | cut -d' ' -f1)
curl -sS -X POST "http://192.0.2.50:8095/api/push/pve" -H "X-Labhud-Time: $t" \
     -H "X-Labhud-Signature: $sig" -H "Content-Type: application/json" --data "$body"
```

Anything else gets `403`, and labhud logs why. The push endpoint answers under any host name
(`LABHUD_HOSTS` does not apply to it): the signature is the check.

## Securing a polled agent

If you keep polling (`LABHUD_JSON_<NAME>_URL`), close what the agent serves:

- `LABHUD_AGENT_READ_KEY=<key>` on the agent, `LABHUD_JSON_<NAME>_AUTH="Bearer <key>"` on labhud:
  without it, anyone who reaches the port reads the sensors.
- `LABHUD_AGENT_TLS_CERT` / `LABHUD_AGENT_TLS_KEY` (PEM files) serve HTTPS; pin the certificate
  on labhud with `LABHUD_PINS` (see [security.md](security.md#certificates)).
