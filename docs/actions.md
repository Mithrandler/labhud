# Actions

labhud can show buttons in a card's panel (wake a host, start or shut down a VM, run a job).
They are **off by default**. With `LABHUD_ACTION_URL` unset, no button is drawn and nothing can
be sent.

## How an action travels

```
tap on the display ──POST /action/<name>──▶  your agent  ──▶  the command
     (browser)          Origin: <display>     (checks)
```

The browser sends the request straight to the agent. **labhud itself never runs or relays an
action.** The labhud server holds API keys for every service it reads. If it could also run
actions, anyone who reached it, or any other container on the same host, could run them too.
Keeping actions out of the server means the agent can trust the device the display runs on,
and nothing else.

## Turning them on

1. Run an agent on the machine that should do the work. [`agents/labhud-agent.py`](../agents/labhud-agent.py)
   is a complete one: standard library only, with commands listed in a TOML file
   ([`actions.example.toml`](../agents/actions.example.toml)) and a systemd unit
   ([`labhud-agent.service`](../agents/labhud-agent.service)).
2. Set on the agent:
   - `LABHUD_AGENT_ACTIONS`: the actions file.
   - `LABHUD_AGENT_ORIGIN`: the display's origin exactly as the browser sends it, for example
     `http://192.0.2.5:8095`. Use the address the display device actually opens, not `localhost`.
   - `LABHUD_AGENT_ALLOW`: the IPs allowed to post. This is **the device the display runs on**
     (a wall tablet, or a VPN peer), **not** the labhud server.
3. Set `LABHUD_ACTION_URL=http://<agent host>:9189/action/` for labhud, then restart it. The startup
   log says `actions: on`.
4. List the actions on a card in `config.toml`:

   ```toml
   [[page.group.card]]
   id = "nas"
   name = "NAS"
   ping = "192.0.2.20"
   actions = ["wol-nas"]
   ```

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

## The contract, if you write your own agent

- `POST /action/<name>`, empty body. Names are `[a-z0-9-]`.
- Accept it only if **both** checks pass:
  - `Origin` equals the display's origin. This stops other web pages from posting through the
    viewer's browser.
  - The source IP is on your allowlist. `Origin` is trivial to fake outside a browser (`curl -H`),
    so it is not enough on its own.
- Answer JSON `{"ok": true, "action": "<name>"}` or `{"ok": false, "error": "<short text>"}`, with
  `Access-Control-Allow-Origin: <display origin>`. Without that header the browser hides the
  answer and the display shows "could not send the action".
- Never pass anything from the request to a shell. The name should only select a command you
  wrote down in advance.
- Firewall the port to the same IPs as well. The allowlist is the last line of defence, not the
  only one.

## Security notes

labhud has no login. Anyone who can open the page can press the buttons, and the allowlist
only narrows that down to people standing at an allowed device. Put the display behind a VPN or
a reverse proxy with authentication, and keep destructive actions (shutdown, stop) to what you
would be fine with a guest pressing.
