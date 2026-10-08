"use strict";
// The wall display — the browser side.
//
// Layout: one page per config page, no submenu. All groups of a page sit on the same screen, in
// columns, and must fit without scrolling — that is why a Proxmox guest card is a single row.
// The rotation changes page every 20 s and pauses for 60 s after a touch.
//
// Three rules that keep the phone cool (all paid for once, on the old version):
//  1. ONLY the current page is in the DOM. The rest does not exist, so it is not rendered or measured.
//  2. The structure is built once per page change; data only rewrites text, and only when it
//     actually changed. No needless DOM write, so no needless relayout.
//  3. No uncontrolled setInterval: everything goes through `tick()`, which skips while the page is
//     not visible. With the screen off the phone does nothing.
//
// Data comes through a single SSE connection. The browser knows no API key and talks to no service
// in the fleet — except for actions, which go straight to the action agent (`action_url` in
// /api/config, from LABHUD_ACTION_URL) because that agent's allowlist is on the display's own IP.

const $ = (s, r) => (r || document).querySelector(s);
const PERIOD = 20000;   // how long a page stays in the rotation
const PAUSE = 60000;    // how long the rotation holds after a touch

let CFG = null;           // the topology, from /api/config
let CARD_BY_ID = {};      // every card, flattened: the bottom strip looks its state up here
let D = {};               // the latest snapshot
let page = 0;             // index of the current page
let updaters = [];        // the functions that rewrite values in the mounted page
let pausedUntil = 0;

// ---------------------------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------------------------

/** setInterval that skips ticks while the page is not visible. */
function tick(fn, ms) {
  return setInterval(() => { if (!document.hidden) fn(); }, ms);
}

/** Reads a dotted path from the snapshot: "proxmox.pve.vms". */
function get(path) {
  let v = D;
  for (const part of path.split(".")) {
    if (v == null) return null;
    v = v[part];
  }
  return v === undefined ? null : v;
}

const nf = new Intl.NumberFormat("en-US");
const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

function bytes(n) {
  if (n == null || isNaN(n)) return "—";
  const u = ["B", "K", "M", "G", "T"];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (v >= 100 || i === 0 ? Math.round(v) : nf1.format(v)) + u[i];
}

/** "14G / 60G", plus the percentage when asked: how much of the total is used. */
function used_of(used, total, with_percent) {
  if (used == null || !total) return "—";
  const t = `${bytes(used)} / ${bytes(total)}`;
  return with_percent ? `${t} · ${Math.round(100 * used / total)}%` : t;
}

function rate(n) {
  if (n == null || isNaN(n)) return "—";
  const v = Number(n);
  if (v < 1024) return "0";
  if (v < 1024 * 1024) return Math.round(v / 1024) + "K";
  return nf1.format(v / 1048576) + "M";
}

function duration(s) {
  if (!s) return "—";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

function format(v, fmt) {
  if (v == null || v === "") return "—";
  switch (fmt) {
    case "percent": return (typeof v === "number" ? Math.round(v) : v) + "%";
    case "percent1": return (typeof v === "number" ? nf1.format(v) : v) + "%";
    case "bytes": return bytes(v);
    case "used_of": return Array.isArray(v) && v[1] ? `${bytes(v[0])} / ${bytes(v[1])}` : "—";
    case "rate": return rate(v);
    case "celsius": return (typeof v === "number" ? Math.round(v) : v) + "°";
    case "count": return typeof v === "number" ? nf.format(v) : String(v);
    default: return String(v);
  }
}

/** Is memory really a problem? PSI, not the percentage. `some` = how long at least one process
    waited for memory, `full` = how long ALL of them did. Any sustained `full` is serious. */
function bad_pressure(g) {
  if (!g) return false;
  if (g.pressure_full > 1) return true;
  return (g.pressure || 0) > 10;
}

/** Colour thresholds: only for the quantities where "high" really means "bad". */
function severity(path, fmt, v) {
  if (fmt === "used_of" && Array.isArray(v) && v[1] && /\.(mem_used_of|disk_used_of)$/.test(path)) {
    const p = 100 * (v[0] || 0) / v[1];
    return p >= 92 ? "crit" : p >= 80 ? "warn" : "";
  }
  if (typeof v !== "number") return "";
  if (fmt === "celsius") return v >= 85 ? "crit" : v >= 75 ? "warn" : "";
  if (fmt === "percent" && /\.(cpu|mem|used_percent|disk\.percent)$/.test(path)) {
    return v >= 92 ? "crit" : v >= 80 ? "warn" : "";
  }
  // the HEALTH page and the INTERNET card (1 Oct 2026): any value above zero is already a problem
  if (/\.latency\.loss$/.test(path)) return v >= 5 ? "crit" : v > 0 ? "warn" : "";
  if (/\.cert_min_days$/.test(path)) return v < 7 ? "crit" : v < 14 ? "warn" : "";
  if (/\.(cert_under_14|problems|stale|security)$/.test(path)) return v > 0 ? "crit" : "";
  return "";
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

/** Writes to the DOM only when the value actually changed. */
function put(node, text) {
  if (node.textContent !== text) node.textContent = text;
}
function cls(node, name, on) {
  if (node.classList.contains(name) !== !!on) node.classList.toggle(name, !!on);
}

// ---------------------------------------------------------------------------------------------
// The SSE stream
// ---------------------------------------------------------------------------------------------

function start_stream() {
  // `?static` fetches the data once and opens no stream. It is for check screenshots: with an
  // open EventSource the page never becomes "idle", so headless Chrome waits until the timeout
  // and dies with "Unexpected renderer destruction", without writing the picture.
  if (location.search.includes("static")) {
    fetch("/api/snapshot").then((r) => r.json()).then((d) => {
      D = d; update();
      // Layout diagnostic, only in check mode: how much space the page asks for versus how much
      // it has. Above 1.0 means something is cut — groups must fit WITHOUT scrolling, and that
      // does not show on a screenshot if you only look at what stayed visible.
      // setTimeout, not requestAnimationFrame: under `--virtual-time-budget` Chrome produces no
      // frames, so a rAF scheduled at the end never runs and the measurement stays empty.
      // `&panel=<id>` opens a card's panel, so it can be checked on a screenshot: otherwise
      // there is no way to see it without touching the screen.
      const asked = new URLSearchParams(location.search).get("panel");
      if (asked && CARD_BY_ID[asked]) open_panel(CARD_BY_ID[asked]);
      setTimeout(() => {
        const c = $("#content");
        const groups = [...c.querySelectorAll(".group")].map((g) =>
          g.querySelector("h2").textContent + ":" + Math.round(g.getBoundingClientRect().height));
        // A column taller than the content box is cut at the bottom; more columns than fit are
        // narrowed instead (see layout), which shows as a sideways overflow only if that failed.
        const bottom = c.getBoundingClientRect().bottom - parseFloat(getComputedStyle(c).paddingBottom);
        const cut = [...c.querySelectorAll(".stack")].some((s) => s.getBoundingClientRect().bottom > bottom + 1);
        const width = c.scrollWidth > c.clientWidth + 1 ? " CUT-SIDEWAYS" : "";
        document.body.dataset.measure =
          `h=${c.scrollHeight}/${c.clientHeight} w=${c.scrollWidth}/${c.clientWidth}${width}${cut ? " CUT-BOTTOM" : ""}` +
          ` | ${groups.join(" ")}`;
      }, 400);
    });
    return;
  }
  const s = new EventSource("/api/stream");
  s.addEventListener("full", (e) => { D = JSON.parse(e.data); document.body.classList.remove("disconnected"); update(); });
  s.addEventListener("delta", (e) => { Object.assign(D, JSON.parse(e.data)); update(); });
  s.onopen = () => document.body.classList.remove("disconnected");
  s.onerror = () => document.body.classList.add("disconnected");  // EventSource reconnects by itself
}

/** A single entry point for any new data, deferred to the next frame. */
let deferred = false;
function update() {
  if (deferred) return;
  deferred = true;
  requestAnimationFrame(() => {
    deferred = false;
    for (const fn of updaters) fn();
    render_strip();
    render_alerts();
    render_weather();
    mark_menu_alerts();
    if (panelCard) fill_panel();   // an open panel refreshes with the new data
  });
}

// ---------------------------------------------------------------------------------------------
// The page menu
// ---------------------------------------------------------------------------------------------

function render_menu() {
  const m = $("#menu");
  m.textContent = "";
  CFG.pages.forEach((p, i) => {
    const b = el("button", i === page ? "active" : "", p.title);
    b.dataset.page = String(i);
    b.type = "button";
    b.addEventListener("click", () => { touched(); go(i); });
    m.appendChild(b);
  });
}

function go(i) {
  page = (i + CFG.pages.length) % CFG.pages.length;
  document.body.className = document.body.className.replace(/\bp-\w+\b/g, "").trim();
  document.body.classList.add("p-" + CFG.pages[page].id);
  for (const b of $("#menu").children) cls(b, "active", Number(b.dataset.page) === page);
  mount();
  history.replaceState(null, "", "#" + CFG.pages[page].id);
}

// ---------------------------------------------------------------------------------------------
// Mounting the current page: all its groups, in columns
// ---------------------------------------------------------------------------------------------

let pageGroups = [];       // the mounted page's group elements, in config order
let layoutSign = "";       // what the last layout was computed for (see relayout_if_needed)
let layoutWatch = null;    // a ResizeObserver: a group or the content box changed size

function mount() {
  const host = $("#content");
  host.textContent = "";
  updaters = [];
  pageGroups = [];
  if (layoutWatch) layoutWatch.disconnect();

  if (CFG.pages[page].games) { mount_games(host, CFG.pages[page].games); update(); return; }

  for (const g of CFG.pages[page].groups) {
    const cards = CFG.cards[g.id] || [];
    if (!cards.length) continue;
    // A group is a framed box: its title is the big heading, its cards sit inside with smaller
    // names. A card named like its group (PVE in group PVE) would only repeat the title, so it
    // loses its own heading and its status dot moves up into the group's.
    const group = el("section", "group");
    const title = el("h2");
    const same = cards.filter((c) => !is_guest(c) && norm(c.name) === norm(g.title));
    const merged = same.length === 1 ? same[0] : null;
    title.style.setProperty("--len", String(g.title.length + (merged && merged.check ? 2 : 0)));
    if (merged && merged.check) add_dot(title, merged);
    title.appendChild(el("span", "t", g.title));
    group.appendChild(title);
    const body = el("div", "body");
    group.appendChild(body);
    // Proxmox guests sit two per row, like tiles: with one full row each, a host with eight
    // guests is taller than the column and would be cut.
    let box = null;
    for (const c of cards) {
      if (is_guest(c)) {
        if (!box) { box = el("div", "guests"); body.appendChild(box); }
        box.appendChild(make_card(c));
      } else {
        box = null;
        body.appendChild(make_card(c, c === merged));
      }
    }
    // the whole frame answers a tap when it IS the card, heading included
    if (merged && cards.length === 1 && has_details(merged)) bind_touch(group, merged);
    pageGroups.push(group);
  }
  update();
  layout();
  // a timer, not requestAnimationFrame: headless screenshots (and a phone with the screen off)
  // give no frames; and not inside the callback itself, where moving groups trips the
  // "ResizeObserver loop" warning
  layoutWatch = new ResizeObserver(() => setTimeout(relayout_if_needed, 0));
  layoutWatch.observe(host);
  for (const g of pageGroups) layoutWatch.observe(g);
}

function norm(text) { return String(text || "").trim().toLowerCase(); }

/** What a layout depends on: the content box and every group's height. */
function layout_sign() {
  const host = $("#content");
  return `${host.clientWidth}x${host.clientHeight}|` + pageGroups.map((g) => g.offsetHeight).join(",");
}

/** Lists grow when data arrives, the alert banner shrinks the box, the phone rotates: lay out
    again, but only if something really changed — moving the groups into place fires the
    observer too, with the same sizes, and that must not loop. */
function relayout_if_needed() {
  if (pageGroups.length && layout_sign() !== layoutSign) layout();
}

/** Places the groups in columns, a group never split between two. Each column is filled in config
    order up to a target height; the lowest target that fits the page in the available columns
    wins, so the columns come out balanced, like newspaper columns but with whole groups. A group
    taller than the screen gets two columns' width, its cards flowing in two columns inside the
    frame. A page that still does not fit is zoomed out a step at a time (down to 60%), so it keeps
    its columns whole instead of being cut. Heights are measured on screen (getBoundingClientRect),
    so they already include the zoom. */
function layout() {
  const host = $("#content");
  if (!pageGroups.length) return;
  const cs = getComputedStyle(host);
  const cols = parseInt(cs.getPropertyValue("--cols"), 10) || 5;
  const gap = parseFloat(cs.columnGap) || 6;
  const W = host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const H = host.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  const colW = (W - gap * (cols - 1)) / cols;
  const width = (span) => span * colW + (span - 1) * gap;   // on screen, zoom included

  const plan = (zoom) => {
    host.textContent = "";
    const probe = el("div", "stack");
    probe.style.zoom = zoom;
    probe.style.width = width(1) / zoom + "px";
    host.appendChild(probe);
    for (const g of pageGroups) { g.classList.remove("wide"); probe.appendChild(g); }
    const items = pageGroups.map((g) => ({ g, h: g.getBoundingClientRect().height, span: 1 }));
    const tall = items.filter((it) => it.h > H);
    if (tall.length) {
      probe.style.width = width(2) / zoom + "px";
      for (const it of tall) { it.g.classList.add("wide"); it.span = 2; }
      for (const it of tall) it.h = it.g.getBoundingClientRect().height;
    }
    const gapOn = gap * zoom;   // the gap inside a zoomed column shrinks with it
    const pack = (target) => {
      const stacks = [];
      let cur = null;
      for (const it of items) {
        if (it.span === 2) { stacks.push({ items: [it], span: 2, h: it.h }); cur = null; continue; }
        if (cur && cur.h + gapOn + it.h <= target) { cur.items.push(it); cur.h += gapOn + it.h; continue; }
        cur = { items: [it], span: 1, h: it.h };
        stacks.push(cur);
      }
      return stacks;
    };
    const used = (stacks) => stacks.reduce((n, s) => n + s.span, 0);
    const narrow = items.filter((it) => it.span === 1);
    let target = Math.max(0, ...narrow.map((it) => it.h), narrow.reduce((n, it) => n + it.h, 0) / cols);
    let stacks = pack(target);
    while (used(stacks) > cols && target < H) { target = Math.min(H, target + 8); stacks = pack(target); }
    const fits = used(stacks) <= cols && stacks.every((s) => s.h <= H + 1);
    return { stacks, fits, zoom, used: used(stacks) };
  };

  let best = null;
  for (const zoom of [1, 0.92, 0.85, 0.78, 0.72, 0.66, 0.6]) {
    best = plan(zoom);
    if (best.fits) break;
  }

  host.textContent = "";
  const { stacks, zoom } = best;
  const inCols = best.used <= cols;
  for (const s of stacks) {
    const col = el("div", "stack");
    col.style.zoom = zoom;
    // more columns than room (only past the last zoom step): they all narrow to fit
    col.style.flex = inCols ? `0 0 ${width(s.span) / zoom}px` : `${s.span} 1 0`;
    for (const it of s.items) col.appendChild(it.g);
    host.appendChild(col);
  }
  layoutSign = layout_sign();
}

/** true/false/"scheduled"/"on-demand"/null for a card's status. */
function card_status(c) {
  if (!c || !c.check) return null;
  if (c.check[0] === "proxmox") {
    const g = get("proxmox." + c.check[1] + ".guests." + c.check[2]);
    return g ? !!g.running : null;
  }
  if (c.check[0] === "known") return c.check[1];   // game servers know their own state
  const v = get("status." + c.id);
  if (v === "scheduled") return "scheduled";       // off on schedule, not down
  // Off by design (PBS is woken by WoL when needed): not an outage, so neither red nor pulsing —
  // a dot pulsing non-stop on the strip is noise, and an endless animation tied to no real event.
  if (v === false && c.on_demand) return "on-demand";
  return typeof v === "boolean" ? v : null;
}

function add_dot(head, c) {
  const dot = el("span", "dot");
  head.appendChild(dot);
  updaters.push(() => {
    const alive = card_status(c);
    cls(dot, "up", alive === true);
    cls(dot, "down", alive === false);
    cls(dot, "scheduled", alive === "scheduled" || alive === "on-demand");
  });
}

/** A Proxmox guest card: a small tile, two per row. */
function is_guest(c) {
  return !c.large && !c.list && !c.torrents && c.check && c.check[0] === "proxmox";
}

/** Does the card actually have anything to show in a panel? Without this, a tap on a card that has
    nothing beyond what its face already shows (the status dot, the spec) looks like a broken
    press: it lights up "pressed" and nothing happens. Better not make it tappable at all.
    `c.subtitle` alone does NOT count: it is already on the card's face, so repeating it in the
    panel is not detail (see mikrotik/openwrt — only spec + status, nothing more). */
function has_details(c) {
  return !!(c.metrics || c.list || c.torrents || c.panel ||
    (c.actions && c.actions.length) || (c.check && c.check[0] === "proxmox"));
}

/** `merged`: the card is named like its group, whose heading already shows its name and dot. */
function make_card(c, merged = false) {
  if (is_guest(c)) return make_guest_card(c);

  const n = el("div", "card " + (c.large ? "large" : "small") + (merged ? " merged" : ""));
  n.dataset.id = c.id;
  if (!merged) {
    const head = el("div", "head");
    if (c.check) add_dot(head, c);
    head.appendChild(el("span", "name", c.name));
    n.appendChild(head);
  }
  if (c.subtitle) n.appendChild(el("div", "spec", c.subtitle));
  if (c.metrics) n.appendChild(make_metrics(c.metrics));
  if (c.list) n.appendChild(make_list(c.list, c.limit || 6));
  if (c.torrents) n.appendChild(make_torrents(c.torrents));

  if (c.metrics) {
    const err = el("div", "error");
    err.hidden = true;
    n.appendChild(err);
    // "live.X.y" is a sub-source of the live agent (X fails on its own, with its own "unavailable") — the rest
    // ("synology.y", "pbs.y" ...) are single-level sources, named by everything before the dot.
    const path0 = c.metrics[0][0];
    const source = path0.startsWith("live.") ? path0.split(".").slice(0, 2).join(".") : path0.split(".")[0];
    updaters.push(() => {
      // If the host is visibly down (or off on schedule), the source's error adds nothing:
      // it is the consequence, not a second problem. PBS, for example, is normally off.
      const st = card_status(c);
      if (st === false || st === "scheduled" || st === "on-demand") {
        err.hidden = st === false;
        if (st !== false) {
          cls(err, "scheduled", true);
          put(err, st === "scheduled" ? "off on schedule" : "off · started on demand");
        }
        return;
      }
      // A source left out of the settings is not a fault: grey, like "off on schedule".
      if (get(source.split(".")[0] + ".not_configured")) {
        err.hidden = false;
        cls(err, "scheduled", true);
        put(err, "not configured");
        return;
      }
      cls(err, "scheduled", false);
      const why = get(source + ".unavailable");
      err.hidden = !why;
      if (why) put(err, String(why));
    });
  }
  if (has_details(c)) bind_touch(n, c);
  return n;
}

function make_guest_card(c) {
  const n = el("div", "card guest");
  n.dataset.id = c.id;
  const head = el("div", "head");
  add_dot(head, c);
  head.appendChild(el("span", "name", c.name));
  n.appendChild(head);
  const val = el("div", "val");
  n.appendChild(val);
  const disk = el("div", "disk");
  n.appendChild(disk);
  const base = `proxmox.${c.check[1]}.guests.${c.check[2]}`;
  updaters.push(() => {
    const g = get(base);
    if (!g || !g.running) { put(val, "—"); put(disk, ""); cls(val, "hot", false); return; }
    put(val, `CPU ${Math.round(g.cpu || 0)}%\nRAM ${Math.round(g.mem || 0)}%`);
    put(disk, g.disk_used != null ? "Disk " + used_of(g.disk_used, g.disk_total, false) : "");
    // Do NOT colour by percentage: Proxmox computes it as `total - MemFree`, so it counts the page
    // cache too, and any Linux machine that ran for a day reaches ~95% without any problem.
    // The only signal that means something is PSI: how long processes actually stalled on memory.
    cls(val, "hot", bad_pressure(g));
  });
  bind_touch(n, c);
  return n;
}

function make_metrics(specs) {
  const host = el("div", "metrics");
  for (const [path, label, fmt] of specs) {
    const b = el("div", "metric");
    b.appendChild(el("span", "lbl", label));
    const vl = el("span", "vl", "—");
    b.appendChild(vl);
    host.appendChild(b);
    updaters.push(() => {
      const v = get(path);
      put(vl, format(v, fmt));
      const sev = severity(path, fmt, v);
      cls(b, "warn", sev === "warn");
      cls(b, "crit", sev === "crit");
    });
  }
  return host;
}

function make_list(path, limit) {
  const host = el("div", "list");
  // Rows are reused: `limit` rows are created once and only rewritten/hidden. Without this, every
  // update would throw away and rebuild the DOM, which is exactly what we set out to avoid.
  const rows = [];
  for (let i = 0; i < limit; i++) {
    const r = el("div", "row");
    const name = el("span", "n"), val = el("span", "v");
    r.appendChild(name); r.appendChild(val);
    r.hidden = true;
    host.appendChild(r);
    rows.push({ r, name, val });
  }
  updaters.push(() => {
    const data = get(path);
    const items = Array.isArray(data) ? data : [];
    rows.forEach((x, i) => {
      const d = items[i];
      if (!d) { if (!x.r.hidden) x.r.hidden = true; return; }
      if (x.r.hidden) x.r.hidden = false;
      put(x.name, String(d.name ?? d.text ?? ""));
      put(x.val, String(d.value ?? d.label ?? ""));
      // `bad: true` comes explicitly from the source; the words remain for lists that do not set it yet
      cls(x.r, "bad", d.bad === true ||
        (d.bad !== false && /failed|\bdown\b|missing|expired/i.test(String(d.value ?? ""))));
    });
  });
  return host;
}

function make_torrents(path) {
  const host = el("div", "list");
  const rows = [];
  for (let i = 0; i < 4; i++) {
    const t = el("div", "torrent");
    const title = el("div", "t");
    const bar = el("div", "b"), fill = el("i");
    bar.appendChild(fill);
    const foot = el("div", "d");
    const remaining = el("span"), eta = el("span");
    foot.appendChild(remaining); foot.appendChild(eta);
    t.appendChild(title); t.appendChild(bar); t.appendChild(foot);
    t.hidden = true;
    host.appendChild(t);
    rows.push({ t, title, fill, remaining, eta });
  }
  updaters.push(() => {
    const items = get(path) || [];
    rows.forEach((x, i) => {
      const d = items[i];
      if (!d) { if (!x.t.hidden) x.t.hidden = true; return; }
      if (x.t.hidden) x.t.hidden = false;
      put(x.title, d.name || "");
      const width = Math.max(0, Math.min(100, d.progress || 0)) + "%";
      if (x.fill.style.width !== width) x.fill.style.width = width;
      put(x.remaining, bytes(d.remaining) + " left");
      put(x.eta, d.eta && d.eta < 8640000 ? duration(d.eta) : "—");
    });
  });
  return host;
}

/** A games page: the grid comes live from the server list at `path` (Pterodactyl), so it has no
    fixed cards in the topology. */
function mount_games(host, path) {
  const tiles = [];
  for (let i = 0; i < 15; i++) {
    const d = el("div", "tile");
    const g = el("div", "g"), s = el("div", "s");
    d.appendChild(g); d.appendChild(s);
    d.hidden = true;
    host.appendChild(d);
    // A game's card changes on every update, so its state and actions are read on demand,
    // from `dataset`, not frozen at build time.
    const card = {
      id: "game" + i, name: "server", panel: "game", game: null,
      get check() { return ["known", d.dataset.running === "1"]; },
      get actions() {
        const id = d.dataset.ident;
        return id ? ["game-start-" + id, "game-stop-" + id, "game-restart-" + id] : [];
      },
    };
    bind_touch(d, card);
    tiles.push({ d, g, s, card });
  }
  updaters.push(() => {
    const servers = get(path) || [];
    tiles.forEach((x, i) => {
      const sv = servers[i];
      if (!sv) { if (!x.d.hidden) x.d.hidden = true; return; }
      if (x.d.hidden) x.d.hidden = false;
      put(x.g, sv.game || sv.name || "?");
      const running = sv.state === "running";
      // The numbers are in `query` (null when the game has no public query or did not answer), not on the server.
      const q = sv.query || null;
      const players = q && q.players != null ? q.players + (q.max ? "/" + q.max : "") + " players" : "running";
      put(x.s, running ? players : "stopped");
      cls(x.d, "on", running);
      x.d.dataset.ident = sv.id || "";
      x.d.dataset.running = running ? "1" : "0";
      x.card.name = sv.game || sv.name || "server";
      x.card.game = sv;
    });
  });
}

// ---------------------------------------------------------------------------------------------
// The traffic strip
// ---------------------------------------------------------------------------------------------

let stripNodes = null;

function build_strip() {
  const b = $("#strip");
  b.textContent = "";
  stripNodes = {};
  for (const [code, info] of Object.entries(CFG.strip)) {
    const n = el("div", "node");
    const nm = el("div", "nm", code), rt = el("div", "rt", "—");
    n.appendChild(nm); n.appendChild(rt);
    b.appendChild(n);
    stripNodes[code] = { nm, rt, info };
  }
}

function render_strip() {
  if (!stripNodes) return;
  const perIp = traffic_map();
  for (const [code, x] of Object.entries(stripNodes)) {
    // The state is not measured here: it is taken from the node's card, whatever its source.
    const alive = card_status(CARD_BY_ID[x.info.card]);
    cls(x.nm, "up", alive === true);
    cls(x.nm, "down", alive === false);
    cls(x.nm, "scheduled", alive === "scheduled" || alive === "on-demand");
    let dn = null, up = null;
    if (x.info.wan) { dn = get("opnsense.wan_dn"); up = get("opnsense.wan_up"); }
    else if (x.info.ip && perIp[x.info.ip]) { dn = perIp[x.info.ip].dn; up = perIp[x.info.ip].up; }
    put(x.rt, dn == null && up == null ? "—" : "↓" + rate(dn) + " ↑" + rate(up));
    // Green only when something actually flows: a permanent colour would say nothing.
    cls(x.rt, "flowing", (dn || 0) + (up || 0) > 64 * 1024);
  }
}

/** iftop on the router's LAN: IP -> {dn, up}. What only crosses the switch never gets here. */
function traffic_map() {
  const top = get("opnsense.top_lan");
  const out = {};
  const rows = (top && (top.records || top.rows || (top.lan && top.lan.records))) || [];
  for (const r of rows) {
    const ip = r.address || r.ip;
    if (!ip) continue;
    out[ip] = { dn: Number(r.rate_bits_in || r.in || 0) / 8, up: Number(r.rate_bits_out || r.out || 0) / 8 };
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// The alert banner
// ---------------------------------------------------------------------------------------------

const DISMISSED_KEY = "dismissed-alerts";

function dismissed() {
  try {
    // "alerte-ascunse": the key's name before 8 Oct 2026, read once so nothing dismissed comes back
    return JSON.parse(localStorage.getItem(DISMISSED_KEY) || localStorage.getItem("alerte-ascunse") || "{}");
  } catch (e) { return {}; }
}

function render_alerts() {
  const bar = $("#alerts");
  if (bar.dataset.id === "message") return;   // an action message takes priority for a few seconds
  const dis = dismissed();
  const all = (CFG.alerts && get(CFG.alerts.path) || []).filter((a) => !dis[a.id]);
  if (!all.length) {
    if (!bar.hidden) { bar.hidden = true; bar.textContent = ""; bar.dataset.id = ""; }
    document.body.classList.remove("with-alert");
    return;
  }
  const a = all[0];
  if (bar.dataset.id !== a.id) {
    bar.textContent = "";
    bar.appendChild(el("span", "txt", a.text + (all.length > 1 ? `  (+${all.length - 1})` : "")));
    const b = el("button", null, "OK");
    b.type = "button";
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      const x = dismissed(); x[a.id] = Date.now();
      try { localStorage.setItem(DISMISSED_KEY, JSON.stringify(x)); } catch (err) { /* private mode */ }
      bar.dataset.id = ""; render_alerts();
    });
    bar.appendChild(b);
    bar.dataset.id = a.id;
  }
  bar.hidden = false;
  document.body.classList.add("with-alert");
}

function mark_menu_alerts() {
  const dis = dismissed();
  const any = (CFG.alerts && get(CFG.alerts.path) || []).some((a) => !dis[a.id]);
  for (const b of $("#menu").children) {
    cls(b, "has-alert", any && CFG.pages[Number(b.dataset.page)].id === CFG.alerts.page);
  }
}

// ---------------------------------------------------------------------------------------------
// Clock and weather
// ---------------------------------------------------------------------------------------------

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function render_clock() {
  const d = new Date();
  put($("#clock .time"), String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"));
  put($("#clock .date"), DAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()]);
}

// WMO codes, grouped: a 360 px screen does not need all 28 variants.
function icon(code) {
  if (code == null) return "";
  if (code === 0) return "☀";
  if (code <= 2) return "⛅";
  if (code === 3) return "☁";
  if (code <= 48) return "🌫";
  if (code <= 67) return "🌧";
  if (code <= 77) return "❄";
  if (code <= 82) return "🌦";
  if (code <= 86) return "🌨";
  return "⛈";
}

function render_weather() {
  const w = get("weather");
  const host = $("#weather");
  if (!w || w.temp == null) { cls(host, "empty", true); return; }
  cls(host, "empty", false);
  put($(".ic", host), icon(w.code));
  put($(".t", host), Math.round(w.temp) + "°");
}

// ---------------------------------------------------------------------------------------------
// Touch: rotation, swipe, opening the panel
// ---------------------------------------------------------------------------------------------

function touched() { pausedUntil = Date.now() + PAUSE; }

/** A short tap on any card opens its panel. Actions are in the panel, not here: the long press
    was invisible — there was no way to know it existed. */
function bind_touch(node, c) {
  let started = 0, moved = false;
  node.addEventListener("pointerdown", () => { touched(); moved = false; started = Date.now(); cls(node, "pressed", true); });
  node.addEventListener("pointerup", () => {
    cls(node, "pressed", false);
    if (!moved && Date.now() - started < 900) open_panel(c);
  });
  node.addEventListener("pointercancel", () => { moved = true; cls(node, "pressed", false); });
  node.addEventListener("pointermove", (e) => {
    if (Math.abs(e.movementX) + Math.abs(e.movementY) > 6) { moved = true; cls(node, "pressed", false); }
  });
}

/** The button label: from [action.<name>] in the config, or else from the name itself. */
function action_label(name) {
  const own = (CFG.actions || {})[name];
  if (own && own.label) return own.label;
  if (/^wol-/.test(name)) return "START";
  if (/^shutdown-/.test(name)) return "SHUT DOWN";
  let m = /^vm-[A-Za-z0-9]+-\d+-(start|shutdown|reboot)$/.exec(name);
  if (m) return { start: "START", shutdown: "SHUT DOWN", reboot: "REBOOT" }[m[1]];
  m = /^game-(start|stop|restart)-/.exec(name);
  if (m) return { start: "START", stop: "STOP", restart: "RESTART" }[m[1]];
  return name;
}

/** Hides the actions that make no sense right now: we do not stop what is already stopped. */
function useful_actions(c) {
  const all = CFG.action_url ? c.actions || [] : [];   // no action agent: no buttons
  const alive = card_status(c);
  const starts = (a) => /-start$/.test(a) || /^wol-/.test(a) || /^game-start-/.test(a);
  const stops = (a) => /-(shutdown|reboot)$/.test(a) || /^shutdown-/.test(a) || /^game-(stop|restart)-/.test(a);
  if (alive === true) return all.filter((a) => !starts(a));
  return all.filter((a) => !stops(a));   // what is not a stop (a report, an update) works either way
}

function ask_confirmation(c, action) {
  const box = $("#confirm");
  const buttons = $("#confirm .buttons");
  // A full text from the config, where the generic template (label + card name) is not enough.
  const own = (CFG.actions || {})[action];
  put($("#confirm-text"), (own && own.confirm) || `${action_label(action)} ${c.name}?`);
  buttons.textContent = "";
  const no = el("button", null, "NO");
  no.type = "button";
  no.addEventListener("click", () => { box.hidden = true; });
  const yes = el("button", "danger", "YES");
  yes.type = "button";
  yes.addEventListener("click", () => { box.hidden = true; send_action(action); });
  buttons.appendChild(no); buttons.appendChild(yes);
  box.hidden = false;
  touched();
}

function send_action(name) {
  fetch(CFG.action_url + encodeURIComponent(name), { method: "POST", mode: "cors" })
    .then((r) => r.json().catch(() => ({ ok: r.ok })))
    .then((r) => show_message(r.ok ? "✓ " + (r.action || name) : "✕ " + (r.error || "failed")))
    .catch(() => show_message("✕ could not send the action"));
}

let messageTimer = null;
function show_message(text) {
  const bar = $("#alerts");
  bar.textContent = "";
  bar.appendChild(el("span", "txt", text));
  bar.dataset.id = "message";
  bar.hidden = false;
  document.body.classList.add("with-alert");
  clearTimeout(messageTimer);
  messageTimer = setTimeout(() => { bar.dataset.id = ""; render_alerts(); }, 6000);
}

// ---------------------------------------------------------------------------------------------
// The detail panel
// ---------------------------------------------------------------------------------------------

let panelCard = null;

function open_panel(c) {
  panelCard = c;
  // fill_panel has already written everything it has to show: if nothing real came out, the card
  // has no panel to open — do not show an empty one (or one with just "status: running") on a tap.
  if (!fill_panel()) {
    panelCard = null;
    return;
  }
  put($("#panel-title"), c.name || "");
  $("#panel").hidden = false;
  touched();
}

function close_panel() { panelCard = null; $("#panel").hidden = true; }

function section(body, title, rows) {
  if (!rows.length) return;
  body.appendChild(el("h3", null, title));
  const l = el("div", "list");
  for (const [n, v, bad] of rows) {
    const r = el("div", "row" + (bad ? " bad" : ""));
    r.appendChild(el("span", "n", String(n)));
    r.appendChild(el("span", "v", String(v)));
    l.appendChild(r);
  }
  body.appendChild(l);
}

/** Fills the panel and returns whether anything came out beyond a plain "status: running" — a row
    the coloured dot on the card already shows, so on its own it does not justify opening the
    panel (see open_panel). */
function fill_panel() {
  const c = panelCard;
  if (!c) return false;
  const body = $("#panel-body");
  body.textContent = "";

  if (c.subtitle) body.appendChild(el("p", "panel-spec", c.subtitle));

  const alive = card_status(c);
  if (alive !== null) {
    const label = alive === true ? "running" : alive === false ? "DOWN"
      : alive === "scheduled" ? "off on schedule" : "off · started on demand";
    section(body, "status", [["status", label, alive === false]]);
  }
  const afterStatus = body.children.length;

  // ---- Proxmox guest: its numbers, plus the Docker containers on it ----
  if (c.check && c.check[0] === "proxmox") {
    const [, node, vmid] = c.check;
    const g = get(`proxmox.${node}.guests.${vmid}`) || {};
    section(body, "resources", [
      ["CPU", format(g.cpu, "percent")],
      ["RAM", `${format(g.mem, "percent")} · ${bytes(g.mem_bytes)} / ${bytes(g.mem_max)}`],
      ["memory pressure", g.pressure == null ? "—"
        : (bad_pressure(g) ? `${nf1.format(g.pressure)}% — REALLY short`
           : `${nf1.format(g.pressure || 0)}% — the % above includes cache`),
        bad_pressure(g)],
      ["disk", g.disk_used != null ? used_of(g.disk_used, g.disk_total, true) : bytes(g.disk)],
      ["uptime", duration(g.uptime)],
      ["type", g.type === "lxc" ? "LXC" : "VM"],
      ["node", `${node} / ${vmid}`],
    ]);
    const cont = get("live.containers." + vmid);
    if (Array.isArray(cont) && cont.length) {
      const running = cont.filter((x) => x.state === "running").length;
      section(body, `docker containers (${running}/${cont.length})`,
        cont.map((x) => [x.name, x.state === "running" ? (x.status || "running") : "STOPPED", x.state !== "running"]));
    } else if (cont && cont.unavailable) {
      section(body, "docker containers", [["could not read", String(cont.unavailable)]]);
    }
  }

  // ---- Proxmox host ----
  if (c.panel && c.panel.startsWith("host:")) {
    const node = c.panel.slice("host:".length);   // the node name, exactly as Proxmox has it
    const p = get("proxmox." + node) || {};
    section(body, "host", [["CPU", format(p.cpu, "percent")], ["RAM", format(p.mem, "percent")],
      ["RAM used", bytes(p.mem_used) + " / " + bytes(p.mem_total)], ["uptime", duration(p.uptime)]]);
    // GPU utilisation no longer fits on the card (it would add a row of metrics and the group would
    // be cut). `sensors` points at a sensors object (as agents/labhud-agent.py serves it): gpu<N>_util, gpu<N>_mem, gpu<N>_temp.
    const t = (c.sensors && get(c.sensors)) || {};
    const gpus = [];
    for (let i = 0; t[`gpu${i}_util`] != null; i++) {
      gpus.push([`GPU${i}`, `${format(t[`gpu${i}_util`], "percent")} · ${format(t[`gpu${i}_mem`], "percent")} mem · ${format(t[`gpu${i}_temp`], "celsius")}`]);
    }
    if (gpus.length) section(body, "GPUs", gpus);
    if ((p.storage || []).length) {
      section(body, "storage", p.storage.map((x) => [x.name, used_of(x.used, x.total, true)]));
    }
    section(body, "guests", Object.entries(p.guests || {}).map(([vmid, g]) =>
      [`${vmid} ${g.name || ""}`, g.running
        ? `CPU ${format(g.cpu, "percent")} · RAM ${format(g.mem, "percent")}`
          + (g.disk_used != null ? ` · Disk ${used_of(g.disk_used, g.disk_total, false)}` : "")
        : "stopped", !g.running]));
  }

  // ---- game server ----
  if (c.panel === "game" && c.game) {
    const q = c.game.query || {};
    section(body, "server", [["game", c.game.game || "—"], ["address", c.game.address || "—"],
      ["players", q.players != null ? `${q.players}${q.max ? " / " + q.max : ""}` : "—"],
      // Minecraft has no map in its query protocol, but gives the version; the rest give the map.
      [q.map || !q.version ? "map" : "version", q.map || q.version || "—"],
      ["RAM", `${Math.round(c.game.ram_mb || 0)} / ${c.game.ram_max_mb || "?"} MB`],
      ["uptime", duration(Math.round(c.game.uptime_s || 0))]]);
    // Names come from A2S_PLAYER / the Minecraft sample / players.json — Valheim and Enshrouded give none.
    section(body, "who is playing", (q.names || []).map((n) => [n, ""]));
  }

  // ---- content panels ----
  const p = c.panel || "";
  const rows = (path) => (get(path) || []).map((d) => [d.name, d.value]);
  if (p === "dns:details") {
    section(body, "top blocked domains", rows("live.display.dns_blocked"));
    section(body, "top clients", rows("live.display.dns_clients"));
  } else if (p === "security:log") {
    section(body, "latest events", (get("live.log") || []).slice(0, 40).map((j) => [j.text, short_time(j.ts)]));
  } else if (p === "security:suricata") {
    const s = get("live.suricata") || {};
    section(body, "signatures", (s.signatures || []).slice(0, 15).map((x) => [x.name, nf.format(x.n)]));
  } else if (p === "security:sources") {
    section(body, "top sources", rows("live.display.sources"));
    section(body, "countries", rows("live.display.countries"));
  } else if (p === "security:tripwire") {
    section(body, "tripwire · 30 days", rows("live.display.tripwire"));
  } else if (p === "security:logins") {
    // `user` is empty on moonlight rows (no named login, just "unauthorized request"), which is
    // why the application is shown first, not last.
    section(body, "recent", (get("live.logins.recent") || []).slice(0, 25).map((x) =>
      [`${x.app || "?"} · ${x.user || x.ip || "—"}`, short_time(x.ts)]));
  } else if (p === "network:devices") {
    section(body, "active", (get("live.devices.list") || []).slice(0, 40).map((d) => [d.name || d.mac || "?", d.ip || ""]));
  } else if (p === "network:traffic") {
    section(body, "interfaces", Object.entries(get("opnsense.interfaces") || {}).map(([n, r]) => [n, `↓${rate(r.dn)} ↑${rate(r.up)}`]));
  } else if (p === "media:jellyfin") {
    // "who is watching" is empty when nobody watches anything — the library stays, so the panel
    // does not look empty exactly when there is no session to show.
    section(body, "who is watching", (get("jellyfin.sessions") || []).map((s) => [`${s.user}: ${s.title}`, s.client || ""]));
    section(body, "library", [
      ["box sets" /* BoxSet: movie/series collections */, nf.format(get("jellyfin.BoxSetCount") || 0)],
      ["trailers", nf.format(get("jellyfin.TrailerCount") || 0)],
      ["items in total", nf.format(get("jellyfin.ItemCount") || 0)],
    ]);
  } else if (p === "media:navidrome") {
    section(body, "who is listening", (get("navidrome.sessions") || []).map((s) => [`${s.user}: ${s.title}`, s.state || ""]));
  } else if (p === "media:sonarr") {
    section(body, "missing", (get("sonarr.missing") || []).map((t) => [t, ""]));
  } else if (p === "media:radarr") {
    section(body, "missing", (get("radarr.missing") || []).map((t) => [t, ""]));
  } else if (p === "media:prowlarr") {
    section(body, "indexers", (get("prowlarr.indexers") || []).map((i) =>
      [i.name, i.failed ? `${i.grabs} grabs · ${i.failed} failed` : `${i.grabs} grabs`, !!i.failed]));
  } else if (p === "media:torrents") {
    section(body, "downloading", (get("qbt.torrents") || []).map((t) => [t.name, `${Math.round(t.progress)}% · ${bytes(t.remaining)} left`]));
  } else if (p.startsWith("list:")) {
    // A list meant for the panel only, longer than the card's: flat rows, or sections of rows.
    const data = get(p.slice("list:".length)) || [];
    if (data.some((s) => s && Array.isArray(s.rows))) {
      for (const s of data) section(body, String(s.title ?? ""), (s.rows || []).map(detail_row));
    } else {
      section(body, "details", data.map(detail_row));
    }
  }

  // ---- fallback: the card has no dedicated panel above, but it still has numbers or a list ----
  // The metrics and lists on the card's face are trimmed (little space, small font); here they fit
  // whole, and the list is no longer limited to `c.limit`.
  if (body.children.length === afterStatus) {
    if (c.metrics) {
      section(body, "numbers", c.metrics.map(([path, label, fmt]) => [label, format(get(path), fmt)]));
    }
    if (c.list) {
      section(body, "details", (get(c.list) || []).map(detail_row));
    }
  }

  render_actions(c);
  return body.children.length > afterStatus || !$("#panel-actions").hidden;
}

/** The action buttons, at the bottom of the panel. One tap on the card gets you here. */
function render_actions(c) {
  const bar = $("#panel-actions");
  bar.textContent = "";
  const useful = useful_actions(c);
  if (!useful.length) { bar.hidden = true; return; }
  bar.hidden = false;
  for (const a of useful) {
    const b = el("button", /shutdown|stop/.test(a) ? "danger" : "", action_label(a));
    b.type = "button";
    b.addEventListener("click", () => ask_confirmation(c, a));
    bar.appendChild(b);
  }
}

/** One `{name, value, bad}` item (or `{text, label}`) as a panel row. */
function detail_row(d) {
  return [String(d.name ?? d.text ?? ""), String(d.value ?? d.label ?? ""), d.bad === true];
}

function short_time(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
}

// ---------------------------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------------------------

function bind_gestures() {
  let x0 = 0, y0 = 0, x1 = 0, y1 = 0;
  const finish = (x, y) => {
    const dx = x - x0, dy = y - y0;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    if (!$("#panel").hidden) { close_panel(); return; }
    go(page + (dx < 0 ? 1 : -1));
  };
  document.addEventListener("pointerdown", (e) => { x0 = x1 = e.clientX; y0 = y1 = e.clientY; touched(); }, { passive: true });
  document.addEventListener("pointermove", (e) => { x1 = e.clientX; y1 = e.clientY; }, { passive: true });
  document.addEventListener("pointerup", (e) => finish(e.clientX, e.clientY), { passive: true });
  // The phone may cancel the gesture (not deliver it as pointerup) when it takes it for its own —
  // the browser's back/forward swipe, for example. Without this, a touch from the edge is lost
  // silently: the page does not change, although the finger really moved 60+ px.
  document.addEventListener("pointercancel", () => finish(x1, y1), { passive: true });

  $("#panel-x").addEventListener("click", close_panel);
  document.addEventListener("visibilitychange", () => {
    document.body.classList.toggle("page-hidden", document.hidden);
  });
}

function from_address() {
  const m = /^#([a-z]+)/.exec(location.hash || "");
  if (!m) return;
  const i = CFG.pages.findIndex((x) => x.id === m[1]);
  if (i >= 0) page = i;
}

fetch("/api/config").then((r) => r.json()).then((cfg) => {
  CFG = cfg;
  document.title = CFG.title || "LABHUD";
  put($("#weather .city"), CFG.city || "");
  for (const items of Object.values(CFG.cards)) for (const c of items) CARD_BY_ID[c.id] = c;
  from_address();
  render_menu();
  build_strip();
  bind_gestures();
  go(page);
  start_stream();

  render_clock();
  tick(render_clock, 10000);
  tick(() => { if (Date.now() >= pausedUntil && $("#panel").hidden && $("#confirm").hidden) go(page + 1); }, PERIOD);
});
