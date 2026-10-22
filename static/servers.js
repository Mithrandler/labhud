// Servers (/servers): the hosts' Docker, over labhud's own /api/servers/*. Plain JS, no build step.
// Everything that comes from a host is put in the page with textContent, never as HTML. The page is
// served only over HTTPS with a policy that has no inline script or style (server.py SERVERS_CSP).
"use strict";

const ADMIN_KEY = "labhud-admin";    // the admin's ticket, the same one the display keeps
const LOCK_KEY = "labhud-unlock";    // the PIN's ticket (this tab only)
const LOG_LINES = 1000;
const $ = (id) => document.getElementById(id);
const S = { hosts: [], host: null, tab: "overview", rows: [], filter: "", sheet: null, timer: 0 };
const TABS = [["overview", "Overview", true], ["containers", "Containers", true], ["stacks", "Stacks", true],
              ["images", "Images", true], ["volumes", "Volumes", true], ["networks", "Networks", true],
              ["vms", "VMs & LXC", "pve"], ["host", "Host", true]];

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function btn(text, cls, fn) {
  const b = el("button", cls, text);
  b.type = "button";
  if (fn) b.addEventListener("click", fn);
  return b;
}
function get(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } }
function put(key, v, store = localStorage) { try { if (v) store.setItem(key, v); else store.removeItem(key); } catch (e) { /* not kept */ } }
function pct(v) { return v == null ? "–" : Math.round(v) + "%"; }
function bytes(n) {
  if (!n) return "0";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (n >= 100 || i === 0 ? Math.round(n) : n.toFixed(1)) + " " + u[i];
}

// ---- calls: the admin's ticket and the PIN's ticket go with every one ----

function headers() {
  const h = { "Content-Type": "application/json" };
  const a = get(ADMIN_KEY);
  if (a) h["X-Labhud-Admin"] = a;
  let u = "";
  try { u = sessionStorage.getItem(LOCK_KEY) || ""; } catch (e) { /* none */ }
  if (u) h["X-Labhud-Unlock"] = u;
  return h;
}
async function post(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: headers(), body: JSON.stringify(body || {}) });
    const d = await r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" }));
    d.status = r.status;
    return d;
  } catch (e) {
    return { ok: false, status: 0, error: "could not reach LabHUD" };
  }
}

/** A call to /api/servers/<op>. A session that ended, or a refusal that signing in or the PIN may
 * mend, asks for that once and tries again. */
async function call(op, body) {
  let r = await post("/api/servers/" + op, body);
  if (r.status === 401 && r.resume || (r.status === 403 && r.error === "forbidden")) {
    if (await begin()) r = await post("/api/servers/" + op, body);
  }
  return r;
}

/** Starts the session: signs in and types the PIN when they are asked for. */
async function begin() {
  for (let step = 0; step < 3; step++) {
    const r = await post("/api/servers/session", {});
    if (r.ok) { $("session").textContent = "session open · 15 min idle"; return true; }
    if (r.status === 429) { await notice("Too many refused calls: try again in " + (r.retry_after || 60) + " s."); return false; }
    if (r.status !== 403) { await notice(r.error || "refused"); return false; }
    const known = await post("/api/admin/check", {});
    if (!known.ok) { if (!(await sign_in())) return false; continue; }
    if (!(await pin())) return false;
  }
  return false;
}

function ask(title, fields, okText) {
  return new Promise((done) => {
    const m = $("modal");
    m.textContent = "";
    const box = el("form", "box");
    box.appendChild(el("h2", "", title));
    const inputs = fields.map(([label, type, name]) => {
      const i = el("input"); i.type = type; i.name = name; i.autocomplete = type === "password" ? "current-password" : "off";
      i.required = true;
      box.append(el("label", "", label), i);
      return i;
    });
    const msg = el("div", "msg");
    const go = btn(okText, "primary"); go.type = "submit";
    const no = btn("Cancel", "", () => { m.hidden = true; done(null); });
    const row = el("div", "bar"); row.append(go, no);
    box.append(msg, row);
    box.addEventListener("submit", (e) => { e.preventDefault(); m.hidden = true; done({ values: inputs.map((i) => i.value), msg }); });
    m.appendChild(box);
    m.hidden = false;
    inputs[0].focus();
  });
}
async function notice(text) { await ask(text, [], "OK"); }

async function sign_in() {
  const a = await ask("Sign in as the admin", [["User", "text", "user"], ["Password", "password", "pw"], ["Code", "text", "code"]], "Sign in");
  if (!a) return false;
  const r = await post("/api/admin/login", { user: a.values[0], password: a.values[1], code: a.values[2] });
  if (!r.ok) { await notice(r.error || "refused"); return false; }
  put(ADMIN_KEY, r.ticket);
  return true;
}
async function pin() {
  const a = await ask("Enter the PIN", [["PIN", "password", "pin"]], "Unlock");
  if (!a) return false;
  const r = await post("/api/lock/unlock", { pin: a.values[0] });
  if (!r.ok) { await notice(r.error || "wrong PIN"); return false; }
  put(LOCK_KEY, r.ticket, sessionStorage);
  return true;
}

// ---- hosts and tabs ----

async function load_hosts() {
  const r = await call("hosts");
  if (!r.ok) return show_error(r.error || "could not load");
  S.hosts = r.hosts;
  if (!S.host || !S.hosts.some((h) => h.name === S.host)) S.host = (S.hosts.find((h) => h.docker) || S.hosts[0] || {}).name || null;
  draw_hosts();
  draw_tabs();
  draw_view();
}
function show_error(text) { $("view").textContent = ""; $("view").appendChild(el("p", "msg bad", text)); }

function draw_hosts() {
  const nav = $("hosts");
  nav.textContent = "";
  for (const h of S.hosts) {
    const b = btn("", "host" + (h.name === S.host ? " on" : ""), () => { S.host = h.name; S.sheet && close_sheet(); if (S.tab === "vms" && !h.pve) S.tab = "overview"; draw_hosts(); draw_tabs(); draw_view(); });
    b.append(el("span", "dot " + (h.online ? "ok" : "bad")), el("span", "", h.name),
      el("span", "nums", h.online ? h.containers.running + "/" + h.containers.total : "–"));
    nav.appendChild(b);
  }
  if (!S.hosts.length) nav.appendChild(el("p", "muted", "No agents yet. Add one in Settings > Hosts and agents."));
}
function draw_tabs() {
  const t = $("tabs");
  t.textContent = "";
  for (const [id, label, on] of TABS) {
    const b = btn(label, "tab" + (id === S.tab ? " on" : ""), () => { S.tab = id; draw_tabs(); draw_view(); });
    b.disabled = on === "pve" ? !(host_now() || {}).pve : !on;
    b.setAttribute("role", "tab");
    t.appendChild(b);
  }
}
function host_now() { return S.hosts.find((h) => h.name === S.host); }

async function draw_view() {
  clearInterval(S.timer);
  const v = $("view");
  const h = host_now();
  v.textContent = "";
  if (!h) return;
  if (S.tab === "overview") return overview(v, h);
  if (S.tab === "containers") return containers(v, h);
  if (S.tab === "stacks") return stacks(v);
  if (S.tab === "vms") return guests(v);
  if (S.tab === "host") return host_tab(v);
  if (S.tab === "images") return objects(v, "images", images_view);
  if (S.tab === "volumes") return objects(v, "volumes", volumes_view);
  if (S.tab === "networks") return objects(v, "networks", networks_view);
}

function overview(v, h) {
  const cards = el("div", "cards");
  const add = (label, big, sub) => { const c = el("div", "card"); c.append(el("div", "muted", label), el("div", "big", big), el("div", "muted", sub || "")); cards.appendChild(c); };
  add("Status", h.online ? "Online" : "Offline", h.version ? "agent " + h.version : "");
  add("Containers", h.containers.running + " / " + h.containers.total, "running / all");
  add("CPU", pct(h.cpu), "");
  add("Memory", pct(h.mem), "");
  if (h.update) add("Agent", "Update", "a newer agent is out");
  v.appendChild(cards);
}

// ---- containers ----

async function containers(v, h) {
  const bar = el("div", "bar");
  const f = el("input"); f.type = "search"; f.placeholder = "Filter"; f.value = S.filter;
  f.addEventListener("input", () => { S.filter = f.value; draw_rows(); });
  bar.append(f, btn("Refresh", "", () => refresh_rows(true)));
  const table = el("table");
  table.id = "rows";
  v.append(bar, table);
  await refresh_rows(false);
  S.timer = setInterval(() => refresh_rows(false), 15000);
}
async function refresh_rows() {
  const r = await call("containers", { agent: S.host });
  if (!r.ok) return show_error(r.error || "could not load");
  S.rows = r.containers;
  draw_rows();
}
function draw_rows() {
  const t = $("rows");
  if (!t) return;
  t.textContent = "";
  const head = el("tr");
  for (const c of ["", "Name", "Image", "CPU", "Memory", "State"]) head.appendChild(el("th", c === "CPU" || c === "Memory" ? "num" : "", c));
  t.appendChild(head);
  const q = S.filter.trim().toLowerCase();
  for (const c of S.rows.filter((x) => !q || x.name.toLowerCase().includes(q) || x.image.toLowerCase().includes(q))) {
    const tr = el("tr", "row");
    const ok = c.state === "running" && c.health !== "unhealthy";
    tr.append(el("td", "", ""), el("td", "", c.name), el("td", "muted", c.image), el("td", "num", c.state === "running" ? pct(c.cpu) : "–"),
      el("td", "num", c.state === "running" ? pct(c.mem_pct) : "–"));
    tr.firstChild.appendChild(el("span", "dot " + (ok ? "ok" : c.state === "running" ? "warn" : "bad")));
    const st = el("td"); st.appendChild(el("span", "chip " + (ok ? "ok" : "bad"), c.health && c.state === "running" ? c.health : c.state));
    tr.appendChild(st);
    tr.addEventListener("click", () => open_sheet(c));
    t.appendChild(tr);
  }
}

// ---- a container's sheet: actions, Logs live, Shell, Inspect, Stats ----

function close_sheet() {
  const s = S.sheet;
  if (s) { s.alive = false; if (s.stream) call("stream-close", { id: s.stream }); if (s.term) s.term.dispose(); }
  S.sheet = null;
  $("sheet").hidden = true;
  $("sheet").textContent = "";
  document.body.classList.remove("locked");
}
/** Shows the sheet: the page underneath stays put (no scrolling) and an X in the corner closes it. */
function show_sheet() {
  const sh = $("sheet");
  sh.hidden = false;
  sh.scrollTop = 0;
  document.body.classList.add("locked");
  const x = btn("\u2715", "sheet-x", close_sheet);
  x.setAttribute("aria-label", "Close");
  sh.appendChild(x);
  return sh;
}
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && S.sheet) close_sheet(); });
function open_sheet(c) {
  close_sheet();
  S.sheet = { c, alive: true, stream: null, term: null };
  const sh = show_sheet();
  sh.append(el("h2", "", c.name), el("div", "muted", c.image + " · " + c.state));
  const acts = el("div", "actions");
  for (const verb of ["start", "stop", "restart", "update"]) {
    const b = btn(verb[0].toUpperCase() + verb.slice(1), verb === "stop" ? "danger" : "", () => act(c, verb, b));
    b.disabled = !c.can.includes(verb) || (verb === "start" && c.state === "running") || (verb === "stop" && c.state !== "running");
    acts.appendChild(b);
  }
  acts.append(btn("Close", "", close_sheet));
  const tabs = el("div", "actions");
  const body = el("div");
  const show = (fn) => { reset_body(body); fn(body, c); };
  tabs.append(btn("Logs live", "", () => show(live_logs)), btn("Inspect", "", () => show(inspect)), btn("Stats", "", () => show(stats)));
  const shell = btn("Shell", "", () => show(shell_view));
  shell.disabled = !c.shell;
  shell.title = c.shell ? "a terminal in the container (needs the PIN)" : "tick Terminal for it in Hosts and agents";
  tabs.appendChild(shell);
  sh.append(acts, tabs, body);
  show(live_logs);
}
function reset_body(body) {
  const s = S.sheet;
  if (s.stream) { call("stream-close", { id: s.stream }); s.stream = null; }
  if (s.term) { s.term.dispose(); s.term = null; }
  s.gen = (s.gen || 0) + 1;
  body.textContent = "";
}
async function act(c, verb, button) {
  if (verb === "stop" || verb === "update") {
    const a = await ask((verb === "stop" ? "Stop " : "Update ") + c.name + "?", [["Type its name to confirm", "text", "name"]], verb === "stop" ? "Stop" : "Update");
    if (!a || a.values[0] !== c.name) return;
  }
  button.disabled = true;
  const r = await call("action", { agent: S.host, name: c.name, verb });
  button.disabled = false;
  if (!r.ok) await notice(r.error || "refused");
  refresh_rows(true);
}

function b64_bytes(text) {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function bytes_b64(buf) {
  let bin = "";
  for (let i = 0; i < buf.length; i += 4096) bin += String.fromCharCode(...buf.subarray(i, i + 4096));
  return btoa(bin);
}

/** Reads a stream until the sheet moves on (gen changes) or it ends. */
async function follow(s, gen, id, data, end) {
  let after = 0;
  let fails = 0;
  while (s.alive && s.gen === gen) {
    const r = await post("/api/servers/stream-read", { id, after });
    if (!s.alive || s.gen !== gen) return;
    if (!r.ok) {
      if (++fails > 3 || r.error === "no such stream") return end(r.error || "lost");
      await new Promise((ok) => setTimeout(ok, 1000));
      continue;
    }
    fails = 0; after = r.next;
    if (r.data) data(b64_bytes(r.data));
    if (r.end) return end(r.end);
  }
}

async function live_logs(body, c) {
  const s = S.sheet, gen = s.gen;
  const status = el("div", "muted", "opening…");
  const pre = el("pre", "out");
  body.append(status, pre);
  const r = await call("stream-open", { agent: S.host, kind: "logs", name: c.name });
  if (s.gen !== gen) { if (r.ok) call("stream-close", { id: r.id }); return; }
  if (!r.ok) { status.textContent = "✕ " + (r.error || "failed"); return; }
  s.stream = r.id;
  status.textContent = "live";
  const dec = new TextDecoder();
  let lines = [""];
  follow(s, gen, r.id, (buf) => {
    const parts = dec.decode(buf, { stream: true }).split("\n");
    lines[lines.length - 1] += parts[0];
    lines.push(...parts.slice(1));
    if (lines.length > LOG_LINES) lines = lines.slice(-LOG_LINES);
    const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
    pre.textContent = lines.join("\n");
    if (stick) pre.scrollTop = pre.scrollHeight;
  }, (why) => { status.textContent = "ended: " + why; s.stream = null; });
}

async function inspect(body, c) {
  const gen = S.sheet.gen;
  body.appendChild(el("div", "muted", "loading…"));
  const r = await call("inspect", { agent: S.host, name: c.name });
  if (S.sheet.gen !== gen) return;
  body.textContent = "";
  if (!r.ok) return body.appendChild(el("p", "msg bad", r.error || "failed"));
  const i = r.inspect;
  const dl = el("dl");
  const row = (k, v) => { if (v === "" || v == null || (Array.isArray(v) && !v.length)) return; dl.append(el("dt", "", k), el("dd", "", Array.isArray(v) ? v.join("\n") : String(v))); };
  row("Image", i.image); row("Created", i.created); row("Started", i.started); row("Restart", i.restart + (i.restarts ? " (" + i.restarts + " restarts)" : ""));
  row("Health", i.health); row("Command", i.command); row("Ports", i.ports); row("Mounts", i.mounts); row("Networks", i.networks);
  row("Environment (names only)", i.env);
  row("Labels", Object.entries(i.labels).map(([k, v]) => k + "=" + v));
  body.appendChild(dl);
}

async function stats(body, c) {
  const gen = S.sheet.gen;
  const out = el("div", "cards");
  body.appendChild(out);
  const draw = async () => {
    const r = await call("stats", { agent: S.host, name: c.name });
    if (!S.sheet || S.sheet.gen !== gen) return;
    out.textContent = "";
    if (!r.ok) return out.appendChild(el("p", "msg bad", r.error || "failed"));
    const s = r.stats;
    const add = (label, big, sub) => { const k = el("div", "card"); k.append(el("div", "muted", label), el("div", "big", big), el("div", "muted", sub || "")); out.appendChild(k); };
    add("CPU", pct(s.cpu), "of one core"); add("Memory", bytes(s.mem), s.mem_limit ? pct(s.mem_pct) + " of " + bytes(s.mem_limit) : "");
    add("Network", "↓ " + bytes(s.net_rx), "↑ " + bytes(s.net_tx) + " since start"); add("Disk", "r " + bytes(s.block_read), "w " + bytes(s.block_write));
    add("Processes", String(s.pids), "");
  };
  await draw();
  const t = setInterval(() => { if (S.sheet && S.sheet.gen === gen) draw(); else clearInterval(t); }, 5000);
}

let XTERM = null;
function load_xterm() {
  if (XTERM) return XTERM;
  const script = (src) => new Promise((ok, no) => {
    const s = document.createElement("script");
    s.src = src; s.onload = ok; s.onerror = () => no(new Error("could not load " + src));
    document.head.appendChild(s);
  });
  const css = document.createElement("link");
  css.rel = "stylesheet"; css.href = "/vendor/xterm/xterm.css";
  document.head.appendChild(css);
  XTERM = script("/vendor/xterm/xterm.js").then(() => script("/vendor/xterm/addon-fit.js"));
  XTERM.catch(() => { XTERM = null; });
  return XTERM;
}

async function shell_view(body, c) {
  const s = S.sheet, gen = s.gen;
  const status = el("div", "muted", "opening…");
  const host = el("div", "term");
  body.append(status, host);
  try { await load_xterm(); } catch (e) { status.textContent = "✕ " + e.message; return; }
  if (s.gen !== gen) return;
  const term = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 2000, theme: { background: "#000000" } });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit); term.open(host); fit.fit();
  s.term = term;
  const r = await call("stream-open", { agent: S.host, kind: "shell", name: c.name, cols: term.cols, rows: term.rows });
  if (s.gen !== gen) { if (r.ok) call("stream-close", { id: r.id }); return; }
  if (!r.ok) { status.textContent = "✕ " + (r.error || "failed"); return; }
  s.stream = r.id;
  status.textContent = "open · closes after 3 minutes without a key";
  let pending = [], timer = 0;
  const send = () => { timer = 0; const all = pending.join(""); pending = []; if (all) post("/api/servers/stream-input", { id: r.id, data: bytes_b64(new TextEncoder().encode(all)) }); };
  term.onData((d) => { pending.push(d); if (!timer) timer = setTimeout(send, 15); });
  term.onResize(({ cols, rows }) => post("/api/servers/stream-resize", { id: r.id, cols, rows }));
  term.focus();
  follow(s, gen, r.id, (buf) => term.write(buf), (why) => { status.textContent = "ended: " + why; term.write("\r\n[" + why + "]\r\n"); s.stream = null; });
}

// ---- the host's Docker: images, volumes, networks (needs "Unused images" ticked for the host) ----

const used_text = (used, by) => !used ? "unused" : !by || !by.length ? "used by " + used : "used by " + by.join(", ") + (used > by.length ? " +" + (used - by.length) : "");

/** The word typed before something goes: resolves with true only when it is exactly [word]. */
async function typed(title, word, button) {
  const a = await ask(title, [["Type  " + word + "  to confirm", "text", "word"]], button);
  return !!a && a.values[0] === word;
}

/** A filter and a table for one kind of object, loaded again after a change. */
async function objects(v, op, render) {
  const bar = el("div", "bar"), said = el("div", "msg bad"), table = el("table");
  const f = el("input"); f.type = "search"; f.placeholder = "Filter"; f.value = S.filter;
  bar.appendChild(f);
  v.append(bar, said, table);
  let data = [];
  const load = async (fresh) => {
    const r = await call(op, { agent: S.host, fresh: !!fresh });
    said.textContent = r.ok ? "" : r.error || "could not load";
    if (r.ok) { data = r[op]; draw(); }
  };
  const draw = () => { table.textContent = ""; render(table, data, f.value.trim().toLowerCase(), load, said); };
  f.addEventListener("input", () => { S.filter = f.value; draw(); });
  bar.appendChild(btn("Refresh", "", () => load(true)));
  if (op === "images") {
    bar.append(btn("Pull image…", "", async () => {
      const a = await ask("Pull an image", [["Image, e.g. nginx:1.27", "text", "ref"]], "Pull");
      if (!a) return;
      said.textContent = "Pulling " + a.values[0] + "…";
      const r = await call("pull", { agent: S.host, image: a.values[0] });
      said.textContent = r.ok ? "" : "pull: " + (r.error || "failed");
      load(true);
    }));
    for (const [what, label] of [["images", "Clean unused"], ["dangling", "Clean untagged"]]) {
      bar.appendChild(btn(label, "", async () => {
        if (!(await typed(label + "?", what, "Clean"))) return;
        const r = await call("clean", { agent: S.host, what, confirm: what });
        said.textContent = r.ok ? r.text : "clean: " + (r.error || "failed");
        load(true);
      }));
    }
  }
  await load(false);
}

function head_row(t, labels) {
  const tr = el("tr");
  for (const c of labels) tr.appendChild(el("th", c === "Size" ? "num" : "", c));
  t.appendChild(tr);
}
function row_of(t, cells, onclick, bad) {
  const tr = el("tr", "row");
  cells.forEach((c, i) => tr.appendChild(el("td", i === 2 && cells.length >= 3 ? "num" : i ? "muted" : "", c)));
  if (bad) tr.firstChild.classList.add("warn-text");
  tr.addEventListener("click", onclick);
  t.appendChild(tr);
  return tr;
}

// an image's CVEs from the agent's scan (trivy/grype): [text, tone] for the row and the sheet
function cve_text(sc) {
  if (!sc) return ["○ not scanned", ""];
  if (sc.critical) return ["● " + sc.critical + " crit · " + sc.high + " high", "crit"];
  if (sc.high) return ["● " + sc.high + " high", "warn"];
  return ["● clean", "ok"];
}
function cve_facts(sc) {
  if (!sc) return [["Vulnerabilities", "not scanned (the host needs the scan capability, and a container that runs this image ticked)"]];
  const when = sc.t ? new Date(sc.t * 1000).toLocaleString() : "?";
  return [["Vulnerabilities", cve_text(sc)[0]], ["Scanner", (sc.tool || "?") + " · scanned " + when],
    ...(sc.affects.length ? [["Affects", sc.affects.join(", ")]] : [])];
}

function images_view(t, rows, q, reload, said) {
  head_row(t, ["Image", "Used", "Size", "CVEs"]);
  const title = (i) => i.tags[0] || i.id;
  for (const i of rows.filter((x) => !q || x.id.includes(q) || x.tags.some((g) => g.toLowerCase().includes(q))).sort((a, b) => b.size - a.size)) {
    const [ct, tone] = cve_text(i.scan);
    const tr = row_of(t, [title(i) + (i.tags.length > 1 ? "  +" + (i.tags.length - 1) : ""), used_text(i.used, i.used_by), bytes(i.size), ct], () => open_object(i.tags[0] || i.id, [
      ["Id", i.id], ["Size", bytes(i.size)], ["Used", used_text(i.used, i.used_by)], ...cve_facts(i.scan)], i.tags.length ? i.tags : [i.id], i.used ? "in use" : "", async (n) => {
      if (!(await typed("Remove " + n + "?", n, "Remove"))) return;
      const r = await call("rm", { agent: S.host, kind: "image", name: n, confirm: n });
      said.textContent = r.ok ? "" : "remove: " + (r.error || "failed");
      close_sheet(); reload(true);
    }, [["Pull again", async () => { const r = await call("pull", { agent: S.host, image: i.tags[0] }); said.textContent = r.ok ? "" : "pull: " + (r.error || "failed"); reload(true); }]], !i.tags.length), !i.used);
    if (tone) tr.lastChild.classList.add(tone);
  }
}

function networks_view(t, rows, q, reload, said) {
  head_row(t, ["Network", "Used", "Driver"]);
  for (const n of rows.filter((x) => !q || x.name.toLowerCase().includes(q))) {
    row_of(t, [n.name + (n.builtin ? "  (built in)" : ""), used_text(n.used, n.used_by), n.driver], async () => {
      open_object(n.name, [["Driver", n.driver], ["Scope", n.scope], ["Used", used_text(n.used, n.used_by)]], n.builtin ? [] : [n.name], n.used ? "in use" : n.builtin ? "built in: never removed" : "", async (name) => {
        if (!(await typed("Remove " + name + "?", name, "Remove"))) return;
        const r = await call("rm", { agent: S.host, kind: "network", name, confirm: name });
        said.textContent = r.ok ? "" : "remove: " + (r.error || "failed");
        close_sheet(); reload(true);
      }, [], false, async (box) => {
        const r = await call("network", { agent: S.host, name: n.name });
        if (!r.ok) return box.appendChild(el("p", "msg bad", r.error || "failed"));
        const dl = el("dl");
        for (const c of r.network.containers) dl.append(el("dt", "", c.name), el("dd", "", c.ipv4));
        box.appendChild(r.network.containers.length ? dl : el("p", "muted", "No container is on it."));
      });
    }, !n.used && !n.builtin);
    const names = (n.used_by || []).slice(0, 12);   // the tree: what is on the network, the IPs are in its sheet
    names.forEach((c, i) => {
      const tr = el("tr", "sub"), td = el("td", "muted", (i === names.length - 1 && names.length >= n.used ? "└ " : "├ ") + c);
      td.colSpan = 3; tr.appendChild(td); t.appendChild(tr);
    });
    if (n.used > names.length) { const tr = el("tr", "sub"), td = el("td", "muted", "… +" + (n.used - names.length)); td.colSpan = 3; tr.appendChild(td); t.appendChild(tr); }
  }
}

function volumes_view(t, rows, q, reload, said) {
  head_row(t, ["Volume", "Used", "Size"]);
  for (const x of rows.filter((v) => !q || v.name.toLowerCase().includes(q)).sort((a, b) => b.size - a.size)) {
    row_of(t, [x.name + (x.browse ? "  · browsable" : ""), used_text(x.used, x.used_by), x.size < 0 ? "–" : bytes(x.size)], () => {
      open_object(x.name, [["Driver", x.driver], ["Used", used_text(x.used, x.used_by)]], [x.name], x.used ? "in use" : "", async (name) => {
        if (!(await typed("Remove " + name + "?", name, "Remove"))) return;
        const r = await call("rm", { agent: S.host, kind: "volume", name, confirm: name });
        said.textContent = r.ok ? "" : "remove: " + (r.error || "failed");
        close_sheet(); reload(true);
      }, [], false, (box) => {
        const sw = el("label", "", "");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = x.browse;
        sw.append(cb, document.createTextNode(" Browse its files (read only; secrets are hidden)"));
        const area = el("div");
        box.append(sw, area);
        const show = () => { area.textContent = ""; if (cb.checked) browse(area, x.name, "/"); };
        cb.addEventListener("change", async () => {
          const r = await call("volume-access", { agent: S.host, volume: x.name, on: cb.checked });
          if (!r.ok) { cb.checked = !cb.checked; return notice(r.error || "refused"); }
          x.browse = cb.checked; show();
        });
        show();
      });
    }, !x.used);
  }
}

/** A volume's folders and files, read only: a path across the top. */
async function browse(area, volume, path) {
  area.textContent = "";
  const crumbs = el("div", "bar");
  const parts = path.split("/").filter(Boolean);
  crumbs.appendChild(btn("/", "", () => browse(area, volume, "/")));
  parts.forEach((p, i) => crumbs.appendChild(btn(p, "", () => browse(area, volume, "/" + parts.slice(0, i + 1).join("/")))));
  const list = el("div"), body = el("pre", "out");
  body.hidden = true;
  area.append(crumbs, list, body);
  const r = await call("volume-ls", { agent: S.host, volume, path });
  if (!r.ok) return list.appendChild(el("p", "msg bad", r.error || "failed"));
  const t = el("table");
  for (const e of r.entries) {
    const tr = el("tr", "row");
    tr.append(el("td", "", (e.dir ? "▸ " : "") + e.name), el("td", "num muted", e.dir ? "" : bytes(e.size)));
    tr.addEventListener("click", async () => {
      const to = (path === "/" ? "" : path) + "/" + e.name;
      if (e.dir) return browse(area, volume, to);
      if (e.link) return;
      const f = await call("volume-read", { agent: S.host, volume, path: to });
      body.hidden = false;
      body.textContent = !f.ok ? f.error || "failed" : f.file.masked && !f.file.text ? "Looks like a secret: its content is not shown." : f.file.binary ? "A binary file." : f.file.text;
    });
    t.appendChild(tr);
  }
  list.appendChild(t);
  if (r.truncated) list.appendChild(el("p", "muted", "More than this is in the folder."));
}

/** A sheet for one object: facts, Remove for each of [names], extra buttons, and [more] for what loads. */
function open_object(title, facts, names, why, remove, extra, untagged, more) {
  close_sheet();
  S.sheet = { alive: true, stream: null, term: null, gen: 0 };
  const sh = show_sheet();
  sh.appendChild(el("h2", "", title));
  const dl = el("dl");
  for (const [k, val] of facts) dl.append(el("dt", "", k), el("dd", "", val));
  sh.appendChild(dl);
  const acts = el("div", "actions");
  for (const n of names) {
    const b = btn("Remove " + n, "danger", () => remove(n));
    b.disabled = !!why;
    acts.appendChild(b);
  }
  for (const [label, fn] of extra || []) acts.appendChild(btn(label, "", fn));
  acts.appendChild(btn("Close", "", close_sheet));
  sh.appendChild(acts);
  if (why) sh.appendChild(el("p", "muted", why + (why === "in use" ? ": remove what uses it first." : "")));
  if (untagged) sh.appendChild(el("p", "muted", "Untagged layer: removed by its id."));
  if (more) { const box = el("div"); sh.appendChild(box); more(box); }
}

// ---- VMs & LXC (Proxmox): a list, and a sheet with Config / Snapshots / Backups / Tasks ----

const uptime = (s) => (s >= 86400 ? Math.floor(s / 86400) + " d" : s >= 3600 ? Math.floor(s / 3600) + " h" : Math.floor(s / 60) + " min");

async function guests(v) {
  const bar = el("div", "bar"), said = el("div", "msg bad"), table = el("table");
  bar.append(btn("Refresh", "", () => load()));
  v.append(bar, said, table);
  async function load() {
    const r = await call("guests", { agent: S.host });
    said.textContent = r.ok ? "" : r.error || "could not load";
    table.textContent = "";
    if (!r.ok) return;
    if (!r.enabled) return table.appendChild(el("p", "muted", "Proxmox is not ticked for this host: tick it in Settings > Hosts and agents."));
    head_row(table, ["", "Guest", "Kind", "CPU", "Memory", "Up", "Tags"]);
    for (const g of r.guests) {
      const tr = el("tr", "row" + (g.ticked ? "" : " dim"));
      const dot = el("span", "dot " + (g.running ? "ok" : g.template ? "" : "bad"));
      const first = el("td"); first.appendChild(dot);
      tr.append(first, el("td", "", g.id + " · " + g.name), el("td", "muted", g.template ? "template" : g.type === "lxc" ? "LXC" : "VM"),
        el("td", "muted", g.running ? g.cpu + "%" : "–"), el("td", "muted", g.running && g.mem != null ? pct(g.mem) : "–"),
        el("td", "muted", g.running ? uptime(g.uptime) : "stopped"), el("td", "muted", g.tags.join(", ")));
      if (g.ticked) tr.addEventListener("click", () => open_guest(g, load));
      table.appendChild(tr);
    }
    if (!r.guests.length) table.appendChild(el("p", "muted", "No guest on this node yet."));
  }
  await load();
}

function open_guest(g, reload) {
  close_sheet();
  S.sheet = { alive: true, stream: null, term: null };
  const sh = show_sheet();
  const said = el("div", "msg");
  const say = (t, bad) => { said.textContent = t; said.className = "msg" + (bad ? " bad" : ""); };
  sh.append(el("h2", "", g.name), el("div", "muted", g.id + " · " + (g.type === "lxc" ? "LXC" : "VM") + " · " + (g.running ? "running" : "stopped")));
  const acts = el("div", "actions");
  for (const [verb, label] of [["start", "Start"], ["shutdown", "Shut down"], ["reboot", "Reboot"]]) {
    const b = btn(label, verb === "shutdown" ? "danger" : "", async () => {
      if (verb === "shutdown" && !(await typed("Shut down " + g.name + "?", g.name, "Shut down"))) return;
      say(label + "…");
      const r = await call("guest-act", { agent: S.host, id: g.id, verb });
      say(r.ok ? label + " sent" : label + ": " + (r.error || "failed"), !r.ok);
      reload();
    });
    b.disabled = !g.can.includes(verb) || (verb === "start" && g.running) || (verb !== "start" && !g.running);
    acts.appendChild(b);
  }
  const tabs = el("div", "actions"), body = el("div");
  sh.append(acts, tabs, body, said);
  const views = { Config: guest_config, Snapshots: guest_snapshots, Backups: guest_backups, Tasks: guest_tasks };
  let tab = "Config";
  const draw = () => {
    tabs.textContent = "";
    for (const name of Object.keys(views)) tabs.appendChild(btn(name, name === tab ? "primary" : "", () => { tab = name; draw(); }));
    body.textContent = "";
    views[tab](body, g, say);
  };
  draw();
}

async function guest_config(body, g, say) {
  const r = await call("guest-config", { agent: S.host, id: g.id });
  if (!r.ok) return say(r.error || "failed", true);
  const dl = el("dl");
  for (const x of r.config) dl.append(el("dt", "", x.key), el("dd", "", x.value));
  body.appendChild(dl);
}

async function guest_snapshots(body, g, say) {
  const r = await call("guest-snapshots", { agent: S.host, id: g.id });
  if (!r.ok) return say(r.error || "failed", true);
  const take = btn("Take a snapshot…", "", async () => {
    const a = await ask("A snapshot of " + g.name, [["Name (letters, digits, - and _; starts with a letter)", "text", "name"]], "Take");
    if (!a) return;
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(a.values[0])) return notice("A snapshot's name starts with a letter and has letters, digits, - and _ (40 at most).");
    say("Taking the snapshot… (the host may need a minute)");
    const m = await call("snap-create", { agent: S.host, id: g.id, name: a.values[0] });
    say(m.ok ? "Snapshot " + a.values[0] + " made" : "Snapshot: " + (m.error || "failed"), !m.ok);
    if (m.ok) { body.textContent = ""; guest_snapshots(body, g, say); }
  });
  take.classList.add("gap");
  body.appendChild(take);
  const table = el("table");
  head_row(table, ["Snapshot", "Taken", ""]);
  for (const x of r.snapshots) {
    const tr = el("tr"), end = el("td"), ab = el("div", "actions");
    const redo = async (verb, word, title, label) => {
      if (!(await typed(title, word, label))) return;
      say(label + "…");
      const m = await call(verb, { agent: S.host, id: g.id, name: x.name, confirm: word });
      say(m.ok ? label + " done" : label + ": " + (m.error || "failed"), !m.ok);
      body.textContent = ""; guest_snapshots(body, g, say);
    };
    end.appendChild(ab);
    ab.append(btn("Roll back", "danger", () => redo("snap-rollback", g.name, "Roll " + g.name + " back to " + x.name + "? What it did since is lost.", "Roll back")),
      btn("Delete", "danger", () => redo("snap-delete", x.name, "Delete the snapshot " + x.name + "?", "Delete")));
    tr.append(el("td", "", x.name + (x.description ? " · " + x.description : "")), el("td", "muted", when(x.time)), end);
    table.appendChild(tr);
  }
  if (!r.snapshots.length) body.appendChild(el("p", "muted", "No snapshot yet. Taking one needs VM.Snapshot on the agent's Proxmox token."));
  else body.appendChild(table);
}

async function guest_backups(body, g, say) {
  const r = await call("guest-backups", { agent: S.host, id: g.id });
  if (!r.ok) return say(r.error || "failed", true);
  const table = el("table");
  head_row(table, ["Backup", "Storage", "Size", "Taken"]);
  for (const x of r.backups) {
    const tr = el("tr");
    tr.append(el("td", "", x.volid.split("/").slice(-2).join("/") + (x.protected ? " · protected" : "") + (x.notes ? " · " + x.notes : "")),
      el("td", "muted", x.storage), el("td", "num", bytes(x.size)), el("td", "muted", when(x.time)));
    table.appendChild(tr);
  }
  body.appendChild(r.backups.length ? table : el("p", "muted", "No backup found. Listing them needs Datastore.Audit on the agent's Proxmox token."));
}

async function guest_tasks(body, g, say) {
  const r = await call("guest-tasks", { agent: S.host, id: g.id });
  if (!r.ok) return say(r.error || "failed", true);
  const table = el("table");
  head_row(table, ["Task", "Result", "User", "Started"]);
  for (const x of r.tasks) {
    const tr = el("tr");
    tr.append(el("td", "", x.type), el("td", x.status === "OK" ? "muted" : "", x.status), el("td", "muted", x.user), el("td", "muted", when(x.start)));
    table.appendChild(tr);
  }
  body.appendChild(r.tasks.length ? table : el("p", "muted", "No task for this guest yet."));
}

// ---- stacks: a list, and a sheet with Compose / .env / History / Git (Validate, Save and Discard; Save never deploys) ----

const NEW_COMPOSE = "services:\n  app:\n    image: nginx:stable\n    restart: unless-stopped\n";
const when = (t) => new Date(t * 1000).toLocaleString();

async function stacks(v) {
  const bar = el("div", "bar"), said = el("div", "msg bad"), table = el("table");
  bar.append(btn("Refresh", "", () => load()), btn("New stack…", "", async () => {
    const a = await ask("A new stack", [["Name (lowercase letters, digits, - and _)", "text", "name"]], "Create");
    if (!a) return;
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(a.values[0])) return notice("A name is lowercase letters, digits, - and _.");
    open_stack({ name: a.values[0], services: 0, running: 0, source: null, hook: false }, true, load);
  }));
  v.append(bar, said, table);
  async function load() {
    const r = await call("stacks", { agent: S.host });
    said.textContent = r.ok ? "" : r.error || "could not load";
    table.textContent = "";
    if (!r.ok) return;
    if (!r.enabled) return table.appendChild(el("p", "muted", "Stacks is not ticked for this host: tick it in Settings > Hosts and agents."));
    head_row(table, ["Stack", "Running", "Source"]);
    for (const x of r.stacks) {
      const tr = el("tr", "row");
      tr.append(el("td", "", x.name), el("td", "muted", x.services ? x.running + " / " + x.services : "stopped"),
        el("td", "muted", x.source ? "git" + (x.hook ? " · webhook" : "") : ""));
      tr.addEventListener("click", () => open_stack(x, false, load));
      table.appendChild(tr);
    }
    if (!r.stacks.length) table.appendChild(el("p", "muted", "No stack on this host yet."));
  }
  await load();
}

function open_stack(x, fresh, reload) {
  close_sheet();
  S.sheet = { alive: true, stream: null, term: null, gen: 0 };
  const sh = show_sheet();
  const ed = { compose: fresh ? NEW_COMPOSE : "", env: "", c0: fresh ? "" : null, e0: "", has_env: false, mask: "********", versions: [], loaded: fresh, tab: "compose", at: null };
  const said = el("div", "msg");
  const say = (t, bad) => { said.textContent = t; said.className = "msg" + (bad ? " bad" : ""); };
  sh.append(el("h2", "", x.name), el("div", "muted", fresh ? "new stack: nothing is written until Save" : (x.services ? x.running + " / " + x.services + " running" : "stopped")));
  const acts = el("div", "actions");
  const run = async (verb, label) => {
    say(label + "…");
    const r = await call("stack-act", { agent: S.host, name: x.name, verb });
    say(r.ok ? label + " done" : label + ": " + (r.error || "failed") + (r.output ? "\n" + r.output : ""), !r.ok);
    reload();
  };
  const bRe = btn("Redeploy", "", () => run("redeploy", "Redeploy")), bRs = btn("Restart", "", () => run("restart", "Restart"));
  const bDown = btn("Down", "danger", async () => {
    if (!(await typed("Take " + x.name + " down? (volumes stay)", x.name, "Down"))) return;
    say("Down…");
    const r = await call("stack-down", { agent: S.host, name: x.name, confirm: x.name });
    say(r.ok ? "Down done" : "Down: " + (r.error || "failed"), !r.ok);
    reload();
  });
  if (fresh) { bRe.disabled = bRs.disabled = bDown.disabled = true; }
  acts.append(bRe, bRs, bDown);
  const tabs = el("div", "actions"), body = el("div");
  sh.append(acts, tabs, body, said);
  const dirty = () => ed.loaded && (ed.compose !== ed.c0 || ed.env !== ed.e0);
  const draw_tabs = () => {
    tabs.textContent = "";
    for (const [id, label] of [["compose", "Compose"], ["env", ".env"], ["history", "History"], ["git", "Git"]]) {
      const b = btn(label + (id === "compose" && ed.compose !== ed.c0 && ed.loaded || id === "env" && ed.env !== ed.e0 && ed.loaded ? " •" : ""), id === ed.tab ? "primary" : "", () => { keep(); ed.tab = id; draw_tabs(); draw(); });
      b.disabled = fresh && (id === "history" || id === "git");
      tabs.appendChild(b);
    }
  };
  let area = null;
  const keep = () => { if (area && (ed.tab === "env" || ed.tab === "compose")) ed[ed.tab] = area.value; };
  async function load_files(version) {
    const r = await call("stack-read", { agent: S.host, name: x.name, version });
    if (!r.ok) return say(r.error || "could not read", true);
    return r;
  }
  async function draw() {
    body.textContent = "";
    area = null;
    if (!ed.loaded) {
      const r = await load_files();
      if (!r) return;
      Object.assign(ed, { compose: r.compose, c0: r.compose, env: r.env, e0: r.env, has_env: r.has_env, mask: r.mask, versions: r.versions, loaded: true });
      draw_tabs();
    }
    if (ed.tab === "compose" || ed.tab === "env") return editor();
    if (ed.tab === "history") return history();
    return git();
  }
  function editor() {
    const isEnv = ed.tab === "env";
    area = el("textarea", "editor");
    area.value = isEnv ? ed.env : ed.compose;
    area.spellcheck = false;
    area.rows = 18;
    area.addEventListener("input", () => { keep(); draw_tabs(); sync(); });
    body.appendChild(area);
    if (isEnv) body.appendChild(el("p", "muted", "Values stay on the host and show as " + ed.mask + ". Leave a line as it is to keep its value; type a new value to change it."));
    const row = el("div", "actions");
    const check = btn("Validate", "", async () => { keep(); say("Checking…"); const r = await call("stack-validate", { agent: S.host, name: x.name, compose: ed.compose, env: ed.env }); say(r.ok ? "The compose file is valid." : "✕ " + (r.error || "failed") + (r.output ? "\n" + r.output : ""), !r.ok); });
    const save = btn("Save", "primary", async () => {
      keep(); say("Checking and saving…");
      const r = await call("stack-save", { agent: S.host, name: x.name, compose: ed.compose, env: ed.env });
      if (!r.ok) return say("✕ " + (r.error || "failed") + (r.output ? "\n" + r.output : ""), true);
      say("Saved. Redeploy to run it.");
      fresh = false; bRe.disabled = bRs.disabled = bDown.disabled = false;
      ed.loaded = false; ed.c0 = ed.compose; ed.e0 = ed.env;
      reload();
      const again = await load_files();
      if (again) Object.assign(ed, { compose: again.compose, c0: again.compose, env: again.env, e0: again.env, versions: again.versions, loaded: true });
      draw_tabs(); sync();
    });
    const discard = btn("Discard", "", () => { ed.compose = ed.c0; ed.env = ed.e0; area.value = isEnv ? ed.env : ed.compose; say(""); draw_tabs(); sync(); });
    const sync = () => { save.disabled = discard.disabled = !dirty(); };
    row.append(check, save, discard);
    body.appendChild(row);
    sync();
  }
  function history() {
    body.appendChild(el("p", "muted", "Each save and deploy keeps the files it replaces (the last 10). Restore writes an older version back; it does not deploy."));
    if (dirty()) body.appendChild(el("p", "warn-text", "You have unsaved changes: Save or Discard them before restoring."));
    const t = el("table");
    for (const id of ed.versions) {
      const tr = el("tr");
      const view = btn("View", "", async () => {
        const r = await load_files(String(id));
        if (!r) return;
        pre.hidden = false; pre.textContent = r.compose + (r.has_env ? "\n# .env\n" + r.env : "");
      });
      const restore = btn("Restore", "", async () => {
        if (dirty()) return say("Save or Discard your changes first.", true);
        const r = await call("stack-restore", { agent: S.host, name: x.name, version: String(id) });
        say(r.ok ? "Restored. Redeploy to run it." : "✕ " + (r.error || "failed"), !r.ok);
        if (r.ok) { ed.loaded = false; draw(); reload(); }
      });
      const td = el("td"); td.append(view, document.createTextNode(" "), restore);
      tr.append(el("td", "", when(id)), td);
      t.appendChild(tr);
    }
    const pre = el("pre", "out"); pre.hidden = true;
    body.append(ed.versions.length ? t : el("p", "muted", "No older version yet."), pre);
  }
  function git() {
    const src = x.source || {};
    const f = (label, name, val, ph) => { const i = el("input"); i.value = val || ""; i.placeholder = ph; i.name = name; const l = el("label", "", label); l.appendChild(i); body.appendChild(l); return i; };
    body.appendChild(el("p", "muted", "The compose file can come from a repository. Sync pulls it, saves it (the .env stays) and redeploys. A token for a private repository is used once and never kept, so a webhook only works for a repository that needs none."));
    const repo = f("Repository", "repo", src.repo, "https://git.example/owner/name");
    const branch = f("Branch", "branch", src.branch || "main", "main");
    const path = f("Path of the file", "path", src.path, "stacks/web/compose.yaml");
    const row = el("div", "actions"), out = el("div");
    const saveSrc = btn("Save source", "primary", async () => {
      const r = await call("stack-source", { agent: S.host, name: x.name, repo: repo.value.trim(), branch: branch.value.trim(), path: path.value.trim() });
      say(r.ok ? "Source saved." : "✕ " + (r.error || "failed"), !r.ok);
      if (r.ok) { x.source = { repo: repo.value.trim(), branch: branch.value.trim() || "main", path: path.value.trim() }; reload(); draw(); }
    });
    const sync = btn("Sync now", "", async () => {
      const a = await ask("Sync " + x.name + " from git", [["Token (only for a private repository; leave empty otherwise)", "password", "t"]], "Sync");
      if (!a) return;
      say("Syncing…");
      const r = await call("stack-sync", { agent: S.host, name: x.name, token: a.values[0] || undefined });
      say(r.ok ? "Synced and redeployed." : "✕ " + (r.error || "failed") + (r.output ? "\n" + r.output : ""), !r.ok);
      ed.loaded = false; reload();
    });
    const forget = btn("Forget source", "danger", async () => {
      const r = await call("stack-source", { agent: S.host, name: x.name, clear: true });
      say(r.ok ? "Source and webhook removed." : "✕ " + (r.error || "failed"), !r.ok);
      if (r.ok) { x.source = null; x.hook = false; reload(); draw(); }
    });
    const hook = btn(x.hook ? "New webhook URL" : "Make a webhook URL", "", async () => {
      const r = await call("stack-hook", { agent: S.host, name: x.name });
      if (!r.ok) return say("✕ " + (r.error || "failed"), true);
      x.hook = true; reload();
      out.textContent = "";
      out.append(el("p", "muted", "Shown once. Call it with POST from your git host; it only syncs this stack, at most once in 30 s:"), el("pre", "out", location.origin + r.path));
    });
    saveSrc.disabled = false; sync.disabled = forget.disabled = !x.source; hook.disabled = !x.source;
    row.append(saveSrc, sync, hook, forget);
    body.append(row, out);
  }
  draw_tabs();
  draw();
}

// ---- start ----

(async function start() {
  if (location.protocol !== "https:") {
    const w = $("warn");
    w.hidden = false;
    w.textContent = "This page is meant for HTTPS. Over plain HTTP LabHUD refuses Servers.";
  }
  if (!(await begin())) return show_error("Not signed in. Reload to try again.");
  await load_hosts();
  setInterval(() => { if (!S.sheet) load_hosts(); }, 30000);
})();

// ---- Host: the system, its systemd units, and what was done to it ----

async function host_tab(v) {
  const said = el("div", "msg bad"), top = el("div"), units = el("div"), sched = el("div"), log = el("div");
  v.append(said, top, units, sched, log);
  const load = async () => {
    const r = await call("host", { agent: S.host });
    said.textContent = r.ok ? "" : r.error || "could not load";
    if (r.ok) { draw_host(top, units, r, said, load); scheduler(sched, r); activity(log); }
  };
  await load();
}

function uptime_text(s) {
  if (!s) return "–";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? d + "d " + h + "h" : h ? h + "h " + m + "m" : m + "m";
}

function draw_host(top, units, r, said, reload) {
  top.textContent = ""; units.textContent = "";
  const dl = el("dl");
  for (const [k, val] of [["Host", r.host.hostname || S.host], ["System", r.host.os || "–"], ["Kernel", r.host.kernel || "–"],
    ["Arch", r.host.arch || "–"], ["Up", uptime_text(r.uptime)], ["Agent", r.version || "–"]]) dl.append(el("dt", "", k), el("dd", "", val));
  if (r.updates) dl.append(el("dt", "", "Updates"), el("dd", r.updates.pending ? "warn-text" : "",
    r.updates.pending ? r.updates.pending + " pending" + (r.updates.security ? " (" + r.updates.security + " security)" : "") + (r.updates.names.length ? ": " + r.updates.names.slice(0, 5).join(", ") + (r.updates.pending > 5 ? "…" : "") : "") : "up to date"));
  top.appendChild(dl);
  const acts = el("div", "actions");
  const run = async (op, body, ok) => { const x = await call(op, Object.assign({ agent: S.host }, body)); said.textContent = x.ok ? ok : op + ": " + (x.error || "failed"); reload(); };
  const update = btn("Update OS", "", async () => {
    if (await typed("Update the packages of " + S.host + "?", S.host, "Update")) run("host-update", {}, "The update runs in the background; the timeline says how it ended.");
  });
  update.disabled = !r.can.update;
  acts.appendChild(update);
  for (const verb of ["reboot", "shutdown"]) {
    const b = btn(verb[0].toUpperCase() + verb.slice(1), "danger", async () => {
      if (await typed(verb[0].toUpperCase() + verb.slice(1) + " " + S.host + "?", S.host, verb[0].toUpperCase() + verb.slice(1))) run("host-power", { verb, confirm: S.host }, "Sent: " + verb + ".");
    });
    b.disabled = !r.can.power;
    acts.appendChild(b);
  }
  top.appendChild(acts);
  if (!r.can.update) top.appendChild(el("p", "muted", "Tick Host for this agent in Settings > Hosts and agents to update or reboot it here."));
  units.appendChild(el("h3", "", "Systemd units" + (r.failed.length ? " · " + r.failed.length + " failed" : "")));
  const t = el("table"); units.appendChild(t);
  head_row(t, ["Unit", "State", "Description"]);
  const failed = new Set(r.failed);
  for (const u of r.units) {
    const tr = row_of(t, [u.name + (u.ticked ? "" : "  (read only)"), u.active + (u.sub && u.sub !== u.active ? " · " + u.sub : ""), u.description], () => unit_sheet(u, said, reload), failed.has(u.name) || u.active === "failed");
    tr.lastChild.classList.remove("num");
    if (!u.ticked) tr.classList.add("dim");
  }
}

function unit_sheet(u, said, reload) {
  close_sheet();
  S.sheet = { alive: true, stream: null, term: null, gen: 0 };
  const sh = show_sheet();
  sh.appendChild(el("h2", "", u.name));
  const dl = el("dl");
  dl.append(el("dt", "", "State"), el("dd", "", u.active + " · " + u.sub), el("dt", "", "About"), el("dd", "", u.description));
  sh.appendChild(dl);
  const acts = el("div", "actions");
  const run = async (op, body) => { const r = await call(op, Object.assign({ agent: S.host, name: u.name }, body)); said.textContent = r.ok ? "" : op + ": " + (r.error || "failed"); close_sheet(); reload(); };
  const start = btn("Start", "", () => run("unit", { verb: "start" })), restart = btn("Restart", "", () => run("unit", { verb: "restart" }));
  const stop = btn("Stop", "danger", async () => { if (await typed("Stop " + u.name + "?", u.name, "Stop")) run("unit-stop", { confirm: u.name }); });
  for (const b of [start, restart, stop]) { b.disabled = !u.ticked; acts.appendChild(b); }
  acts.appendChild(btn("Close", "", close_sheet));
  sh.appendChild(acts);
  if (!u.ticked) sh.appendChild(el("p", "muted", "Not ticked: tick this unit for the agent in Settings > Hosts and agents to act on it."));
}

async function activity(box) {
  box.textContent = "";
  box.appendChild(el("h3", "", "Activity"));
  const bar = el("div", "bar"), said = el("div", "msg"), t = el("table");
  bar.appendChild(btn("Verify chain", "", async () => {
    const r = await call("audit-verify", {});
    said.className = "msg" + (r.ok && r.intact ? "" : " bad");
    said.textContent = !r.ok ? r.error || "failed" : r.intact ? "Intact: " + r.checked + " entries checked." : "Broken: " + r.broken;
  }));
  box.append(bar, said, t);
  let offset = 0;
  const more = btn("Older", "", () => page());
  const page = async () => {
    const r = await call("activity", { agent: S.host, offset });
    if (!r.ok) return said.textContent = r.error || "could not load";
    if (!offset) head_row(t, ["When", "Who", "What", "Target"]);
    for (const e of r.rows) {
      const tr = el("tr");
      tr.append(el("td", "muted", e.ts.replace("T", " ").slice(0, 16)), el("td", "", e.who), el("td", e.ok ? "" : "warn-text", e.action.replace("servers/", "").replace("agent/", "agent: ") + (e.ok ? "" : e.source === "agent" ? " (failed)" : " (refused)")), el("td", "muted", e.target));
      t.appendChild(tr);
    }
    offset += r.rows.length;
    more.style.display = r.more ? "" : "none";
    if (!offset) box.appendChild(el("p", "muted", "Nothing done from here yet."));
  };
  box.appendChild(more);
  await page();
}

// ---- Scheduler: jobs that run on this host (and the ones for every host) at a time of day ----

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const KIND_LABEL = { "update": "Update containers", "prune": "Prune unused images", "host-update": "Update OS", "backup": "Backup Proxmox guests" };

function days_text(d) { return d.length === 7 ? "every day" : d.map((x) => DAYS[x]).join(" "); }

async function scheduler(box, host) {
  box.textContent = "";
  box.appendChild(el("h3", "", "Scheduler"));
  const said = el("div", "msg bad"), t = el("table");
  const bar = el("div", "bar");
  box.append(bar, said, t);
  const r = await call("jobs", {});
  if (!r.ok) return said.textContent = r.error || "could not load";
  const mine = r.jobs.filter((j) => j.agent === S.host || j.agent === "*");
  const reload = () => scheduler(box, host);
  bar.appendChild(btn("Add job", "", () => job_sheet(null, r.kinds, reload)));
  head_row(t, ["Job", "When", "Last run"]);
  for (const j of mine) {
    const last = j.last && j.last.t ? new Date(j.last.t * 1000).toLocaleString() + (j.last.ok ? "" : " · failed") : "never";
    const tr = row_of(t, [j.name + (j.enabled ? "" : "  (off)") + (j.agent === "*" ? "  · all hosts" : ""), days_text(j.days) + " " + j.at, last], () => job_sheet(j, r.kinds, reload), j.last && j.last.t && !j.last.ok);
    tr.lastChild.classList.remove("num");
    if (!j.enabled) tr.classList.add("dim");
  }
  if (!mine.length) t.appendChild(el("tr")).appendChild(el("td", "muted", "No job for this host yet."));
}

// the editor: nothing is saved until Save; Discard closes it
function job_sheet(job, kinds, reload) {
  close_sheet();
  S.sheet = { alive: true, stream: null, term: null, gen: 0 };
  const sh = show_sheet();
  const isnew = !job;
  const cur = job || { name: "", kind: "prune", agent: S.host, days: [6], at: "04:00", enabled: true, last: {} };
  sh.appendChild(el("h2", "", isnew ? "New job" : cur.name));
  const form = el("div", "form"), said = el("div", "msg bad");
  const name = el("input"); name.type = "text"; name.value = cur.name; name.maxLength = 60; name.placeholder = "Name";
  const kind = el("select");
  for (const k of kinds) { const o = el("option", "", KIND_LABEL[k] || k); o.value = k; o.selected = k === cur.kind; kind.appendChild(o); }
  kind.disabled = !isnew;
  const where = el("select");
  const set_where = () => {
    where.textContent = "";
    if (kind.value === "update") { const o = el("option", "", "All hosts"); o.value = "*"; where.appendChild(o); }
    const o = el("option", "", S.host); o.value = S.host; where.appendChild(o);
    where.value = cur.agent === "*" && kind.value === "update" ? "*" : kind.value === "update" && !isnew ? cur.agent : S.host;
  };
  set_where();
  kind.addEventListener("change", set_where);
  where.disabled = !isnew && cur.kind === "update" && cur.id === "00000001";
  const time = el("input"); time.type = "time"; time.value = cur.at;
  const day_row = el("div", "days"), boxes = [];
  DAYS.forEach((d, i) => { const l = el("label", "", ""), c = el("input"); c.type = "checkbox"; c.checked = cur.days.includes(i); boxes.push(c); l.append(c, " " + d + " "); day_row.appendChild(l); });
  const on = el("input"); on.type = "checkbox"; on.checked = cur.enabled;
  const on_l = el("label", "", ""); on_l.append(on, " Enabled");
  // a backup job also names its guests (the ticked ones of the host), a storage and a mode
  const bk = el("div", "form");
  const guests = el("div", "days"), gboxes = [];
  const storage = el("input"); storage.type = "text"; storage.maxLength = 40; storage.placeholder = "Backup storage (e.g. local)"; storage.value = cur.storage || "";
  const mode = el("select");
  for (const m of ["snapshot", "suspend", "stop"]) { const o = el("option", "", m); o.value = m; o.selected = m === (cur.mode || "snapshot"); mode.appendChild(o); }
  bk.append(el("p", "muted", "Guests to back up"), guests, storage, mode);
  const show_bk = () => { bk.style.display = kind.value === "backup" ? "" : "none"; };
  show_bk(); kind.addEventListener("change", show_bk);
  call("guests", { agent: S.host }).then((g) => {
    if (!g.ok) return guests.appendChild(el("span", "muted", g.error || "no guests"));
    for (const x of g.guests.filter((x) => x.ticked && !x.template)) {
      const l = el("label", ""), c = el("input"); c.type = "checkbox"; c.value = String(x.id); c.checked = (cur.vmids || []).includes(String(x.id));
      gboxes.push(c); l.append(c, " " + x.id + " " + x.name + " "); guests.appendChild(l);
    }
    if (!gboxes.length) guests.appendChild(el("span", "muted", "No ticked guest on this host."));
  });
  form.append(name, kind, where, time, day_row, on_l, bk);
  sh.append(form, said);
  if (!isnew && cur.last && cur.last.t) sh.appendChild(el("p", "muted", "Last run: " + new Date(cur.last.t * 1000).toLocaleString() + " · " + (cur.last.ok ? "ok" : "failed") + " · " + cur.last.text));
  const acts = el("div", "actions");
  const body = () => ({ id: isnew ? undefined : cur.id, name: name.value, kind: kind.value, agent: where.value, at: time.value, enabled: on.checked, days: boxes.map((c, i) => (c.checked ? i : -1)).filter((i) => i >= 0),
    ...(kind.value === "backup" ? { vmids: gboxes.filter((c) => c.checked).map((c) => c.value), storage: storage.value.trim(), mode: mode.value } : {}) });
  acts.appendChild(btn("Save", "primary", async () => {
    const r = await call("job-save", { job: body() });
    if (!r.ok) return said.textContent = r.error || "not saved";
    close_sheet(); reload();
  }));
  acts.appendChild(btn("Discard", "", close_sheet));
  if (!isnew) {
    acts.appendChild(btn("Run now", "", async () => {
      said.className = "msg"; said.textContent = "Running…";
      const r = await call("job-run", { id: cur.id });
      said.className = "msg" + (r.ok ? "" : " bad");
      said.textContent = r.ok ? "Done: " + r.text : "Failed: " + (r.error || "");
    }));
    if (cur.id !== "00000001") acts.appendChild(btn("Delete", "danger", async () => {
      if (!(await typed("Delete " + cur.name + "?", cur.name, "Delete"))) return;
      const r = await call("job-delete", { id: cur.id, confirm: cur.name });
      if (!r.ok) return said.textContent = r.error || "not deleted";
      close_sheet(); reload();
    }));
  }
  sh.appendChild(acts);
}
