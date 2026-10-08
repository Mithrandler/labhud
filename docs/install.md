# Install

labhud is one container: a Python server that polls your services and pushes a snapshot to the
browser over Server-Sent Events. You need Docker (or Python 3.11+), a config file describing your
screen, and an `.env` file with the keys for the services you want to show.

> **labhud has no login.** Anyone who can open the page sees your lab's internal addresses, and,
> if you turn actions on, can press the buttons. Read [security.md](security.md) before you expose
> the port to anything but the display.

## 1. Try the demo

```sh
docker run --rm -p 127.0.0.1:8095:8095 -e LABHUD_DEMO=1 ghcr.io/mithrandler/labhud:latest
```

Open <http://localhost:8095>. The demo runs on made-up data, with no network access and no keys.
On another port or address, add the name to `LABHUD_HOSTS`, for example
`-p 8080:8095 -e LABHUD_HOSTS=localhost:8080`; otherwise the page answers 421 and says why.

## 2. Describe your screen

```sh
mkdir labhud && cd labhud
curl -fsSLO https://raw.githubusercontent.com/Mithrandler/labhud/main/config.example.toml
curl -fsSLO https://raw.githubusercontent.com/Mithrandler/labhud/main/.env.example
curl -fsSLO https://raw.githubusercontent.com/Mithrandler/labhud/main/compose.example.yaml
mv config.example.toml config.toml && mv .env.example .env && mv compose.example.yaml compose.yaml
chmod 644 config.toml && chmod 600 .env
```

`config.toml` is the screen: pages, groups of cards, and what each card checks (ping, a TCP port,
or a Proxmox guest's state) and shows. The example is commented line by line and uses addresses
from 192.0.2.0/24, the range reserved for documentation: replace them with yours. Check the file
before starting:

```sh
docker run --rm -v "$PWD/config.toml:/app/config.toml:ro" ghcr.io/mithrandler/labhud:latest \
  python3 /app/config.py /app/config.toml
```

Every problem is listed at once, with its location.

## 3. Give it keys

In `.env`, uncomment and fill in only the services you have. A source runs when its URL and key
are set; otherwise its cards say "not configured". Use read-only accounts and API tokens wherever
the service offers them (Proxmox: a token for a user with the `PVEAuditor` role).

Set `LABHUD_HOSTS` to every `host:port` the display will open labhud under.

## 4. Start it

```sh
docker compose up -d
docker compose logs labhud
```

The log's first lines list the active sources, the ones not configured, the accepted host names
and whether actions are on. Open `http://<server>:8095` on the display.

## The display

Any browser that runs modern JavaScript works. The layout is made for a phone or small tablet in
landscape (800×360 CSS pixels and up). For a wall: a kiosk browser that keeps the screen on and
restarts the page after a crash, and a firewall rule that lets only the display reach port 8095.
Pages rotate on their own; `http://<server>:8095/#<page id>` opens one directly.

## Without Docker

```sh
git clone https://github.com/Mithrandler/labhud && cd labhud
cp config.example.toml config.toml && cp .env.example .env   # then edit both
set -a && . ./.env && set +a && python3 server.py
```

Standard library only, Python 3.11 or newer. Without `iputils` ping (BusyBox has no `-W`), a dead
host can slow down status collection.

## Updating

Images are tagged `X.Y.Z`, `X.Y` and `latest`. `docker compose pull && docker compose up -d`, or
let a tool such as Watchtower or Dockhand do it. Changes are listed in
[CHANGELOG.md](../CHANGELOG.md).

## Your own data

- [live-agent.md](live-agent.md): one JSON document of your own (alerts, DNS, logins, game
  servers) feeds the built-in panels and the alert banner.
- [actions.md](actions.md): buttons that wake hosts or start VMs, run by an agent you control.
  Off by default.
