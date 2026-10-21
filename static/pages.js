// ⚙ > Dashboard > Pages and groups: where every card sits, for every screen (layout.py). Hold a card
// or a group's title and drag it (a finger too: the hold tells a drag from a scroll), or use its
// ⋯ menu. Nothing is written until Save; Organize by type proposes groups for one page.
"use strict";

const L = { saved: null, pages: null, kinds: [], titles: {}, dirty: false, msg: "", kind: "", preview: null, page: "", managed: null };
const HOLD_MS = 350;

const lay_clone = (x) => JSON.parse(JSON.stringify(x));

function layout_settings(s) {
  const host = el("div", "lay");
  s.appendChild(host);
  if (L.saved) return draw_layout(host);
  host.appendChild(el("p", "hint", "Reading the pages…"));
  load_layout(host);
}

function load_layout(host) {
  setup_post("status").then((st) => {
    L.managed = !!st.managed;
    if (!st.ok || !st.managed) return draw_layout(host);
    return setup_post("layout").then((r) => {
      if (!r.ok) { L.msg = r.error || "refused"; L.kind = "warn"; return draw_layout(host); }
      Object.assign(L, { saved: r.pages, pages: lay_clone(r.pages), kinds: r.kinds, titles: r.titles, dirty: false });
      if (!L.page && r.pages.length) L.page = r.pages[0].id;
      draw_layout(host);
    });
  });
}

function lay_say(m, kind) { L.msg = m; L.kind = kind || ""; }
function lay_changed() { L.dirty = true; lay_say("Not saved yet.", ""); redraw_layout(); }
function redraw_layout() { const h = $("#settings .lay"); if (h) draw_layout(h); }

function draw_layout(host) {
  host.textContent = "";
  if (L.managed === false) {
    host.appendChild(el("p", "hint", "To move cards and groups from here, LabHUD keeps its own copy of the config: "
      + "⚙ > System > Take over (once). Until then this screen can still hide pages and cards, below."));
    const b = el("button", "choice", "Open System");
    b.type = "button";
    b.addEventListener("click", () => settings_go("system"));
    host.appendChild(b);
    return;
  }
  if (!L.pages) { host.appendChild(el("p", "hint " + L.kind, L.msg || "…")); return; }

  // the bar: organize, save, discard
  const bar = el("div", "lay-bar");
  const sel = el("select");
  for (const p of L.pages) { const o = el("option", null, p.title); o.value = p.id; sel.appendChild(o); }
  sel.value = L.page;
  sel.addEventListener("change", () => { L.page = sel.value; });
  const org = el("button", "choice", "Organize by type");
  org.type = "button";
  org.addEventListener("click", () => organize_preview(sel.value));
  const save = el("button", "choice on", "Save");
  save.type = "button";
  save.disabled = !L.dirty;
  save.addEventListener("click", save_layout);
  const discard = el("button", "choice", "Discard");
  discard.type = "button";
  discard.disabled = !L.dirty;
  discard.addEventListener("click", () => { L.pages = lay_clone(L.saved); L.dirty = false; L.preview = null; lay_say("Back to the saved pages."); redraw_layout(); });
  bar.append(org, sel, el("span", "grow"), discard, save);
  host.appendChild(bar);
  if (L.msg) host.appendChild(el("p", "hint " + L.kind, L.msg));
  if (L.preview) host.appendChild(draw_preview());
  host.appendChild(el("p", "hint", "Hold a card or a group's title, then drag it. ⋯ moves, renames and sets the kind. "
    + "⋯ also sets a card's size and icon, and a page's icon. Nothing changes on the screens until Save; they take it when ⚙ closes."));

  const cols = el("div", "lay-cols");
  L.pages.forEach((p, pi) => cols.appendChild(draw_page(p, pi)));
  const add = el("button", "choice lay-add-page", "+ Page");
  add.type = "button";
  add.addEventListener("click", () => {
    const id = new_page_id("page");
    L.pages.push({ id, title: "NEW PAGE", games: false, groups: [], fresh: true });
    lay_changed();
    lay_rename("page", L.pages.length - 1);
  });
  cols.appendChild(add);
  host.appendChild(cols);
}

function draw_page(p, pi) {
  const col = el("div", "lay-page");
  col.dataset.page = String(pi);
  const head = el("div", "lay-page-head");
  if (p.icon) head.appendChild(lay_icon(p.icon));
  head.appendChild(el("span", "t", p.title));
  head.appendChild(lay_more(() => page_menu(pi)));
  col.appendChild(head);
  if (p.games) col.appendChild(el("p", "hint", "Game servers, as the panel lists them."));
  p.groups.forEach((g, gi) => col.appendChild(draw_group(g, pi, gi)));
  const add = el("button", "choice small lay-add", "+ Group");
  add.type = "button";
  add.addEventListener("click", () => {
    p.groups.push({ id: new_group_id(), title: "New group", auto: false, cards: [] });
    lay_changed();
    lay_rename("group", pi, p.groups.length - 1);
  });
  col.appendChild(add);
  return col;
}

function draw_group(g, pi, gi) {
  const box = el("div", "lay-group");
  box.dataset.page = String(pi);
  box.dataset.group = String(gi);
  const head = el("div", "lay-group-head");
  head.appendChild(el("span", "grip", "⠿"));
  head.appendChild(el("span", "t", g.title));
  head.appendChild(lay_more(() => group_menu(pi, gi)));
  hold_to_drag(head, { type: "group", pi, gi });
  box.appendChild(head);
  if (g.auto) box.appendChild(el("p", "hint", "Adds the node's guests by itself."));
  const list = el("div", "lay-cards");
  g.cards.forEach((c, ci) => {
    const chip = el("div", "lay-card" + (c.kind ? " own" : ""));
    chip.dataset.page = String(pi); chip.dataset.group = String(gi); chip.dataset.card = String(ci);
    if (c.icon) chip.appendChild(lay_icon(c.icon));
    chip.appendChild(el("span", "n", c.name));
    if (c.node) chip.appendChild(el("span", "node", c.node));
    if (c.size) chip.appendChild(el("span", "size", c.size.toUpperCase()));
    chip.appendChild(lay_more(() => card_menu(pi, gi, ci)));
    hold_to_drag(chip, { type: "card", pi, gi, ci });
    list.appendChild(chip);
  });
  if (!g.cards.length && !g.auto) list.appendChild(el("p", "hint lay-empty", "Drop cards here"));
  box.appendChild(list);
  return box;
}

function lay_more(run) {
  const b = el("button", "lay-more", "⋯");
  b.type = "button";
  b.setAttribute("aria-label", "More");
  b.addEventListener("pointerdown", (e) => e.stopPropagation());
  b.addEventListener("click", (e) => { e.stopPropagation(); run(); });
  return b;
}

// ---------------------------------------------------------------------------------------------
// Menus (the confirmation dialog: big buttons, over ⚙)
// ---------------------------------------------------------------------------------------------

function lay_menu(title, options) {
  put($("#confirm-text"), title);
  ask(options.map(([label, run, danger]) => [label, run, !!danger]));
}

function move_in(list, i, by) {
  const j = i + by;
  if (j < 0 || j >= list.length) return false;
  [list[i], list[j]] = [list[j], list[i]];
  return true;
}

function card_menu(pi, gi, ci) {
  const g = L.pages[pi].groups[gi];
  const c = g.cards[ci];
  const kind = L.titles[c.kind || c.guess] || "not a machine";
  lay_menu(`${c.name} · ${kind}${c.kind ? "" : " (guessed)"}`, [
    ["UP", () => { if (move_in(g.cards, ci, -1)) lay_changed(); }],
    ["DOWN", () => { if (move_in(g.cards, ci, 1)) lay_changed(); }],
    ["TO GROUP…", () => pick_group(`Move ${c.name} to:`, (tp, tg) => { g.cards.splice(ci, 1); L.pages[tp].groups[tg].cards.push(c); lay_changed(); })],
    ["KIND…", () => lay_menu(`What is ${c.name}?`, [
      [`AUTO (${L.titles[c.guess] || "not a machine"})`, () => { c.kind = ""; lay_changed(); }],
      ...L.kinds.map((k) => [L.titles[k].toUpperCase(), () => { c.kind = k; lay_changed(); }]),
    ])],
    ["SIZE…", () => lay_menu(`${c.name}: how many cells of the grid`, [
      ["AUTO", () => { c.size = ""; lay_changed(); }],
      ...LAY_SIZES.map(([k, label]) => [label, () => { c.size = k; lay_changed(); }]),
    ])],
    ["ICON…", () => lay_pick_icon(c, `.lay-card[data-page="${pi}"][data-group="${gi}"][data-card="${ci}"] .n`)],
  ]);
}

function group_menu(pi, gi) {
  const p = L.pages[pi];
  const g = p.groups[gi];
  const opts = [
    ["RENAME", () => lay_rename("group", pi, gi)],
    ["UP", () => { if (move_in(p.groups, gi, -1)) lay_changed(); }],
    ["DOWN", () => { if (move_in(p.groups, gi, 1)) lay_changed(); }],
    ["TO PAGE…", () => lay_menu(`Move ${g.title} to:`, L.pages.map((q, qi) => [q.title, () => {
      if (qi === pi) return;
      p.groups.splice(gi, 1); q.groups.push(g); lay_changed();
    }]))],
  ];
  if (!g.cards.length && !g.auto) opts.push(["REMOVE", () => { p.groups.splice(gi, 1); lay_changed(); }, true]);
  lay_menu(g.title, opts);
}

function page_menu(pi) {
  const p = L.pages[pi];
  const opts = [
    ["RENAME", () => lay_rename("page", pi)],
    ["ICON…", () => lay_pick_icon(p, `.lay-page[data-page="${pi}"] .lay-page-head .t`)],
    ["LEFT", () => { if (move_in(L.pages, pi, -1)) lay_changed(); }],
    ["RIGHT", () => { if (move_in(L.pages, pi, 1)) lay_changed(); }],
  ];
  if (!p.groups.some((g) => g.cards.length || g.auto) && !p.games) opts.push(["REMOVE", () => { L.pages.splice(pi, 1); lay_changed(); }, true]);
  lay_menu(p.title, opts);
}

function pick_group(title, done) {
  const opts = [];
  L.pages.forEach((p, pi) => p.groups.forEach((g, gi) => opts.push([`${p.title} › ${g.title}`, () => done(pi, gi)])));
  lay_menu(title, opts);
}

// a card's size: s 1×1, m 2×1, l 2×2, xl 4×2 cells of the grid (config.CARD_SIZES)
const LAY_SIZES = [["s", "S · 1×1"], ["m", "M · 2×1"], ["l", "L · 2×2"], ["xl", "XL · 4×2"]];
let LAY_ICONS = null;   // the icons labhud ships (/api/display/icons), read once

function lay_icon(name) {
  // the same lookup as the card's own (a line icon, the service's logo, a plain box), at the size of a chip
  const box = card_icon(name);
  box.classList.add("lay-icon");
  return box;
}

/** A card's or a page's icon: a text field in place of its name, with the shipped icons offered
    as it is typed (a datalist). Empty = the default (from its source, or none). */
function lay_pick_icon(obj, sel) {
  const span = $("#settings " + sel);
  if (!span) return;
  const list = el("datalist");
  list.id = "lay-icons";
  const fill = () => { list.textContent = ""; for (const n of LAY_ICONS || []) { const o = el("option"); o.value = n; list.appendChild(o); } };
  if (LAY_ICONS) fill();
  else fetch("/api/display/icons").then((r) => r.json()).then((r) => { LAY_ICONS = r.icons || []; fill(); }).catch(() => {});
  const input = el("input", "lay-title");
  input.value = obj.icon || "";
  input.placeholder = "icon, e.g. proxmox (empty: default)";
  input.setAttribute("list", "lay-icons");
  input.maxLength = 64;
  let done = false;
  const keep = () => {
    if (done) return;
    done = true;
    const v = input.value.trim().toLowerCase();
    if (v && !/^[a-z0-9][a-z0-9-]{0,63}$/.test(v)) { lay_say("An icon is a name like proxmox (letters, digits, -).", "warn"); return redraw_layout(); }
    if (v !== (obj.icon || "")) { obj.icon = v; lay_changed(); } else redraw_layout();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") keep(); if (e.key === "Escape") { done = true; redraw_layout(); } });
  input.addEventListener("blur", keep);
  span.replaceWith(input, list);
  input.focus();
}

/** The title becomes a text field in place; Enter or leaving it keeps what was typed. */
function lay_rename(what, pi, gi) {
  const sel = what === "page" ? `.lay-page[data-page="${pi}"] .lay-page-head .t`
    : `.lay-group[data-page="${pi}"][data-group="${gi}"] .lay-group-head .t`;
  const span = $("#settings " + sel);
  if (!span) return;
  const obj = what === "page" ? L.pages[pi] : L.pages[pi].groups[gi];
  const input = el("input", "lay-title");
  input.value = obj.title;
  input.maxLength = 60;
  let done = false;
  const keep = () => {
    if (done) return;
    done = true;
    const v = input.value.trim();
    if (v && v !== obj.title) {
      obj.title = v;
      if (what === "page" && obj.fresh) obj.id = new_page_id(v);
      lay_changed();
    } else redraw_layout();
  };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") keep(); if (e.key === "Escape") { done = true; redraw_layout(); } });
  input.addEventListener("blur", keep);
  span.replaceWith(input);
  input.focus();
  input.select();
}

function new_group_id() {
  const taken = new Set(L.pages.flatMap((p) => p.groups.map((g) => g.id)));
  let i = 1;
  while (taken.has("group-" + i)) i++;
  return "group-" + i;
}

/** A page's id is its address (#id): a-z only, from its title when it is new. */
function new_page_id(title) {
  const base = (title.toLowerCase().replace(/[^a-z]/g, "") || "page").slice(0, 24);
  const taken = new Set(L.pages.map((p) => p.id));
  let id = base, n = 0;
  while (taken.has(id)) id = base + "abcdefghijklmnopqrstuvwxyz"[n++ % 26].repeat(1 + Math.floor(n / 26));
  return id;
}

// ---------------------------------------------------------------------------------------------
// Organize by type
// ---------------------------------------------------------------------------------------------

function organize_preview(pageId) {
  // the proposal is made from what is saved: organize the edits first would mix two things
  if (L.dirty) { lay_say("Save or discard the changes first, then organize.", "warn"); return redraw_layout(); }
  setup_post("organize", { page: pageId }).then((r) => {
    if (!r.ok) { lay_say(r.error || "refused", "warn"); return redraw_layout(); }
    L.preview = { page: pageId, pages: r.pages };
    lay_say("");
    redraw_layout();
  });
}

function draw_preview() {
  const box = el("div", "lay-preview");
  const p = L.preview.pages.find((x) => x.id === L.preview.page);
  box.appendChild(el("b", null, `${p.title}, by type:`));
  for (const g of p.groups) {
    const row = el("div", "lay-prev-row");
    row.appendChild(el("span", "g", g.title));
    row.appendChild(el("span", "c", g.cards.map((c) => c.name + (c.node ? ` · ${c.node}` : "")).join(", ") || (g.auto ? "(its guests)" : "")));
    box.appendChild(row);
  }
  const row = el("div", "choices");
  const yes = el("button", "choice on", "Use this");
  yes.type = "button";
  yes.addEventListener("click", () => {
    L.pages = lay_clone(L.preview.pages);
    L.preview = null;
    lay_changed();
    lay_say("Organized. Move what you like, then Save.", "ok");
    redraw_layout();
  });
  const no = el("button", "choice", "Cancel");
  no.type = "button";
  no.addEventListener("click", () => { L.preview = null; redraw_layout(); });
  row.append(yes, no);
  box.appendChild(row);
  box.appendChild(el("p", "hint", "Guessed from each card: a Proxmox node, a NAS or backup by its name, an agent with Docker, "
    + "a guest. ⋯ > Kind on a card corrects it, and the next Organize keeps that."));
  return box;
}

function save_layout() {
  const pages = L.pages.map((p) => ({ id: p.id, title: p.title, icon: p.icon || "",
    groups: p.groups.map((g) => ({ id: g.id, title: g.title,
      cards: g.cards.map((c) => ({ id: c.id, kind: c.kind || "", icon: c.icon || "", size: c.size || "" })) })) }));
  lay_say("Saving…");
  redraw_layout();
  setup_post("layout-save", { pages }).then((r) => {
    if (!r.ok) { lay_say("Not saved: " + (r.error || "refused"), "warn"); return redraw_layout(); }
    L.saved = null;
    lay_say("Saved. The screens take it when ⚙ closes.", "ok");
    const host = $("#settings .lay");
    if (host) load_layout(host);
  });
}

// ---------------------------------------------------------------------------------------------
// Drag and drop: hold, then drag (mouse, pen or finger)
// ---------------------------------------------------------------------------------------------

let LDRAG = null;

function hold_to_drag(node, what) {
  node.addEventListener("pointerdown", (e) => {
    if (e.button > 0) return;
    const start = { x: e.clientX, y: e.clientY };
    const timer = setTimeout(() => begin_drag(node, what, e), e.pointerType === "mouse" ? 150 : HOLD_MS);
    const cancel = (ev) => {
      // moved before the hold ended: it was a scroll, not a drag
      if (ev.type !== "pointermove" || Math.hypot(ev.clientX - start.x, ev.clientY - start.y) > 8) {
        clearTimeout(timer);
        node.removeEventListener("pointermove", cancel);
      }
    };
    node.addEventListener("pointermove", cancel);
    node.addEventListener("pointerup", () => clearTimeout(timer), { once: true });
    node.addEventListener("pointercancel", () => clearTimeout(timer), { once: true });
  });
}

function begin_drag(node, what, e) {
  const r = node.getBoundingClientRect();
  const ghost = node.cloneNode(true);
  ghost.classList.add("lay-ghost");
  ghost.style.width = r.width + "px";
  document.body.appendChild(ghost);
  node.classList.add("lay-dragging");
  LDRAG = { what, node, ghost, dx: e.clientX - r.left, dy: e.clientY - r.top, target: null, scroll: 0 };
  if (navigator.vibrate) try { navigator.vibrate(15); } catch (err) { /* not allowed */ }
  move_ghost(e.clientX, e.clientY);
  window.addEventListener("pointermove", drag_move, { passive: false });
  window.addEventListener("pointerup", drag_end);
  window.addEventListener("pointercancel", drag_cancel);
  window.addEventListener("touchmove", no_scroll, { passive: false });
  touched();
}

function no_scroll(e) { if (LDRAG) e.preventDefault(); }

function move_ghost(x, y) {
  LDRAG.ghost.style.transform = `translate(${x - LDRAG.dx}px, ${y - LDRAG.dy}px)`;
  LDRAG.x = x; LDRAG.y = y;
}

function drag_move(e) {
  if (!LDRAG) return;
  e.preventDefault();
  move_ghost(e.clientX, e.clientY);
  mark_target(find_target(e.clientX, e.clientY));
  // near the top or bottom edge, ⚙ scrolls by itself
  const sc = $("#settings-scroll").getBoundingClientRect();
  LDRAG.scroll = e.clientY < sc.top + 40 ? -12 : e.clientY > sc.bottom - 40 ? 12 : 0;
  if (LDRAG.scroll && !LDRAG.scrolling) {
    LDRAG.scrolling = setInterval(() => {
      if (!LDRAG || !LDRAG.scroll) { if (LDRAG) { clearInterval(LDRAG.scrolling); LDRAG.scrolling = null; } return; }
      $("#settings-scroll").scrollTop += LDRAG.scroll;
      mark_target(find_target(LDRAG.x, LDRAG.y));
    }, 30);
  }
}

/** Where a drop would go: {pi, gi, ci, after} for a card, {pi, gi, after} for a group. */
function find_target(x, y) {
  const at = document.elementFromPoint(x, y);
  if (!at) return null;
  if (LDRAG.what.type === "card") {
    const chip = at.closest(".lay-card");
    if (chip === LDRAG.node) return null;   // back where it was
    if (chip) {
      const r = chip.getBoundingClientRect();
      return { node: chip, pi: +chip.dataset.page, gi: +chip.dataset.group, ci: +chip.dataset.card, after: x > r.left + r.width / 2 };
    }
    const g = at.closest(".lay-group");
    if (g) return { node: g, pi: +g.dataset.page, gi: +g.dataset.group, ci: null, after: true };
    return null;
  }
  const g = at.closest(".lay-group");
  if (g && g !== LDRAG.node.parentNode) {
    const r = g.getBoundingClientRect();
    return { node: g, pi: +g.dataset.page, gi: +g.dataset.group, after: y > r.top + r.height / 2 };
  }
  const p = at.closest(".lay-page");
  if (p && !g) return { node: p, pi: +p.dataset.page, gi: null, after: true };
  return null;
}

function mark_target(t) {
  if (LDRAG.target && LDRAG.target.node) LDRAG.target.node.classList.remove("drop-before", "drop-after", "drop-in");
  LDRAG.target = t;
  if (t) t.node.classList.add(t.ci === null || t.gi === null ? "drop-in" : t.after ? "drop-after" : "drop-before");
}

function drag_stop() {
  window.removeEventListener("pointermove", drag_move);
  window.removeEventListener("pointerup", drag_end);
  window.removeEventListener("pointercancel", drag_cancel);
  window.removeEventListener("touchmove", no_scroll);
  if (LDRAG.scrolling) clearInterval(LDRAG.scrolling);
  LDRAG.ghost.remove();
  LDRAG.node.classList.remove("lay-dragging");
  if (LDRAG.target) LDRAG.target.node.classList.remove("drop-before", "drop-after", "drop-in");
}

function drag_cancel() { if (!LDRAG) return; drag_stop(); LDRAG = null; }

function drag_end() {
  if (!LDRAG) return;
  const { what, target: t } = LDRAG;
  drag_stop();
  LDRAG = null;
  if (!t) return;
  if (what.type === "card") {
    const from = L.pages[what.pi].groups[what.gi].cards;
    const card = from[what.ci];
    const to = L.pages[t.pi].groups[t.gi].cards;
    // the index is taken before removing, so moving down the same group lands where it was shown
    let at = t.ci === null ? to.length : t.ci + (t.after ? 1 : 0);
    if (from === to && what.ci < at) at--;
    from.splice(what.ci, 1);
    to.splice(at, 0, card);
  } else {
    const from = L.pages[what.pi].groups;
    const group = from[what.gi];
    const to = L.pages[t.pi].groups;
    let at = t.gi === null ? to.length : t.gi + (t.after ? 1 : 0);
    if (from === to && what.gi < at) at--;
    from.splice(what.gi, 1);
    to.splice(at, 0, group);
  }
  lay_changed();
}
