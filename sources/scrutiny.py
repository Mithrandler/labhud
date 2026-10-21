# Scrutiny: the health of every disk its collectors report (S.M.A.R.T. and Scrutiny's own limits).
#
#   LABHUD_SCRUTINY_URL   http://host:8080 (the web/API container)
#
# Scrutiny's API has no key: keep it on a network only labhud reaches.
# Data: scrutiny.{total, failed, hottest, disks}; `disks` is a list for a card, failed first:
# list = "scrutiny.disks".

from ._common import env, fmt_bytes, request, source


@source("scrutiny", every=300, env=("SCRUTINY_URL",),
         title="Scrutiny", about="S.M.A.R.T. health of every disk (no key: keep it on a private network).",
         hints={"SCRUTINY_URL": "http://host:8080, the web/API container"})
def scrutiny():
    data = request(env("SCRUTINY_URL").rstrip("/") + "/api/summary", timeout=15)
    summary = ((data.get("data") or {}).get("summary")) or {}
    disks, temps = [], []
    for item in summary.values():
        dev = item.get("device") or {}
        smart = item.get("smart") or {}
        # device_status: 0 passed; bit 1 failed S.M.A.R.T., bit 2 failed Scrutiny's thresholds
        status = dev.get("device_status") or 0
        temp = smart.get("temp")
        if isinstance(temp, (int, float)) and temp > 0:
            temps.append(temp)
        name = " ".join(filter(None, [dev.get("host_id"), dev.get("device_name")])) or dev.get("wwn") or "?"
        model = " ".join(filter(None, [dev.get("model_name"), fmt_bytes(dev["capacity"]) if dev.get("capacity") else ""]))
        why = "failed S.M.A.R.T." if status & 1 else "failed thresholds" if status & 2 else "passed"
        disks.append({"name": f"{name} · {model}" if model else name,
                      "value": (f"{round(temp)}° · " if temp else "") + why, "bad": bool(status)})
    disks.sort(key=lambda d: (not d["bad"], d["name"]))
    return {"total": len(disks), "failed": sum(d["bad"] for d in disks),
            "hottest": max(temps) if temps else None,
            "disks": disks or [{"name": "no disks reported", "value": "—"}]}
