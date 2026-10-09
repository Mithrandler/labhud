# Integrations

Services labhud reads besides the built-in ones, and MQTT for Home Assistant. Each source is
off until its variables are set (see `.env.example`); its data is then addressable from
`config.toml` like any other.

## Healthchecks

Cron jobs, backups and anything else that pings [Healthchecks](https://healthchecks.io), on the
hosted service or your own instance.

```sh
LABHUD_HEALTHCHECKS_URL=https://healthchecks.io
LABHUD_HEALTHCHECKS_KEY=<Project Settings > API Access > read-only key>
```

| path | what |
|---|---|
| `healthchecks.total`, `.up`, `.down`, `.grace`, `.paused` | counts |
| `healthchecks.checks` | a list, down first, then late (`grace`) |

```toml
[[page.group.card]]
id = "jobs"
name = "JOBS"
metrics = [{ key = "healthchecks.down", label = "Down", format = "count" },
           { key = "healthchecks.grace", label = "Late", format = "count" }]
list = "healthchecks.checks"
limit = 6
```

The read-only key can list checks but not pause or delete them: use it.

## Scrutiny

Disk health from [Scrutiny](https://github.com/AnalogJ/scrutiny): S.M.A.R.T. and its own
failure thresholds, for every disk its collectors report.

```sh
LABHUD_SCRUTINY_URL=http://192.0.2.30:8080
```

| path | what |
|---|---|
| `scrutiny.total`, `.failed` | counts |
| `scrutiny.hottest` | the highest disk temperature, in °C |
| `scrutiny.disks` | a list, failed first: "host device · model size", "34° · passed" |

Scrutiny's API has no key. Keep it reachable only from labhud's host.

## Uptime Kuma

Your existing [Uptime Kuma](https://github.com/louislam/uptime-kuma) monitors, so they are not
defined twice. labhud reads its `/metrics` with an API key.

```sh
LABHUD_UPTIMEKUMA_URL=http://192.0.2.40:3001
LABHUD_UPTIMEKUMA_KEY=<Settings > API Keys > Add>
```

| path | what |
|---|---|
| `uptimekuma.total`, `.up`, `.down`, `.pending`, `.maintenance` | counts |
| `uptimekuma.monitors` | a list, down first: the name and "48 ms", or the state |

## Docker

Every container's state, from one or more Docker engines: running, unhealthy (its healthcheck
fails), restarting (a crash loop), exited with an error. Give labhud a read-only
[docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) with only `CONTAINERS=1`,
never the socket itself: the socket is root on that host.

```yaml
  socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    environment: { CONTAINERS: 1 }
    volumes: ["/var/run/docker.sock:/var/run/docker.sock:ro"]
```

```sh
LABHUD_DOCKER_URL=http://socket-proxy:2375
# several engines: LABHUD_DOCKER_URL=nas=http://192.0.2.30:2375,vps=http://192.0.2.80:2375
```

Containers can put themselves on the display with labels, without a line in `config.toml`:

```yaml
    labels:
      labhud.enable: "true"            # listed in docker.labelled
      labhud.name: "Jellyfin"          # the name shown (default: the container's)
      labhud.group: "media"            # also listed in docker.group.media
      labhud.url: "http://192.0.2.5:8096"   # OPEN in the list view
```

```toml
metrics = [{ key = "docker.running", label = "Up", format = "count" },
           { key = "docker.unhealthy", label = "Unhealthy", format = "count" }]
list = "docker.labelled"        # or docker.containers, docker.group.media, docker.nas.containers
```

## MQTT and Home Assistant

labhud can publish every card's state and every line of its history to an MQTT broker.

```sh
LABHUD_MQTT_URL=mqtt://192.0.2.60:1883      # or mqtts://...:8883
LABHUD_MQTT_USER=labhud
LABHUD_MQTT_PASS_FILE=/run/secrets/mqtt     # or LABHUD_MQTT_PASS
#LABHUD_MQTT_PREFIX=labhud
#LABHUD_MQTT_DISCOVERY=homeassistant        # "off" for no discovery
```

| topic | payload | retained |
|---|---|---|
| `labhud/card/<card id>` | `up`, `down`, `maintenance`, `scheduled` or `on-demand` | yes |
| `labhud/event` | `{"text": "NAS down", "bad": true, "t": 1791474703}` | no |
| `homeassistant/binary_sensor/labhud/<card id>/config` | discovery | yes |

With discovery on, each card with a status shows up in Home Assistant as a binary sensor of
device class *problem* (on while the card is down), grouped under one *labhud* device. An
automation can then flash a light when the NAS goes down, or ignore it in maintenance:
maintenance is its own state, not `down`.

Only changes are sent. If the broker is down, labhud keeps the latest state of each card and
sends it when the broker is back; the display is never slowed by it. With `mqtts://`, the
broker's certificate is checked like the sources' (`LABHUD_PINS`, `LABHUD_VERIFY`). Give labhud
a broker user that may only publish under its prefix and the discovery prefix.
