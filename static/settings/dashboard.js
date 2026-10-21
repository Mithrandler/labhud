/* ⚙ > Display > Dashboard: the page rotation and what this screen shows (kept in this browser),
   and the pages, groups and cards for every screen (pages.js: drag and drop, size and icon). */
"use strict";

register_section({
  id: "dashboard", group: "display", title: "Dashboard", icon: "pages",
  about: "Pages, groups and cards; rotation; what this screen shows",
  keywords: "pages rotation groups cards layout drag drop move hide icon size organize",
  render: (body, ctx) => {
    const rot = ctx.section("Page rotation", { about: "Turns the pages by itself on this screen. Which pages: the tick on top of each page below." });
    ctx.choices(rot, [[true, "On"], [false, "Off"]], S.rotate, (v) => { S.rotate = v; save_settings(); });
    ctx.choices(rot, [10, 20, 30, 60].map((n) => [n, n + " s"]), S.period, (v) => { S.period = v; save_settings(); });
    layout_settings(ctx.section("Pages and groups · every screen", { wide: true,
      about: "Where every card sits, for every screen and the app; a card's size in the grid and its icon." }));
    board_files(ctx.section("Export and import", { about: "The pages, groups and the places and sizes of the cards as a file. Import shows what it would do first; cards it does not know are dropped, cards it leaves out go to a group \"Unplaced\"." }));
    discovered_settings(ctx.section("Discovered · to approve", { wide: true,
      about: "Guests, containers and services labhud sees but has no card for. Nothing is added by itself: approve the ones you want, or dismiss the rest." }));
    widget_settings(ctx.section("Widgets", { wide: true,
      about: "Small cards the browser draws by itself: a clock (with its own timezone), the month's calendar, or a page inside a frame." }));
    board_theme_settings(ctx.section("Theme · every screen", { about: "The colours behind the cards and the accent, for every screen and the app that has not picked its own in ⚙ > Look." }));
    role_pages(ctx.section("Pages per role", { wide: true, about: "What an API token or phone sees in the app, by role. A role with no tick sees every page. Alerts do not depend on it: a card a role does not see still raises its alert." }));
    page_columns(ctx.section("Shown on this screen", { wide: true,
      about: "Hide a group or a card on this screen only; the top tick puts the page in the rotation." }));
  },
});

function board_post(path, body) {
  return fetch(path, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body),
  }).then((r) => r.json());
}

/** What labhud sees and has no card for (GET /api/discover/list): approve into the page "Discovered", or dismiss. */
function discovered_settings(s) {
  const msg = el("span", "device-seen", "Looking…");
  const list = el("div", "choices");
  s.append(list, msg);
  const act = (what, keys) => board_post("/api/discover/" + what, { keys }).then((r) => {
    if (r.ok) { if (what === "approve") location.reload(); else open_settings(); } else msg.textContent = r.error || "Not saved";
  });
  fetch("/api/discover/list", { headers: admin_headers({}) }).then((r) => r.json()).then((r) => {
    if (!r.ok) { msg.textContent = signed_in() ? (r.error || "Not read") : "Sign in as the admin to see them."; return; }
    const kinds = { pve: "VM/LXC", docker: "Container", svc: "Service" };
    for (const it of r.items) {
      const row = el("div", "choices");
      row.append(el("span", "lbl", `${kinds[it.kind] || it.kind} · ${it.name}`), el("span", "device-seen", it.detail));
      const ok = el("button", "choice small", "Add"); ok.type = "button";
      ok.addEventListener("click", () => act("approve", [it.key]));
      const no = el("button", "choice small", "Dismiss"); no.type = "button";
      no.addEventListener("click", () => act("dismiss", [it.key]));
      row.append(ok, no);
      s.insertBefore(row, msg);
    }
    if (r.items.length > 1) {
      const all = el("button", "choice", "Add all (" + r.items.length + ")"); all.type = "button";
      all.addEventListener("click", () => act("approve", r.items.map((i) => i.key)));
      list.appendChild(all);
    }
    if (r.dismissed) {
      const back = el("button", "choice small", "Show " + r.dismissed + " dismissed"); back.type = "button";
      back.addEventListener("click", () => board_post("/api/discover/restore", {}).then(() => open_settings()));
      list.appendChild(back);
    }
    msg.textContent = r.items.length ? "" : "Nothing new: every guest and container has a card.";
  }).catch(() => { msg.textContent = "Not read"; });
}

/** Clock, calendar and frame cards (POST /api/boards/widget): the list, a form to add one, change or remove. */
function widget_settings(s) {
  const msg = el("span", "device-seen", "Reading…");
  const list = el("div", "choices");
  const form = el("div", "choices");
  s.append(list, form, msg);
  const field = (placeholder, type, width) => {
    const i = el("input"); i.type = type || "text"; i.placeholder = placeholder; if (width) i.style.width = width;
    return i;
  };
  const select = (options) => {
    const x = el("select");
    for (const [v, t] of options) { const o = el("option", null, t); o.value = v; x.appendChild(o); }
    return x;
  };
  const load = () => Promise.all([board_post("/api/boards/widget", { op: "list" }), setup_post("layout")]).then(([w, lay]) => {
    if (!w.ok || !lay.ok) { msg.textContent = signed_in() ? (w.error || lay.error || "Not read") : "Sign in as the admin to edit widgets."; return; }
    draw(w.widgets, lay.pages.filter((p) => !p.games));
  }).catch(() => { msg.textContent = "Not read"; });
  const draw = (widgets, pages) => {
    list.textContent = ""; form.textContent = "";
    msg.textContent = widgets.length ? "" : "No widget yet.";
    let editing = null;
    const kind = select([["clock", "Clock"], ["calendar", "Calendar"], ["frame", "Frame"]]);
    const name = field("Name", "text", "10em");
    const tz = field("Timezone, e.g. Europe/Bucharest", "text", "16em");
    const url = field("https://…", "url", "18em");
    const height = field("Height 120-800", "number", "9em"); height.min = 120; height.max = 800;
    const page = select(pages.map((p) => [p.id, p.title]));
    const group = select([]);
    const fill_groups = () => {
      group.textContent = "";
      const p = pages.find((x) => x.id === page.value);
      for (const g of (p ? p.groups : [])) { const o = el("option", null, g.title); o.value = g.id; group.appendChild(o); }
    };
    const shown = () => { tz.hidden = kind.value !== "clock"; url.hidden = height.hidden = kind.value !== "frame"; };
    page.addEventListener("change", fill_groups); kind.addEventListener("change", shown);
    fill_groups(); shown();
    const save = el("button", "choice", "Add"); save.type = "button";
    save.addEventListener("click", () => board_post("/api/boards/widget", {
      op: "save", id: editing || undefined, kind: kind.value, name: name.value, timezone: tz.value || undefined,
      url: url.value || undefined, height: height.value ? Number(height.value) : undefined, page: page.value, group: group.value,
    }).then((r) => { if (r.ok) location.reload(); else msg.textContent = r.error || "Not saved"; }));
    form.append(kind, name, tz, url, height, page, group, save);
    for (const w of widgets) {
      const row = el("div", "choices");
      row.append(el("span", "lbl", `${w.name} · ${w.kind}`), el("span", "device-seen", `${w.page} / ${w.group}` + (w.timezone ? ` · ${w.timezone}` : "") + (w.url ? ` · ${w.url}` : "")));
      const edit = el("button", "choice small", "Change"); edit.type = "button";
      edit.addEventListener("click", () => {
        editing = w.id; kind.value = w.kind; name.value = w.name; tz.value = w.timezone; url.value = w.url; height.value = w.height || "";
        page.value = w.page; fill_groups(); group.value = w.group; shown(); save.textContent = "Save"; msg.textContent = "Changing " + w.name;
      });
      const rm = el("button", "choice small", "Remove"); rm.type = "button";
      rm.addEventListener("click", () => {
        if (window.confirm("Remove the widget " + w.name + "?")) board_post("/api/boards/widget", { op: "remove", id: w.id }).then((r) => { if (r.ok) location.reload(); else msg.textContent = r.error || "Not removed"; });
      });
      row.append(edit, rm);
      list.appendChild(row);
    }
  };
  load();
}

/** The board's theme: base and accent, saved in the config (POST /api/boards/theme). */
function board_theme_settings(s) {
  const now = board_theme();
  const msg = el("span", "device-seen", "");
  const save = (base, accent) => board_post("/api/boards/theme", { base, accent }).then((r) => {
    if (r.ok) location.reload(); else msg.textContent = r.error || "Not saved";
  });
  const bases = el("div", "choices");
  for (const [k, v] of Object.entries(BASES)) {
    const b = el("button", "choice" + (k === now.base ? " on" : ""), v.label);
    b.type = "button";
    b.addEventListener("click", () => save(k, now.accent));
    bases.appendChild(b);
  }
  const accents = el("div", "choices");
  for (const [k, c] of Object.entries(ACCENTS)) {
    const b = el("button", "choice swatch" + (k === now.accent ? " on" : ""), k[0].toUpperCase() + k.slice(1));
    b.type = "button"; b.style.setProperty("--sw", c[1]);
    b.addEventListener("click", () => save(now.base, k));
    accents.appendChild(b);
  }
  const custom = el("label", "choice swatch custom" + (ACCENTS[now.accent] ? "" : " on"));
  const picker = el("input"); picker.type = "color"; picker.value = accent_colours(now.accent)[1];
  picker.addEventListener("change", () => save(now.base, picker.value));
  custom.append(picker, el("span", null, "custom"));
  accents.appendChild(custom);
  s.append(bases, accents, msg);
}

/** [roles.<role>] pages: one row of page ticks per role (POST /api/boards/roles). */
function role_pages(s) {
  const pages = (CFG && CFG.pages) || [];
  const msg = el("span", "device-seen", "");
  s.appendChild(msg);
  fetch("/api/boards/roles", { headers: admin_headers({}) }).then((r) => r.json()).then((r) => {
    if (r.ok) draw(r.roles || {}); else msg.textContent = signed_in() ? (r.error || "Not read") : "Sign in as the admin to see and change them.";
  }).catch(() => { msg.textContent = "Not read"; });
  const draw = (roles) => { for (const role of ["viewer", "operator", "admin"]) {
    const row = el("div", "choices");
    row.appendChild(el("span", "lbl", role[0].toUpperCase() + role.slice(1)));
    const chosen = new Set(roles[role] || []);
    const save = () => board_post("/api/boards/roles", { role, pages: pages.map((p) => p.id).filter((id) => chosen.has(id)) })
      .then((r) => { if (r.ok) location.reload(); else msg.textContent = r.error || "Not saved"; });
    for (const p of pages) {
      const b = el("button", "choice small" + (chosen.has(p.id) ? " on" : ""), p.title);
      b.type = "button";
      b.addEventListener("click", () => { chosen.has(p.id) ? chosen.delete(p.id) : chosen.add(p.id); save(); });
      row.appendChild(b);
    }
    s.insertBefore(row, msg);
  } };
}

function board_files(s) {
  const post = board_post;
  const msg = el("span", "device-seen", "");
  const exp = el("button", "choice", "Export"); exp.type = "button";
  const file = el("input"); file.type = "file"; file.accept = "application/json,.json"; file.hidden = true;
  const imp = el("button", "choice", "Import…"); imp.type = "button";
  exp.addEventListener("click", () => post("/api/boards/export", {}).then((r) => {
    if (!r.ok) { msg.textContent = r.error || "Not exported"; return; }
    const a = el("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(r.doc, null, 2)], { type: "application/json" }));
    a.download = "labhud-board.json"; a.click(); URL.revokeObjectURL(a.href); msg.textContent = "Exported";
  }));
  imp.addEventListener("click", () => file.click());
  file.addEventListener("change", () => {
    const f = file.files[0]; file.value = "";
    if (!f) return;
    f.text().then((t) => {
      let doc; try { doc = JSON.parse(t); } catch (e) { msg.textContent = "Not a JSON file"; return; }
      post("/api/boards/import", { doc }).then((r) => {
        if (!r.ok) { msg.textContent = r.error || "Not imported"; return; }
        const { unknown, unplaced } = r.report;
        const ask = "Apply this board? " + unknown.length + " unknown card(s) dropped, " + unplaced.length + " card(s) go to Unplaced.";
        if (!window.confirm(ask)) { msg.textContent = "Cancelled"; return; }
        post("/api/boards/import", { doc, apply: true }).then((r2) => {
          msg.textContent = r2.ok ? "Imported" : (r2.error || "Not imported");
          if (r2.ok) location.reload();
        });
      });
    });
  });
  const row = el("div", "choices");
  row.append(exp, imp, msg, file);
  s.appendChild(row);
}
