# The labhud command

Everything ⚙ does, from a terminal. It runs inside labhud's container, next to the server:

```sh
docker exec -it labhud labhud agent list
```

`labhud` calls the same routes as ⚙, with the same checks, on `127.0.0.1`. It proves who it is
with a key the server writes at every start into `LABHUD_DATA/cli.json` (mode 600). Only the
server's own user can read that file, and `docker exec` runs as that user. The server takes the
key only from loopback. So labhud must be running, with `LABHUD_DATA` set. `admin` and `pin`
also work while it is stopped: they write their files directly, like `init.py`.

```text
    labhud status                                  what runs, where the config is
    labhud admin [--env|--remove]                  the one admin account (init.py admin)
    labhud pin [--clear]                           the display's lock PIN (init.py pin)
    labhud source list                             every source a form can set up, and which are set
    labhud source test <name> [KEY=value ...]      try one with these values (the rest as set)
    labhud source add <name> [KEY=value ...] [--page <page>]   tried, saved, and a card for it
    labhud source remove <name>                    its keys and its card go
    labhud agent list                              the agents, what each may do
    labhud agent code                              a pairing code and the lines that install one
    labhud agent allow <agent> <cap> [target ... | --all]      tick a capability (and its targets)
    labhud agent deny <agent> <cap> [target ...]   untick the targets, or the whole capability
    labhud agent logs <agent> <container>          a ticked container's last lines
    labhud agent prune <agent>                     clean the unused images (the target "images" ticked)
    labhud agent update <agent> | --all            update to the release labhud pins (a service, not a container)
    labhud agent auto on|off                       older agents update themselves when they dial in
    labhud phone pair [name]                       a one-time link and QR code for the app
    labhud phone list / phone remove <name>        the paired phones
    labhud page list                               pages, groups and cards
    labhud page move <card> <page>[/<group>]       a card to another page or group
    labhud organize <page> [--yes]                 the page grouped by kind of host (shown first)
    labhud config show | edit | take-over          the config file: print it, edit it in $EDITOR, take it over
    labhud scan [<cidr>]                           look for services on the network
    labhud alert list                              the rules and what is wrong now
    labhud alert add <card|*> <cpu|mem|disk|temp|offline|failed> [above] [minutes]
    labhud alert remove <number>                   (the number from `alert list`)
    labhud notify test [<url>] [--token T]         one notification now (to <url>, or where it is set)
    labhud notify set <url> [--token T]            the ntfy topic labhud sends to (labhud restarts)
    labhud notify forget                           no ntfy any more (labhud restarts)
```

In ⚙, the **?** beside a section's title shows the commands for that section, each with Copy.

Notes:

- `source add <name>` without `KEY=value` asks for each field. Secrets are not echoed. A field
  left empty keeps what is already set.
- `agent allow <agent> wol <mac>` takes a MAC typed in, like ⚙: a machine that is off is not in
  the agent's ARP table.
- `alert add` and `alert remove` wait until labhud has applied the new rules (it reads the file
  again within a few seconds), so the numbers in the next `alert list` are the new ones.
- `config edit` opens `$VISUAL` or `$EDITOR` (default `vi`). Labhud checks the file; when it does
  not load, you see why and can edit it again. Nothing is saved until it loads.
- After `config take-over`, or after a source's keys are saved, labhud restarts with a new key.
  The next command reads that new key by itself.
