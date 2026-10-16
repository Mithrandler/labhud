"""HTTPS by default: labhud's own certificate, made once at the first start.

Over plain HTTP everything a phone sends to labhud (the admin's password and code, the PIN, the
app's token) crosses the network as a postcard, readable by anything else on it. So labhud serves
HTTPS on its own port, with a self-signed certificate it makes itself the first time, into
LABHUD_DATA/tls/ (key readable only by labhud), valid for 10 years so it never needs renewing:

- the app and the agents learn its SHA-256 when they pair (the QR code, the agent's code) and
  accept that certificate and no other, so they need no warning and no authority;
- a browser shows a warning once ("Advanced → Proceed"), since no authority vouches for it.

The names in it (LABHUD_HOSTS, plus localhost) are only for the browsers; it is not made again
when they change, because a new certificate means pairing every phone and agent again.

What decides (choose() returns the files and a line for the log):
- LABHUD_TLS_CERT / LABHUD_TLS_KEY: your own certificate, as before;
- LABHUD_HTTP=on: plain HTTP (behind a reverse proxy that does TLS, or by choice);
- otherwise the one in LABHUD_DATA/tls/, made with `openssl` if missing. Without LABHUD_DATA or
  without openssl, plain HTTP, with the reason in the log and on /status.
"""

import ipaddress
import os
import shutil
import subprocess

DAYS = 3650


def _names(hosts):
    """subjectAltName entries for "host:port" names: DNS or IP, plus localhost; no duplicates."""
    out = ["DNS:localhost", "IP:127.0.0.1"]
    for h in hosts:
        name = h.rsplit(":", 1)[0].strip("[]") if h.count(":") <= 1 or h.startswith("[") else h
        if not name:
            continue
        try:
            entry = "IP:" + str(ipaddress.ip_address(name))
        except ValueError:
            entry = "DNS:" + name
        if entry not in out:
            out.append(entry)
    return out


def make(folder, hosts, openssl=None):
    """Makes cert.pem and key.pem in `folder` (ECDSA P-256, self-signed, 10 years). Returns
    (cert, key) or raises OSError / subprocess.CalledProcessError."""
    openssl = openssl or shutil.which("openssl")
    if not openssl:
        raise OSError("openssl not found")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    cert, key = os.path.join(folder, "cert.pem"), os.path.join(folder, "key.pem")
    tmp_key = key + ".new"
    # the key is written by openssl: an umask keeps it private from its first byte
    old = os.umask(0o077)
    try:
        subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                        "-nodes", "-days", str(DAYS), "-subj", "/CN=LabHUD",
                        "-addext", "subjectAltName=" + ",".join(_names(hosts)),
                        "-addext", "extendedKeyUsage=serverAuth",
                        "-keyout", tmp_key, "-out", cert + ".new"],
                       check=True, capture_output=True, timeout=60)
    finally:
        os.umask(old)
    os.replace(tmp_key, key)
    os.replace(cert + ".new", cert)
    return cert, key


def choose(env, data_dir, hosts, demo=False):
    """(cert, key, line): the files to serve HTTPS with ("" for plain HTTP) and what to log."""
    if env.get("LABHUD_TLS_CERT"):
        return env["LABHUD_TLS_CERT"], env.get("LABHUD_TLS_KEY", ""), "HTTPS with LABHUD_TLS_CERT"
    if (env.get("LABHUD_HTTP") or "").lower() in ("on", "1", "true", "yes"):
        return "", "", "plain HTTP (LABHUD_HTTP=on)"
    if demo:
        return "", "", "plain HTTP (demo)"
    if not data_dir:
        return "", "", "plain HTTP: no LABHUD_DATA to keep a certificate in (set it, or LABHUD_HTTP=on to say so)"
    folder = os.path.join(data_dir, "tls")
    cert, key = os.path.join(folder, "cert.pem"), os.path.join(folder, "key.pem")
    if os.path.exists(cert) and os.path.exists(key):
        return cert, key, f"HTTPS with labhud's own certificate ({folder})"
    try:
        make(folder, hosts)
    except (OSError, subprocess.SubprocessError) as e:
        why = getattr(e, "stderr", b"") or b""
        why = why.decode(errors="replace").strip().splitlines()[-1:] if why else [str(e)]
        return "", "", f"plain HTTP: could not make a certificate ({why[0] if why else e})"
    return cert, key, f"HTTPS with a new certificate made for {', '.join(_names(hosts))} ({folder})"


def current(env, data_dir):
    """The certificate a running labhud serves, without making one: for init.py, another process
    that prints addresses and pairing links. "" for plain HTTP."""
    if env.get("LABHUD_TLS_CERT"):
        return env["LABHUD_TLS_CERT"]
    if (env.get("LABHUD_HTTP") or "").lower() in ("on", "1", "true", "yes") or not data_dir:
        return ""
    cert = os.path.join(data_dir, "tls", "cert.pem")
    return cert if os.path.exists(cert) else ""
