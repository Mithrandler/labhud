# Security model

labhud is built for one screen on a trusted network. It has **no login**, by design: a wall
display cannot type a password.

## What the page reveals

The snapshot sent to the browser holds what you configured: host names, internal addresses,
guest names, storage use, and whatever your own JSON documents carry (logins, alerts, DNS
statistics). Treat the page as you would your network map.

The keys never leave the server. The browser only receives the results.

## The layers to put in front of it

1. **A firewall rule** that lets only the display (and, if you like, your VPN) reach port 8095.
   This is the protection that matters.
2. **`LABHUD_HOSTS`**: labhud answers only to the `host:port` names you list, and returns 421 to
   anything else. Without it, a web page opened in the display's browser could point a domain it
   controls at your server (DNS rebinding) and read the snapshot.
3. If you want it outside your LAN: **a VPN** (WireGuard, Tailscale), or a reverse proxy **with
   authentication** in front (Authelia, Authentik, oauth2-proxy). Never a plain port forward.

The container runs as an unprivileged user (uid 10001), on a read-only filesystem, with every
capability dropped except `NET_RAW` for ping (see `compose.example.yaml`).

## Actions

Off unless `LABHUD_ACTION_URL` is set. labhud never runs an action: it only shows buttons, and
the browser sends a POST to an agent you control. With no login, anyone who opens the page can
press them, so the agent must check:

- the `Origin` header (exactly the address the display uses), and
- the source IP (only the display),

and run only the commands listed in its own file. `agents/labhud-agent.py` does all three; see
[actions.md](actions.md).

## Reporting a problem

Open an issue on GitHub. For anything that should not be public, use GitHub's private
vulnerability reporting (the repository's *Security* tab).
