# Sources: recipes

For each service: the key to create, the least it needs, what to put in `.env`, and a card to
start from. Every source is optional. Keys can also come from files (`LABHUD_<NAME>_FILE`, see
[security.md](security.md#secrets-in-files)), and HTTPS certificates should be pinned
(`python3 init.py fingerprint https://host:port`, then `LABHUD_PINS`).

`/status` shows, per source, whether it answers and why not. For Proxmox VE and PBS it also says
whether the key can do more than read.

The integrations (Healthchecks, Scrutiny, Uptime Kuma, MQTT) are in
[integrations.md](integrations.md); your own JSON, polled or pushed, in
[live-agent.md](live-agent.md).

## Proxmox VE

A token with privilege separation and the `PVEAuditor` role, on each node (or once for a cluster):

```sh
pveum user add labhud@pve --comment "labhud, read-only"
pveum user token add labhud@pve wall --privsep 1        # prints the secret once
pveum acl modify / --tokens 'labhud@pve!wall' --roles PVEAuditor
```

```sh
LABHUD_PROXMOX_NODES=pve                                  # as Proxmox spells them, comma separated
LABHUD_PROXMOX_PVE_URL=https://192.0.2.10:8006
LABHUD_PROXMOX_PVE_TOKEN_ID=labhud@pve!wall
LABHUD_PROXMOX_PVE_TOKEN_SECRET=<secret>
LABHUD_PINS=192.0.2.10:8006=<sha256>                      # init.py fingerprint
```

`<NODE>` in the variable names is the node name in capitals, other characters turned into `_`.
A guest's disk usage comes from its QEMU guest agent and needs `VM.GuestAgent.Audit` (Proxmox 9)
or `VM.Monitor` (Proxmox 8) on top; without it that number stays empty. `python3 init.py` makes
a first config from your nodes and guests.

```toml
[[page.group.card]]
id = "host-pve"
name = "PVE"
ping = "192.0.2.10"
metrics = [{ key = "proxmox.pve.cpu", label = "CPU", format = "percent" },
           { key = "proxmox.pve.mem_used_of", label = "RAM", format = "used_of" },
           { key = "proxmox.pve.vms", label = "VM", format = "count" }]

[[page.group.card]]
id = "vm-pve-101"
name = "docker"
proxmox = "pve/101"            # its state, CPU and RAM come from Proxmox
```

## Proxmox Backup Server

```sh
proxmox-backup-manager user create labhud@pbs --comment "labhud, read-only"
proxmox-backup-manager user generate-token labhud@pbs wall
proxmox-backup-manager acl update / Audit --auth-id 'labhud@pbs!wall'
```

```sh
LABHUD_PBS_URL=https://192.0.2.11:8007
LABHUD_PBS_TOKEN_ID=labhud@pbs!wall
LABHUD_PBS_TOKEN_SECRET=<secret>
```

A PBS that sleeps between backups: mark its card `on_demand = true`, so off is grey, not red.

```toml
[[page.group.card]]
id = "backup"
name = "BACKUP"
ping = "192.0.2.11"
on_demand = true
metrics = [{ key = "pbs.used_percent", label = "Used", format = "percent" },
           { key = "pbs.failed", label = "Failed 24h", format = "count" }]
```

### Upkeep: snapshots, storage running out, PBS verify and GC

- `backups.snapshots` lists every Proxmox VE snapshot older than 14 days (red past 30;
  `backups.old_snapshots` counts those): `list = "backups.snapshots"`.
- Each storage row in `proxmox.<node>.storage` says `full in Nd` when, at the pace of the last
  days, it fills within 60 days (red under 14). labhud samples once an hour and needs 12 hours
  before it estimates; a restart starts over.
- PBS: `pbs.datastores` (one row per datastore, with PBS's own "full in" estimate),
  `pbs.full_days`, `pbs.verify_failed` (failed verify jobs in 7 days) and `pbs.gc_age_days`.

```toml
metrics = [{ key = "pbs.used_percent", label = "Used", format = "percent" },
           { key = "pbs.verify_failed", label = "Verify failed", format = "count" },
           { key = "pbs.gc_age_days", label = "GC days ago", format = "count" }]
list = "pbs.datastores"
```

## OPNsense

*System > Access > Users*: a user of its own, with exactly two privileges,
`Diagnostics: System Activity` (`page-diagnostics-system-activity`) and `Status: Traffic Graph`
(`page-status-trafficgraph`). Then *+* under *API keys* downloads its key and secret.

```sh
LABHUD_OPNSENSE_URL=https://192.0.2.1
LABHUD_OPNSENSE_KEY=<key>
LABHUD_OPNSENSE_SECRET=<secret>
```

```toml
metrics = [{ key = "opnsense.cpu", label = "CPU", format = "percent" },
           { key = "opnsense.wan_dn", label = "Down", format = "rate" },
           { key = "opnsense.wan_up", label = "Up", format = "rate" }]
```

## Synology DSM

A DSM user of its own, not in `administrators`, allowed to read resource use and storage.

```sh
LABHUD_SYNOLOGY_URL=https://192.0.2.20:5001
LABHUD_SYNOLOGY_USER=labhud
LABHUD_SYNOLOGY_PASS=<password>
```

```toml
metrics = [{ key = "synology.used_percent", label = "Used", format = "percent" },
           { key = "synology.free", label = "Free", format = "bytes" },
           { key = "synology.cpu", label = "CPU", format = "percent" }]
```

## OpenMediaVault

Through a small agent on the OMV host that serves disk usage as JSON; it has no key, so keep its
port reachable only from labhud. `LABHUD_OMV_URL=http://192.0.2.21:9190/`.

## Sonarr, Radarr, Prowlarr, Bazarr

Each one's *Settings > General > API Key*. These keys have full rights, there is no read-only
kind: keep `.env` (or the key files) readable only by you.

```sh
LABHUD_SONARR_URL=http://192.0.2.50:8989
LABHUD_SONARR_KEY=<key>
LABHUD_RADARR_URL=http://192.0.2.50:7878
LABHUD_RADARR_KEY=<key>
```

With Sonarr or Radarr set, two more sources run: `recent` (last imports and the queue) and
`calendar` (what comes next).

```toml
metrics = [{ key = "sonarr.series", label = "Series", format = "count" },
           { key = "sonarr.wanted", label = "Wanted", format = "count" }]
list = "recent.added"
```

## Jellyfin, Seerr

*Dashboard > API Keys* (Jellyfin), *Settings > General > API Key* (Jellyseerr, Overseerr). Both
have full rights.

```sh
LABHUD_JELLYFIN_URL=http://192.0.2.50:8096
LABHUD_JELLYFIN_KEY=<key>
LABHUD_SEERR_URL=http://192.0.2.50:5055
LABHUD_SEERR_KEY=<key>
```

```toml
metrics = [{ key = "jellyfin.streams", label = "Playing", format = "count" }]
list = "jellyfin.now"
```

## qBittorrent, Navidrome

A user of their own where the service allows it. Navidrome's Subsonic API puts the password in
the query string: a dedicated account with no admin rights, over HTTPS or on a trusted segment.

```sh
LABHUD_QBITTORRENT_URL=http://192.0.2.50:8080
LABHUD_QBITTORRENT_USER=labhud
LABHUD_QBITTORRENT_PASS=<password>
```

```toml
metrics = [{ key = "qbt.dl", label = "Down", format = "rate" }, { key = "qbt.ul", label = "Up", format = "rate" }]
torrents = "qbt.torrents"
```

## Weather

Open-Meteo, no key, set in `config.toml`. Its certificate is always checked (a public service).

```toml
[weather]
latitude = 52.52
longitude = 13.41
city = "Berlin"
```

## Poll intervals

Each source has its own pace (Proxmox 10 s, OPNsense 5 s, the *arr family 5 min...). Change one
with `LABHUD_<SOURCE>_EVERY=<seconds>`, for example `LABHUD_PROXMOX_EVERY=20`. A failing source
is retried less and less often, up to every 10 minutes, and keeps its last answer, marked stale,
for `LABHUD_SOURCE_STALE` seconds (60) before its cards show the error.
