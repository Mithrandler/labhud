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
    page_columns(ctx.section("Shown on this screen", { wide: true,
      about: "Hide a group or a card on this screen only; the top tick puts the page in the rotation." }));
  },
});

function board_files(s) {
  const post = (path, body) => fetch(path, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body),
  }).then((r) => r.json());
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
