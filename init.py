#!/usr/bin/env python3
"""labhud init: a first config.toml and .env from your Proxmox, in a few questions.

    python3 init.py [folder]          # writes <folder>/config.toml and <folder>/.env
    python3 init.py agent <name> [URL]  # a key and a card for a machine running labhud-agent
    python3 init.py edit [minutes]      # a one-time link to edit config.toml in a browser
    python3 init.py pin [--clear|--hash]  # the display's lock PIN (also settable on the screen, ⚙)
    python3 init.py pair [name] [URL]   # a QR code that pairs the LabHUD app (also in the display's ⚙)
    python3 init.py devices             # the paired phones; init.py unpair <name> removes one
    docker run --rm -it -v "$PWD:/out" ghcr.io/mithrandler/labhud:latest python3 /app/init.py /out

It asks for a Proxmox VE address and an API token, checks that they work and that the token can
only read, finds the nodes and guests, and writes a GENERAL page with one group per node (the node
and its guests) and a status strip. A weather city is optional. Files that already exist are
not touched: the new ones are written next to them as config.toml.new / .env.new.

Standard library only, like the rest of labhud. Edit the result by hand afterwards:
config.example.toml explains every option.
"""

import getpass
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from onboard import add_agent, build_config, build_env, discover, fingerprint, geocode, write  # noqa: E402,F401


def ask(question, default="", secret=False, optional=False):
    shown = f" [{default}]" if default else ""
    while True:
        answer = (getpass.getpass if secret else input)(f"{question}{shown}: ").strip()
        if answer or default or optional:
            return answer or default


def agent(args):
    """init.py agent <name> [labhud URL]: a key and a card for a machine running labhud-agent,
    and the line that installs it there."""
    import envfiles
    if not args:
        sys.exit("usage: python3 init.py agent <name> [http://labhud-host:8095]")
    name = args[0]
    cfg = config.config_path()
    env_path = envfiles.dotenv_path()
    try:
        key = add_agent(name, cfg, env_path)
    except (ValueError, OSError) as e:
        sys.exit(f"not added: {e}")
    url = args[1] if len(args) > 1 else ""
    if not url:
        hosts = [h for h in os.environ.get("LABHUD_HOSTS", "").split(",") if h.strip()
                 and not h.startswith(("localhost", "127."))]
        scheme = "https" if os.environ.get("LABHUD_TLS_CERT") else "http"
        url = f"{scheme}://{hosts[0].strip()}" if hosts else "http://<labhud-host>:8095"
    url = url.rstrip("/")
    print(f"Added {name}: its key is in {env_path}, its card in {cfg}; labhud picks both up in a few seconds.\n")
    print("On that machine (Linux with systemd and python3 3.11+), run:\n")
    print(f"  curl -fsSL {url}/agent/install.sh | sudo sh -s -- {name} {url}\n")
    print("and paste this key when it asks (it is shown only now):\n")
    print(f"  {key}\n")
    print(f"The card stays empty until the first push; /status lists {name} under the setup checklist.")


def edit(args):
    """init.py edit [minutes]: a one-time link to edit config.toml in a browser."""
    import editmode
    minutes = int(args[0]) if args and args[0].isdigit() else editmode.DEFAULT_MINUTES
    cfg = config.config_path()
    if not os.access(cfg, os.W_OK):
        sys.exit(f"{cfg} is not writable by labhud: mount it read-write to edit it from a browser")
    try:
        code = editmode.open_ticket(cfg, minutes)
    except OSError as e:
        sys.exit(f"cannot write {editmode.ticket_path(cfg)} ({e.strerror}): the editor needs config.toml's "
                 "folder writable (the setup page's /config folder)")
    hosts = [h for h in os.environ.get("LABHUD_HOSTS", "").split(",") if h.strip()]
    scheme = "https" if os.environ.get("LABHUD_TLS_CERT") else "http"
    where = f"{scheme}://{hosts[0].strip()}" if hosts else "http://<labhud-host>:8095"
    print(f"Open {where}/edit#{code}  (valid {minutes} minutes)")


def lock_pin(args):
    """init.py pin: set the display's 4-digit lock PIN (kept as a scrypt hash in LABHUD_DATA);
    --clear removes it (a forgotten PIN), --hash only prints the line for .env (no LABHUD_DATA)."""
    import envfiles  # noqa: F401  (LABHUD_DATA may come from a _FILE)
    import lock
    import store
    if args[:1] == ["--clear"]:
        if not store.enabled():
            sys.exit(f"no usable LABHUD_DATA ({store.PROBLEM or 'not set'}): remove {lock.ENV_KEY} from .env instead")
        store.save(lock.STORE_KEY, "")
        return print("PIN removed: the display is open to every touch again.")
    while True:
        first = ask("New PIN, 4 digits (not shown)", secret=True)
        if not lock.PIN.fullmatch(first):
            print("  exactly 4 digits")
            continue
        if ask("Again (not shown)", secret=True) == first:
            break
        print("  the two do not match, again")
    hashed = lock.hash_pin(first)
    if args[:1] == ["--hash"] or not store.enabled():
        if args[:1] != ["--hash"]:
            print(f"no usable LABHUD_DATA ({store.PROBLEM or 'not set'}), so add this line to .env and recreate the container:")
        return print(f"{lock.ENV_KEY}={hashed}")
    store.save(lock.STORE_KEY, hashed)
    print("PIN set: the display locks now (open screens within a few seconds).")


def display_url():
    hosts = [h.strip() for h in os.environ.get("LABHUD_HOSTS", "").split(",") if h.strip()
             and not h.strip().startswith(("localhost", "127."))]
    scheme = "https" if os.environ.get("LABHUD_TLS_CERT") else "http"
    return f"{scheme}://{hosts[0]}" if hosts else ""


def pair(args):
    """init.py pair [name] [URL]: a one-time code for the LabHUD app, as a link and a QR code.
    URL is labhud's address as the phone will reach it (default: the first name in LABHUD_HOSTS)."""
    import envfiles  # noqa: F401  (LABHUD_DATA may come from .env or a _FILE)
    import devices
    import qr
    import store
    url = next((a for a in args if re.match(r"^https?://", a)), "") or display_url()
    name = " ".join(a for a in args if not re.match(r"^https?://", a))
    if not store.enabled():
        sys.exit(f"no usable LABHUD_DATA ({store.PROBLEM or 'not set'}): pairing keeps its devices there")
    if not url:
        sys.exit("usage: python3 init.py pair [name] http://labhud-host:8095  (no LABHUD_HOSTS name to use)")
    link = devices.pair_link(url, devices.new_code(name), os.environ.get("LABHUD_TLS_CERT", ""))
    print(qr.terminal(link))
    print(f"\nScan it with the LabHUD app (Add server), or paste this link there:\n\n  {link}\n")
    print(f"It works once, within {devices.PAIR_MINUTES} minutes. The phone must reach {url} (home Wi-Fi or VPN).")


def list_devices(args):
    """init.py devices: the paired phones. init.py unpair <name>: remove one, at once."""
    import envfiles  # noqa: F401
    import devices
    import store
    if not store.enabled():
        sys.exit(f"no usable LABHUD_DATA ({store.PROBLEM or 'not set'})")
    if args:
        name = " ".join(args)
        if not devices.unpair(name):
            sys.exit(f"no paired device called {name!r} (init.py devices lists them)")
        return print(f"{name}: unpaired, its token no longer works.")
    found = devices.listing()
    if not found:
        return print("No paired devices. init.py pair adds one.")
    day = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "never"  # noqa: E731
    for d in found:
        print(f"{d['name']:<24} paired {day(d['paired'])}   last seen {day(d['seen'])}")


def main():
    if sys.argv[1:2] == ["pair"]:
        return pair(sys.argv[2:])
    if sys.argv[1:2] == ["devices"]:
        return list_devices([])
    if sys.argv[1:2] == ["unpair"]:
        if len(sys.argv) < 3:
            sys.exit("usage: python3 init.py unpair <name>")
        return list_devices(sys.argv[2:])
    if sys.argv[1:2] == ["pin"]:
        return lock_pin(sys.argv[2:])
    if sys.argv[1:2] == ["edit"]:
        return edit(sys.argv[2:])
    if sys.argv[1:2] == ["agent"]:
        return agent(sys.argv[2:])
    if sys.argv[1:2] == ["fingerprint"]:
        if len(sys.argv) < 3:
            sys.exit("usage: python3 init.py fingerprint https://host:port [...]")
        pins = []
        for url in sys.argv[2:]:
            where, fp = fingerprint(url)
            print(f"{where}  {':'.join(fp[i:i + 2] for i in range(0, 64, 2)).upper()}")
            pins.append(f"{where}={fp}")
        print("\nCompare with the fingerprint the service shows itself, then add to .env:")
        print("LABHUD_PINS=" + ",".join(pins))
        return
    folder = sys.argv[1] if len(sys.argv) > 1 else "."
    print("labhud init: a first config from your Proxmox VE. Ctrl-C stops without writing anything.\n")
    print("You need an API token: Datacenter > Permissions > API Tokens > Add, with privilege")
    print("separation on, then give the token the role PVEAuditor on '/' (Permissions > Add).\n")
    while True:
        url = ask("Proxmox address", "https://192.0.2.10:8006").rstrip("/")
        if not re.match(r"^https?://", url):
            url = "https://" + url
        if not re.search(r":\d+$", urllib.parse.urlsplit(url).netloc):
            url += ":8006"
        token_id = ask("Token ID (user@realm!name)")
        secret = ask("Token secret (not shown)", secret=True)
        try:
            found = discover(url, token_id, secret)
            break
        except Exception as e:
            print(f"  could not read Proxmox: {e}. Check the address and the token, and try again.\n")
    total = sum(len(g) for g in found["guests"].values())
    print(f"  Proxmox {found['version']}: {len(found['nodes'])} node(s) ({', '.join(found['nodes'])}), {total} guest(s).")
    if found["extra"]:
        print(f"  WARNING: this token can do more than read: {', '.join(found['extra'])}.")
        print("  labhud only needs PVEAuditor. It works anyway, but a leaked .env could then change your lab.")
    else:
        print("  The token can only read. Good.")

    host = urllib.parse.urlsplit(url).hostname
    pin = None
    if url.startswith("https://"):
        try:
            pin = fingerprint(url)
            fp = ":".join(pin[1][i:i + 2] for i in range(0, 64, 2)).upper()
            print(f"\n  Its certificate: {fp}")
            print("  Compare with Node > System > Certificates (SHA-256 fingerprint) in Proxmox.")
            if ask("  Pin it, so labhud talks only to this certificate? (y/n)", "y").lower() not in ("y", "yes"):
                pin = None
        except Exception as e:
            print(f"  could not read its certificate ({e}); not pinned")
    weather = None
    city = ask("\nCity for the weather (empty: no weather)", optional=True)
    if city:
        try:
            weather = geocode(city)
            print(f"  {weather[3]}: {weather[0]}, {weather[1]}" if weather else "  not found, skipped")
        except Exception as e:
            print(f"  could not look it up ({e}), skipped")
    display = ask("\nThe address the display will open, as host:port", f"{host}:8095" if host else "localhost:8095")
    hosts = sorted({display, "localhost:8095", "127.0.0.1:8095"})

    text = build_config(found, host, weather)
    tmp = os.path.join(folder, ".labhud-init-check.toml")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        config.load(tmp)
    finally:
        os.unlink(tmp)
    cfg = write(folder, "config.toml", text, 0o644)
    env = write(folder, ".env", build_env(found, url, token_id, secret, hosts, pin), 0o600)
    print(f"\nWritten: {cfg} and {env}.")
    if cfg.endswith(".new") or env.endswith(".new"):
        print("Some files already existed and were left alone: compare the .new ones and keep what you want.")
    print("Next: docs/install.md, step 4 (start it).")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nstopped, nothing written")
