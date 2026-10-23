// The wall as equal tiles (labhud's redesign of 24 Oct 2026). Loaded before app.js; uses its globals.
//
// Every tile is the same size, with the one gap (--wall-gap) between them and around them. A tile says
// what it is, one number and one line. The rest is one tap away, in the panel that already exists:
//  - a host (a card with panel = "host:<node>") is one tile; its VMs and containers are inside it
//    ("7 VM · all fine") and listed in its panel;
//  - on the pages that list things (everything but General) a group is one tile: how many of its
//    cards have a problem, red if any does, and the whole list in a panel;
//  - every other card is one tile with its worst or first number.
// A card in trouble comes to the front, wherever it is; a group in trouble, as the one tile of its group.

/** General keeps a tile per card; the other pages fold each group of several cards into one tile. */
function tiles_expanded() {
  const p = CFG.pages[page];
  return p.id === "general";
}

/** The node a host card is: panel = "host:<node>", else null. */
function host_node(c) {
  return c.panel && String(c.panel).startsWith("host:") ? String(c.panel).slice(5) : null;
}

/** [value text, label, severity, is text] of the one number a card is about; "up"/"down" are words, set at the size of text. */
function tile_headline(c) {
  const node = host_node(c);
  if (node) {
    const cpu = get(`proxmox.${node}.cpu`), mem = get(`proxmox.${node}.mem`);
    const useCpu = typeof cpu === "number" && !(typeof mem === "number" && mem > cpu);
    const path = `proxmox.${node}.${useCpu ? "cpu" : "mem"}`, v = useCpu ? cpu : mem;
    if (typeof v === "number") return [format(v, "percent"), useCpu ? "CPU" : "RAM", severity(path, "percent", v)];
  }
  if (is_guest(c)) {
    const g = get(`proxmox.${c.check[1]}.guests.${c.check[2]}`);
    if (g && g.running) { const h = guest_headline(g); return [`${Math.round(h.pct)}%`, h.label, h.sev]; }
    return ["—", "", ""];
  }
  let best = null;
  for (const [path, label, fmt] of c.metrics || []) {
    const v = get(path);
    if (v == null || v === "") continue;
    const sev = severity(path, fmt, v);
    if (!best || SEV_RANK[sev] > SEV_RANK[best[2]]) best = [format(v, fmt), label, sev];
  }
  if (best) return best;
  const st = card_status(c);
  return st === true ? ["up", "", "", true] : st === false ? ["down", "", "crit", true] : ["—", "", ""];
}

/** "crit": red on the screen (down, or a number past its critical mark); "warn": past a warning mark; "". */
function tile_trouble(c) {
  if (card_bad(c) === true) return "crit";
  const [, , sev] = tile_headline(c);
  return sev === "warn" ? "warn" : "";
}

/** What is wrong inside a card that lists things (FAILURES, UPDATES PENDING): the rows marked bad, [name, value]. */
function card_items(c) {
  if (!c.list) return [];
  const rows = get(c.list);
  return Array.isArray(rows) ? rows.map(detail_row).filter((r) => r[2]).map((r) => [r[0], r[1]]) : [];
}

/** "a, b +2": the first names of a list, how many more. */
function names_line(names, n) {
  return names.slice(0, n).join(", ") + (names.length > n ? ` +${names.length - n}` : "");
}

/** The line under a host's number: how many guests it runs and whether they are fine. */
function host_line(c) {
  const node = host_node(c);
  const guests = Object.values(((get("proxmox." + node) || {}).guests) || {});
  if (!guests.length) return c.subtitle || "";
  const lxc = guests.filter((g) => g.type === "lxc").length, vm = guests.length - lxc;
  const what = [vm ? `${vm} VM` : "", lxc ? `${lxc} LXC` : ""].filter(Boolean).join(" · ");
  const down = guests.filter((g) => !g.running).length;
  return `${what} · ${down ? down + " stopped" : "all fine"}`;
}

/** What a host shows: CPU, RAM, GPU and video memory, each a number and a bar. With several GPUs the busiest. */
function host_meters(c) {
  const node = host_node(c);
  const t = (c.sensors && get(c.sensors)) || {};
  let gpu = null, vram = null;
  for (let i = 0; t[`gpu${i}_util`] != null || t[`gpu${i}_mem`] != null; i++) {
    if (typeof t[`gpu${i}_util`] === "number") gpu = Math.max(gpu ?? 0, t[`gpu${i}_util`]);
    if (typeof t[`gpu${i}_mem`] === "number") vram = Math.max(vram ?? 0, t[`gpu${i}_mem`]);
  }
  const pick = (label, path, v) => [label, typeof v === "number" ? v : null, path ? severity(path, "percent", v) : ""];
  return [pick("CPU", `proxmox.${node}.cpu`, get(`proxmox.${node}.cpu`)), pick("RAM", `proxmox.${node}.mem`, get(`proxmox.${node}.mem`)),
    pick("GPU", c.sensors ? c.sensors + ".gpu0_util" : "", gpu), pick("VRAM", c.sensors ? c.sensors + ".gpu0_mem" : "", vram)];
}

/** The four meters of a host tile; fill() redraws them. */
function make_meters(c) {
  const box = el("div", "meters");
  const parts = ["CPU", "RAM", "GPU", "VRAM"].map((label) => {
    const m = el("div", "meter");
    const top = el("div", "mt");
    const vl = el("span", "mv", "—");
    top.append(el("span", "ml", label), vl);
    const bar = el("div", "bar");
    const fill = el("i");
    bar.appendChild(fill);
    m.append(top, bar);
    box.appendChild(m);
    return { m, vl, fill };
  });
  return {
    box,
    fill() {
      host_meters(c).forEach(([, v, sev], i) => {
        const { m, vl, fill } = parts[i];
        put(vl, v == null ? "—" : `${Math.round(v)}%`);
        fill.style.width = v == null ? "0%" : `${Math.max(0, Math.min(100, v))}%`;
        cls(m, "warn", sev === "warn"); cls(m, "crit", sev === "crit");
      });
    },
  };
}

/** The tile of one card. */
function make_tile(c, guestOf) {
  const n = el("div", "card tile");
  n.dataset.id = c.id;
  const head = el("div", "head");
  head.appendChild(card_icon(c.icon, is_guest(c) ? "vm" : "app"));
  const title = el("div", "title");
  title.appendChild(el("span", "name", c.name));
  head.appendChild(title);
  if (c.check) add_dot(head, c);
  const num = el("div", "tile-num");
  const nv = el("span", "tn-v", "—"), nl = el("span", "tn-l", "");
  num.append(nv, nl);
  const sub = el("div", "tile-sub");
  n.append(head, num, sub);
  const meters = host_node(c) ? make_meters(c) : null;
  if (meters) { n.classList.add("wide"); n.insertBefore(meters.box, sub); num.hidden = true; }
  updaters.push(() => {
    if (meters) meters.fill();
    const st = card_status(c);
    const [v, label, sev] = tile_headline(c);
    const down = st === false;
    const [, , , word] = tile_headline(c);
    const items = card_items(c);
    put(nv, v); put(nl, c.list ? "" : label);   // a listing card's name says what the number counts
    cls(nv, "txt", word === true);
    cls(n, "down", down);
    cls(n, "crit", !down && (sev === "crit" || card_bad(c) === true));
    cls(n, "warn", !down && sev === "warn");
    cls(n, "frozen", st === false || quiet_off(st));
    put(sub, host_node(c) ? host_line(c) : items.length ? names_line(items.map((i) => i[0]), 2) : quiet_off(st) ? off_label(c, st) : (guestOf || c.subtitle || ""));
  });
  bind_touch(n, c);
  return n;
}

/** The tile of a group of cards on a listing page: how many have a problem, and who. */
function make_group_tile(g, cards) {
  const pseudo = { id: "group:" + g.id, name: g.title, group_cards: cards };
  const n = el("div", "card tile group");
  n.dataset.id = pseudo.id;
  const head = el("div", "head");
  head.appendChild(card_icon(cards[0].icon, "app"));
  const title = el("div", "title");
  title.appendChild(el("span", "name", g.title));
  head.appendChild(title);
  const dot = el("span", "dot");
  head.appendChild(dot);
  const num = el("div", "tile-num");
  const nv = el("span", "tn-v", "0"), nl = el("span", "tn-l", "");
  num.append(nv, nl);
  const sub = el("div", "tile-sub");
  n.append(head, num, sub);
  updaters.push(() => {
    const trouble = cards.map((c) => [c, tile_trouble(c)]).filter(([, t]) => t);
    const crit = trouble.filter(([, t]) => t === "crit");
    const broken = (list) => list.reduce((n, [c]) => n + (card_items(c).length || 1), 0);
    put(nv, String(broken(crit.length ? crit : trouble)));
    put(nl, "");
    cls(n, "crit", crit.length > 0);
    cls(n, "warn", !crit.length && trouble.length > 0);
    cls(n, "fine", !trouble.length);
    cls(dot, "down", crit.length > 0);
    cls(dot, "up", !trouble.length);
    // what is wrong, not who has it: the first broken things of the cards in trouble
    const what = trouble.flatMap(([c]) => { const it = card_items(c); return it.length ? it.map((i) => i[0]) : [c.name]; });
    put(sub, trouble.length ? names_line(what, 2) : "all fine");
  });
  bind_touch(n, pseudo);
  return n;
}

/** The panel of a group tile, one level: for each card in trouble what is wrong in it, then the cards
    that are fine; a tap on a card (not on a broken thing) opens that card's own panel. */
function fill_group_panel(c, body) {
  const rows = c.group_cards.map((x) => [x, tile_trouble(x)]);
  const bad = rows.filter(([, t]) => t), fine = rows.filter(([, t]) => !t);
  for (const [x, t] of bad) {
    const items = card_items(x);
    body.appendChild(el("h3", null, x.name));
    const list = el("div", "list group-list");
    if (items.length) {
      for (const [n, v] of items) {
        const r = el("div", "row bad");
        r.append(el("span", "n", n), el("span", "v", v));
        list.appendChild(r);
      }
    } else {
      const [v, label] = tile_headline(x);
      const r = el("div", "row" + (t === "crit" ? " bad" : " warn"));
      r.append(el("span", "n", x.name), el("span", "v", label ? `${v} ${label}` : v));
      list.appendChild(r);
    }
    if (has_details(x)) list.addEventListener("click", () => { panelCard = null; open_panel(x); });
    body.appendChild(list);
  }
  if (fine.length) {
    body.appendChild(el("h3", null, bad.length ? "fine" : "all fine"));
    const list = el("div", "list group-list");
    for (const [x] of fine) {
      const [v, label] = tile_headline(x);
      const r = el("div", "row");
      r.append(el("span", "n", x.name), el("span", "v", label ? `${v} ${label}` : v));
      if (has_details(x)) r.addEventListener("click", () => { panelCard = null; open_panel(x); });
      list.appendChild(r);
    }
    body.appendChild(list);
  }
  return true;
}

/** The tiles of a games page: the servers come live from the list at `path` (Pterodactyl), so a pool of
    tiles is made once and a tile is shown as a server appears. */
const GAME_TILES = 24;

/** What a game tile says, from whether the server runs and its query answer ({players, max} or null):
    [number, label under it, is a word, bottom line]. */
function game_tile_text(on, q) {
  const players = !!on && !!q && q.players != null;
  if (players) return [String(q.players), "players", false, q.max ? `of ${q.max} slots` : "running"];
  return [on ? "up" : "—", "", true, on ? "running" : "stopped"];
}
function make_game_tiles(path) {
  const out = [];
  for (let i = 0; i < GAME_TILES; i++) {
    const n = el("div", "card tile game");
    const head = el("div", "head");
    let ic = el("span", "ic");
    const name = el("span", "name");
    const title = el("div", "title");
    title.appendChild(name);
    const dot = el("span", "dot");
    head.append(ic, title, dot);
    const num = el("div", "tile-num");
    const nv = el("span", "tn-v", "—"), nl = el("span", "tn-l", "");
    num.append(nv, nl);
    const sub = el("div", "tile-sub");
    n.append(head, num, sub);
    // the tile keeps one card object (bind_touch holds it); each update refreshes what is in it
    const card = { id: "game" + i, name: "server", panel: "game", game: null, check: ["known", false], actions: [] };
    bind_touch(n, card);
    const e = { tile: n, card: null, inner: false, empty: true, pos: i };
    let icon = "";
    updaters.push(() => {
      const sv = (get(path) || [])[i];
      e.empty = !sv;
      if (!sv) return;
      const gc = game_card(sv, i);
      const q = sv.query || null, on = gc.check[1];
      put(name, gc.title);
      const icn = game_icon(sv);
      if (icn !== icon) { icon = icn; ic.replaceWith(ic = card_icon(icn, "games")); }
      const [num, label, word, line] = game_tile_text(on, q);
      put(nv, num); put(nl, label); cls(nv, "txt", word); put(sub, line);
      cls(n, "frozen", !on);
      cls(dot, "up", on);
      Object.assign(card, { name: gc.name, game: sv, check: gc.check, actions: gc.actions });
    });
    out.push(e);
  }
  return out;
}

let tileEntries = [];   // {tile, card, kind: "own" | "inner", pos}: what lays out, in config order

/** Mounts the current page as tiles. Replaces the sections and the zoom search of layout(). */
function mount_tiles() {
  const host = $("#content");
  host.textContent = "";
  updaters = [];
  pageSections = [];
  if (layoutWatch) layoutWatch.disconnect();
  pageGrid = el("div", "tiles");
  tileDots = el("div", "tdots");
  host.append(pageGrid, tileDots);
  tileSub = 0; tileSubs = 1; tileSig = "";
  bind_swipe(pageGrid);
  tileEntries = [];
  const expanded = tiles_expanded();
  let pos = 0;
  const games = CFG.pages[page].games;
  if (games) tileEntries.push(...make_game_tiles(games));
  for (const g of games ? [] : CFG.pages[page].groups) {
    const cards = (CFG.cards[g.id] || []).filter((c) => !is_hidden(g.id, c.id));
    if (!cards.length) continue;
    const hosts = cards.filter((c) => host_node(c));
    const nodes = new Set(hosts.map(host_node));
    const inner = (c) => is_guest(c) && nodes.has(c.check[1]);
    if (expanded || cards.length === 1) {
      for (const c of cards) tileEntries.push({ card: c, tile: make_tile(c, inner(c) ? c.check[1] : ""), inner: inner(c), pos: pos++ });
    } else {
      tileEntries.push({ tile: make_group_tile(g, cards), card: null, group: cards, inner: false, pos: pos++ });
    }
  }
  for (const e of tileEntries) pageGrid.appendChild(e.tile);
  updaters.push(order_tiles);
  update();
  tiles_layout();
  layoutWatch = new ResizeObserver(() => setTimeout(tiles_layout, 0));
  layoutWatch.observe(host);
}

/** A swipe sideways over the tiles turns their pages (a tap on a tile still opens it: it moved too little). */
function bind_swipe(node) {
  let x0 = 0, y0 = 0;
  node.addEventListener("pointerdown", (e) => { x0 = e.clientX; y0 = e.clientY; });
  node.addEventListener("pointerup", (e) => {
    const dx = e.clientX - x0, dy = e.clientY - y0;
    if (Math.abs(dx) < 50 || Math.abs(dx) < 2 * Math.abs(dy)) return;
    touched();
    tiles_step(dx < 0 ? 1 : -1);
  });
}

/** Who is shown, in what order: a card in trouble first (wherever it came from), the rest as configured. */
function order_tiles() {
  const sig = [];
  for (const e of tileEntries) {
    const bad = e.group ? e.group.some((c) => tile_trouble(c) === "crit") : e.card && tile_trouble(e.card) === "crit";
    const visible = (!e.inner || bad) && !e.empty;
    e.tile.hidden = !visible;
    // PVE and PVE2 first, then the other hosts, then what is in trouble, then the rest as configured
    const node = e.card && host_node(e.card);
    const rank = node ? ({ pve: 0, pve2: 1 }[node.toLowerCase()] ?? 2) : 0;
    e.tile.style.order = String(node ? e.pos - 200000 + rank * 10000 : bad ? e.pos - 100000 : e.pos);
    if (visible) sig.push(e.tile.style.order);
  }
  const now = sig.join(",");
  if (now !== tileSig) { tileSig = now; setTimeout(tiles_layout, 0); }
}

let tileSig = "";       // the visible tiles in their order: when it changes, the pages are cut again
let tileSub = 0, tileSubs = 1;   // the page of tiles shown, how many there are
let tileDots = null;

/** How many rows tiles of these widths (1 or 2 cells, in order) take in a grid `cols` wide. Wide tiles take
    their cells first and singles fill the holes ("dense"). */
function tiles_rows(widths, cols) {
  const free = [];
  for (const w of widths.map((x) => Math.min(cols, x)).sort((a, b) => b - a)) {
    const r = free.findIndex((f) => f >= w);
    if (r < 0) free.push(cols - w); else free[r] -= w;
  }
  return free.length;
}

/** The tiles' widths split, in order, into pages of at most `rows` rows: [[index, ...], ...]. */
function tiles_pages(widths, cols, rows) {
  const pages = [[]];
  widths.forEach((w, i) => {
    const last = pages[pages.length - 1];
    if (last.length && tiles_rows([...last, i].map((j) => widths[j]), cols) > rows) pages.push([i]);
    else last.push(i);
  });
  return pages;
}

/** The fixed grid: 4 tiles a row in landscape, 3 in portrait, the rows sharing the height (never below
    --wall-tile-min). More tiles than the rows that fit: pages of them (a swipe, and the rotation, turn them);
    at a desk the page scrolls instead. */
function tiles_layout() {
  const host = $("#content");
  if (!pageGrid || !host.clientWidth) return;
  const cs = getComputedStyle(host);
  const W = host.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const H = host.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
  const root = getComputedStyle(document.documentElement);
  const num = (name, d) => parseFloat(root.getPropertyValue(name)) || d;
  const cols = num(W > H ? "--wall-cols-landscape" : "--wall-cols-portrait", W > H ? 4 : 3);
  const gap = num("--wall-gap", 8);
  const min = num("--wall-tile-min", 92);
  const shown = [...pageGrid.children].filter((x) => !x.hidden).sort((a, b) => Number(a.style.order || 0) - Number(b.style.order || 0));
  const widths = shown.map((t) => (t.classList.contains("wide") ? 2 : 1));
  const fit = Math.max(1, Math.floor((H + gap) / (min + gap)));
  const desk = desk_view();
  const pages = !desk && tiles_rows(widths, cols) > fit ? tiles_pages(widths, cols, fit) : [widths.map((_, i) => i)];
  tileSubs = pages.length;
  tileSub = Math.min(tileSub, tileSubs - 1);
  const mine = new Set(pages[tileSub]);
  shown.forEach((t, i) => cls(t, "off", !mine.has(i)));
  const rows = Math.max(1, tiles_rows(pages[tileSub].map((i) => widths[i]), cols));
  const h = Math.max(min, Math.min(num("--wall-tile-max", 132), (H - gap * (rows - 1)) / rows));
  pageGrid.style.setProperty("--tcols", String(cols));
  pageGrid.style.setProperty("--th", h.toFixed(1) + "px");
  cls(host, "scroll", desk);
  if (tileDots) {
    while (tileDots.children.length > (tileSubs > 1 ? tileSubs : 0)) tileDots.lastChild.remove();
    while (tileDots.children.length < (tileSubs > 1 ? tileSubs : 0)) tileDots.appendChild(el("i"));
    [...tileDots.children].forEach((d, i) => cls(d, "on", i === tileSub));
  }
}

/** One page of tiles on (+1) or back (-1), round the end. */
function tiles_step(d) {
  if (tileSubs < 2) return;
  tileSub = (tileSub + d + tileSubs) % tileSubs;
  tiles_layout();
}

/** For the rotation: the next page of this page's tiles, if there is one (true), else it is time for the next screen. */
function tiles_turn() {
  if (tileSub >= tileSubs - 1) return false;
  tileSub++;
  turnedAt = Date.now();
  tiles_layout();
  return true;
}
