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
  const box = $("#settings");
  const body = $("#settings-body");
  const keep = box.hidden ? 0 : body.scrollTop;   // a choice redraws the panel: stay where you were
  if (box.hidden) { recheck_admin(); check_admin(); }
  body.textContent = "";

  const tab = SETTINGS_TABS.some(([k]) => k === S.tab) ? S.tab : "display";
  settings_tabs(tab);
  const elsewhere = el("div");   // the other tabs' sections are drawn here and never shown
  const section = (title, where = "display") => {
    const s = el("section", "set");
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

  // --- rotation ---
  const rot = section("Page rotation", "display");
  choices(rot, [[true, "On"], [false, "Off"]], S.rotate, (v) => { S.rotate = v; save_settings(); });
  choices(rot, [10, 20, 30, 60].map((n) => [n, n + " s"]), S.period, (v) => { S.period = v; save_settings(); });
  const pages = el("div", "choices");
  for (const p of CFG.pages) {
    const on = !S.skip.includes(p.id);
    const b = el("button", "choice" + (on ? " on" : ""), p.title);
    b.type = "button";
    b.addEventListener("click", () => {
      S.skip = on ? [...S.skip, p.id] : S.skip.filter((x) => x !== p.id);
      save_settings(); open_settings();
    });
    pages.appendChild(b);
  }
  rot.appendChild(el("p", "hint", "Pages in the rotation:"));
  rot.appendChild(pages);

  // --- night and calm ---
  const nt = section("Night", "screen");
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

  const cm = section("Calm (only a clock when nothing is red)", "screen");
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

  // --- layout of this screen ---
  const lay = section("Layout", "display");
  choices(lay, [["compact", "Compact"], ["normal", "Normal"], ["airy", "Airy"]], S.density,
    (v) => { S.density = v; save_settings(); apply_look(); mount(); });
  lay.appendChild(el("p", "hint", "Shown on this screen (tap to hide a group or a card):"));
  for (const p of CFG.pages) {
    for (const g of p.groups) {
      const cards = CFG.cards[g.id] || [];
      if (!cards.length) continue;
      const row = el("div", "choices hide-row");
      const gOn = !S.hidden.includes("g:" + g.id);
      const gb = el("button", "choice group-chip" + (gOn ? " on" : ""), `${p.title} · ${g.title}`);
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
      lay.appendChild(row);
    }
  }

  // --- screen on, signal ---
  const scr = section("Screen and signal", "screen");
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

  // --- agents (agents.py): one per host, what each may do ---
  if (tab === "agents") {
    agents_settings(section("Agents", "agents"));
    return finish_settings(body, keep, box, tab);
  }

  // --- the LabHUD app: a one-time QR code that pairs a phone (devices.py) ---
  if (tab !== "phones") return finish_settings(body, keep, box, tab);
  const app = section("Phone app", "phones");
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

/** The tabs, in two groups: what this browser shows (kept here), and what LabHUD keeps for every
 * screen and the app. A column on the left; a row on top when the screen is narrow. */
const SETTINGS_GROUPS = [
  ["This screen", [["display", "Display"], ["screen", "Screen"]]],
  ["LabHUD", [["phones", "Phones"], ["agents", "Agents"], ["security", "Security"]]],
];
const SETTINGS_TABS = SETTINGS_GROUPS.flatMap(([, tabs]) => tabs);

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
  const b = el("button", "settings-tab" + (key === current ? " on" : ""), label);
  b.type = "button";
  b.setAttribute("role", "tab");
  b.setAttribute("aria-selected", key === current ? "true" : "false");
  b.addEventListener("click", () => {
    if (key === S.tab) return;
    S.tab = key; save_settings();
    $("#settings-scroll").scrollTop = 0;
    open_settings();
  });
  bar.appendChild(b);
}

function finish_settings(body, keep, box, tab) {
  if (tab === "phones" || tab === "agents" || tab === "security") {
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
    host.appendChild(el("p", "pair-link", r.link));
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
// ⚙ > Agents: the paired agents, what each found, what each may do (agents.py)
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

function agents_settings(s) {
  const show = el("div", "pair-qr");
  const add = el("button", "choice", "Add an agent");
  add.type = "button";
  add.addEventListener("click", () => add_agent(show, list));
  const row = el("div", "choices");
  row.appendChild(add);
  const list = el("div", "agents");
  s.append(row, show, list);
  s.appendChild(el("p", "hint", "An agent runs on a host and acts only through its own APIs. Nothing is allowed "
    + "until you tick it here: first what it may do, then on which containers, guests, services or servers."));
  load_agents(list);
}

let agentsWatch = null;
/** The list, drawn again every 10 s while it is on screen (online, last seen, new targets). */
function load_agents(host, quiet) {
  clearTimeout(agentsWatch);
  // the first draw comes before ⚙ is shown; the refreshes stop once it is closed
  if (!host || !host.isConnected || (quiet && $("#settings").hidden)) return;
  agents_post("list").then((r) => {
    agentsWatch = setTimeout(() => load_agents(host, true), 10000);
    if (!r.ok) {
      host.textContent = "";
      const say = (m) => host.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not list the agents.");
    }
    // a redraw while a tick is on its way would undo it on screen: only when nothing is pending
    if (quiet && host.querySelector(".pending")) return;
    host.textContent = "";
    if (!r.agents.length) { host.appendChild(el("p", "hint", "No agent yet.")); return; }
    for (const a of r.agents) host.appendChild(agent_box(a, host));
  });
}

function agent_box(a, host) {
  const box = el("div", "agent");
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
      load_agents(host);
    }), true]]);
  });
  head.append(text, rm);
  box.appendChild(head);

  const caps = a.found.filter((c) => CAP_TEXT[c]);
  if (!caps.length) { box.appendChild(el("p", "hint", "It found nothing it could act on (sensors only).")); return box; }
  const capRow = el("div", "choices");
  for (const c of caps) {
    const on = a.caps.includes(c);
    const b = el("button", "choice" + (on ? " on" : ""), CAP_TEXT[c][0]);
    b.type = "button";
    b.title = CAP_TEXT[c][1];
    b.addEventListener("click", () => {
      b.classList.add("pending");
      const next = on ? a.caps.filter((x) => x !== c) : [...a.caps, c];
      agents_post("caps", { id: a.id, caps: next }).then((r) => {
        if (!r.ok) show_message(r.error || "Refused.");
        // the host has one target, itself: ticking the capability ticks it too
        if (r.ok && c === "host" && !on) return agents_post("targets", { id: a.id, cap: "host", targets: ["host"] });
      }).then(() => load_agents(host));
    });
    capRow.appendChild(b);
  }
  box.appendChild(capRow);
  for (const c of caps) {
    if (!a.caps.includes(c) || c === "host") continue;
    const ticked = (a.targets || {})[c] || [];
    const all = [...new Set([...ticked, ...((a.choices || {})[c] || [])])];
    const t = el("div", "agent-targets");
    t.appendChild(el("h4", null, CAP_TEXT[c][0] + ": " + CAP_TEXT[c][1]));
    if (!all.length) t.appendChild(el("p", "hint", a.online ? "Nothing found yet." : "Shown when the agent is online."));
    const tRow = el("div", "choices");
    if (all.length > 24) {   // a long list (services, the ARP table): a filter above it
      const f = el("input", "agent-filter");
      f.placeholder = `Filter ${all.length}…`;
      f.addEventListener("input", () => {
        const q = f.value.trim().toLowerCase();
        for (const b of tRow.children) b.hidden = !!q && !b.textContent.toLowerCase().includes(q) && !b.classList.contains("on");
      });
      t.appendChild(f);
    }
    for (const x of all) {
      const on = ticked.includes(x);
      const label = ((a.labels || {})[c] || {})[x];
      const b = el("button", "choice" + (on ? " on" : ""), label ? `${label} (${x})` : x);
      b.type = "button";
      b.addEventListener("click", () => {
        b.classList.add("pending");
        agents_post("targets", { id: a.id, cap: c, targets: on ? ticked.filter((y) => y !== x) : [...ticked, x] })
          .then((r) => { if (!r.ok) show_message(r.error || "Refused."); load_agents(host); });
      });
      tRow.appendChild(b);
    }
    t.appendChild(tRow);
    box.appendChild(t);
  }
  return box;
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
    const before = list.querySelectorAll(".agent").length;
    show.appendChild(el("p", "hint", "On the host, as root (a systemd service):"));
    show.appendChild(el("p", "pair-link agent-line", r.install));
    show.appendChild(el("p", "hint", "Or as a container:"));
    show.appendChild(el("p", "pair-link agent-line", r.docker));
    const left = el("p", "hint");
    show.appendChild(left);
    const until = Date.now() + r.minutes * 60000;
    const tick = () => {
      if (!show.isConnected) return;
      const s = Math.round((until - Date.now()) / 1000);
      if (s <= 0) { show.textContent = ""; show.appendChild(el("p", "hint", "The code expired: make a new one.")); return; }
      put(left, `The code works once, for ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")} more.`);
      if (list.querySelectorAll(".agent").length > before) {
        show.textContent = "";
        show.appendChild(el("p", "hint", "Paired. Tick below what it may do."));
        return;
      }
      setTimeout(tick, 1000);
    };
    tick();
  });
  touched();
}
