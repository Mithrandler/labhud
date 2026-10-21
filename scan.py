"""⚙ > Sources > Scan my network: the known services (onboard.KNOWN) on a private subnet, only
when asked, slowly enough not to look like an attack to an IDS (Suricata, CrowdSec):

- two hosts at a time, eight ports at a time on each: at most 16 connections open;
- a host that answers none of the first eight (no reply, not even a refusal) is taken as absent
  and its other ports are not tried; a short pause between rounds and between hosts;
- an open port counts only when the page behind it names the service (onboard._probe), not
  because it is open.

One scan at a time, in a thread; status() is what the page polls. A /24 takes about a minute.
"""

import concurrent.futures
import ipaddress
import socket
import threading
import time

import onboard

PER_HOST = 8          # ports tried at once on one host
HOSTS_AT_ONCE = 2     # 2 x 8 = 16 connections at most
TIMEOUT = 0.4
PAUSE = 0.05          # between rounds on a host
MAX_HOSTS = 1024      # a /22
_lock = threading.Lock()
JOB = {"running": False, "cidr": "", "done": 0, "total": 0, "found": [], "error": "", "started": 0, "ended": 0}


def check(cidr):
    """The network for `cidr` if it may be scanned: private (RFC 1918 / ULA), at most MAX_HOSTS.
    Raises ValueError otherwise."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError:
        raise ValueError(f"{cidr!r} is not a subnet like 192.168.1.0/24") from None
    if not net.is_private or net.is_loopback or net.is_link_local:
        raise ValueError("only a private subnet of your own network (like 192.168.1.0/24)")
    if net.num_addresses > MAX_HOSTS + 2:
        raise ValueError(f"at most {MAX_HOSTS} addresses at once (a /22)")
    return net


def default_cidr(hosts_setting, local_ip=None):
    """The /24 of the first IPv4 address in LABHUD_HOSTS (what the display is opened at), else of
    the address this machine uses to reach the network."""
    for h in (hosts_setting or "").split(","):
        name = h.strip().rsplit(":", 1)[0] if h.count(":") == 1 else h.strip()
        try:
            ip = ipaddress.ip_address(name)
            if ip.version == 4 and ip.is_private and not ip.is_loopback:
                return str(ipaddress.ip_network(f"{ip}/24", strict=False))
        except ValueError:
            continue
    ip = local_ip or _local_ip()
    return str(ipaddress.ip_network(f"{ip}/24", strict=False)) if ip else "192.168.1.0/24"


def _local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))   # nothing is sent: the kernel only picks the route
            return s.getsockname()[0]
    except OSError:
        return None


def _knock(host, port):
    """True open, False refused (the host is there), None no answer."""
    try:
        socket.create_connection((host, port), timeout=TIMEOUT).close()
        return True
    except ConnectionRefusedError:
        return False
    except OSError:
        return None


def _host(host, ports, pool):
    """The open ports of one host, giving up after a first round with no answer at all."""
    open_ = []
    for i in range(0, len(ports), PER_HOST):
        chunk = ports[i:i + PER_HOST]
        answers = list(pool.map(lambda p: _knock(host, p), chunk))
        open_ += [p for p, a in zip(chunk, answers) if a]
        if i == 0 and all(a is None for a in answers):
            return []
        time.sleep(PAUSE)
    return open_


def run(hosts, configured=(), probe=onboard._probe, known=None):
    """[{source, url, configured}] for the known services on `hosts`; updates JOB as it goes."""
    known = known or onboard.KNOWN
    ports = sorted({k[1] for k in known})
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PER_HOST * HOSTS_AT_ONCE) as pool, \
            concurrent.futures.ThreadPoolExecutor(max_workers=HOSTS_AT_ONCE) as hosts_pool:
        for i in range(0, len(hosts), HOSTS_AT_ONCE):
            batch = hosts[i:i + HOSTS_AT_ONCE]
            for host, open_ in zip(batch, hosts_pool.map(lambda h: _host(h, ports, pool), batch)):
                for name, port, scheme, path, marker in known:
                    if port in open_ and probe(host, port, scheme, path, marker):
                        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
                        item = {"source": name, "url": f"{scheme}://{netloc}", "configured": name in configured}
                        found.append(item)
                        with _lock:
                            JOB["found"].append(item)
            with _lock:
                JOB["done"] = min(len(hosts), i + HOSTS_AT_ONCE)
    return found


def start(cidr, extra=(), configured=()):
    """Starts a scan of `cidr` plus the `extra` addresses (the agents'), unless one runs. Returns
    status(). Raises ValueError for a subnet that may not be scanned."""
    net = check(cidr)
    hosts = [str(h) for h in net.hosts()]
    for h in extra:
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            continue
        if ip.is_private and str(ip) not in hosts:
            hosts.append(str(ip))
    with _lock:
        if JOB["running"]:
            raise ValueError("a scan is already running")
        JOB.update(running=True, cidr=str(net), done=0, total=len(hosts), found=[], error="",
                   started=int(time.time()), ended=0)

    def go():
        try:
            run(hosts, set(configured))
        except Exception as e:   # never leave the job "running"
            with _lock:
                JOB["error"] = f"{type(e).__name__}: {e}"[:200]
        finally:
            with _lock:
                JOB.update(running=False, ended=int(time.time()))
            print(f"scan of {net}: {len(JOB['found'])} service(s) found", flush=True)
    threading.Thread(target=go, daemon=True, name="scan").start()
    return status()


def status():
    with _lock:
        return {k: (list(v) if isinstance(v, list) else v) for k, v in JOB.items()}
