# Certificate expiry: how many days each of your public names has left, checked the way a browser
# checks it (a name that does not verify is red too: wrong name, expired, or an incomplete chain).
#
#   LABHUD_CERTS   names to check, comma separated, host or host:port (443 by default)
#
# Data: certs.{cert_min_days, cert_under_14, failed, list}; `list` is a card list, the soonest
# first: list = "certs.list". cert_min_days and cert_under_14 are coloured like the HEALTH page's.

import concurrent.futures
import socket
import ssl
import time

from ._common import env, source

_CTX = ssl.create_default_context()


def days_left(host, port, now=None, timeout=8):
    """Days until the certificate `host:port` presents expires. Raises if it does not verify."""
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with _CTX.wrap_socket(raw, server_hostname=host) as s:
            cert = s.getpeercert()
    return int((ssl.cert_time_to_seconds(cert["notAfter"]) - (now or time.time())) // 86400)


def targets(value):
    out = []
    for item in (x.strip() for x in value.split(",") if x.strip()):
        host, sep, port = item.rpartition(":")
        out.append((host, int(port)) if sep and port.isdigit() else (item, 443))
    return out


@source("certs", every=3600, env=("CERTS",),
        title="Certificate expiry", about="Days left on each public name's certificate, verified like a browser does.",
        hints={"CERTS": "names, comma separated: example.com,mail.example.com:993"})
def certs():
    hosts = targets(env("CERTS"))

    def one(t):
        try:
            return t, days_left(*t), None
        except (OSError, ssl.SSLError, ValueError, KeyError) as e:
            reason = getattr(e, "verify_message", None) or getattr(e, "reason", None) or type(e).__name__
            return t, None, str(reason)[:60]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, hosts))
    rows = []
    for (host, port), days, error in results:
        name = host if port == 443 else f"{host}:{port}"
        if error:
            rows.append({"name": name, "value": error, "bad": True, "_k": -1})
        else:
            rows.append({"name": name, "value": f"{days}d", "bad": days < 14, "_k": days})
    rows.sort(key=lambda r: r["_k"])
    good = [d for _, d, e in results if e is None]
    return {"cert_min_days": min(good) if good else None,
            "cert_under_14": sum(1 for d in good if d < 14),
            "failed": sum(1 for _, _, e in results if e),
            "list": [{k: v for k, v in r.items() if k != "_k"} for r in rows]}
