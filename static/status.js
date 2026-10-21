"use strict";

const STATES = {
  ok: "ok", failing: "failing", host_down: "host down", scheduled: "off on schedule",
  not_configured: "not configured", waiting: "not polled yet",
};

function ago(now, t) {
  const s = Math.max(0, now - t);
  if (s < 90) return s + " s ago";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 172800) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}

function span(cls, text) {
  const n = document.createElement("span");
  n.className = cls;
  n.textContent = text;
  return n;
}

function render(d) {
  const general = document.getElementById("general");
  general.textContent = "";
  const facts = [
    ["version", d.version], ["running since", ago(d.now, d.started)],
    ["displays", d.displays + " of " + d.max_displays], ["actions", d.actions ? "on, " + d.actions : "off"],
  ];
  if (d.demo) facts.unshift(["mode", "DEMO"]);
  const n = d.notify || {};
  facts.push(["notifications", !n.on ? "off" : n.problem ? n.problem
    : n.format + ", " + n.sent + " sent" + (n.last_error ? " · last error: " + n.last_error + " (" + ago(d.now, n.last_error_at) + ")" : "")]);
  // HTTPS to the services: how each host's certificate is checked (see docs/security.md)
  const tls = Object.entries(d.tls || {});
  if (tls.length) {
    const by = {};
    for (const [where, mode] of tls) (by[mode] = by[mode] || []).push(where);
    facts.push(["certificates", ["pinned", "verified", "unchecked"].filter((m) => by[m])
      .map((m) => by[m].length + " " + m + (m === "unchecked" ? " (" + by[m].sort().join(", ") + ")" : "")).join(", ")]);
  }
  if ((d.secret_files || []).length) facts.push(["from files", d.secret_files.join(", ")]);
  const mq = d.mqtt || {};
  if (mq.on) facts.push(["mqtt", mq.problem || mq.sent + " sent" + (mq.last_error ? " · last error: " + mq.last_error + " (" + ago(d.now, mq.last_error_at) + ")" : "")]);
  const st = d.store || {};
  facts.push(["kept across restarts", st.on ? "yes" : st.problem ? "no: " + st.problem : "no (LABHUD_DATA not set)"]);
  const maint = Object.entries(d.maintenance || {});
  if (maint.length) facts.push(["maintenance", maint.map(([card, until]) =>
    card + " until " + new Date(until * 1000).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })).join(", ")]);
  facts.forEach(([k, v], i) => {
    if (i) general.append(" · ");
    general.append(k + " ");
    general.appendChild(document.createElement("b")).textContent = v;
  });

  const cfg = document.getElementById("config");
  cfg.hidden = !(d.config_errors || []).length;
  if (!cfg.hidden) {
    cfg.textContent = "config.toml was edited but not applied (the last good version is running):\n· "
      + d.config_errors.join("\n· ");
  }

  const cl = document.getElementById("checklist");
  cl.hidden = !(d.checklist || []).length;
  const ul = cl.querySelector("ul");
  ul.textContent = "";
  for (const c of d.checklist || []) {
    const li = ul.appendChild(document.createElement("li"));
    li.className = c.ok === true ? "yes" : c.ok === false ? "no" : "na";
    li.append(span("mark", c.ok === true ? "✓" : c.ok === false ? "!" : "–"), span("item", c.item), span("how", c.how));
  }

  const ad = document.getElementById("adoption");
  const a = d.adoption || { new: [], gone: [] };
  ad.hidden = !a.new.length && !a.gone.length;
  const body = ad.querySelector("div");
  body.textContent = "";
  if (a.new.length) {
    const p = body.appendChild(document.createElement("p"));
    p.textContent = a.new.length + " guest(s) with no card. Paste into a group in config.toml, or give a group "
      + "guests = \"<node>\" to add them automatically (guests_tag = \"labhud\" for tagged ones only):";
    const pre = body.appendChild(document.createElement("pre"));
    pre.textContent = a.new.map((g) => `    [[page.group.card]]\n    id = "vm-${g.node}-${g.vmid}"\n    name = ${JSON.stringify(String(g.name))}\n    proxmox = "${g.node}/${g.vmid}"\n`).join("\n");
  }
  if (a.gone.length) {
    const p = body.appendChild(document.createElement("p"));
    p.textContent = "Cards whose guest no longer exists: " + a.gone.map((g) => `${g.card} (${g.node}/${g.vmid})`).join(", ");
  }

  const list = document.getElementById("sources");
  list.textContent = "";
  // Problems first, then the rest in name order (the server sends them sorted).
  const rank = (h) => (h.alarm ? 0 : h.state === "failing" ? 1 : 2);
  const names = Object.keys(d.sources).sort((a, b) => rank(d.sources[a]) - rank(d.sources[b]));
  for (const name of names) {
    const h = d.sources[name];
    const box = document.createElement("div");
    box.className = "src " + h.state + (h.alarm ? " alarm" : "");
    const top = box.appendChild(document.createElement("div"));
    top.className = "top";
    top.append(span("name", name), span("state", STATES[h.state] || h.state));
    const facts = [];
    if (h.last_ok) facts.push("last answer " + ago(d.now, h.last_ok));
    else if (h.last_run) facts.push("never answered");
    if (h.took !== undefined) facts.push("took " + h.took + " s");
    if (h.every) facts.push("every " + h.every + " s");
    if (h.failures) facts.push(h.failures + (h.failures === 6 ? "+" : "") + " failed in a row");
    if (h.next_at && h.state !== "ok") facts.push("next try in " + Math.max(0, h.next_at - d.now) + " s");
    if (h.host) facts.push("host card: " + h.host);
    top.appendChild(span("facts", facts.join(" · ")));
    let why = "";
    if (h.state === "not_configured") why = "needs " + h.needs.join(", ");
    else if (h.state === "failing" || h.state === "host_down") {
      why = h.error + " (" + ago(d.now, h.error_at) + ")";
      if (h.failing_since) why += " · failing since " + ago(d.now, h.failing_since);
    }
    if (why) box.appendChild(span("why", why)).style.display = "block";
    // the key's rights, for the sources whose keys can be limited (Proxmox VE, PBS)
    const k = (d.keys || {})[name];
    if (k && k.error) box.appendChild(span("why", "key rights not checked: " + k.error)).style.display = "block";
    else if (k && k.extra.length) {
      box.classList.add("risky");
      for (const [where, privs] of k.extra) {
        box.appendChild(span("why key", `key (${where}) can do more than read: ${privs.join(", ")}`)).style.display = "block";
      }
    } else if (k) box.appendChild(span("why", "key: read-only")).style.display = "block";
    list.appendChild(box);
  }
}

function load() {
  fetch("/api/status").then((r) => r.json()).then((d) => {
    document.getElementById("error").hidden = true;
    render(d);
  }).catch((e) => {
    const n = document.getElementById("error");
    n.hidden = false;
    n.textContent = "LabHUD does not answer: " + e;
  });
}

load();
if (!location.search.includes("static")) setInterval(load, 10000);
