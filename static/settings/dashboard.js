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
    page_columns(ctx.section("Shown on this screen", { wide: true,
      about: "Hide a group or a card on this screen only; the top tick puts the page in the rotation." }));
  },
});
