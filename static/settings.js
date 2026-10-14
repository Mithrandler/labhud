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
}

apply_look();

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

  const reset = el("button", "reset", "Reset this screen to the defaults");
  reset.type = "button";
  reset.addEventListener("click", () => {
    S = { ...DEFAULTS };
    try { localStorage.removeItem(SETTINGS_KEY); } catch (e) { /* nothing kept anyway */ }
    apply_look(); apply_night(); render_night(); mount(); open_settings();
  });
  body.appendChild(reset);
  body.appendChild(el("p", "hint", "Saved in this browser only: other screens keep their own settings."));
  box.hidden = false;
  body.scrollTop = keep;
}

function close_settings() { $("#settings").hidden = true; touched(); }
