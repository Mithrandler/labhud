/* Settings of THIS screen (the ⚙ button): theme, accent, font, text size, page rotation, night
   and calm. They live in the screen's own browser (localStorage), so the wall can stay OLED black
   while a phone uses grey, and nothing on the server changes. What a screen does not set comes
   from config.toml, as before.

   Loaded before app.js, so the look is applied before the first frame (no flash of the default
   theme). The panel itself is built on demand and uses app.js's helpers ($, el, put, CFG...),
   which exist by the time anyone can press the button. */

const SETTINGS_KEY = "labhud-settings";

/** Background bases: the colours behind the cards. */
const BASES = {
  oled: { label: "OLED black", vars: {} },   // the defaults in app.css
  grey: { label: "Dark grey", vars: { "--bg": "#16181d", "--card": "#1e2128", "--block": "#272b33",
                                      "--line": "#333844", "--border": "#454c59" } },
};

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
  base: "oled", accent: "blue", font: "roboto", scale: 100,
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
  calm_show: ["weather"],
  tab: "display",   // the ⚙ tab last open   // under the calm clock: "weather" and/or ids of cards with a list
};

let S = load_settings();

function load_settings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    return { ...DEFAULTS, ...(saved && typeof saved === "object" ? saved : {}) };
  } catch (e) {
    return { ...DEFAULTS };   // private window, blocked storage: the defaults, every time
  }
}

function save_settings() {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(S)); } catch (e) { /* not kept, still applied */ }
}

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
  const base = BASES[S.base] || BASES.oled;
  for (const name of ["--bg", "--card", "--block", "--line", "--border"]) {
    if (base.vars[name]) root.setProperty(name, base.vars[name]); else root.removeProperty(name);
  }
  const [accent, title] = accent_colours(S.accent);
  if (S.accent === "blue") { root.removeProperty("--accent"); root.removeProperty("--title"); }
  else { root.setProperty("--accent", accent); root.setProperty("--title", title); }
  const font = FONTS[S.font] || FONTS.roboto;
  if (S.font === "roboto") root.removeProperty("--font"); else root.setProperty("--font", font[1]);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = base.vars["--bg"] || "#000000";
  for (const d of ["compact", "airy"]) document.body.classList.toggle("density-" + d, S.density === d);
}

/** Left off this screen from ⚙: a whole group, or one card. mount() in app.js skips them, and a
    hidden card's new problem does not take the screen. */
function is_hidden(groupId, cardId) {
  return S.hidden.includes("g:" + groupId) || (cardId != null && S.hidden.includes("c:" + cardId));
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

function open_settings() {
  touched();
  // ⚙ is the admin's (account.js): a display that is not signed in only shows
  if (!signed_in()) return open_signin();
  if (locked()) return lock_unlock().then((open) => open && open_settings());
  const box = $("#settings");
  const body = $("#settings-body");
  const keep = box.hidden ? 0 : body.scrollTop;   // a choice redraws the panel: stay where you were
  if (box.hidden) { recheck_admin(); check_admin(); }
  body.textContent = "";

  const tab = settings_tab_key(S.tab);
  settings_tabs(tab);
  body.className = "tab-" + tab;
  const elsewhere = el("div");   // the other tabs' sections are drawn here and never shown
  const section = (title, where = "look", wide = false) => {
    const s = el("section", "set" + (wide ? " wide" : ""));
    s.appendChild(el("h3", null, title));
    (where === tab ? body : elsewhere).appendChild(s);
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
  const changed = (fn) => { fn(); save_settings(); apply_look(); };

  // --- look ---
  const look = section("Theme");
  choices(look, Object.entries(BASES).map(([k, v]) => [k, v.label]), S.base, (v) => changed(() => { S.base = v; }));
  const acc = choices(look, Object.keys(ACCENTS).map((k) => [k, k[0].toUpperCase() + k.slice(1), ACCENTS[k][1]]), S.accent,
    (v) => changed(() => { S.accent = v; }));
  const custom = el("label", "choice swatch custom" + (ACCENTS[S.accent] ? "" : " on"));
  const picker = el("input");
  picker.type = "color";
  picker.value = accent_colours(S.accent)[1];
  picker.addEventListener("input", () => changed(() => { S.accent = picker.value; }));
  picker.addEventListener("change", () => open_settings());
  custom.append(picker, el("span", null, "custom"));
  acc.appendChild(custom);

  const type = section("Text");
  choices(type, Object.entries(FONTS).map(([k, v]) => [k, v[0]]), S.font, (v) => changed(() => { S.font = v; }));
  const size = el("div", "slider");
  const range = el("input");
  range.type = "range"; range.min = "85"; range.max = "130"; range.step = "5"; range.value = String(Math.round(ui_scale() * 100));
  const shown = el("span", "val", range.value + "%");
  range.addEventListener("input", () => { put(shown, range.value + "%"); });
  range.addEventListener("change", () => { S.scale = Number(range.value); save_settings(); mount(); });
  size.append(el("span", "lbl", "Size"), range, shown);
  type.appendChild(size);
  type.appendChild(el("p", "hint", "A page that does not fit at this size still shrinks until it does."));

  // --- pages: the rotation, and what this screen shows, one column per page ---
  const rot = section("Page rotation", "pages");
  choices(rot, [[true, "On"], [false, "Off"]], S.rotate, (v) => { S.rotate = v; save_settings(); });
  choices(rot, [10, 20, 30, 60].map((n) => [n, n + " s"]), S.period, (v) => { S.period = v; save_settings(); });
  rot.appendChild(el("p", "hint", "Which pages rotate: the tick at the top of each page below."));
  if (tab === "pages") page_columns(section("Shown on this screen", "pages", true));

  // --- night and calm ---
  const nt = section("Night", "night");
  const server = CFG.night;
  const night = night_cfg();
  choices(nt, [["config", server ? `As configured (${server.from}:00–${server.to}:00)` : "As configured (off)"], ["on", "On"], ["off", "Off"]],
    S.night ? (S.night.on ? "on" : "off") : "config", (v) => {
      S.night = v === "config" ? null : { on: v === "on", from: (night || server || {}).from ?? 23, to: (night || server || {}).to ?? 7, dim: (night || server || {}).dim ?? 30 };
      save_settings(); apply_night(); render_night();
    });
  if (S.night && S.night.on) {
    const hours = (key) => {
      const sel = el("select");
      for (let h = 0; h < 24; h++) { const o = el("option", null, String(h).padStart(2, "0") + ":00"); o.value = String(h); sel.appendChild(o); }
      sel.value = String(S.night[key]);
      sel.addEventListener("change", () => { S.night[key] = Number(sel.value); save_settings(); render_night(); });
      return sel;
    };
    const win = el("div", "slider");
    win.append(el("span", "lbl", "From"), hours("from"), el("span", "lbl", "to"), hours("to"));
    nt.appendChild(win);
    const dim = el("div", "slider");
    const dr = el("input");
    dr.type = "range"; dr.min = "5"; dr.max = "100"; dr.step = "5"; dr.value = String(S.night.dim);
    const dv = el("span", "val", dr.value + "%");
    dr.addEventListener("input", () => { put(dv, dr.value + "%"); });
    dr.addEventListener("change", () => { S.night.dim = Number(dr.value); save_settings(); apply_night(); });
    dim.append(el("span", "lbl", "Brightness"), dr, dv);
    nt.appendChild(dim);
  }
  choices(nt, [[true, "A new problem lights it up"], [false, "Stays dim"]], S.wake, (v) => { S.wake = v; save_settings(); });

  const cm = section("Calm (only a clock when nothing is red)", "night");
  const calmServer = CFG.calm;
  choices(cm, [["config", calmServer ? `As configured (after ${Math.round(calmServer.after / 60)} min)` : "As configured (off)"], ["on", "On"], ["off", "Off"]],
    S.calm ? (S.calm.on ? "on" : "off") : "config", (v) => {
      S.calm = v === "config" ? null : { on: v === "on", after: (calm_cfg() || calmServer || {}).after || 300 };
      save_settings();
    });
  if (S.calm && S.calm.on) {
    choices(cm, [120, 300, 600, 1800].map((n) => [n, `after ${n / 60} min`]), S.calm.after, (v) => { S.calm.after = v; save_settings(); });
  }
  cm.appendChild(el("p", "hint", "Under the clock (the first line of each):"));
  const under = el("div", "choices");
  const listCards = Object.values(CARD_BY_ID).filter((c) => c.list && !is_guest(c));
  for (const [id, label] of [["weather", "Forecast"], ...listCards.map((c) => [c.id, c.name])]) {
    const on = S.calm_show.includes(id);
    const b = el("button", "choice small" + (on ? " on" : ""), label);
    b.type = "button";
    b.addEventListener("click", () => {
      S.calm_show = on ? S.calm_show.filter((x) => x !== id) : [...S.calm_show, id].slice(-4);
      save_settings(); open_settings();
    });
    under.appendChild(b);
  }
  cm.appendChild(under);

  // --- density of this screen ---
  const lay = section("Density", "look");
  choices(lay, [["compact", "Compact"], ["normal", "Normal"], ["airy", "Airy"]], S.density,
    (v) => { S.density = v; save_settings(); apply_look(); mount(); });
  lay.appendChild(el("p", "hint", "How much room the cards leave around their text."));

  // --- screen on, signal ---
  const scr = section("Screen and alerts", "night");
  choices(scr, [[false, "Screen may sleep"], [true, "Keep the screen on"]], S.awake, (v) => { S.awake = v; save_settings(); apply_awake(); });
  if (S.awake && !window.isSecureContext) {
    scr.appendChild(el("p", "hint", "Over plain HTTP this uses a silent video: it starts after the first touch."));
  }
  scr.appendChild(el("p", "hint", "When a problem is new:"));
  choices(scr, [[true, "Flash red"], [false, "No flash"]], S.flash, (v) => { S.flash = v; save_settings(); });
  choices(scr, [[false, "Silent"], [true, "Beep"]], S.sound, (v) => { S.sound = v; save_settings(); if (v) beep(); });
  if (S.sound) choices(scr, [[false, "Silent at night"], [true, "Beep at night too"]], S.loud_night, (v) => { S.loud_night = v; save_settings(); });

  // --- the lock (lock.js): the PIN is labhud's, the same on every screen ---
  lock_settings((title) => section(title, "security"));

  // --- the admin (account.js, admin.py) and the browsers signed in as it ---
  if (tab === "security") admin_settings((title) => section(title, "security"));

  // --- devices: the agents (agents.py), one per host, and the phones (devices.py) ---
  if (tab !== "devices") return finish_settings(body, keep, box, tab);
  agents_settings(section("Agents", "devices", true));
  const app = section("Phones", "devices");
  const show = el("div", "pair-qr");
  const pairButton = el("button", "choice", "Pair a phone");
  pairButton.type = "button";
  pairButton.addEventListener("click", () => pair_phone(show));
  const prow = el("div", "choices");
  prow.appendChild(pairButton);
  const list = el("div", "devices");
  app.append(prow, show, list);
  app.appendChild(el("p", "hint", "Pair a phone shows a QR code for the LabHUD Android app, valid once for a few minutes. "
    + "Remove ends a phone's access at once."));
  load_devices(list);

  finish_settings(body, keep, box, tab);
}

/** One column per page: its rotation tick on top, then its groups and cards, each tick showing or
    hiding it on this screen. Side by side across the width; a narrow screen scrolls sideways. */
function page_columns(s) {
  s.appendChild(el("p", "hint", "Tap a group or a card to hide it on this screen; the top tick puts the page in the rotation."));
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

/** The tabs, in two groups: what this browser shows (kept here), and what LabHUD keeps for every
 * screen and the app. A slim bar of icons on the left; at the bottom on a portrait screen. */
const SETTINGS_GROUPS = [
  ["This screen", [["look", "Look"], ["pages", "Pages"], ["night", "Night & alerts"]]],
  ["LabHUD", [["devices", "Devices"], ["security", "Security"]]],
];
const SETTINGS_TABS = SETTINGS_GROUPS.flatMap(([, tabs]) => tabs);
// the names before 0.3 (kept by browsers, and ?static&settings=<tab>)
const TAB_ALIAS = { display: "look", screen: "night", phones: "devices", agents: "devices" };

function settings_tab_key(k) {
  k = TAB_ALIAS[k] || k;
  return SETTINGS_TABS.some(([x]) => x === k) ? k : "look";
}

// thin-line icons, 24×24, drawn with currentColor (the accent on the open tab)
const TAB_ICONS = {
  look: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 0 0 18c1 0 1.5-.8 1.5-1.6 0-1.2-1-1.6-1-2.6 0-.9.7-1.6 1.6-1.6H16a5 5 0 0 0 5-5c0-4-4-7.2-9-7.2z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="14.5" cy="7" r="1"/>',
  pages: '<rect x="3" y="4" width="7" height="7" rx="1.5"/><rect x="14" y="4" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="6" rx="1.5"/><rect x="14" y="14" width="7" height="6" rx="1.5"/>',
  night: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/><path d="M17 3v3M15.5 4.5h3"/>',
  devices: '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01M11 7h6M11 17h6"/>',
  security: '<path d="M12 3l8 3v6c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
  setup: '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
};

function tab_icon(key) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = TAB_ICONS[key] || "";
  return s;
}

function settings_tabs(current) {
  const bar = $("#settings-tabs");
  if (!bar) return;
  bar.textContent = "";
  for (const [group, tabs] of SETTINGS_GROUPS) {
    bar.appendChild(el("div", "settings-group", group));
    for (const [key, label] of tabs) settings_tab(bar, key, label, current);
  }
}

function settings_tab(bar, key, label, current) {
  const b = el("button", "settings-tab" + (key === current ? " on" : ""));
  b.type = "button";
  b.setAttribute("role", "tab");
  b.setAttribute("aria-selected", key === current ? "true" : "false");
  b.append(tab_icon(key), el("span", "lbl", label));
  b.addEventListener("click", () => {
    if (key === current) return;
    S.tab = key; save_settings();
    $("#settings-scroll").scrollTop = 0;
    open_settings();
  });
  bar.appendChild(b);
}

function finish_settings(body, keep, box, tab) {
  if (tab === "devices" || tab === "security") {
    body.appendChild(el("p", "hint", "Kept by LabHUD itself: the same on every screen."));
    box.hidden = false;
    body.scrollTop = keep;
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
  body.appendChild(el("p", "hint", "Saved in this browser only: other screens keep their own settings."));
  box.hidden = false;
  body.scrollTop = keep;
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
  fetch("/api/v1/devices").then((r) => r.json()).then((r) => {
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
    fetch("/api/v1/devices").then((r) => r.json()).then((r) => {
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
// ⚙ > Devices > Agents: the paired agents, what each found, what each may do (agents.py)
// ---------------------------------------------------------------------------------------------

const CAP_TEXT = {
  docker: ["Docker", "start, stop, restart containers"],
  systemd: ["Services", "start, stop, restart systemd units"],
  host: ["The host", "reboot, shut down, update packages"],
  wol: ["Wake-on-LAN", "wake machines on its network"],
  pve: ["Proxmox", "start, shut down, reboot guests"],
  ptero: ["Game servers", "start, stop, restart (Pterodactyl)"],
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
  const add = el("button", "choice", "+ Add an agent");
  add.type = "button";
  const detail = el("div", "agents-detail");
  add.addEventListener("click", () => { agentSel = "add"; add_agent(detail, list); draw_agent_list(list, lastAgents, detail); });
  side.append(list, add);
  split.append(side, detail);
  s.appendChild(split);
  s.appendChild(el("p", "hint", "An agent runs on a host and acts only through its own APIs. Nothing is allowed "
    + "until you tick it here: first what it may do, then on which containers, guests, services or servers."));
  load_agents(list, false, detail);
}

let agentsWatch = null;
let lastAgents = [];
/** The list (and the open agent), drawn again every 10 s while ⚙ is open: online, last seen,
    new targets. A redraw waits while a tick is on its way or the filter is being typed in. */
function load_agents(list, quiet, detail) {
  clearTimeout(agentsWatch);
  // the first draw comes before ⚙ is shown; the refreshes stop once it is closed
  if (!list || !list.isConnected || (quiet && $("#settings").hidden)) return;
  agents_post("list").then((r) => {
    agentsWatch = setTimeout(() => load_agents(list, true, detail), 10000);
    if (!r.ok) {
      list.textContent = "";
      const say = (m) => list.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not list the agents.");
    }
    const busy = detail.querySelector(".pending") || (document.activeElement && detail.contains(document.activeElement)
      && document.activeElement.tagName === "INPUT");
    if (quiet && busy) return;
    lastAgents = r.agents;
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
  const withTargets = caps.filter((c) => a.caps.includes(c) && c !== "host");
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
    const every = ticked.length === all.length;
    const b = el("button", "choice small", every ? "Untick all" : `Tick all ${all.length}`);
    b.type = "button";
    b.addEventListener("click", () => {
      b.classList.add("pending");
      agents_post("targets", { id: a.id, cap: c, targets: every ? [] : all })
        .then((r) => { if (!r.ok) show_message(r.error || "Refused."); reload(); });
    });
    tools.appendChild(b);
  }
  if (tools.children.length) t.appendChild(tools);
  const grid = el("div", "choices targets");
  for (const x of all) {
    const on = ticked.includes(x);
    const label = ((a.labels || {})[c] || {})[x];
    const b = el("button", "choice" + (on ? " on" : ""), label ? `${label} (${x})` : x);
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
function add_agent(show, list) {
  show.textContent = "";
  show.appendChild(el("p", "hint", "Asking LabHUD…"));
  agents_post("code").then((r) => {
    show.textContent = "";
    if (!r.ok) {
      const say = (m) => show.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Refused.");
    }
    const before = list.querySelectorAll(".agent-item").length;
    show.appendChild(el("p", "hint", "The code:"));
    show.appendChild(copy_line(r.code));
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
    if (r.docker) {
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
      put(left, `The code works once, for ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} more.`);
      if (list.querySelectorAll(".agent-item").length > before) {
        // paired: open it (the newest), to tick what it may do
        const newest = [...lastAgents].sort((x, y) => (y.paired || 0) - (x.paired || 0))[0];
        agentSel = newest ? newest.id : null;
        load_agents(list, false, show);
        return;
      }
      setTimeout(tick, 1000);
    };
    tick();
  });
  touched();
}
