#!/usr/bin/env python3
"""labhud: what ⚙ does, from a terminal, on the labhud that runs next to it.

    docker exec -it labhud labhud <command>

It calls the same routes as ⚙ (the same checks, the same code), on 127.0.0.1, with the key the
running server wrote in LABHUD_DATA/cli.json at its start (mode 600: only the server's own user,
which `docker exec` is, can read it). So labhud must be running, with LABHUD_DATA. Without it
only `admin` and `pin` work, as with init.py.

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
    labhud phone pair [name]                       a one-time link and QR code for the app
    labhud phone list / phone remove <name>        the paired phones
    labhud page list                               pages, groups and cards
    labhud page move <card> <page>[/<group>]       a card to another page or group
    labhud organize <page> [--yes]                 the page grouped by kind of host (shown first)
    labhud config show | edit | take-over          the config file: print it, edit it in $EDITOR, take it over
    labhud scan [<cidr>]                           look for services on the network
    labhud alert list                              the rules and what is wrong now
    labhud alert add <card|*> <cpu|mem|disk|temp|offline> [above] [minutes]
    labhud alert remove <number>                   (the number from `alert list`)
    labhud notify test [<url>] [--token T]         one notification now (to <url>, or where it is set)
    labhud notify set <url> [--token T]            the ntfy topic labhud sends to (labhud restarts)
    labhud notify forget                           no ntfy any more (labhud restarts)

Standard library only, like the rest of labhud.
"""

import getpass
import http.client
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

USAGE = "usage:\n" + __doc__.split("\n\n")[3] + "\n\n(" + __doc__.split("\n\n")[1].strip() + ")"
CLI_FILE = "cli.json"


class Failed(Exception):
    pass


# --- talking to the running labhud ---

def connection():
    """(url, key, host) from LABHUD_DATA/cli.json, which the running server writes."""
    import envfiles  # noqa: F401  (LABHUD_DATA may come from .env or a _FILE)
    data = os.environ.get("LABHUD_DATA", "")
    if not data:
        raise Failed("LABHUD_DATA is not set: the labhud command talks to labhud through a file there")
    path = os.path.join(data, CLI_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
    except FileNotFoundError:
        raise Failed(f"{path} is not there: is labhud running (and at least this version)?") from None
    except PermissionError:
        raise Failed(f"{path}: not readable by uid {os.getuid()}; run it as labhud's own user "
                     "(docker exec labhud labhud …)") from None
    except (OSError, ValueError) as e:
        raise Failed(f"{path}: {e}") from None
    return c["url"], c["key"], c.get("host") or ""


def call(path, body=None, method=None):
    """The JSON answer of one route; Failed with labhud's own words when it says no."""
    url, key, host = connection()
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https":
        # loopback to labhud's own certificate: the key says who we are, the address who it is
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        conn = http.client.HTTPSConnection(u.hostname, u.port, timeout=120, context=ctx)
    else:
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=120)
    headers = {"X-Labhud-Cli": key, "Host": host or u.netloc}
    data = None
    if body is not None or method == "POST":
        data = json.dumps(body or {}).encode()
        headers["Content-Type"] = "application/json"
    try:
        conn.request(method or ("POST" if data is not None else "GET"), path, data, headers)
        r = conn.getresponse()
        raw = r.read()
    except OSError as e:
        raise Failed(f"labhud does not answer at {url} ({e}): is it running?") from None
    finally:
        conn.close()
    try:
        answer = json.loads(raw or b"{}")
    except ValueError:
        raise Failed(f"{path}: HTTP {r.status}, not JSON") from None
    if r.status == 403:
        raise Failed("refused: labhud was restarted since cli.json was read, or this is not its own machine; try again")
    if r.status >= 400 or answer.get("ok") is False:
        problems = answer.get("problems")
        raise Failed("\n".join(problems) if problems else str(answer.get("error") or f"HTTP {r.status}"))
    return answer


def setup(op, body=None):
    return call(f"/api/setup/{op}", body or {})


def table(rows, head):
    rows = [[str(x) for x in r] for r in rows]
    widths = [max(len(x) for x in col) for col in zip(head, *rows)]
    for r in [head] + rows:
        print("  ".join(x.ljust(w) for x, w in zip(r, widths)).rstrip())


def values(args):
    """KEY=value pairs (a missing LABHUD_ prefix is added), the rest left as they are."""
    out, rest = {}, []
    for a in args:
        k, eq, v = a.partition("=")
        if eq and k.replace("_", "").isalnum():
            out[k if k.startswith("LABHUD_") else "LABHUD_" + k.upper()] = v
        else:
            rest.append(a)
    return out, rest


def option(args, name):
    """The value after --name (removed from args), or ""."""
    if name in args:
        i = args.index(name)
        if i + 1 >= len(args):
            raise Failed(f"{name} needs a value")
        v = args[i + 1]
        del args[i:i + 2]
        return v
    return ""


def restarted(answer):
    if answer.get("restart"):
        print("labhud restarts to read the new keys (a few seconds).")


# --- commands ---

def status(args):
    st = setup("status")
    print(f"config: {st['path']}" + (" (kept by labhud: ⚙ and this command write it)" if st["managed"]
                                     else f" (mounted: {st['mounted']})"))
    if st.get("problem"):
        print(f"problem: {st['problem']}")
    if not st["managed"]:
        print("take over: " + ("possible, `labhud config take-over`" if st.get("can_take_over") else "not possible now"))
    print(f"keys set in ⚙: {', '.join(st['secrets']) or 'none'}")
    agents = call("/api/agents/list", {})["agents"]
    print(f"agents: {sum(a['online'] for a in agents)} of {len(agents)} online")


def source(args):
    sub = args[:1]
    if sub == ["list"]:
        cat = setup("catalog")["sources"]
        return table([[c["name"], "yes" if c["configured"] else "", c["title"]] for c in cat], ["NAME", "SET", "WHAT"])
    if sub[0:1] and sub[0] in ("test", "add", "remove") and len(args) >= 2:
        name, rest = args[1], args[2:]
        cat = setup("catalog")["sources"]
        item = next((c for c in cat if c["name"] == name), None)
        if not item:
            raise Failed(f"no source {name!r}: `labhud source list` shows them")
        if sub == ["remove"]:
            restarted(setup("source-forget", {"name": name}))
            return print(f"✓ {name}: keys and card removed")
        page = option(rest, "--page")
        given, extra = values(rest)
        if extra:
            raise Failed(f"not KEY=value: {' '.join(extra)}")
        fields = {f["name"]: f for f in item["fields"]}
        unknown = [k for k in given if k not in fields]
        if unknown:
            raise Failed(f"{name} has no {', '.join(unknown)}; it takes: {', '.join(fields) or 'nothing'}")
        if not given and sys.stdin.isatty():   # nothing given: ask for each, secrets not shown
            for f in item["fields"]:
                ask = getpass.getpass if f["secret"] else input
                v = ask(f"{f['label']} ({f['name']}){' — ' + f['hint'] if f.get('hint') else ''}, empty = as set: ").strip()
                if v:
                    given[f["name"]] = v
        if sub == ["test"]:
            r = setup("try", {"name": name, "values": given})
            if not r.get("ok"):
                raise Failed(f"{name}: {r.get('error')}")
            return print(f"✓ {name} answers ({', '.join(r.get('keys') or []) or 'no values'})")
        r = setup("source-save", {"name": name, "values": given, "page": page})
        print(f"✓ {name} saved" + (f", card {r['card']}" if r.get("card") else ""))
        return restarted(r)
    raise Failed("usage: labhud source list | test <name> [KEY=value …] | add <name> [KEY=value …] [--page <page>] | remove <name>")


def find_agent(name):
    listing = call("/api/agents/list", {})
    a = next((x for x in listing["agents"] if x["name"].lower() == name.lower() or x["id"] == name), None)
    if not a:
        raise Failed(f"no agent {name!r}: {', '.join(x['name'] for x in listing['agents']) or 'none paired'}")
    return a, listing


def agent(args):
    sub = args[:1]
    if sub == ["list"]:
        listing = call("/api/agents/list", {})
        rows = []
        for a in listing["agents"]:
            ticked = [c + (f"({len(a['targets'].get(c, []))})" if c in listing["verbs"] else "") for c in a["caps"]]
            rows.append([a["name"], "yes" if a["online"] else "no", a["version"] or "—", a["addr"] or "—",
                         " ".join(ticked) or "nothing ticked", " ".join(c for c in a["found"] if c not in a["caps"])])
        return table(rows, ["NAME", "ONLINE", "VERSION", "ADDRESS", "TICKED (targets)", "FOUND, NOT TICKED"])
    if sub == ["code"]:
        r = call("/api/agents/code", {})
        print(f"Pairing code: {r['code']}  (once, within {r['minutes']} minutes)\n")
        print(f"As a service:\n  {r['install']}\n")
        if r.get("docker"):
            print(f"As a container:\n  {r['docker']}\n")
        return
    if sub in (["allow"], ["deny"]) and len(args) >= 3:
        a, listing = find_agent(args[1])
        cap, targets = args[2], args[3:]
        if cap not in listing["caps"]:
            raise Failed(f"no capability {cap!r}: {', '.join(listing['caps'])}")
        if cap not in a["found"]:
            raise Failed(f"{a['name']} did not find {cap} on its host (it found: {', '.join(a['found'])})")
        choices = a["choices"].get(cap, [])
        if targets == ["--all"]:
            targets = list(choices)
        missing = [t for t in targets if t not in choices and t not in a["targets"].get(cap, [])]
        if missing and cap != "wol":   # a MAC that is off is not in the ARP table: typed in, like ⚙
            raise Failed(f"{a['name']} has no {cap} {', '.join(missing)}; it has: {', '.join(choices) or 'none'}")
        caps, now = set(a["caps"]), set(a["targets"].get(cap, []))
        if sub == ["allow"]:
            caps.add(cap)
            now |= set(targets)
        elif targets:
            now -= set(targets)
        else:
            caps.discard(cap)
        call("/api/agents/caps", {"id": a["id"], "caps": sorted(caps)})
        if cap in listing["verbs"] and (targets or sub == ["allow"]):
            call("/api/agents/targets", {"id": a["id"], "cap": cap, "targets": sorted(now)})
        state = "ticked" if cap in caps else "off"
        return print(f"✓ {a['name']}: {cap} {state}" + (f", {len(now)} target(s)" if cap in listing["verbs"] and cap in caps else ""))
    if sub == ["logs"] and len(args) == 3:
        print(setup("logs", {"agent": args[1], "target": args[2]}).get("output", ""), end="")
        return
    raise Failed("usage: labhud agent list | code | allow <agent> <cap> [target … | --all] | deny <agent> <cap> [target …] | logs <agent> <container>")


def phone(args):
    sub = args[:1]
    if sub == ["pair"]:
        import qr
        r = call("/api/v1/pair-code", {"name": " ".join(args[1:])})
        print(qr.terminal(r["link"]))
        print(f"\nScan it with the LabHUD app (Add server), or paste this link there:\n\n  {r['link']}\n")
        return print(f"It works once, within {r['minutes']} minutes.")
    if sub == ["list"]:
        d = call("/api/v1/devices")
        when = lambda t: time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "—"  # noqa: E731
        return table([[x["name"], when(x.get("paired")), when(x.get("seen"))] for x in d["devices"]], ["NAME", "PAIRED", "LAST SEEN"])
    if sub == ["remove"] and len(args) >= 2:
        call("/api/v1/unpair", {"name": " ".join(args[1:])})
        return print(f"✓ {' '.join(args[1:])} removed: its token stops working now")
    raise Failed("usage: labhud phone pair [name] | list | remove <name>")


def show_pages(pages):
    for p in pages:
        print(f"{p['id']}  {p['title']}" + ("  (game servers)" if p.get("games") else ""))
        for g in p["groups"]:
            print(f"  {p['id']}/{g['id']}  {g['title']}" + ("  (adds guests by itself)" if g.get("auto") else ""))
            for c in g["cards"]:
                kind = c.get("kind") or c.get("guess") or ""
                print(f"      {c['id']}" + (f"  {c['name']}" if c["name"] != c["id"] else "") + (f"  [{kind}]" if kind else ""))


def as_layout(pages):
    """The editor's view back into what layout-save takes: cards by id, their kind kept."""
    return [{"id": p["id"], "title": p["title"], "groups": [
        {"id": g["id"], "title": g["title"], "cards": [{"id": c["id"], "kind": c.get("kind") or ""} if c.get("kind") else c["id"]
                                                       for c in g["cards"]]} for g in p["groups"]]} for p in pages]


def page(args):
    sub = args[:1]
    if sub == ["list"]:
        return show_pages(setup("layout")["pages"])
    if sub == ["move"] and len(args) == 3:
        card, (pid, _, gid) = args[1], args[2].partition("/")
        pages = setup("layout")["pages"]
        found = None
        for p in pages:
            for g in p["groups"]:
                for c in list(g["cards"]):
                    if c["id"] == card:
                        found = c
                        g["cards"].remove(c)
        if not found:
            raise Failed(f"no card {card!r}: `labhud page list` shows them")
        p = next((x for x in pages if x["id"] == pid), None)
        if not p or not p["groups"]:
            raise Failed(f"no page {pid!r} with a group to put it in")
        g = next((x for x in p["groups"] if x["id"] == gid), None) if gid else next((x for x in p["groups"] if not x["auto"]), p["groups"][0])
        if not g:
            raise Failed(f"no group {gid!r} on {pid}: {', '.join(x['id'] for x in p['groups'])}")
        g["cards"].append(found)
        setup("layout-save", {"pages": as_layout(pages)})
        return print(f"✓ {card} moved to {pid}/{g['id']}")
    raise Failed("usage: labhud page list | move <card> <page>[/<group>]")


def organize(args):
    if not args:
        raise Failed("usage: labhud organize <page> [--yes]")
    v = setup("organize", {"page": args[0]})
    show_pages([p for p in v["pages"] if p["id"] == args[0]])
    if "--yes" not in args:
        if not sys.stdin.isatty() or input("\nSave this? [y/N] ").strip().lower() not in ("y", "yes"):
            return print("not saved (--yes saves without asking)")
    setup("layout-save", {"pages": as_layout(v["pages"])})
    print(f"✓ {args[0]} organized")


def config_cmd(args):
    sub = args[:1]
    if sub == ["show"]:
        return print(setup("text")["text"], end="")
    if sub == ["take-over"]:
        r = setup("take-over")
        return print(f"✓ taken over into {r['path']}; labhud restarts and keeps it from now on")
    if sub == ["edit"]:
        t = setup("text")
        if not t.get("writable"):
            raise Failed(f"{t['path']} is mounted read-only: `labhud config take-over` first")
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        text = t["text"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            while True:
                if subprocess.call([editor, path]) != 0:
                    raise Failed(f"{editor} did not end well: nothing saved")
                with open(path, encoding="utf-8") as f:
                    new = f.read()
                if new == text:
                    return print("no change")
                try:
                    setup("save-text", {"text": new})
                    return print("✓ saved; labhud reads it within seconds")
                except Failed as e:
                    print(f"not saved, labhud says:\n{e}")
                    if not sys.stdin.isatty() or input("Edit again? [Y/n] ").strip().lower() in ("n", "no"):
                        raise Failed("nothing saved") from None
    raise Failed("usage: labhud config show | edit | take-over")


def scan_cmd(args):
    st = setup("scan-status")
    cidr = args[0] if args else st["default"]
    if not st.get("running"):
        st = setup("scan", {"cidr": cidr})
    print(f"Looking at {st['cidr']} ({st['total']} addresses)…", flush=True)
    while st.get("running"):
        time.sleep(1)
        st = setup("scan-status")
        if sys.stdout.isatty():
            print(f"\r  {st['done']}/{st['total']}", end="", flush=True)
    print()
    if st.get("error"):
        raise Failed(st["error"])
    if not st["found"]:
        return print("nothing found")
    table([[x["source"], x["url"], "set up" if x["configured"] else "new: labhud source add " + x["source"]]
           for x in st["found"]], ["SOURCE", "ADDRESS", ""])


def alert(args):
    sub = args[:1]
    a = setup("alerts")
    rules = a["rules"]
    if sub == ["list"]:
        names = {h["id"]: h["name"] for h in a["hosts"]}
        unit = lambda r: "" if r["what"] == "offline" else (" °C" if r["what"] == "temp" else "%")  # noqa: E731
        table([[i + 1, "every host" if r["on"] == "*" else names.get(r["on"], r["on"]), r["what"],
                "" if r["what"] == "offline" else f"> {r['above']}{unit(r)}", f"{r['minutes']} min"]
               for i, r in enumerate(rules)], ["#", "ON", "WHAT", "MARK", "FOR"])
        nt = a["notify"]
        quiet = f"quiet {nt['quiet'][0]}–{nt['quiet'][1]}" if nt.get("quiet") else "no quiet hours"
        print(f"\nnotifications: {nt['via']}, {quiet}; ntfy " + (a["ntfy"]["host"] or "set" if a["ntfy"]["set"] else "not set"))
        for x in a["active"]:
            print(f"now: {x.get('text') or x}")
        return
    if sub == ["add"] and len(args) >= 3:
        on, what = args[1], args[2]
        if what not in a["what"]:
            raise Failed(f"what: one of {', '.join(a['what'])}")
        if on != "*" and on not in {h["id"] for h in a["hosts"]}:
            raise Failed(f"on: * or a card with an agent: {', '.join(h['id'] for h in a['hosts']) or 'none'}")
        try:
            nums = [float(x) if "." in x else int(x) for x in args[3:5]]
        except ValueError:
            raise Failed("above and minutes are numbers") from None
        rule = {"on": on, "what": what}
        if what == "offline":
            rule["minutes"] = nums[0] if nums else 5
        else:
            if not nums:
                raise Failed(f"{what} needs a mark: labhud alert add {on} {what} <above> [minutes]")
            rule["above"] = nums[0]
            rule["minutes"] = nums[1] if len(nums) > 1 else 0
        rules = rules + [rule]
    elif sub == ["remove"] and len(args) == 2 and args[1].isdigit() and 1 <= int(args[1]) <= len(rules):
        rules = [r for i, r in enumerate(rules) if i != int(args[1]) - 1]
    else:
        raise Failed("usage: labhud alert list | add <card|*> <what> [above] [minutes] | remove <number>")
    setup("alerts-save", {"rules": rules, "notify": a["notify"]})
    # labhud applies the new file within a few seconds (it watches it): wait, so that the next
    # `alert list` or `alert remove <n>` sees these rules and not the ones before
    same = lambda got: [{k: r.get(k) for k in ("on", "what", "minutes")} for r in got] == \
        [{k: r.get(k) for k in ("on", "what", "minutes")} for r in rules]  # noqa: E731
    deadline = time.monotonic() + 15
    while not same(setup("alerts")["rules"]) and time.monotonic() < deadline:
        time.sleep(0.5)
    print(f"✓ {len(rules)} rule(s) saved")


def ntfy_values(url, token):
    """As ⚙ > Alerts > ntfy saves them."""
    if not url.startswith(("http://", "https://")):
        raise Failed("the topic address must start with http:// or https://")
    v = {"LABHUD_NOTIFY_URL": url, "LABHUD_NOTIFY_FORMAT": "ntfy"}
    if token:
        v["LABHUD_NOTIFY_AUTH"] = token if token.lower().startswith(("bearer ", "basic ")) else "Bearer " + token
    return v


def notify_cmd(args):
    args = list(args)
    token = option(args, "--token")
    sub, rest = args[:1], args[1:]
    if sub == ["test"] and len(rest) <= 1:
        setup("notify-test", {"url": rest[0] if rest else "", "token": token})
        print("✓ sent")
    elif sub == ["set"] and len(rest) == 1:
        restarted(setup("secrets", {"values": ntfy_values(rest[0], token)}))
        print("✓ ntfy set")
    elif sub == ["forget"] and not rest:
        restarted(setup("secrets", {"values": {"LABHUD_NOTIFY_URL": "", "LABHUD_NOTIFY_AUTH": "",
                                               "LABHUD_NOTIFY_FORMAT": ""}}))
        print("✓ ntfy forgotten")
    else:
        raise Failed("usage: labhud notify test [<url>] [--token T] | set <url> [--token T] | forget")


def offline(name):
    """admin and pin work on files, with labhud running or not (init.py's own code)."""
    def run(args):
        import init
        return {"admin": init.admin_account, "pin": init.lock_pin}[name](args)
    return run


COMMANDS = {"status": status, "admin": offline("admin"), "pin": offline("pin"), "source": source, "agent": agent,
            "phone": phone, "page": page, "organize": organize, "config": config_cmd, "scan": scan_cmd,
            "alert": alert, "notify": notify_cmd}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help") or argv[0] not in COMMANDS:
        print(USAGE)
        return 0 if argv[:1] in ([], ["-h"], ["--help"], ["help"]) else 2
    try:
        COMMANDS[argv[0]](argv[1:])
    except Failed as e:
        print(f"labhud {argv[0]}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
