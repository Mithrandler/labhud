# A UPS through NUT (Network UPS Tools): on mains or on battery, charge, minutes left, load.
#
#   LABHUD_NUT_HOST   host[:port] of upsd (3493), e.g. 192.0.2.20 or nas:3493
#   LABHUD_NUT_UPS    the UPS name in upsd (optional: the first one it lists)
#
# Reading needs no login on a stock upsd (LISTEN on the LAN address in upsd.conf).
# Data: nut.{status, on_battery, low_battery, charge, runtime_min, load, input_voltage, model}.
# A UPS on battery is the one thing a wall display should shout: put nut.on_battery on a card.

import socket

from ._common import env, source

STATES = {"OL": "on mains", "OB": "ON BATTERY", "LB": "LOW BATTERY", "CHRG": "charging",
          "DISCHRG": "discharging", "BYPASS": "bypass", "CAL": "calibrating", "OFF": "off",
          "OVER": "overloaded", "TRIM": "trimming", "BOOST": "boosting", "RB": "replace battery"}


def _talk(host, port, lines, timeout=5):
    """Sends NUT commands, returns the answer lines up to the last END/ERR."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall("".join(line + "\n" for line in lines).encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            text = buf.decode("utf-8", "replace")
            if text.rstrip().splitlines()[-1].startswith(("END LIST", "ERR")) and text.endswith("\n"):
                break
    return buf.decode("utf-8", "replace").splitlines()


def parse_vars(lines):
    out = {}
    for line in lines:
        if line.startswith("ERR"):
            raise RuntimeError("upsd: " + line[4:])
        if line.startswith("VAR "):
            parts = line.split(" ", 3)
            if len(parts) == 4:
                out[parts[2]] = parts[3].strip().strip('"')
    return out


def summarize(v):
    def num(key):
        try:
            return float(v[key])
        except (KeyError, ValueError):
            return None
    flags = (v.get("ups.status") or "").split()
    runtime = num("battery.runtime")
    return {
        "status": ", ".join(STATES.get(f, f) for f in flags) or "?",
        "on_battery": "OB" in flags, "low_battery": "LB" in flags,
        "charge": num("battery.charge"),
        "runtime_min": round(runtime / 60) if runtime is not None else None,
        "load": num("ups.load"), "input_voltage": num("input.voltage"),
        "model": " ".join(x for x in (v.get("device.mfr") or v.get("ups.mfr"), v.get("device.model") or v.get("ups.model")) if x),
    }


@source("nut", every=10, env=("NUT_HOST",),
        title="UPS (NUT)", about="Mains or battery, charge, minutes left and load, from upsd.",
        hints={"NUT_HOST": "host[:port] of upsd (3493); LABHUD_NUT_UPS picks a UPS, else the first"})
def nut():
    host, port = env("NUT_HOST").strip(), 3493
    if host.count(":") == 1:
        host, p = host.split(":")
        port = int(p)
    ups = env("NUT_UPS")
    if not ups:
        names = [line.split()[1] for line in _talk(host, port, ["LIST UPS"]) if line.startswith("UPS ")]
        if not names:
            raise RuntimeError("upsd lists no UPS")
        ups = names[0]
    return dict(summarize(parse_vars(_talk(host, port, [f"LIST VAR {ups}"]))), name=ups)
