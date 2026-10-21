/* ⚙ > Display > Look: this screen's theme, accent, font and size, night, calm and density. Kept
   in this browser (settings.js, S), so the wall can stay OLED black while a phone uses grey. */
"use strict";

register_section({
  id: "look", group: "display", title: "Look", icon: "look",
  about: "Theme, text, density, night and calm on this screen",
  keywords: "theme colour color accent font text size night dim calm clock density compact",
  render: (body, ctx) => look_settings(ctx),
});

function look_settings(ctx) {
  const section = ctx.section, choices = ctx.choices;
  const changed = (fn) => { fn(); save_settings(); apply_look(); };
  const look = section("Theme", { about: "The background behind the cards, and the accent of titles and bars." });
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

  const type = section("Text", { about: "The font and its size on this screen." });
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

  // night and calm
  const nt = section("Night", { about: "Dims this screen in the night hours." });
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

  const cm = section("Calm", { about: "Only a clock when nothing is red, after a while without a touch." });
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

  // density of this screen
  const lay = section("Density", { about: "How much room the cards leave around their text." });
  choices(lay, [["compact", "Compact"], ["normal", "Normal"], ["airy", "Airy"]], S.density,
    (v) => { S.density = v; save_settings(); apply_look(); mount(); });
}
