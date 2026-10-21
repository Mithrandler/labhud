/* The admin on this display (admin.py) and the corner of the top bar that shows it: "Sign in"
   while no one is, the admin's name with a menu (Settings, Sign out) once signed in.
   A display that is not signed in only shows: no ⚙, no buttons on the cards. Without an admin at
   all, every display only shows, and "Sign in" says how to make one.

   The ticket is kept in this browser (localStorage) and lasts 30 days from its last use, or until
   Sign out: here, from another screen's list of sessions, or by a new password. Loaded before
   lock.js and app.js; the dialogs use app.js's helpers ($, el, put...), which exist by the time
   anyone can tap. */

const ADMIN_KEY = "labhud-admin";
let ADMIN_TICKET = "";
try { ADMIN_TICKET = localStorage.getItem(ADMIN_KEY) || ""; } catch (e) { /* not kept: sign in again */ }
let ADMIN_USER = "";      // set once the server said the ticket is live
let ADMIN_SESSION = "";   // this browser's session id, to mark it in the list
let ADMIN_IDLE = 30 * 86400;

/** The wall page in the app's Wall mode: the app put its token in the labhud_device cookie. A phone
 * signed in as the admin makes its Wall mode the admin too (server.py _wall_admin), no ticket here. */
function in_wall_mode() { try { return document.cookie.split(/;\s*/).some((c) => c.startsWith("labhud_device=")); } catch (e) { return false; } }

/** 30 days, 12 hours: how long a session lasts ([admin] session_hours). */
function idle_words(s) { return s >= 86400 ? `${Math.round(s / 86400)} day${s >= 129600 ? "s" : ""}` : `${Math.round(s / 3600)} hour${s >= 5400 ? "s" : ""}`; }

function admin_on() { return !!(typeof CFG !== "undefined" && CFG && CFG.admin && CFG.admin.on); }
function signed_in() { return admin_on() && !!ADMIN_USER; }

function admin_headers(h) {
  return ADMIN_TICKET ? { ...(h || {}), "X-Labhud-Admin": ADMIN_TICKET } : (h || {});
}

function admin_post(step, body) {
  return fetch("/api/admin/" + step, {
    method: "POST", headers: admin_headers({ "Content-Type": "application/json" }), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: r.ok }))).catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

function set_admin(ticket, user, id) {
  ADMIN_TICKET = ticket || "";
  ADMIN_USER = ADMIN_TICKET ? user || "" : "";
  ADMIN_SESSION = ADMIN_TICKET ? id || "" : "";
  try {
    if (ADMIN_TICKET) localStorage.setItem(ADMIN_KEY, ADMIN_TICKET); else localStorage.removeItem(ADMIN_KEY);
  } catch (e) { /* not kept: this tab still has it */ }
  account_changed();
}

/** At start, when ⚙ opens and every few minutes: is this browser's ticket still live? Only a
 * refusal signs out; a network failure keeps the ticket for the next try. */
function check_admin() {
  if (!admin_on() || (!ADMIN_TICKET && !in_wall_mode())) {
    if (ADMIN_USER) set_admin(""); else render_account();
    return Promise.resolve(false);
  }
  return admin_post("check").then((r) => {
    if (r.ok) {
      const changed = ADMIN_USER !== r.user;
      ADMIN_USER = r.user; ADMIN_SESSION = r.id; ADMIN_IDLE = r.idle || ADMIN_IDLE;
      if (changed) account_changed();
      return true;
    }
    if (r.error === "forbidden") set_admin("");
    return false;
  });
}

/** Signed in or out: the corner, the card's buttons in an open panel, and ⚙ (gone when out). */
function account_changed() {
  render_account();
  if (typeof render_lock === "function") render_lock();
  close_account_menu();
  if (!signed_in() && $("#settings") && !$("#settings").hidden) $("#settings").hidden = true;
  if (typeof panelCard !== "undefined" && panelCard && !$("#panel").hidden) render_actions(panelCard);
}

/** Explains a refusal from a route that needs the admin: the session ended somewhere else (Sign
 * out everywhere, a new password) or the admin was removed. */
function admin_forbidden(show) {
  fetch("/api/config").then((r) => r.json()).then((c) => { if (CFG) CFG.admin = c.admin; }).catch(() => {}).then(() => {
    if (!admin_on()) { show("There is no admin on this LabHUD any more."); set_admin(""); return; }
    check_admin().then((live) => {
      if (live) { show("Refused: unlock the screen first."); return; }
      show("Signed out: sign in again.");
      open_signin();
    });
  });
}

/** The admin may be made or removed (init.py admin) while this page stays open: read the flag
 * again when ⚙ opens. */
function recheck_admin() {
  fetch("/api/config").then((r) => r.json()).then((c) => {
    const was = admin_on();
    if (!CFG || !c.admin || !!c.admin.on === was) return;
    CFG.admin = c.admin;
    check_admin();
  }).catch(() => {});
}

// ---------------------------------------------------------------------------------------------
// The corner: "Sign in", or the admin's name and a menu
// ---------------------------------------------------------------------------------------------


function render_account() {
  const b = $("#account");
  if (!b) return;
  const inside = signed_in();
  cls(b, "in", inside);
  b.textContent = "";
  if (inside) {
    b.append(icon_svg("person", 16));
    b.appendChild(el("span", "who", ADMIN_USER));
    b.title = "Signed in as " + ADMIN_USER;
    b.setAttribute("aria-label", "Account: " + ADMIN_USER);
  } else {
    b.appendChild(el("span", "who", "Sign in"));
    b.title = "Sign in as the admin";
    b.setAttribute("aria-label", "Sign in");
  }
  b.setAttribute("aria-expanded", $("#account-menu") && !$("#account-menu").hidden ? "true" : "false");
}

function account_click() {
  if (!signed_in()) return open_signin();
  const m = $("#account-menu");
  if (!m.hidden) return close_account_menu();
  m.textContent = "";
  const head = el("div", "head");
  head.append(el("b", null, ADMIN_USER), el("span", null, "Admin · signed in on this browser"));
  m.appendChild(head);
  const item = (label, run, extra) => {
    const b = el("button", "item" + (extra ? " " + extra : ""), label);
    b.type = "button";
    b.setAttribute("role", "menuitem");
    b.addEventListener("click", () => { close_account_menu(); run(); });
    m.appendChild(b);
  };
  item("Settings", () => open_settings());
  m.appendChild(el("div", "sep"));
  item("Sign out", sign_out, "out");
  m.hidden = false;
  render_account();
  touched();
}

function close_account_menu() {
  const m = $("#account-menu");
  if (m && !m.hidden) { m.hidden = true; render_account(); }
}

// a tap anywhere else closes the menu
document.addEventListener("pointerdown", (e) => {
  const m = $("#account-menu");
  if (m && !m.hidden && !m.contains(e.target) && !$("#account").contains(e.target)) close_account_menu();
}, { passive: true });

function sign_out() {
  admin_post("logout").then(() => { set_admin(""); show_message("Signed out"); });
}

// ---------------------------------------------------------------------------------------------
// Signing in
// ---------------------------------------------------------------------------------------------

function open_signin() {
  close_account_menu();
  const box = $("#signin");
  const body = $("#signin .box");
  body.textContent = "";
  const title = el("h2", "signin-title");
  const mark = el("img");
  mark.src = "/brand/mark.svg"; mark.alt = ""; mark.width = 26; mark.height = 26;
  title.append(mark, el("span", null, "Sign in to LabHUD"));
  body.appendChild(title);
  const close = el("button", "x", "✕");
  close.type = "button";
  close.setAttribute("aria-label", "Close");
  close.addEventListener("click", () => { box.hidden = true; });
  body.appendChild(close);

  if (!admin_on()) {
    body.appendChild(el("p", null, "This LabHUD has no admin yet, so every screen only shows."));
    body.appendChild(el("p", "hint", "Make one on the server, with a password and an authenticator app:"));
    body.appendChild(copy_line("docker exec -it labhud python3 /app/init.py admin", "cmd"));
    box.hidden = false;
    touched();
    return;
  }

  const form = el("form");
  form.autocomplete = "on";
  const field = (label, input) => {
    const l = el("label", null);
    l.append(el("span", null, label), input);
    form.appendChild(l);
    return input;
  };
  const user = el("input"); user.autocomplete = "username"; user.autocapitalize = "none"; user.spellcheck = false; user.required = true;
  const pass = el("input"); pass.type = "password"; pass.autocomplete = "current-password"; pass.required = true;
  const code = el("input"); code.inputMode = "numeric"; code.autocomplete = "one-time-code"; code.maxLength = 7;
  code.pattern = "[0-9 ]{6,7}"; code.required = true; code.placeholder = "6 digits";
  field("User", user);
  field("Password", pass);
  field("Code from the authenticator", code);
  const said = el("p", "said");
  said.setAttribute("role", "alert");
  const go = el("button", "go", "Sign in");
  go.type = "submit";
  form.append(said, go);
  form.appendChild(el("p", "hint", `This browser stays signed in for ${idle_words(ADMIN_IDLE)} after its last use, or until you sign out.`));
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    go.disabled = true;
    put(said, "Checking…");
    admin_post("login", { user: user.value.trim(), password: pass.value, code: code.value.replace(/\s/g, "") }).then((r) => {
      go.disabled = false;
      code.value = "";
      if (!r.ok) {
        put(said, r.wait ? `Too many wrong tries: wait ${Math.ceil(r.wait / 60)} min.` : (r.error || "Refused."));
        if (!r.wait) pass.select();
        return;
      }
      pass.value = "";
      ADMIN_IDLE = r.idle || ADMIN_IDLE;
      set_admin(r.ticket, r.user);
      check_admin();   // learns this browser's session id
      box.hidden = true;
      show_message("✓ Signed in as " + r.user);
    });
  });
  if (CFG.admin.oidc && typeof oidc_signin_button === "function") body.appendChild(oidc_signin_button(said));   // package F
  body.appendChild(form);
  box.hidden = false;
  touched();
  setTimeout(() => user.focus(), 50);
}

// ---------------------------------------------------------------------------------------------
// ⚙ > Security: the admin and the browsers signed in as it
// ---------------------------------------------------------------------------------------------

function admin_settings(section) {
  const s = section("Admin", { about: "The one account that may change LabHUD: a password and an authenticator app." });
  s.appendChild(el("p", "hint", `Signed in as ${ADMIN_USER}. A browser stays signed in for ${idle_words(ADMIN_IDLE)} `
    + "after its last use. A new password or authenticator (labhud admin) signs every browser out."));
  const row = el("div", "choices");
  const button = (label, run, extra) => {
    const b = el("button", "choice" + (extra ? " " + extra : ""), label);
    b.type = "button";
    b.addEventListener("click", run);
    row.appendChild(b);
  };
  button("Edit config", () => settings_go("system"));
  button("Sign out", sign_out);
  s.appendChild(row);

  const list = section("Signed in", { about: "The browsers signed in as the admin; sign one out, or all." });
  const rows = el("div", "devices");
  list.appendChild(rows);
  const all = el("div", "choices");
  const everywhere = el("button", "choice danger", "Sign out everywhere");
  everywhere.type = "button";
  everywhere.addEventListener("click", () => {
    put($("#confirm-text"), "Sign out every browser, this one too? Each one needs the password and a code again.");
    ask([["SIGN OUT ALL", () => admin_post("logout-all").then(() => { set_admin(""); show_message("Signed out everywhere"); }), true]]);
  });
  all.appendChild(everywhere);
  list.appendChild(all);
  load_sessions(rows);
}

function load_sessions(host) {
  admin_post("sessions").then((r) => {
    host.textContent = "";
    if (!r.ok) {
      const say = (m) => host.appendChild(el("p", "hint", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not list the sessions.");
    }
    for (const x of r.sessions) {
      const row = el("div", "device");
      const text = el("div", "device-text");
      const name = el("span", "device-name", x.what || "A browser");
      if (x.id === r.current) name.appendChild(el("span", "tag", "this browser"));
      text.append(name, el("span", "device-seen", `${x.where || "?"} · used ${ago(x.last)} · since ${new Date(x.made * 1000).toLocaleDateString()}`));
      row.appendChild(text);
      const out = el("button", "choice", "Sign out");
      out.type = "button";
      out.addEventListener("click", () => {
        if (x.id === r.current) return sign_out();
        admin_post("revoke", { id: x.id }).then(() => load_sessions(host));
      });
      row.appendChild(out);
      host.appendChild(row);
    }
  });
}
