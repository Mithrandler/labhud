/* ⚙'s sections, in one list: settings.js draws the sidebar and the open section from it, so a new
   section is a file that calls register_section(), and nothing else changes.

   register_section({
     id: "audit",                 // the key: ?settings=<id>, the sidebar, S.tab
     group: "system",             // display | infra | monitoring | system (SETTINGS_GROUP_ORDER)
     title: "API & audit",        // the sidebar's label and the page's heading
     icon: "key",                 // a line icon (icons.js); else the id is tried as one
     about: "Tokens for scripts…",  // one line under the title (the list on a phone, search results)
     keywords: "token log who",   // what ⚙'s search finds it by, besides the title
     admin: true,                 // only shown while signed in as the admin
     soon: false,                 // the whole section is still to come: a "soon" mark in the sidebar
     render: (body, ctx) => {},   // draws the section into body (see settings.js, open_settings)
   })

   ctx: section(title, {wide, about}) a settings card · soon(title, about) a card marked "soon" ·
   danger(about, actions) the red card · choices(host, options, current, pick) · redraw().
   One section per file, static/settings/<file>.js, loaded by index.html after settings.js. */

// The four groups of ⚙'s sidebar (the Android app's Settings has the same four, plus THIS PHONE)
const SETTINGS_GROUP_ORDER = [
  ["display", "Display"],
  ["infra", "Infrastructure"],
  ["monitoring", "Monitoring"],
  ["system", "System"],
];
const SETTINGS_SECTIONS = [];

function register_section(s) {
  if (!s || !/^[a-z][a-z0-9-]*$/.test(s.id || "")) throw new Error("register_section: an id like \"audit\"");
  if (!SETTINGS_GROUP_ORDER.some(([g]) => g === s.group)) throw new Error(`register_section: no group ${s.group}`);
  const at = SETTINGS_SECTIONS.findIndex((x) => x.id === s.id);
  const section = { icon: s.id, keywords: "", about: "", admin: false, soon: false, ...s };
  if (at >= 0) SETTINGS_SECTIONS[at] = section; else SETTINGS_SECTIONS.push(section);
  return section;
}

/** Sets the groups' order and titles (a later layout of ⚙ calls it once). */
function settings_groups_order(groups) {
  SETTINGS_GROUP_ORDER.splice(0, SETTINGS_GROUP_ORDER.length, ...groups);
}

/** [[group title, [section, …]], …]: the sections this browser may see, grouped, in order. */
function settings_groups() {
  const admin = typeof signed_in === "function" && signed_in();
  return SETTINGS_GROUP_ORDER
    .map(([g, title]) => [title, SETTINGS_SECTIONS.filter((s) => s.group === g && (!s.admin || admin))])
    .filter(([, list]) => list.length);
}

function settings_section(id) {
  return SETTINGS_SECTIONS.find((s) => s.id === id) || null;
}

/** The sections whose title or keywords hold every word of `query` (⚙'s search). */
function find_settings(query) {
  const words = String(query || "").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return settings_groups().flatMap(([, list]) => list)
    .filter((s) => words.every((w) => `${s.title} ${s.keywords} ${s.about}`.toLowerCase().includes(w)));
}
