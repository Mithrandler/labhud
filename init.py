#!/usr/bin/env python3
"""labhud init: a first config.toml and .env from your Proxmox, in a few questions.

    python3 init.py [folder]          # writes <folder>/config.toml and <folder>/.env
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
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from onboard import build_config, build_env, discover, fingerprint, geocode, write  # noqa: E402,F401


def ask(question, default="", secret=False, optional=False):
    shown = f" [{default}]" if default else ""
    while True:
        answer = (getpass.getpass if secret else input)(f"{question}{shown}: ").strip()
        if answer or default or optional:
            return answer or default


def main():
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
