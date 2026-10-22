// Servers (/servers): the hosts' Docker, over labhud's own /api/servers/*. Plain JS, no build step.
// Everything that comes from a host is put in the page with textContent, never as HTML. The page is
// served only over HTTPS with a policy that has no inline script or style (server.py SERVERS_CSP).
"use strict";

const ADMIN_KEY = "labhud-admin";    // the admin's ticket, the same one the display keeps
const LOCK_KEY = "labhud-unlock";    // the PIN's ticket (this tab only)
const LOG_LINES = 1000;
const $ = (id) => document.getElementById(id);
const S = { hosts: [], host: null, tab: "overview", rows: [], filter: "", sheet: null, timer: 0 };
const TABS = [["overview", "Overview", true], ["containers", "Containers", true], ["stacks", "Stacks", false],
              ["images", "Images", true], ["volumes", "Volumes", true], ["networks", "Networks", true],
              ["vms", "VMs & LXC", false], ["host", "Host", false]];

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
    const b = btn("", "host" + (h.name === S.host ? " on" : ""), () => { S.host = h.name; S.sheet && close_sheet(); draw_hosts(); draw_view(); });
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
    b.disabled = !on;
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
  cells.forEach((c, i) => tr.appendChild(el("td", i === cells.length - 1 && cells.length === 3 ? "num" : i ? "muted" : "", c)));
  if (bad) tr.firstChild.classList.add("warn-text");
  tr.addEventListener("click", onclick);
  t.appendChild(tr);
}

function images_view(t, rows, q, reload, said) {
  head_row(t, ["Image", "Used", "Size"]);
  const title = (i) => i.tags[0] || i.id;
  for (const i of rows.filter((x) => !q || x.id.includes(q) || x.tags.some((g) => g.toLowerCase().includes(q))).sort((a, b) => b.size - a.size)) {
    row_of(t, [title(i) + (i.tags.length > 1 ? "  +" + (i.tags.length - 1) : ""), used_text(i.used, i.used_by), bytes(i.size)], () => open_object(i.tags[0] || i.id, [
      ["Id", i.id], ["Size", bytes(i.size)], ["Used", used_text(i.used, i.used_by)]], i.tags.length ? i.tags : [i.id], i.used ? "in use" : "", async (n) => {
      if (!(await typed("Remove " + n + "?", n, "Remove"))) return;
      const r = await call("rm", { agent: S.host, kind: "image", name: n, confirm: n });
      said.textContent = r.ok ? "" : "remove: " + (r.error || "failed");
      close_sheet(); reload(true);
    }, [["Pull again", async () => { const r = await call("pull", { agent: S.host, image: i.tags[0] }); said.textContent = r.ok ? "" : "pull: " + (r.error || "failed"); reload(true); }]], !i.tags.length), !i.used);
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
