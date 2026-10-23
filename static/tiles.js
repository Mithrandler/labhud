// The wall as equal tiles (labhud's redesign of 24 Oct 2026). Loaded before app.js; uses its globals.
//
// Every tile is the same size, with the one gap (--wall-gap) between them and around them. A tile says
// what it is, one number and one line. The rest is one tap away, in the panel that already exists:
//  - a host (a card with panel = "host:<node>") is one tile; its VMs and containers are inside it
//    ("7 VM · all fine") and listed in its panel;
//  - on the pages that list things (everything but General) a group is one tile: how many of its
//    cards have a problem, red if any does, and the whole list in a panel;
//  - every other card is one tile with its worst or first number.
// A card in trouble comes to the front as a tile of its own, wherever it is.

/** General keeps a tile per card; the other pages fold each group of several cards into one tile. */
function tiles_expanded() {
  const p = CFG.pages[page];
  return p.id === "general";
}

/** The node a host card is: panel = "host:<node>", else null. */
function host_node(c) {
  return c.panel && String(c.panel).startsWith("host:") ? String(c.panel).slice(5) : null;
}

/** [value text, label, severity] of the one number a card is about. */
/** [value text, label, severity] of the one number a card is about. */
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
  return st === true ? ["up", "", ""] : st === false ? ["down", "", "crit"] : ["—", "", ""];
}

/** "crit": red on the screen (down, or a number past its critical mark); "warn": past a warning mark; "". */
function tile_trouble(c) {
  if (card_bad(c) === true) return "crit";
  const [, , sev] = tile_headline(c);
  return sev === "warn" ? "warn" : "";
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
  updaters.push(() => {
    const st = card_status(c);
    const [v, label, sev] = tile_headline(c);
    const down = st === false;
    put(nv, v); put(nl, label);
    cls(n, "down", down);
    cls(n, "crit", !down && (sev === "crit" || card_bad(c) === true));
    cls(n, "warn", !down && sev === "warn");
    cls(n, "frozen", st === false || quiet_off(st));
    put(sub, host_node(c) ? host_line(c) : quiet_off(st) ? off_label(c, st) : (guestOf || c.subtitle || ""));
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
    put(nv, String(crit.length || trouble.length));
    put(nl, crit.length ? (crit.length === 1 ? "problem" : "problems") : trouble.length ? "warning" + (trouble.length === 1 ? "" : "s") : "problems");
    cls(n, "crit", crit.length > 0);
    cls(n, "warn", !crit.length && trouble.length > 0);
    cls(n, "fine", !trouble.length);
    cls(dot, "down", crit.length > 0);
    cls(dot, "up", !trouble.length);
    put(sub, trouble.length ? trouble.slice(0, 2).map(([c]) => c.name).join(", ") + (trouble.length > 2 ? ` +${trouble.length - 2}` : "")
      : "all fine");
  });
  bind_touch(n, pseudo);
  return n;
}

/** The panel of a group tile: its cards, the ones in trouble first; a tap opens the card's own panel. */
function fill_group_panel(c, body) {
  const rows = c.group_cards.map((x) => [x, tile_trouble(x)]).sort((a, b) => (b[1] === "crit") - (a[1] === "crit") || (b[1] === "warn") - (a[1] === "warn"));
  const list = el("div", "list group-list");
  for (const [x, t] of rows) {
    const [v, label] = tile_headline(x);
    const r = el("div", "row" + (t === "crit" ? " bad" : t === "warn" ? " warn" : ""));
    r.appendChild(el("span", "n", x.name));
    r.appendChild(el("span", "v", label ? `${v} ${label}` : v));
    if (has_details(x)) r.addEventListener("click", () => { panelCard = null; open_panel(x); });
    list.appendChild(r);
  }
  body.appendChild(list);
  return true;
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
  host.appendChild(pageGrid);
  tileEntries = [];
  const expanded = tiles_expanded();
  let pos = 0;
  for (const g of CFG.pages[page].groups) {
    const cards = (CFG.cards[g.id] || []).filter((c) => !is_hidden(g.id, c.id));
    if (!cards.length) continue;
    const hosts = cards.filter((c) => host_node(c));
    const nodes = new Set(hosts.map(host_node));
    const inner = (c) => is_guest(c) && nodes.has(c.check[1]);
    if (expanded || cards.length === 1) {
      for (const c of cards) tileEntries.push({ card: c, tile: make_tile(c, inner(c) ? c.check[1] : ""), inner: inner(c), pos: pos++ });
    } else {
      const grp = { tile: make_group_tile(g, cards), card: null, inner: false, pos: pos++ };
      tileEntries.push(grp);
      for (const c of cards) tileEntries.push({ card: c, tile: make_tile(c), inner: true, pos: pos++ });
    }
  }
  for (const e of tileEntries) pageGrid.appendChild(e.tile);
  updaters.push(order_tiles);
  update();
  tiles_layout();
  layoutWatch = new ResizeObserver(() => setTimeout(tiles_layout, 0));
  layoutWatch.observe(host);
}

/** Who is shown, in what order: a card in trouble first (wherever it came from), the rest as configured. */
function order_tiles() {
  let shown = 0;
  for (const e of tileEntries) {
    const bad = e.card && tile_trouble(e.card) === "crit";
    const visible = !e.inner || bad;
    e.tile.hidden = !visible;
    e.tile.style.order = String(bad ? e.pos - 100000 : e.pos);
    if (visible) shown++;
  }
  if (shown !== tileShown) { tileShown = shown; setTimeout(tiles_layout, 0); }
}
let tileShown = -1;

/** The fixed grid: 4 tiles a row in landscape, 3 in portrait, the rows sharing the height (never below
    --wall-tile-min: if they do not fit then, the page is cut and body.dataset.measure says so). */
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
  const n = [...pageGrid.children].filter((t) => !t.hidden).length;
  const rows = Math.max(1, Math.ceil(n / cols));
  const h = Math.max(num("--wall-tile-min", 92), Math.min(num("--wall-tile-max", 132), (H - gap * (rows - 1)) / rows));
  pageGrid.style.setProperty("--tcols", String(cols));
  pageGrid.style.setProperty("--th", h.toFixed(1) + "px");
  cls(host, "scroll", desk_view());
}
