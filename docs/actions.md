# Actions

labhud can show buttons in a card's panel (wake a host, start or shut down a VM, run a job).
They are **off by default**. With `LABHUD_ACTION_URL` unset, no button is drawn and nothing can
be sent.

## How an action travels

```
tap on a display ──POST /action/<name>──▶  labhud  ──POST + signature──▶  your agent  ──▶  the command
or in the app         (page or token)      (checks)                        (checks)
```

A display posts to labhud's own page; the phone app posts with its token. labhud checks where the
request comes from (`Origin` for a page, the token for an app), that the display's lock allows it,
and that the action is offered by a card in `config.toml`. Then it forwards the action to the
agent with a signature: an HMAC-SHA256 of the time, a random nonce and the action's name, made
with a secret only labhud and the agent know. The agent runs only actions signed with that
secret, less than 30 seconds old, and never the same signature twice. A copied request cannot
be replayed, and nothing else on your network can make a valid one, not even another container
on labhud's host. The agent also accepts posts only from labhud's IP.

Both `LABHUD_ACTION_URL` and `LABHUD_ACTION_SECRET` are needed. With the URL alone actions stay
off and the startup log says why.

## Turning them on

1. Run an agent on the machine that should do the work. [`agents/labhud-agent.py`](../agents/labhud-agent.py)
   is a complete one: standard library only, with commands listed in a TOML file
   ([`actions.example.toml`](../agents/actions.example.toml)) and a systemd unit
   ([`labhud-agent.service`](../agents/labhud-agent.service)).
2. Make a secret, once: `python3 -c "import secrets; print(secrets.token_hex(32))"`.
3. Set on the agent:
   - `LABHUD_AGENT_ACTIONS`: the actions file.
   - `LABHUD_AGENT_SECRET`: the secret.
   - `LABHUD_AGENT_ALLOW`: **the labhud server's IP** (with Docker: the host's address as the
     agent sees it).
4. Set for labhud `LABHUD_ACTION_URL=http://<agent host>:9189/action/` and
   `LABHUD_ACTION_SECRET=<the same secret>`, then restart it. The startup log says
   `actions: on, signed`.
5. List the actions on a card in `config.toml`:

   ```toml
   [[page.group.card]]
   id = "nas"
   name = "NAS"
   ping = "192.0.2.20"
   actions = ["wol-nas"]
   ```

Keep the secret in a root-only file on each side: on the agent's machine with systemd,
`/etc/credstore/<name>` and `ImportCredential=` in the unit; for labhud, `LABHUD_ACTION_SECRET_FILE`
(see [security.md](security.md)). Copy it from one machine to the other directly, never through
a chat or a ticket.

## Which buttons show

A card shows only what makes sense for its current state:

- **Up:** everything except starts (`*-start`, `wol-*`, `game-start-*`).
- **Down:** everything except stops (`*-shutdown`, `*-reboot`, `shutdown-*`, `game-stop-*`,
  `game-restart-*`). Other actions, such as a report or an update, always show.

Every button asks for confirmation first. Labels come from the name (`wol-*` gives START,
`shutdown-*` gives SHUT DOWN, `vm-<node>-<vmid>-start|shutdown|reboot` gives START, SHUT DOWN
or REBOOT). Anything else can be named in the config:

```toml
[action.fleet-update]
label = "UPDATE"
confirm = "Update every host now? Some may reboot."
```

## Asking instead of confirming

An action can offer choices instead of YES/NO. Each choice is a separate action name that your
agent knows, so the protocol does not change:

```toml
[[page.group.card]]
id = "vm-node1-101"
actions = ["vm-node1-101-reboot"]

[action.vm-node1-101-reboot]
confirm = "Reboot the VM when?"
choices = [{ label = "NOW", action = "vm-node1-101-reboot" },
           { label = "TONIGHT", action = "vm-node1-101-reboot-tonight" }]
```

labhud forwards only the actions on a card and the choices written here.

## The contract, if you write your own agent

- `POST /action/<name>`, empty body. Names are `[a-z0-9-]`.
- Accept it only from labhud's IP, and only if
  - `X-Labhud-Time` is within 30 seconds of your clock,
  - `X-Labhud-Signature` equals the hex HMAC-SHA256, keyed with the secret, of
    `<X-Labhud-Time>.<X-Labhud-Nonce>.<name>` (compare in constant time),
  - you have not accepted that signature in the last 30 seconds.
- Answer JSON `{"ok": true, "action": "<name>"}` or `{"ok": false, "error": "<short text>"}`.
- Never pass anything from the request to a shell. The name should only select a command you
  wrote down in advance.
- Firewall the port to the same IPs as well. The checks are the last line of defence, not the
  only one.

## Security notes

Signing proves the press came through labhud, not who pressed. Who may press is labhud's part:
a display shows the buttons only when signed in as the admin (top right), its PIN (`[lock]`, ⚙ >
Security) gates them on top of that, and an app needs a paired token (⚙ > Devices > Phones). Keep labhud itself on your LAN or behind a VPN, and keep
destructive actions (shutdown, stop) to what you would be fine with a guest at the display
pressing once it is unlocked.
