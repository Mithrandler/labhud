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
  calm_show: ["weather"],   // under the calm clock: "weather" and/or ids of cards with a list
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
  const box = $("#settings");
  const body = $("#settings-body");
  const keep = box.hidden ? 0 : body.scrollTop;   // a choice redraws the panel: stay where you were
  body.textContent = "";

  const section = (title) => { const s = el("section", "set"); s.appendChild(el("h3", null, title)); body.appendChild(s); return s; };
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
  const rot = section("Page rotation");
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
  const nt = section("Night");
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

  const cm = section("Calm (only a clock when nothing is red)");
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
  const lay = section("Layout");
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
  const scr = section("Screen and signal");
  choices(scr, [[false, "Screen may sleep"], [true, "Keep the screen on"]], S.awake, (v) => { S.awake = v; save_settings(); apply_awake(); });
  if (S.awake && !window.isSecureContext) {
    scr.appendChild(el("p", "hint", "Over plain HTTP this uses a silent video: it starts after the first touch."));
  }
  scr.appendChild(el("p", "hint", "When a problem is new:"));
  choices(scr, [[true, "Flash red"], [false, "No flash"]], S.flash, (v) => { S.flash = v; save_settings(); });
  choices(scr, [[false, "Silent"], [true, "Beep"]], S.sound, (v) => { S.sound = v; save_settings(); if (v) beep(); });
  if (S.sound) choices(scr, [[false, "Silent at night"], [true, "Beep at night too"]], S.loud_night, (v) => { S.loud_night = v; save_settings(); });

  // --- the lock (lock.js): the PIN is labhud's, the same on every screen ---
  lock_settings(section);

  // --- the LabHUD app: a one-time QR code that pairs a phone (devices.py) ---
  const app = section("Phone app");
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
    method: "POST", headers: lock_headers({ "Content-Type": "application/json" }), body: "{}",
  }).then((r) => r.json()).then((r) => {
    host.textContent = "";
    if (!r.ok) { host.appendChild(el("p", "hint", r.error === "forbidden" ? "Unlock the screen first." : r.error)); return; }
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
    method: "POST", headers: lock_headers({ "Content-Type": "application/json" }), body: JSON.stringify({ name }),
  }).then((r) => r.json()).then((r) => {
    if (!r.ok) show_message(r.error === "forbidden" ? "Unlock the screen first." : r.error);
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
