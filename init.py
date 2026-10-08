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
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from sources._common import _NO_VERIFY, beyond_reading, env_name  # noqa: E402


def fingerprint(url):
    """The SHA-256 of the certificate a service presents, as LABHUD_PINS wants it. Compare it with
    what the service itself shows (Proxmox: Node > System > Certificates) before you trust it."""
    import hashlib
    import ssl
    u = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    pem = ssl.get_server_certificate((u.hostname, u.port or 443), timeout=10)
    return f"{u.hostname}:{u.port or 443}", hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def ask(question, default="", secret=False, optional=False):
    shown = f" [{default}]" if default else ""
    while True:
        answer = (getpass.getpass if secret else input)(f"{question}{shown}: ").strip()
        if answer or default or optional:
            return answer or default


def get(url, header=None, timeout=10):
    req = urllib.request.Request(url, headers=header or {})
    with urllib.request.urlopen(req, timeout=timeout, context=_NO_VERIFY) as r:
        return json.loads(r.read() or b"{}")


def discover(url, token_id, secret):
    """{"version", "nodes": [name], "guests": {node: [(vmid, name, type)]}, "extra": [privilege],
    "addresses": {node: ip, or "" for the node at `url`}}."""
    header = {"Authorization": f"PVEAPIToken={token_id}={secret}"}
    version = get(f"{url}/api2/json/version", header).get("data", {}).get("version", "?")
    nodes = sorted(n["node"] for n in get(f"{url}/api2/json/nodes", header).get("data", []))
    guests = {n: [] for n in nodes}
    for r in get(f"{url}/api2/json/cluster/resources?type=vm", header).get("data", []):
        if r.get("node") in guests and not r.get("template"):
            guests[r["node"]].append((int(r["vmid"]), r.get("name") or str(r["vmid"]), r.get("type", "qemu")))
    for g in guests.values():
        g.sort()
    extra = beyond_reading(get(f"{url}/api2/json/access/permissions", header).get("data"))
    # what each node card pings: the address given for the node that answered, the others' own IPs
    addresses = {}
    try:
        for r in get(f"{url}/api2/json/cluster/status", header).get("data", []):
            if r.get("type") == "node" and r.get("name") in guests:
                addresses[r["name"]] = "" if r.get("local") else r.get("ip", "")
    except Exception:
        pass  # older versions or missing rights: the cards then ping the node names
    return {"version": version, "nodes": nodes, "guests": guests, "extra": extra, "addresses": addresses}


def geocode(city):
    """(latitude, longitude, timezone, name) from Open-Meteo's free geocoding, or None."""
    q = urllib.parse.urlencode({"name": city, "count": 1})
    found = get(f"https://geocoding-api.open-meteo.com/v1/search?{q}").get("results") or []
    if not found:
        return None
    r = found[0]
    return round(r["latitude"], 2), round(r["longitude"], 2), r.get("timezone", ""), r.get("name", city)


def ident(text):
    """A config id from any name: "My Node" -> "my-node"."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def q(text):
    """A TOML string (JSON's escaping is valid TOML for a basic string)."""
    return json.dumps(text, ensure_ascii=False)


def build_config(found, host, weather=None, title="LABHUD"):
    """The text of config.toml for what discover() found. `host` is what the node cards ping."""
    out = [
        "# Made by labhud init. Every option is explained in config.example.toml; check this",
        "# file after an edit with: python3 config.py config.toml",
        "",
        f"title = {q(title)}",
        "",
        "[[page]]",
        'id = "general"',
        'title = "GENERAL"',
    ]
    strip = []
    for node in found["nodes"]:
        nid = ident(node)
        out += ["", "  [[page.group]]", f'  id = "node-{nid}"', f"  title = {q(node.upper())}", "",
                "    [[page.group.card]]", f'    id = "host-{nid}"', f"    name = {q(node.upper())}"]
        # the node that answered is pinged at the address given; the others at their own IP
        addr = found.get("addresses", {}).get(node)
        out.append(f"    ping = {q(host if addr == '' or len(found['nodes']) == 1 else addr or node)}")
        out += ["    large = true", "    metrics = ["]
        for key, label, fmt in (("vms", "VM", "count"), ("lxc", "LXC", "count"), ("cpu", "CPU", "percent"),
                                ("mem_used_of", "RAM", "used_of"), ("disk_used_of", "Disk", "used_of")):
            out.append(f'      {{ key = "proxmox.{node}.{key}", label = "{label}", format = "{fmt}" }},')
        out += ["    ]", f"    panel = {q('host:' + node)}"]
        for vmid, name, _ in found["guests"][node]:
            out += ["", "    [[page.group.card]]", f'    id = "vm-{nid}-{vmid}"', f"    name = {q(name)}",
                    f'    proxmox = "{node}/{vmid}"']
        strip.append((node.upper()[:4], node, f"host-{nid}"))
    for code, name, card in strip:
        out += ["", "[[strip]]", f"code = {q(code)}", f"name = {q(name)}", f"card = {q(card)}"]
    if weather:
        lat, lon, tz, city = weather
        out += ["", "[weather]", f"latitude = {lat}", f"longitude = {lon}"]
        if tz:
            out.append(f"timezone = {q(tz)}")
        out.append(f"city = {q(city)}")
    return "\n".join(out) + "\n"


def build_env(found, url, token_id, secret, hosts, pin=None):
    out = ["# Made by labhud init. Keep this file readable only by you (chmod 600).",
           "# Every other source and setting: .env.example.",
           f"LABHUD_HOSTS={','.join(hosts)}",
           ""]
    if pin:
        out += ["# The certificate Proxmox presented during init: anything else is refused (docs/security.md).",
                f"LABHUD_PINS={pin[0]}={pin[1]}", ""]
    out += [f"LABHUD_PROXMOX_NODES={','.join(found['nodes'])}"]
    for node in found["nodes"]:
        key = env_name(node)
        out += [f"LABHUD_PROXMOX_{key}_URL={url}", f"LABHUD_PROXMOX_{key}_TOKEN_ID={token_id}",
                f"LABHUD_PROXMOX_{key}_TOKEN_SECRET={secret}"]
    return "\n".join(out) + "\n"


def write(folder, name, text, mode):
    path = os.path.join(folder, name)
    if os.path.exists(path):
        path += ".new"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)
    return path


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
