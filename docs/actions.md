# Actions

labhud can show buttons in a card's panel (wake a host, start or shut down a VM, run a job).
They are **off by default**. With `LABHUD_ACTION_URL` unset, no button is drawn and nothing can
be sent.

## Two ways an action travels

**Signed (recommended)**, with `LABHUD_ACTION_SECRET` set:

```
tap on the display ──POST /action/<name>──▶  labhud  ──POST + signature──▶  your agent  ──▶  the command
     (browser)          Origin: labhud         (checks)                        (checks)
```

The browser posts to labhud's own page. labhud checks that the request comes from that page
(`Origin`) and that the action is offered by a card in `config.toml`, then forwards it to the
agent with a signature: an HMAC-SHA256 of the time, a random nonce and the action's name, made
with a secret only labhud and the agent know. The agent runs only actions signed with that
secret, less than 30 seconds old, and never the same signature twice. A copied request cannot
be replayed, and nothing else on your network can make a valid one, not even another container
on labhud's host. The agent accepts posts only from labhud's IP.

**Direct**, without a secret:

```
tap on the display ──POST /action/<name>──▶  your agent  ──▶  the command
     (browser)          Origin: <display>     (checks)
```

The browser posts straight to the agent, which checks `Origin` and that the source IP is the
display device. labhud never touches the action. This works, but `Origin` can be faked outside
a browser, so the display's IP is the whole protection, and a display on Wi-Fi or behind a VPN
may not have a fixed one.

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

For the direct way, leave out both secrets and set on the agent `LABHUD_AGENT_ORIGIN` (the
display's origin exactly as its browser sends it, e.g. `http://192.0.2.5:8095`) and
`LABHUD_AGENT_ALLOW` = the display device's IP, not labhud's.

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

labhud forwards (in signed mode) only the actions on a card and the choices written here.

## The contract, if you write your own agent

- `POST /action/<name>`, empty body. Names are `[a-z0-9-]`.
- **Signed:** accept it only from labhud's IP, and only if
  - `X-Labhud-Time` is within 30 seconds of your clock,
  - `X-Labhud-Signature` equals the hex HMAC-SHA256, keyed with the secret, of
    `<X-Labhud-Time>.<X-Labhud-Nonce>.<name>` (compare in constant time),
  - you have not accepted that signature in the last 30 seconds.
- **Direct:** accept it only if `Origin` equals the display's origin **and** the source IP is
  on your allowlist, and answer with `Access-Control-Allow-Origin: <display origin>` (without
  it the browser hides the answer and the display says "could not send the action").
- Answer JSON `{"ok": true, "action": "<name>"}` or `{"ok": false, "error": "<short text>"}`.
- Never pass anything from the request to a shell. The name should only select a command you
  wrote down in advance.
- Firewall the port to the same IPs as well. The checks are the last line of defence, not the
  only one.

## Security notes

labhud has no login. Anyone who can open the page can press the buttons; signing proves the
press came through labhud's page, not who pressed. Put the display behind a VPN or a reverse
proxy with authentication, and keep destructive actions (shutdown, stop) to what you would be
fine with a guest pressing.
