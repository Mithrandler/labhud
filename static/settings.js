/* Settings of THIS screen (the ⚙ button): theme, accent, font, text size, page rotation, night
   and calm. They live in the screen's own browser (localStorage), so the wall can stay OLED black
   while a phone uses grey, and nothing on the server changes. What a screen does not set comes
   from config.toml, as before.

   Loaded before app.js, so the look is applied before the first frame (no flash of the default
   theme). The panel itself is built on demand and uses app.js's helpers ($, el, put, CFG...),
   which exist by the time anyone can press the button. */

const SETTINGS_KEY = "labhud-settings";

/* design:grey (design/tokens.json, written by design/build.py: edit there) */
const GREY_VARS = { "--bg": "#16181d", "--card": "#1e2128", "--block": "#272b33", "--line": "#333844", "--border": "#454c59" };
const PAPER_BASE = { label: "Paper", light: true, vars: { "--bg": "#f6f3ec", "--card": "#ffffff", "--block": "#efebe2", "--line": "#e2ddd0", "--border": "#cfc8b6", "--text": "#1c1b18", "--sub": "#55524a", "--muted": "#8a8577", "--accent": "#1f4fd8", "--title": "#1a3fb0", "--on-title": "#ffffff", "--green": "#1f8a3b", "--warn": "#b25e00", "--red": "#c62828", "--red-bg": "#fde8e6", "--red-text": "#7a1612", "--info-bg": "#e9eefb", "--on-accent": "#ffffff" } };
const TERMINAL_BASE = { label: "Terminal", light: false, font: "mono", vars: { "--bg": "#050805", "--card": "#0a110a", "--block": "#0f1a0f", "--line": "#16301a", "--border": "#1f4a26", "--text": "#c8f7c5", "--sub": "#7fbf7a", "--muted": "#4f7d4b", "--accent": "#33ff66", "--title": "#ffb000", "--on-title": "#000000", "--green": "#33ff66", "--warn": "#ffb000", "--red": "#ff4d4d", "--red-bg": "#2a0a0a", "--red-text": "#ffc9c9", "--info-bg": "#0a1a0e", "--on-accent": "#001a06" } };
const GLASS_BASE = { label: "Glass", light: false, vars: { "--bg": "#0b1220", "--card": "#ffffff12", "--block": "#ffffff1f", "--line": "#ffffff1f", "--border": "#ffffff33", "--text": "#f2f6ff", "--sub": "#b9c4dd", "--muted": "#7f8db0", "--accent": "#7aa2ff", "--title": "#9bb8ff", "--on-title": "#07102a", "--green": "#3ddc84", "--warn": "#ffb454", "--red": "#ff6b6b", "--red-bg": "#3a1020", "--red-text": "#ffd6dd", "--info-bg": "#ffffff0f", "--on-accent": "#07102a" } };
/* design:end */

/** Background bases: the colours behind the cards. */
const BASES = {
  oled: { label: "OLED black", vars: {} },   // the defaults in app.css
  grey: { label: "Dark grey", vars: GREY_VARS },
  paper: PAPER_BASE,
  terminal: TERMINAL_BASE,
  glass: GLASS_BASE,
};

/** Every variable any base sets: a base that does not set one gives it back to the default in app.css. */
const BASE_VAR_NAMES = [...new Set(Object.values(BASES).flatMap((b) => Object.keys(b.vars)))];

/** Accents: [accent, titles]. Status colours (green/orange/red dots) never change with them. */
const ACCENTS = {
  blue: ["#8ab4f8", "#7cc4ff"],
  green: ["#39ff88", "#5cffa0"],
  orange: ["#ffa94d", "#ffb86b"],
  red: ["#ff8a80", "#ff9e94"],
  violet: ["#b39dff", "#c4b2ff"],
  cyan: ["#4ce7ff", "#7aeeff"],
  pink: ["#ff7eb6", "#ff9ac6"],
  yellow: ["#ffe066", "#ffe98a"],
};

const FONTS = {
  roboto: ["Roboto", '"Roboto", system-ui, sans-serif'],
  inter: ["Inter", '"Inter", system-ui, sans-serif'],
  mono: ["JetBrains Mono", '"JetBrains Mono", ui-monospace, monospace'],
  system: ["System", "system-ui, sans-serif"],
};

const DEFAULTS = {
  base: null, accent: null,   // null = the board's (⚙ > Dashboard > Theme); otherwise this screen's own
  font: "roboto", scale: 100,
  rotate: true, period: 20, skip: [],
  night: null,   // null = as in config.toml; otherwise {on, from, to, dim}
  wake: true,    // a new problem lights a dimmed screen up
  calm: null,    // null = as in config.toml; otherwise {on, after}
  hidden: [],    // "g:<group id>" / "c:<card id>" left off this screen
  density: "normal",   // "compact" / "normal" / "airy"
  awake: false,  // keep the screen on (Wake Lock, or a silent video over plain HTTP)
  flash: true,   // a new problem flashes a red frame three times
  sound: false,  // ... and beeps
  loud_night: false,   // the beep also at night
  calm_show: ["weather"],   // under the calm clock: "weather" and/or ids of cards with a list
  tab: "look",   // the ⚙ section last open
};

let S = load_settings();

function load_settings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    const out = { ...DEFAULTS, ...(saved && typeof saved === "object" ? saved : {}) };
    // Older versions wrote their defaults into the saved settings: those were never a choice, so
    // they follow the board's theme (a screen that picked something else keeps it)
    if (out.base === "oled") out.base = null;
    if (out.accent === "blue") out.accent = null;
    return out;
  } catch (e) {
    return { ...DEFAULTS };   // private window, blocked storage: the defaults, every time
  }
}

function save_settings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(S)); } catch (e) { /* not kept, still applied */ }
}

/** The board's theme ({base, accent}, [display] in the config), or the built-in one before /api/config is read. */
function board_theme() {
  const d = (typeof CFG !== "undefined" && CFG && CFG.display) || {};
  return { base: BASES[d.base] ? d.base : "oled", accent: d.accent || "blue" };
}

/** What this screen shows: its own choice, else the board's. */
function look_base() { return BASES[S.base] ? S.base : board_theme().base; }
function look_accent() { return S.accent || board_theme().accent; }

/** The accent as [accent, title], a preset name or a custom "#rrggbb". */
function accent_colours(a) {
  if (ACCENTS[a]) return ACCENTS[a];
  return /^#[0-9a-f]{6}$/i.test(a || "") ? [a, a] : ACCENTS.blue;
}

/** Text size as a factor (0.85..1.3); layout() in app.js multiplies its zoom steps by it. */
function ui_scale() {
  const n = Number(S.scale);
  return Number.isFinite(n) ? Math.min(130, Math.max(85, n)) / 100 : 1;
}

function apply_look() {
  const root = document.documentElement.style;
  const base = BASES[look_base()] || BASES.oled;
  for (const name of BASE_VAR_NAMES) {
    if (base.vars[name]) root.setProperty(name, base.vars[name]); else root.removeProperty(name);
  }
  root.colorScheme = base.light ? "light" : "dark";
  document.documentElement.classList.toggle("light-ground", !!base.light);
  // a base with an accent of its own (Paper, Terminal, Glass) keeps it: the presets are made for a dark ground
  if (!base.vars["--accent"]) {
    const [accent, title] = accent_colours(look_accent());
    if (look_accent() === "blue") { root.removeProperty("--accent"); root.removeProperty("--title"); }
    else { root.setProperty("--accent", accent); root.setProperty("--title", title); }
  }
  const font = FONTS[S.font] || FONTS.roboto;
  // the font chosen on this screen wins; else the base's (Terminal is monospace)
  const own = S.font === "roboto" && base.font ? FONTS[base.font] : font;
  if (S.font === "roboto" && !base.font) root.removeProperty("--font"); else root.setProperty("--font", own[1]);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = base.vars["--bg"] || "#000000";
  apply_density();
}

/** The spacing: this screen's (⚙ > Look), unless the page being shown sets its own (⚙ > Dashboard). */
function apply_density() {
  // settings.js runs apply_look() before app.js has declared CFG (and page): until then, this screen's own
  const cfg = typeof CFG === "undefined" ? null : CFG;
  const own = cfg && typeof page !== "undefined" && cfg.pages[page] ? cfg.pages[page].density : "";
  const d = own || S.density;
  for (const k of ["compact", "airy"]) document.body.classList.toggle("density-" + k, d === k);
}

/** Left off this screen from ⚙: a whole group, or one card. mount() in app.js skips them, and a
    hidden card's new problem does not take the screen. */
function is_hidden(groupId, cardId) {
  if (config_hidden(groupId, cardId)) return true;
  return S.hidden.includes("g:" + groupId) || (cardId != null && S.hidden.includes("c:" + cardId));
}

/** Hidden for every screen, in the config (⚙ > Dashboard > Pages and groups): the group, or the card. */
function config_hidden(groupId, cardId) {
  for (const p of CFG.pages) {
    const g = p.groups.find((x) => x.id === groupId);
    if (!g) continue;
    if (g.hidden) return true;
    return cardId != null && !!(CFG.cards[groupId] || []).find((c) => c.id === cardId && c.hidden);
  }
  return false;
}

// ---------------------------------------------------------------------------------------------
// Keeping the screen on
// ---------------------------------------------------------------------------------------------

let wakeLock = null, wakeVideo = null;

/** The browser's Wake Lock where it exists (it needs HTTPS); over plain HTTP, a silent looping
    video does the same on phones (the NoSleep.js trick, its clips are in static/). Both need the
    page to be visible, so they are asked again when the screen comes back. */
async function apply_awake() {
  if (!S.awake) {
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    if (wakeVideo) { wakeVideo.pause(); wakeVideo.remove(); wakeVideo = null; }
    return;
  }
  if (document.hidden) return;
  if ("wakeLock" in navigator && window.isSecureContext) {
    try {
      if (!wakeLock || wakeLock.released) wakeLock = await navigator.wakeLock.request("screen");
      return;
    } catch (e) { /* refused (battery saver): try the video */ }
  }
  if (!wakeVideo) {
    wakeVideo = document.createElement("video");
    wakeVideo.muted = true; wakeVideo.loop = true; wakeVideo.playsInline = true;
    wakeVideo.setAttribute("playsinline", "");
    wakeVideo.style.cssText = "position:fixed;width:1px;height:1px;opacity:0;pointer-events:none";
    for (const [src, type] of [["/keepawake.webm", "video/webm"], ["/keepawake.mp4", "video/mp4"]]) {
      const so = document.createElement("source"); so.src = src; so.type = type; wakeVideo.appendChild(so);
    }
    document.body.appendChild(wakeVideo);
  }
  wakeVideo.play().catch(() => { /* needs a touch first: the next one starts it */ });
}

document.addEventListener("visibilitychange", () => { if (!document.hidden) apply_awake(); });
// a video may only start after a touch: every touch retries while it is not playing
document.addEventListener("pointerdown", () => { if (S.awake && wakeVideo && wakeVideo.paused) wakeVideo.play().catch(() => {}); }, { passive: true });

// ---------------------------------------------------------------------------------------------
// The signal of a new problem
// ---------------------------------------------------------------------------------------------

let audio = null;

/** Called by app.js when a problem is new (focus_on). A red frame flashes three times — bound to
    that one event, so it is not an endless animation — and, if chosen, two short tones. */
function signal_problem() {
  if (S.flash) {
    document.body.classList.remove("flash");
    void document.body.offsetWidth;   // restart the animation when two problems come close
    document.body.classList.add("flash");
    setTimeout(() => document.body.classList.remove("flash"), 1700);
  }
  if (S.sound && (S.loud_night || !night_now())) beep();
}

function beep() {
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
    const t = audio.currentTime;
    for (const [at, hz] of [[0, 880], [0.22, 660]]) {
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = hz; o.type = "sine";
      g.gain.setValueAtTime(0.0001, t + at);
      g.gain.exponentialRampToValueAtTime(0.25, t + at + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + at + 0.18);
      o.connect(g).connect(audio.destination);
      o.start(t + at); o.stop(t + at + 0.2);
    }
  } catch (e) { /* no audio on this device */ }
}

apply_look();
apply_awake();

/** The night window in force on this screen: its own, or config.toml's. null = no night. */
function night_cfg() {
  if (S.night) return S.night.on ? S.night : null;
  return (typeof CFG !== "undefined" && CFG && CFG.night) || null;
}

/** Calm in force on this screen, the same way. */
function calm_cfg() {
  if (S.calm) return S.calm.on ? S.calm : null;
  return (typeof CFG !== "undefined" && CFG && CFG.calm) || null;
}

function apply_night() {
  const n = night_cfg();
  if (n) document.documentElement.style.setProperty("--shade", String(1 - n.dim / 100));
}

/** Pages left in the rotation: all but the skipped ones. */
function rotation_pages() {
  return CFG.pages.map((p, i) => i).filter((i) => !S.skip.includes(CFG.pages[i].id));
}

// ---------------------------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------------------------

// ⚙ is drawn one section at a time: the sidebar (four groups, settings/registry.js), then the open
// section's own render(). On a narrow screen the sidebar is a list of its own, and a section a
// sub-page with ‹ back. The search on top finds a section by its title, keywords and line.
let setList = false;     // narrow screen: the list of sections is shown instead of one section
let setFind = "";        // what is typed in ⚙'s search

/** A narrow screen (a phone held upright): ⚙ is a list, then one section. Same test as settings.css. */
function settings_narrow() {
  return window.matchMedia("(max-width: 700px) and (orientation: portrait)").matches;
}

/** Opens ⚙ on the section `id` (a link inside ⚙: "Open System", the account menu's Edit config). */
function settings_go(id) {
  S.tab = settings_tab_key(id); save_settings();
  setList = false; setFind = "";
  const sc = $("#settings-scroll");
  if (sc) sc.scrollTop = 0;
  open_settings();
}

function open_settings() {
  touched();
  // ⚙ is the admin's (account.js): a display that is not signed in only shows
  if (!signed_in()) return open_signin();
  if (locked()) return lock_unlock().then((open) => open && open_settings());
  const box = $("#settings");
  const body = $("#settings-body");
  const scroller = $("#settings-scroll");
  const fresh = box.hidden;
  const keep = fresh ? 0 : scroller.scrollTop;   // a choice redraws the section: stay where you were
  if (fresh) {
    recheck_admin(); check_admin();
    // ⚙ opened from its button on a phone starts at the list; ?settings=<id> opens that section
    setList = settings_narrow() && !new URLSearchParams(location.search).get("settings");
    setFind = "";
  }
  settings_bar();
  body.textContent = "";

  const tab = settings_tab_key(S.tab);
  const own = settings_section(tab);
  settings_tabs(tab);
  box.classList.toggle("list", setList && !setFind);
  box.classList.toggle("finding", !!setFind);
  body.className = "tab-" + tab;
  if (setFind) { settings_results(body); box.hidden = false; return; }

  const ctx = settings_ctx(body, tab);
  const head = el("div", "set-head");
  head.append(tab_icon(tab), el("h2", null, own.title));
  if (own.soon) head.appendChild(el("span", "soon", "soon"));
  body.appendChild(head);
  if (own.about) body.appendChild(el("p", "set-about", own.about));
  if (own.render) own.render(body, ctx);
  finish_settings(body, own);
  box.hidden = false;
  scroller.scrollTop = keep;
}

/** What a section's render() gets: helpers that draw ⚙'s parts the same way everywhere. */
function settings_ctx(body, tab) {
  // section(title, {wide, about, cls}); section(title, true) is wide; the modules' older
  // section(title, "<tab>", wide) still works
  const section = (title, opts, wide) => {
    const o = typeof opts === "boolean" ? { wide: opts } : typeof opts === "string" ? { wide: !!wide } : (opts || {});
    const s = el("section", "set" + (o.wide ? " wide" : "") + (o.cls ? " " + o.cls : ""));
    const h = el("h3", null, title);
    s.appendChild(h);
    if (CLI_HELP[title] && signed_in()) cli_help(s, h, title);
    if (o.about) s.appendChild(el("p", "set-desc", o.about));
    body.appendChild(s);
    return s;
  };
  const choices = (host, options, current, pick) => {
    const row = el("div", "choices");
    for (const [value, label, swatch] of options) {
      const b = el("button", "choice" + (value === current ? " on" : ""), label);
      b.type = "button";
      if (swatch) { b.style.setProperty("--sw", swatch); b.classList.add("swatch"); }
      b.addEventListener("click", () => { pick(value); open_settings(); });
      row.appendChild(b);
    }
    host.appendChild(row);
    return row;
  };
  // ◌ a part still to come (a later package): its title, a line on what it will do, "soon"
  const soon = (title, about, wide) => {
    const s = section(title, { wide, about, cls: "is-soon" });
    s.querySelector("h3").appendChild(el("span", "soon", "soon"));
    return s;
  };
  const danger = (about, actions) => danger_zone(section("Danger zone", { wide: true, about, cls: "danger-zone" }), actions);
  return { tab, body, section, choices, soon, danger, redraw: open_settings };
}

/** The red card: each action asks for a word to be typed before its button works.
    actions: [[label, what it does, the word to type, run, soon?], …] */
function danger_zone(s, actions) {
  for (const [label, about, word, run, later] of actions) {
    const row = el("div", "danger-row");
    const text = el("div", "danger-text");
    text.append(el("b", null, label), el("span", null, about));
    const b = el("button", "choice danger", later ? "soon" : label);
    b.type = "button";
    b.disabled = !!later;
    row.append(text, b);
    const sure = el("form", "danger-sure");
    sure.hidden = true;
    const input = el("input");
    input.autocomplete = "off"; input.spellcheck = false; input.autocapitalize = "characters";
    input.placeholder = `Type ${word} to confirm`;
    input.setAttribute("aria-label", `Type ${word} to confirm`);
    const go = el("button", "choice danger on", label);
    go.type = "submit";
    go.disabled = true;
    const cancel = el("button", "choice small", "Cancel");
    cancel.type = "button";
    sure.append(input, go, cancel);
    input.addEventListener("input", () => { go.disabled = input.value.trim().toUpperCase() !== word; });
    cancel.addEventListener("click", () => { sure.hidden = true; b.hidden = false; input.value = ""; });
    sure.addEventListener("submit", (e) => {
      e.preventDefault();
      if (input.value.trim().toUpperCase() !== word) return;
      sure.hidden = true; b.hidden = false; input.value = "";
      run();
    });
    b.addEventListener("click", () => { b.hidden = true; sure.hidden = false; go.disabled = true; input.focus(); });
    s.append(row, sure);
  }
  return s;
}

/** ⚙'s top bar: the mark and "Settings", the search, ✕ (‹ back to the list on a phone). */
function settings_bar() {
  const bar = $("#settings-bar");
  if (!bar.querySelector(".set-find")) {
    bar.textContent = "";
    const back = el("button", "set-back");
    back.type = "button";
    back.setAttribute("aria-label", "All settings");
    back.appendChild(icon_svg("back"));
    back.addEventListener("click", () => { setList = true; setFind = ""; open_settings(); });
    const brand = el("span", "set-brand");
    const mark = el("img");
    mark.src = "/brand/mark.svg"; mark.alt = ""; mark.width = 22; mark.height = 22;
    brand.append(mark, el("span", null, "Settings"));
    const find = el("label", "set-find");
    find.appendChild(icon_svg("search"));
    const input = el("input");
    input.type = "search"; input.placeholder = "Search settings"; input.autocomplete = "off"; input.spellcheck = false;
    input.setAttribute("aria-label", "Search settings");
    input.addEventListener("input", () => { setFind = input.value; open_settings(); input.focus(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { const hit = find_settings(setFind)[0]; if (hit) settings_go(hit.id); }
    });
    find.appendChild(input);
    const x = el("button", "set-x", "✕");
    x.id = "settings-x"; x.type = "button"; x.setAttribute("aria-label", "Close");
    x.addEventListener("click", close_settings);
    bar.append(back, brand, find, x);
  }
  const input = bar.querySelector(".set-find input");
  if (input.value !== setFind) input.value = setFind;
}

/** The search's answer: the sections that match, each with its group and line. */
function settings_results(body) {
  const hits = find_settings(setFind);
  body.appendChild(el("h3", "set-results", hits.length ? `${hits.length} found` : "Nothing found"));
  const list = el("div", "set-list");
  for (const s of hits) list.appendChild(section_link(s));
  body.appendChild(list);
}

/** A section as a row: its icon, title and line, ›. The search results and the phone's list. */
function section_link(s, current) {
  const b = el("button", "set-link" + (s.id === current ? " on" : ""));
  b.type = "button";
  const text = el("span", "set-link-text");
  const title = el("span", "t", s.title);
  if (s.soon) title.appendChild(el("span", "soon", "soon"));
  text.appendChild(title);
  if (s.about) text.appendChild(el("span", "d", s.about));
  b.append(tab_icon(s.id), text, icon_svg("chevron_right"));
  b.addEventListener("click", () => settings_go(s.id));
  return b;
}

// Escape: the search first, then back to the list (a phone), then ⚙ closes. A dialog over ⚙
// (confirm, sign in, the keypad) takes its own Escape.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const box = document.getElementById("settings");
  if (!box || box.hidden) return;
  if (["confirm", "signin"].some((id) => { const n = document.getElementById(id); return n && !n.hidden; })) return;
  if (typeof pad !== "undefined" && pad) return;
  if (document.activeElement && document.activeElement.classList.contains("lay-title")) return;
  e.preventDefault();
  if (setFind) { setFind = ""; open_settings(); return; }
  if (settings_narrow() && !setList) { setList = true; open_settings(); return; }
  close_settings();
});

/** ⚙ > Alerts > On this screen: what this screen does when something goes wrong (kept here). */
function screen_signal_settings(ctx) {
  const scr = ctx.section("On this screen", { about: "What this screen does when a problem is new. Kept in this browser only." });
  ctx.choices(scr, [[false, "Screen may sleep"], [true, "Keep the screen on"]], S.awake, (v) => { S.awake = v; save_settings(); apply_awake(); });
  if (S.awake && !window.isSecureContext) {
    scr.appendChild(el("p", "hint", "Over plain HTTP this uses a silent video: it starts after the first touch."));
  }
  ctx.choices(scr, [[true, "Flash red"], [false, "No flash"]], S.flash, (v) => { S.flash = v; save_settings(); });
  ctx.choices(scr, [[false, "Silent"], [true, "Beep"]], S.sound, (v) => { S.sound = v; save_settings(); if (v) beep(); });
  if (S.sound) ctx.choices(scr, [[false, "Silent at night"], [true, "Beep at night too"]], S.loud_night, (v) => { S.loud_night = v; save_settings(); });
}

/** ⚙ > Phones: pair one with a QR code, and the paired ones (devices.py). */
function phones_settings(ctx) {
  const app = ctx.section("Phones", { wide: true, about: "The LabHUD Android app. Pair shows a QR code, valid once for a few minutes; Remove ends a phone's access at once." });
  const show = el("div", "pair-qr");
  const pairButton = el("button", "choice on", "Pair a phone");
  pairButton.type = "button";
  pairButton.addEventListener("click", () => pair_phone(show));
  const prow = el("div", "choices");
  prow.appendChild(pairButton);
  const list = el("div", "devices");
  app.append(prow, show, list);
  load_devices(list);
}

/** One column per page: its rotation tick on top, then its groups and cards, each tick showing or
    hiding it on this screen. Side by side across the width; a narrow screen scrolls sideways. */
function page_columns(s) {
  const cols = el("div", "page-cols");
  for (const p of CFG.pages) {
    const col = el("div", "page-col");
    const inRot = !S.skip.includes(p.id);
    const head = el("div", "page-head");
    head.appendChild(el("span", "page-title", p.title));
    const rb = el("button", "choice small" + (inRot ? " on" : ""), inRot ? "↻ rotates" : "↻ skipped");
    rb.type = "button";
    rb.addEventListener("click", () => {
      S.skip = inRot ? [...S.skip, p.id] : S.skip.filter((x) => x !== p.id);
      save_settings(); open_settings();
    });
    head.appendChild(rb);
    col.appendChild(head);
    for (const g of p.groups) {
      const cards = CFG.cards[g.id] || [];
      if (!cards.length) continue;
      const row = el("div", "choices hide-row");
      const gOn = !S.hidden.includes("g:" + g.id);
      const gb = el("button", "choice group-chip" + (gOn ? " on" : ""), g.title);
      gb.type = "button";
      gb.addEventListener("click", () => { toggle_hidden("g:" + g.id); });
      row.appendChild(gb);
      if (gOn && cards.length > 1) {
        for (const c of cards) {
          const on = !S.hidden.includes("c:" + c.id);
          const cb = el("button", "choice small" + (on ? " on" : ""), c.name);
          cb.type = "button";
          cb.addEventListener("click", () => { toggle_hidden("c:" + c.id); });
          row.appendChild(cb);
        }
      }
      col.appendChild(row);
    }
    if (p.games) col.appendChild(el("p", "hint", "Game servers, as the panel lists them."));
    cols.appendChild(col);
  }
  s.appendChild(cols);
}

// the names before 0.4 (kept by browsers, links, and ?static&settings=<tab>) → the sections now
const TAB_ALIAS = {
  display: "look", night: "look", screen: "alerts", pages: "dashboard", layout: "dashboard",
  devices: "hosts", agents: "hosts", sources: "integrations", scan: "integrations",
  notify: "notifications", ntfy: "notifications", advanced: "system", config: "system", about: "system",
  pin: "security", lock: "security", admin: "security", tokens: "api", audit: "api", docker: "containers",
};

/** A known section's id for `k` (an alias, an old name), or "" for none. Any registered section:
    ?settings=<id> is read before the admin check answers, and ⚙ itself is the admin's anyway. */
function settings_known(k) {
  k = TAB_ALIAS[k] || k;
  return settings_section(k) ? k : "";
}

function settings_tab_key(k) {
  return settings_known(k) || "look";
}

function tab_icon(key) {
  const s = settings_section(key);
  return icon_svg((s && s.icon) || key);
}

/** The sidebar: each group's title, then its sections (icon and name). On a phone the same list is
    the first page of ⚙, with each section's line under its name. */
function settings_tabs(current) {
  const bar = $("#settings-tabs");
  if (!bar) return;
  bar.textContent = "";
  for (const [group, list] of settings_groups()) {
    bar.appendChild(el("div", "settings-group", group));
    for (const s of list) settings_tab(bar, s, current);
  }
}

function settings_tab(bar, s, current) {
  const on = s.id === current && !setList;
  const b = el("button", "settings-tab" + (on ? " on" : "") + (s.soon ? " is-soon" : ""));
  b.type = "button";
  b.setAttribute("role", "tab");
  b.setAttribute("aria-selected", on ? "true" : "false");
  const text = el("span", "lbl");
  text.appendChild(el("span", "t", s.title));
  if (s.about) text.appendChild(el("span", "d", s.about));
  b.append(tab_icon(s.id), text);
  if (s.soon) b.appendChild(el("span", "soon", "soon"));
  b.appendChild(icon_svg("chevron_right"));
  b.addEventListener("click", () => {
    if (s.id === current && !setList) return;
    settings_go(s.id);
  });
  bar.appendChild(b);
}

/** Under a section: what keeps it. This screen's own (Display) can go back to the defaults. */
function finish_settings(body, own) {
  if (own.group !== "display") {
    body.appendChild(el("p", "hint set-foot", "Kept by LabHUD itself: the same on every screen and in the app."));
    return;
  }
  const reset = el("button", "reset", "Reset this screen to the defaults");
  reset.type = "button";
  reset.addEventListener("click", () => {
    S = { ...DEFAULTS };
    try { localStorage.removeItem(SETTINGS_KEY); } catch (e) { /* nothing kept anyway */ }
    apply_look(); apply_night(); apply_awake(); render_night(); mount(); open_settings();
  });
  body.appendChild(reset);
  body.appendChild(el("p", "hint set-foot", "Saved in this browser only: other screens keep their own settings."));
}

/** Asks labhud for a pairing code and shows it as a QR code (an SVG made by the server, qr.py). */
function pair_phone(host) {
  host.textContent = "";
  host.appendChild(el("p", "hint", "Asking LabHUD…"));
  fetch("/api/v1/pair-code", {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: "{}",
  }).then((r) => r.json()).then((r) => {
    host.textContent = "";
    if (!r.ok) {
      const say = (m) => host.appendChild(el("p", "hint", m));
      if (r.error === "forbidden") admin_forbidden(say); else say(r.error);
      return;
    }
    const svg = new DOMParser().parseFromString(r.svg, "image/svg+xml").documentElement;
    host.appendChild(document.importNode(svg, true));
    host.appendChild(el("p", "hint", `Scan it in the app (Add server). It works once, within ${r.minutes} minutes.`));
    host.appendChild(copy_line(r.link, "pair-link"));
    wait_for_pairing(host.parentNode.querySelector(".devices"));
  }).catch(() => { host.textContent = ""; host.appendChild(el("p", "hint", "Could not reach LabHUD.")); });
  touched();
}

/** "5 min ago", "3 h ago", "2 days ago": when a phone was last seen. */
function ago(t) {
  if (!t) return "never";
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 600) return "just now";   // labhud writes "last seen" at most every 5 minutes
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + (s < 172800 ? " day ago" : " days ago");
}

/** The paired phones, each with a Remove button (asks first). */
function load_devices(host) {
  if (!host) return;
  fetch("/api/v1/devices", { headers: lock_headers() }).then((r) => r.json()).then((r) => {
    host.textContent = "";
    if (!r.pairing) { host.appendChild(el("p", "hint", "Pairing needs LABHUD_DATA (a folder labhud can write).")); return; }
    if (!r.devices.length) { host.appendChild(el("p", "hint", "No phone paired yet.")); return; }
    for (const d of r.devices) {
      const row = el("div", "device");
      const text = el("div", "device-text");
      text.append(el("span", "device-name", d.name), el("span", "device-seen", "seen " + ago(d.seen)));
      const rm = el("button", "choice danger", "Remove");
      rm.type = "button";
      rm.addEventListener("click", () => {
        put($("#confirm-text"), `Remove ${d.name}? Its LabHUD app stops working until it is paired again.`);
        ask([["REMOVE", () => unpair_device(d.name, host), true]]);
      });
      row.append(text, rm);
      host.appendChild(row);
    }
  }).catch(() => { host.textContent = ""; host.appendChild(el("p", "hint", "Could not list the paired phones.")); });
}

function unpair_device(name, host) {
  fetch("/api/v1/unpair", {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify({ name }),
  }).then((r) => r.json()).then((r) => {
    if (!r.ok) (r.error === "forbidden" ? admin_forbidden(show_message) : show_message(r.error));
    load_devices(host);
  }).catch(() => show_message("Could not reach LabHUD."));
  touched();
}

/** While a QR code is shown, the list notices the new phone: checked every 3 s for 5 minutes. */
let pairWatch = null;
function wait_for_pairing(host) {
  clearInterval(pairWatch);
  const before = host ? host.querySelectorAll(".device").length : 0;
  const until = Date.now() + 300000;
  pairWatch = setInterval(() => {
    if (!host || !host.isConnected || Date.now() > until) { clearInterval(pairWatch); return; }
    fetch("/api/v1/devices", { headers: lock_headers() }).then((r) => r.json()).then((r) => {
      if ((r.devices || []).length > before) {
        clearInterval(pairWatch);
        const qr = host.parentNode.querySelector(".pair-qr");
        if (qr) { qr.textContent = ""; qr.appendChild(el("p", "hint", `Paired: ${r.devices[r.devices.length - 1].name}.`)); }
        load_devices(host);
      }
    }).catch(() => {});
  }, 3000);
}

function toggle_hidden(key) {
  S.hidden = S.hidden.includes(key) ? S.hidden.filter((x) => x !== key) : [...S.hidden, key];
  save_settings(); mount(); open_settings();
}

function close_settings() { $("#settings").hidden = true; touched(); }

// ---------------------------------------------------------------------------------------------
// ⚙ > Hosts & agents: the paired agents, what each found, what each may do (agents.py)
// ---------------------------------------------------------------------------------------------

const CAP_TEXT = {
  docker: ["Docker", "start, stop, restart containers"],
  systemd: ["Services", "start, stop, restart systemd units"],
  host: ["The host", "reboot, shut down, update packages"],
  wol: ["Wake-on-LAN", "wake machines on its network"],
  pve: ["Proxmox", "start, shut down, reboot guests"],
  ptero: ["Game servers", "start, stop, restart (Pterodactyl)"],
  scan: ["Image scan", "CVEs in the ticked containers' images (trivy or grype on the host)"],
  terminal: ["Terminal", "a shell in the ticked containers, for the admin with the PIN (the agent needs LABHUD_AGENT_TERMINAL=on)"],
  stacks: ["Stacks", "deploy, redeploy, restart and take down docker compose stacks (docker compose on the host)"],
};

function agents_post(op, body) {
  return fetch("/api/agents/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: r.ok }))).catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

// What the Agents section shows: one agent's detail, or the add-an-agent flow ("add"), and the
// capability whose targets are open, per agent. Kept while ⚙ is open, across the 10 s redraws.
let agentSel = null;
const agentCap = {};

function agents_settings(s) {
  const split = el("div", "agents-split");
  const side = el("div", "agents-side");
  const list = el("div", "agents");
  const add = el("button", "choice on", "+ Add system");
  add.type = "button";
  const detail = el("div", "agents-detail");
  add.addEventListener("click", () => { agentSel = "add"; addType = ""; add_agent(detail, list); draw_agent_list(list, lastAgents, detail); });
  // "Update agents automatically": an older agent installed as a service updates itself when it dials in
  const auto = el("button", "choice agent-auto");
  auto.type = "button";
  auto.addEventListener("click", () => agents_post("auto", { on: !agentsAuto }).then((r) => {
    if (!r.ok) return show_message(r.error || "Refused.");
    agentsAuto = r.auto_update;
    draw_auto(auto);
  }));
  side.append(list, add, auto);
  split.append(side, detail);
  s.appendChild(split);
  load_agents(list, false, detail);
}

let agentsWatch = null;
let lastAgents = [];
let agentsRelease = "", agentsAuto = false;   // the agent release labhud pins, and ⚙'s automatic updates
let agentsWindow = { days: [0, 1, 2, 3, 4, 5, 6], at: "04:00" };   // when the containers "on their own" are updated
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function draw_auto(b) {
  if (!b) return;
  b.classList.toggle("on", agentsAuto);
  b.textContent = (agentsAuto ? "✓ " : "") + "Update agents automatically";
  b.title = agentsRelease ? `An agent older than ${agentsRelease}, installed as a service, updates itself when it dials in` : "";
}
/** The list (and the open agent), drawn again every 10 s while ⚙ is open: online, last seen,
    new targets. A redraw waits while a tick is on its way or the filter is being typed in. */
function load_agents(list, quiet, detail) {
  clearTimeout(agentsWatch);
  // the first draw comes before ⚙ is shown; the refreshes stop once it is closed
  if (!list || !list.isConnected || (quiet && $("#settings").hidden)) return;
  agents_post("list").then((r) => {
    agentsWatch = setTimeout(() => load_agents(list, true, detail), agentSel === "add" ? 3000 : 10000);
    if (!r.ok) {
      list.textContent = "";
      const say = (m) => list.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not list the agents.");
    }
    const busy = detail.querySelector(".pending") || (document.activeElement && detail.contains(document.activeElement)
      && document.activeElement.tagName === "INPUT");
    if (quiet && busy) return;
    lastAgents = r.agents;
    agentsRelease = r.release || "";
    agentsAuto = !!r.auto_update;
    agentsWindow = r.window || agentsWindow;
    draw_auto(list.parentNode && list.parentNode.querySelector(".agent-auto"));
    if (agentSel !== "add" && !r.agents.some((a) => a.id === agentSel)) agentSel = r.agents.length ? r.agents[0].id : null;
    draw_agent_list(list, r.agents, detail);
    if (agentSel === "add") return;
    detail.textContent = "";
    const a = r.agents.find((x) => x.id === agentSel);
    if (a) agent_detail(a, detail, () => load_agents(list, false, detail));
    else detail.appendChild(el("p", "hint", "No agent yet: Add an agent, on the left."));
  });
}

function draw_agent_list(list, agents, detail) {
  list.textContent = "";
  for (const a of agents) {
    const b = el("button", "agent-item" + (a.id === agentSel ? " on" : ""));
    b.type = "button";
    const name = el("span", "agent-name");
    name.append(el("span", "dot" + (a.online ? " up" : "")), document.createTextNode(a.name));
    const n = Object.entries(a.targets || {}).filter(([c]) => a.caps.includes(c) && c !== "host")
      .reduce((t, [, v]) => t + v.length, 0);
    const facts = [a.caps.length ? a.caps.map((c) => (CAP_TEXT[c] || [c])[0]).join(", ") : "nothing ticked",
      n ? n + " targets" : ""].filter(Boolean).join(" · ");
    b.append(name, el("span", "agent-facts", facts));
    b.addEventListener("click", () => {
      agentSel = a.id;
      draw_agent_list(list, agents, detail);
      detail.textContent = "";
      agent_detail(a, detail, () => load_agents(list, false, detail));
    });
    list.appendChild(b);
  }
}

/** "Update to vX": labhud names the release it pins; the agent downloads and checks it itself. */
function agent_update_row(a, box, reload) {
  if (a.update) {
    const row = el("div", "agent-update");
    row.appendChild(el("span", null, `v${a.update} is out (this one is v${a.version}).`));
    const b = el("button", "choice on", "Update to v" + a.update);
    b.type = "button";
    b.disabled = !a.online;
    b.addEventListener("click", () => {
      put($("#confirm-text"), `Update ${a.name} to v${a.update}? It downloads the release from GitHub, checks it, `
        + "and starts again in a few seconds.");
      ask([["UPDATE", () => {
        b.disabled = true;
        b.classList.add("pending");
        b.textContent = "updating…";
        agents_post("update", { id: a.id }).then((r) => {
          show_message(r.ok ? `${a.name} is updating to v${a.update}.` : (r.error || "Refused."));
          setTimeout(reload, r.ok ? 8000 : 0);
        });
      }, false]]);
    });
    row.appendChild(b);
    box.appendChild(row);
  } else if (agentsRelease && a.version && a.version !== agentsRelease && !a.install) {
    box.appendChild(el("p", "hint", `v${agentsRelease} is out. This agent is too old to update itself: run its install line `
      + "again (+ Add system), without the code; from then on it updates from here."));
  } else if (agentsRelease && a.version && a.version !== agentsRelease && a.install === "container") {
    box.appendChild(el("p", "hint", "In a container the image is the agent: your image updater (or a new docker run) updates it."));
  }
}

function agent_detail(a, box, reload) {
  const head = el("div", "device");
  const text = el("div", "device-text");
  const name = el("span", "device-name");
  name.append(el("span", "dot" + (a.online ? " up" : "")), document.createTextNode(" " + a.name));
  const facts = [a.online ? "online" : "offline, seen " + ago(a.seen), a.version && "v" + a.version,
    a.host && a.host.os].filter(Boolean).join(" · ");
  text.append(name, el("span", "device-seen", facts));
  const rm = el("button", "choice danger", "Remove");
  rm.type = "button";
  rm.addEventListener("click", () => {
    put($("#confirm-text"), `Remove ${a.name}? Its key ends at once and the agent stops; it needs a new code to come back.`);
    ask([["REMOVE", () => agents_post("remove", { id: a.id }).then((r) => {
      if (!r.ok) show_message(r.error || "Refused.");
      agentSel = null;
      reload();
    }), true]]);
  });
  head.append(text, rm);
  box.appendChild(head);
  agent_update_row(a, box, reload);

  const caps = a.found.filter((c) => CAP_TEXT[c]);
  if (!caps.length) { box.appendChild(el("p", "hint", "It found nothing it could act on (sensors only).")); return; }
  box.appendChild(el("h4", null, "What it may do"));
  const capRow = el("div", "choices");
  for (const c of caps) {
    const on = a.caps.includes(c);
    const b = el("button", "choice" + (on ? " on" : ""), CAP_TEXT[c][0]);
    b.type = "button";
    b.title = CAP_TEXT[c][1];
    b.addEventListener("click", () => {
      b.classList.add("pending");
      const next = on ? a.caps.filter((x) => x !== c) : [...a.caps, c];
      if (!on) agentCap[a.id] = c;   // ticked: open its targets
      agents_post("caps", { id: a.id, caps: next }).then((r) => {
        if (!r.ok) show_message(r.error || "Refused.");
        // the host has one target, itself: ticking the capability ticks it too
        if (r.ok && c === "host" && !on) return agents_post("targets", { id: a.id, cap: "host", targets: ["host"] });
      }).then(reload);
    });
    capRow.appendChild(b);
  }
  box.appendChild(capRow);

  // the ticked capabilities that have targets, as tabs; the open one's targets across the width
  const withTargets = caps.filter((c) => a.caps.includes(c) && c !== "host" && c !== "scan");   // scan reads docker's
  if (!withTargets.length) return;
  const cur = withTargets.includes(agentCap[a.id]) ? agentCap[a.id] : withTargets[0];
  box.appendChild(el("h4", null, "On what"));
  const tabs = el("div", "cap-tabs");
  for (const c of withTargets) {
    const all = new Set([...((a.targets || {})[c] || []), ...((a.choices || {})[c] || [])]);
    const t = el("button", "cap-tab" + (c === cur ? " on" : ""), `${CAP_TEXT[c][0]} ${((a.targets || {})[c] || []).length}/${all.size}`);
    t.type = "button";
    t.addEventListener("click", () => { agentCap[a.id] = c; box.textContent = ""; agent_detail(a, box, reload); });
    tabs.appendChild(t);
  }
  box.appendChild(tabs);
  box.appendChild(agent_targets(a, cur, reload));
  if (cur === "docker") {
    // updating on their own, webhooks and the clean-up: ⚙ > Containers, every agent's at once
    const more = el("button", "choice small", "Updates, webhooks, clean-up › Containers");
    more.type = "button";
    more.addEventListener("click", () => settings_go("containers"));
    box.appendChild(more);
  }
}

/** ⚙ > Containers: every agent with Docker ticked, its ticked containers, what updates them on
    their own (in one window for all), the webhooks for CI and the clean-up of unused images. */
function containers_settings(ctx) {
  const win = ctx.section("Update window", { about: "When the containers ticked below update on their own: "
    + "one after the other, as UPDATE does (the old container comes back if the new one does not stay up)." });
  const host = el("div", "ctr-hosts");
  ctx.body.appendChild(host);
  host.appendChild(el("p", "hint", "Reading the agents…"));
  agents_post("list").then((r) => {
    host.textContent = "";
    if (!r.ok) {
      const say = (m) => host.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not list the agents.");
    }
    lastAgents = r.agents;
    agentsWindow = r.window || agentsWindow;
    agent_window_row(win, () => open_settings());
    const docker = r.agents.filter((a) => a.caps.includes("docker"));
    for (const a of r.agents.filter((x) => x.caps.includes("stacks"))) {
      const s = ctx.section(a.name + " · stacks", { wide: true, about: `${Object.keys(a.stacks || {}).length} stack(s) kept by this agent`
        + (a.online ? "" : " · offline, seen " + ago(a.seen)) });
      agent_stacks_row(a, s);
      host.appendChild(s);
    }
    if (!docker.length && !r.agents.some((x) => x.caps.includes("stacks"))) {
      const s = ctx.section("No containers yet", { wide: true, about: "No agent may act on Docker: tick Docker for one in Hosts & agents." });
      const go = el("button", "choice", "Hosts & agents");
      go.type = "button";
      go.addEventListener("click", () => settings_go("hosts"));
      s.appendChild(go);
      return;
    }
    for (const a of docker) {
      const ticked = ((a.targets || {}).docker || []).filter((x) => x !== "images");
      const s = ctx.section(a.name, { wide: true, about: `${ticked.length} container${ticked.length === 1 ? "" : "s"} ticked`
        + (a.online ? "" : " · offline, seen " + ago(a.seen)) });
      s.classList.add("ctr-host");
      const reload = () => open_settings();
      if (!ticked.length) s.appendChild(el("p", "hint", "None ticked: Hosts & agents › " + a.name + " › Docker."));
      agent_auto_row(a, s, reload);
      agent_hook_row(a, s, reload);
      if (a.prune) agent_prune_row(a, s);
      host.appendChild(s);
    }
  });
}

/** Compose stacks of an agent with `stacks` ticked: the list, a button for each action, and an
    editor (the compose file, the .env) that deploys. The .env's values are never read back: the
    editor shows the names, and what is typed there replaces the host's .env (empty keeps it). */
function agent_stacks_row(a, box) {
  const list = el("div", "stacks");
  const out = el("p", "hint", "");
  const editor = el("div", "stack-editor");
  editor.hidden = true;
  const name = el("input", "agent-filter");
  name.placeholder = "stack name (lowercase letters, digits, - _)";
  const compose = el("textarea", "stack-text");
  compose.placeholder = "compose.yaml";
  compose.spellcheck = false;
  // or the compose file from a git repository: the server fetches it; the token is used once and never kept
  const repo = el("input", "agent-filter");
  repo.placeholder = "or from git: https://git.example/owner/name";
  const branch = el("input", "agent-filter");
  branch.placeholder = "branch (main)";
  const gpath = el("input", "agent-filter");
  gpath.placeholder = "path in the repository, e.g. web/compose.yaml";
  const token = el("input", "agent-filter");
  token.type = "password";
  token.autocomplete = "off";
  token.placeholder = "token, for a private repository (not saved)";
  const env = el("textarea", "stack-text stack-env");
  env.placeholder = ".env: KEY=value lines (empty keeps the one on the host)";
  env.spellcheck = false;
  const noenv = el("label", "hint");
  const noenv_box = el("input");
  noenv_box.type = "checkbox";
  noenv.append(noenv_box, " remove the .env");
  const go = el("button", "choice", "DEPLOY");
  go.type = "button";
  const note = el("span", "hint", "");
  const run = (verb, nm, extra) => {
    out.textContent = verb + " " + nm + "…";
    return setup_post("stack-run", Object.assign({ agent: a.name, verb, name: nm }, extra || {})).then((r) => {
      out.textContent = (r.ok ? "✓ " : "✕ " + (r.error || "failed") + "\n") + (r.output || "");
      out.style.whiteSpace = "pre-wrap";
      return r;
    });
  };
  go.addEventListener("click", () => {
    const nm = name.value.trim();
    const from_git = repo.value.trim() !== "";
    if (!nm || (from_git ? !gpath.value.trim() : !compose.value.trim())) return put(note, from_git ? "a name, the repository and the path" : "a name and a compose file");
    put($("#confirm-text"), `Deploy the stack ${nm} on ${a.name}${from_git ? " from " + repo.value.trim() : ""}? A compose file can do anything the Docker socket can.`);
    const source = from_git ? { git: { repo: repo.value.trim(), branch: branch.value.trim() || "main", path: gpath.value.trim(), token: token.value || undefined } } : { compose: compose.value };
    ask([["DEPLOY", () => run("deploy", nm, Object.assign(source, { env: noenv_box.checked ? "" : env.value ? env.value : undefined }))
      .then((r) => { if (r.ok) { env.value = ""; token.value = ""; stacks_load(); } })]]);
  });
  editor.append(name, compose, repo, branch, gpath, token, env, noenv, go, note);
  const edit = (nm) => {
    editor.hidden = false;
    name.value = nm;
    compose.value = "";
    env.value = "";
    repo.value = branch.value = gpath.value = token.value = "";
    noenv_box.checked = false;
    if (!nm) return;
    setup_post("stack-run", { agent: a.name, verb: "show", name: nm }).then((r) => {
      if (!r.ok) return put(note, "✕ " + (r.error || "failed"));
      const [text, names] = r.output.split("\n# .env: ");
      compose.value = text;
      put(note, names ? ".env on the host has: " + names.trim() : "no .env on the host");
    });
  };
  const draw = (stacks) => {
    list.replaceChildren();
    const names = Object.keys(stacks).sort();
    if (!names.length) list.appendChild(el("p", "hint", a.online ? "No stack yet: add one below." : "Shown when the agent is online."));
    for (const nm of names) {
      const x = stacks[nm];
      const row = el("div", "choices");
      row.appendChild(el("strong", null, nm));
      row.appendChild(el("span", "hint", ` ${x.running}/${x.services} running`));
      const btn = (label, fn, danger) => {
        const b = el("button", "choice small", label);
        b.type = "button";
        b.addEventListener("click", fn);
        row.appendChild(b);
      };
      btn("EDIT", () => edit(nm));
      btn("REDEPLOY", () => { put($("#confirm-text"), `Pull and redeploy ${nm}?`); ask([["REDEPLOY", () => run("redeploy", nm).then(stacks_load)]]); });
      btn("RESTART", () => { put($("#confirm-text"), `Restart ${nm}?`); ask([["RESTART", () => run("restart", nm).then(stacks_load)]]); });
      btn("DOWN", () => { put($("#confirm-text"), `Take ${nm} down? Its containers go, its volumes stay.`); ask([["DOWN", () => run("down", nm).then(stacks_load)]]); });
      list.appendChild(row);
    }
  };
  function stacks_load() {
    setup_post("stacks", { agent: a.name }).then((r) => { if (r.ok) draw(r.stacks); });
  }
  draw(a.stacks || {});
  const add = el("button", "choice", "New stack");
  add.type = "button";
  add.addEventListener("click", () => edit(""));
  box.append(list, add, editor, out);
}

/** Webhooks: a secret URL per container that CI calls after it pushed a new image (POST), and
    labhud updates that container. The URL is shown once; a new one replaces it. */
function agent_hook_row(a, box, reload) {
  const ticked = ((a.targets || {}).docker || []).filter((x) => x !== "images");
  if (!ticked.length) return;
  const hooks = a.hooks || [];
  box.appendChild(el("h4", null, "Webhooks"));
  box.appendChild(el("p", "hint", "For CI: a POST to the container's URL updates it, as UPDATE does (at most once in 30 s). "
    + "The URL is the password: it is shown once, and a new one replaces it."));
  const grid = el("div", "choices targets");
  for (const x of ticked) {
    const on = hooks.includes(x);
    const b = el("button", "choice" + (on ? " on" : ""), x);
    b.type = "button";
    b.addEventListener("click", () => {
      const make = () => agents_post("hook", { id: a.id, target: x }).then((r) => {
        if (!r.ok) return show_message(r.error || "Refused.");
        put($("#confirm-text"), `${x}'s webhook (copy it now, it is not shown again):\n\ncurl -fsSk -X POST ${r.url}`);
        ask([]);
        reload();
      });
      if (!on) return make();
      put($("#confirm-text"), `${x} has a webhook. A new URL stops the old one.`);
      ask([["NEW URL", make], ["REMOVE", () => agents_post("unhook", { id: a.id, target: x }).then(reload), true]]);
    });
    grid.appendChild(b);
  }
  box.appendChild(grid);
}

/** "Update on their own": the ticked containers labhud updates by itself in the window, when their
    image has a newer one; the window (days, time) is the same for every agent. */
function agent_auto_row(a, box, reload) {
  const ticked = ((a.targets || {}).docker || []).filter((x) => x !== "images");
  if (!ticked.length) return;
  const auto = a.auto || [];
  box.appendChild(el("h4", null, "Update on their own"));
  const w = agentsWindow;
  const when = w.days.length === 7 ? "every day" : w.days.map((d) => DAY_NAMES[d]).join(", ");
  box.appendChild(el("p", "hint", `When their registry has a newer image: ${when} at ${w.at}.`));
  const grid = el("div", "choices targets");
  for (const x of ticked) {
    const on = auto.includes(x);
    const b = el("button", "choice" + (on ? " on" : ""), x);
    b.type = "button";
    b.addEventListener("click", () => {
      b.classList.add("pending");
      agents_post("autoupdate", { id: a.id, targets: on ? auto.filter((y) => y !== x) : [...auto, x] })
        .then((r) => { if (!r.ok) show_message(r.error || "Refused."); reload(); });
    });
    grid.appendChild(b);
  }
  box.appendChild(grid);
}

/** The update window (days, time): the same for every agent. */
function agent_window_row(box, reload) {
  const w = agentsWindow;
  const row = el("div", "choices");
  const days = new Set(w.days);
  DAY_NAMES.forEach((name, d) => {
    const b = el("button", "choice small" + (days.has(d) ? " on" : ""), name);
    b.type = "button";
    b.addEventListener("click", () => { if (days.has(d)) days.delete(d); else days.add(d); b.classList.toggle("on", days.has(d)); });
    row.appendChild(b);
  });
  const at = el("input", "agent-at");
  at.type = "time";
  at.value = w.at;
  const save = el("button", "choice small", "Save");
  save.type = "button";
  save.addEventListener("click", () => agents_post("window", { days: [...days].sort(), at: at.value }).then((r) => {
    if (!r.ok) return show_message(r.error || "Refused.");
    agentsWindow = r.window;
    show_message("✓ saved");
    reload();
  }));
  row.append(at, save);
  box.appendChild(row);
}

/** "images" ticked under Docker: the host's Docker. Nothing is asked until the admin opens it;
    then the images, volumes and networks with their sizes, and a button for each cleaning. */
const CLEANINGS = [
  ["images", "Unused images", "Remove the images no container uses, unused for a day at least. An image a stopped container uses stays."],
  ["dangling", "Untagged layers", "Remove the image layers no tag points to."],
  ["networks", "Unused networks", "Remove the networks no container is on (made a day ago at least). The built-in ones stay."],
  ["volumes", "Anonymous volumes", "Remove the volumes with no name that no container uses. A volume with a name is never removed."],
];

function agent_prune_row(a, box) {
  const wrap = el("div", "docker-host");
  const head = el("div", "choices");
  const open = el("button", "choice", "Docker host: images, volumes, networks");
  open.type = "button";
  const note = el("span", "hint", "Sizes and what uses them, read when you open it.");
  head.append(open, note);
  const body = el("div", "docker-host-body");
  body.hidden = true;
  open.addEventListener("click", () => {
    body.hidden = !body.hidden;
    if (!body.hidden) host_load(a, body, note);
  });
  wrap.append(head, body);
  box.appendChild(wrap);
}

function host_load(a, body, note, fresh) {
  put(note, "reading…");
  return setup_post("inventory", { agent: a.name, fresh: !!fresh }).then((r) => {
    body.replaceChildren();
    if (!r.ok) return put(note, "✕ " + (r.error || "failed"));
    put(note, `${r.images.length} images (${bytes(r.totals.images)}, ${bytes(r.totals.unused_images)} unused) · ` +
      `${r.volumes.length} volumes (${bytes(r.totals.volumes)}) · ${r.networks.length} networks`);
    const table = (title, head, rows) => {
      body.appendChild(el("h4", null, title));
      const t = el("table", "host-table");
      const tr = el("tr");
      head.forEach((h) => tr.appendChild(el("th", null, h)));
      t.appendChild(tr);
      rows.forEach((cells) => {
        const row = el("tr");
        cells.forEach((c) => row.appendChild(el("td", null, c)));
        t.appendChild(row);
      });
      body.appendChild(t);
    };
    table("Images", ["Image", "Size", "Used by"], r.images.map((i) =>
      [i.tags.join(", ") || `(untagged) ${i.id}`, bytes(i.size), i.used ? i.used + " containers" : "unused"]));
    table("Volumes", ["Volume", "Size", "Used by"], r.volumes.map((v) =>
      [v.anonymous ? `(anonymous) ${v.name.slice(0, 12)}` : v.name, v.size < 0 ? "—" : bytes(v.size), v.used ? v.used + " containers" : "unused"]));
    table("Networks", ["Network", "Driver", "Containers"], r.networks.map((n) =>
      [n.name + (n.builtin ? " (built in)" : ""), n.driver, String(n.used)]));
    if (Object.keys(r.more || {}).length) body.appendChild(el("p", "hint", "More than shown: " + Object.entries(r.more).map(([k, v]) => `${v} ${k}`).join(", ") + "."));
    const row = el("div", "choices");
    const out = el("span", "hint", "");
    CLEANINGS.forEach(([what, label, text]) => {
      const b = el("button", "choice", "Clean: " + label);
      b.type = "button";
      b.addEventListener("click", () => {
        put($("#confirm-text"), `${text} On ${a.name}.`);
        ask([["CLEAN", () => {
          b.disabled = true;
          put(out, "cleaning…");
          setup_post("prune", { agent: a.name, what }).then((c) => {
            b.disabled = false;
            put(out, c.ok ? "✓ " + c.output : "✕ " + (c.error || "failed"));
            if (c.ok) host_load(a, body, note, true).then(() => put(note, "✓ " + c.output + " · " + note.textContent));
          });
        }]]);
      });
      row.appendChild(b);
    });
    body.append(row, out);
  });
}

function agent_targets(a, c, reload) {
  const ticked = (a.targets || {})[c] || [];
  const all = [...new Set([...ticked, ...((a.choices || {})[c] || [])])];
  const t = el("div", "agent-targets");
  t.appendChild(el("p", "hint", CAP_TEXT[c][1] + "."));
  if (!all.length) t.appendChild(el("p", "hint", a.online ? "Nothing found yet." : "Shown when the agent is online."));
  const tools = el("div", "target-tools");
  if (all.length > 12) {   // a long list (containers, services, the ARP table): a filter
    const f = el("input", "agent-filter");
    f.placeholder = `Filter ${all.length}…`;
    f.addEventListener("input", () => {
      const q = f.value.trim().toLowerCase();
      for (const b of t.querySelectorAll(".targets .choice")) b.hidden = !!q && !b.textContent.toLowerCase().includes(q);
    });
    tools.appendChild(f);
  }
  if (all.length > 1) {   // a host with 28 containers: one tap instead of 28
    const every = ticked.filter((x) => !(c === "docker" && x === "images")).length === all.filter((x) => !(c === "docker" && x === "images")).length;
    const b = el("button", "choice small", every ? "Untick all" : `Tick all ${all.filter((x) => !(c === "docker" && x === "images")).length}`);
    b.type = "button";
    b.addEventListener("click", () => {
      b.classList.add("pending");
      // "images" (docker's clean-up) is never ticked by the way: on its own button only
      const keep = c === "docker" && ticked.includes("images") ? ["images"] : [];
      agents_post("targets", { id: a.id, cap: c, targets: every ? [] : [...all.filter((x) => !(c === "docker" && x === "images")), ...keep] })
        .then((r) => { if (!r.ok) show_message(r.error || "Refused."); reload(); });
    });
    tools.appendChild(b);
  }
  if (tools.children.length) t.appendChild(tools);
  const grid = el("div", "choices targets");
  for (const x of all) {
    const on = ticked.includes(x);
    const label = ((a.labels || {})[c] || {})[x];
    const b = el("button", "choice" + (on ? " on" : ""), label && label.toLowerCase().includes(x) ? label : label ? `${label} (${x})` : x);
    b.type = "button";
    b.addEventListener("click", () => {
      b.classList.add("pending");
      agents_post("targets", { id: a.id, cap: c, targets: on ? ticked.filter((y) => y !== x) : [...ticked, x] })
        .then((r) => { if (!r.ok) show_message(r.error || "Refused."); reload(); });
    });
    grid.appendChild(b);
  }
  t.appendChild(grid);
  // a machine that is off is not in the ARP table: its MAC can be typed in
  if (c === "wol") {
    const f = el("form", "agent-add-mac");
    const mac = el("input", "agent-filter");
    mac.placeholder = "Add a MAC (aa:bb:cc:dd:ee:ff)";
    mac.autocomplete = "off";
    const go = el("button", "choice", "Add");
    go.type = "submit";
    f.append(mac, go);
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      const x = mac.value.trim().toLowerCase().replace(/-/g, ":");
      if (!/^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/.test(x)) return show_message("Not a MAC address.");
      if (ticked.includes(x)) return;
      agents_post("targets", { id: a.id, cap: c, targets: [...ticked, x] })
        .then((r) => { if (!r.ok) show_message(r.error || "Refused."); reload(); });
    });
    t.appendChild(f);
  }
  return t;
}

/** A one-time code with the two ways to install, counting down its 5 minutes. */
/** "+ Add system": what kind of host, then the right command first, and once the agent dials in
 * the capabilities that kind of host uses are ticked (its targets still are not: nothing can be
 * pressed until you tick them). [key, title, what it gives, capabilities, the command first] */
const SYSTEM_TYPES = [
  ["proxmox", "Proxmox node", "The node itself: its VMs and LXCs (start, shut down, reboot), sensors, updates, waking other machines.",
    ["sensors", "host", "pve", "wol"], "service"],
  ["docker", "Docker host", "A VM or machine with containers: start, stop and restart them, sensors, updates.",
    ["sensors", "host", "docker"], "docker"],
  ["linux", "Linux server", "Any other machine: sensors, its services (systemd), updates, reboot.", ["sensors", "host", "systemd"], "service"],
  ["ptero", "Pterodactyl node", "Game servers through the panel, plus the node's own sensors.", ["sensors", "host", "ptero"], "service"],
  ["phone", "Phone", "The LabHUD app on a phone or tablet: a QR code to scan.", null, null],
];
let addType = "";

function add_agent(show, list) {
  show.textContent = "";
  if (!addType) {
    show.appendChild(el("p", null, "What are you adding?"));
    const grid = el("div", "sys-types");
    for (const [key, title, about] of SYSTEM_TYPES) {
      const b = el("button", "sys-type");
      b.type = "button";
      b.append(el("b", null, title), el("span", null, about));
      b.addEventListener("click", () => { addType = key; add_agent(show, list); });
      grid.appendChild(b);
    }
    show.appendChild(grid);
    return;
  }
  const type = SYSTEM_TYPES.find((t) => t[0] === addType);
  const back = el("button", "choice small", "‹ Another kind");
  back.type = "button";
  back.addEventListener("click", () => { addType = ""; add_agent(show, list); });
  show.appendChild(back);
  show.appendChild(el("p", null, type[1]));
  if (addType === "phone") {
    const wrap = el("div");
    const qr = el("div", "pair-qr");
    const devs = el("div", "devices");
    devs.hidden = true;
    wrap.append(qr, devs);
    show.appendChild(wrap);
    load_devices(devs);
    pair_phone(qr);
    return;
  }
  const wait = el("p", "hint");
  show.appendChild(wait);
  put(wait, "Asking LabHUD…");
  agents_post("code").then((r) => {
    wait.remove();
    if (!r.ok) {
      const say = (m) => show.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Refused.");
    }
    const before = list.querySelectorAll(".agent-item").length;
    show.appendChild(el("p", "hint", "The code:"));
    show.appendChild(copy_line(r.code));
    // a Docker host: the container first, where there is an image to run
    if (type[4] === "docker" && r.docker) {
      show.appendChild(el("p", "hint", "On the host, as a container (it sees the host's Docker, sensors and network):"));
      show.appendChild(copy_line(r.docker, "agent-line"));
      show.appendChild(el("p", "hint", "Or as a systemd service:"));
    }
    if (r.released) {
      show.appendChild(el("p", "hint", `On the host, as root: it downloads labhud-agent ${r.version} (${r.builds.join(", ")}), `
        + "checks it against the SHA-256 this LabHUD pins and installs it as a systemd service."));
      show.appendChild(copy_line(r.install, "agent-line"));
    } else {
      show.appendChild(el("p", "hint", `This LabHUD pins no labhud-agent release yet, so the installer cannot download one. `
        + "Build the agent (see its README) or copy the binary to the host as ./labhud-agent, then, as root, run this with "
        + "YOUR_SHA256 replaced by the checksum you got where you built it (not one worked out on the host from the same file):"));
      show.appendChild(copy_line(r.install, "agent-line"));
    }
    if (r.docker && type[4] !== "docker") {
      show.appendChild(el("p", "hint", "Or as a container:"));
      show.appendChild(copy_line(r.docker, "agent-line"));
    }
    const left = el("p", "hint");
    show.appendChild(left);
    const until = Date.now() + r.minutes * 60000;
    const tick = () => {
      if (!show.isConnected) return;
      const s = Math.round((until - Date.now()) / 1000);
      if (s <= 0) { show.textContent = ""; show.appendChild(el("p", "hint", "The code expired: make a new one.")); return; }
      put(left, `⏳ Waiting for the agent… The code works once, for ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} more.`);
      if (list.querySelectorAll(".agent-item").length > before) {
        // paired: tick what this kind of host uses (of what it found), then open it for the targets
        const newest = [...lastAgents].sort((x, y) => (y.paired || 0) - (x.paired || 0))[0];
        agentSel = newest ? newest.id : null;
        const caps = newest ? type[3].filter((c) => (newest.found || []).includes(c)) : [];
        const done = () => { toast(`✓ ${newest ? newest.name : "The agent"} is in: tick what it may act on`); load_agents(list, false, show); };
        if (newest && caps.length) agents_post("caps", { id: newest.id, caps }).then(done); else done();
        return;
      }
      setTimeout(tick, 1000);
    };
    tick();
  });
  touched();
}

// --- "?": the same step from a terminal, with the `labhud` command (cli.py) ---

const CLI_HELP = {
  "Pages and groups · every screen": ["page list", "page move <card> <page>[/<group>]", "organize <page>"],
  "Agents": ["agent list", "agent code", "agent allow <agent> <cap> [target … | --all]", "agent deny <agent> <cap> [target …]",
    "agent logs <agent> <container>"],
  "Phones": ["phone pair [name]", "phone list", "phone remove <name>"],
  "Sources": ["source list", "source test <name> [KEY=value …]", "source add <name> [KEY=value …] [--page <page>]", "source remove <name>"],
  "Scan my network": ["scan [<cidr>]"],
  "Alert rules": ["alert list", "alert add <card|*> <cpu|mem|disk|temp|offline|failed> [above] [minutes]", "alert remove <number>"],
  "Notifications": ["notify set <url> [--token T]", "notify test [<url>] [--token T]", "notify forget"],
  "Config file": ["status", "config take-over"],
  "Edit config": ["config show", "config edit"],
  "Admin": ["admin"],
  "Lock": ["pin", "pin --clear"],
};
const CLI_OPEN = new Set();   // the sections whose "?" is open, across redraws of ⚙

function cli_help(s, h, title) {
  const q = el("button", "cli-q" + (CLI_OPEN.has(title) ? " on" : ""), "?");
  q.type = "button";
  q.title = "The same from a terminal";
  h.appendChild(q);
  const box = el("div", "cli-help");
  box.appendChild(el("p", "hint", "From a terminal on labhud's host (labhud = the container's name):"));
  for (const c of CLI_HELP[title]) box.appendChild(copy_line(`docker exec -it labhud labhud ${c}`));
  box.hidden = !CLI_OPEN.has(title);
  s.appendChild(box);
  q.addEventListener("click", () => {
    if (CLI_OPEN.has(title)) CLI_OPEN.delete(title); else CLI_OPEN.add(title);
    box.hidden = !CLI_OPEN.has(title);
    cls(q, "on", CLI_OPEN.has(title));
  });
}
