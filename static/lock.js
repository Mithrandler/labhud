/* The lock (lock.py on the server): with a PIN set, a screen signed in as the admin goes back to
   only showing after [lock] idle seconds (2 min) without a touch, or at once with the lock next to
   ⚙. Only showing is what a screen that is not signed in does: the pages, the panels, the rotation
   go on, without ⚙ and without the cards' buttons; the admin's 30-day session stays. The lock, ⚙
   and the panel's UNLOCK ask for the PIN; the right one gets a ticket from labhud, sent with every
   request that changes something. A screen that is not signed in has no lock: it only shows anyway.

   Loaded before app.js, like settings.js; it uses app.js's helpers ($, el, put, touched...) only
   once someone touches the screen.

   Over plain HTTP nothing here is a secret: the PIN is checked by the server, and the ticket only
   lives in this tab (sessionStorage), so a reload keeps it but another tab does not. */

const LOCK_KEY = "labhud-unlock";
const LOCK_RENEW = 30000;      // ms: a touch tells the server the ticket is still in use, at most this often
const PAD_IDLE = 30000;        // ms: a keypad nobody types on closes by itself
const LOCK_OFF = location.search.includes("static");   // check screenshots: never locked

let LOCK = { on: false, idle: 120, settable: false, ticket: "" };
let lockUsedAt = Date.now();   // the last touch on an open screen
let lockRenewedAt = 0;
let pad = null;                // the keypad's state while it is open
let lockChanging = false;      // ⚙ is changing the PIN: the stream's news of it must not lock this screen

function locked() { return LOCK.on && !LOCK.ticket && !LOCK_OFF && signed_in(); }

/** The cards' buttons, maintenance and ⚙: the admin's, on an open screen. */
function may_act() { return signed_in() && !locked(); }

/** The request headers with the ticket, for labhud's own routes (never a cross-origin agent). */
function lock_headers(headers) {
  const h = LOCK.ticket ? { ...(headers || {}), "X-Labhud-Unlock": LOCK.ticket } : (headers || {});
  return admin_headers(h);   // and the admin's ticket (account.js): buttons and ⚙ need both
}

function lock_post(step, body) {
  return fetch("/api/lock/" + step, {
    method: "POST", headers: lock_headers({ "Content-Type": "application/json" }), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: r.ok }))).catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

function set_ticket(t) {
  LOCK.ticket = t || "";
  try {
    if (LOCK.ticket) sessionStorage.setItem(LOCK_KEY, LOCK.ticket); else sessionStorage.removeItem(LOCK_KEY);
  } catch (e) { /* not kept: a reload asks again */ }
  lockUsedAt = lockRenewedAt = Date.now();
  render_lock();
  // an open panel shows its buttons, or UNLOCK in their place
  if (typeof panelCard !== "undefined" && panelCard && $("#panel") && !$("#panel").hidden) render_actions(panelCard);
}

/** From /api/config at start, and from the stream when the PIN is set or removed anywhere. */
function lock_state(state) {
  const was = LOCK.on;
  LOCK.on = !!(state && state.on);
  LOCK.idle = (state && state.idle) || 120;
  LOCK.settable = !!(state && state.settable);
  if (!LOCK.on) set_ticket("");
  else if (!was && !LOCK.ticket) { try { LOCK.ticket = sessionStorage.getItem(LOCK_KEY) || ""; } catch (e) { /* none */ } }
  // a ticket from before may have ended on the server (a restart, a new PIN): ask, lock if so.
  // Not while ⚙ changes the PIN here: its answer brings this screen's new ticket.
  if (!lockChanging) {
    if (LOCK.on && LOCK.ticket) lock_post("touch").then((r) => { if (!r.ok) lock_close(false); });
    if (LOCK.on && !LOCK.ticket) lock_close(false);
  }
  render_lock();
  if ($("#settings") && !$("#settings").hidden && typeof open_settings === "function") open_settings();
}

/** Back to only showing: ⚙ and a confirmation close; an open panel stays, without its buttons. */
function lock_close(tell = true) {
  if (tell && LOCK.ticket) lock_post("lock");
  set_ticket("");
  if (!LOCK.on) return;
  for (const id of ["#settings", "#confirm"]) { const n = $(id); if (n) n.hidden = true; }
}

/** A request answered 403 while a PIN is set: the ticket may have ended on the server. */
function lock_refused(status) {
  if (status === 403 && LOCK.on && LOCK.ticket) lock_post("touch").then((r) => { if (!r.ok) lock_close(false); });
}

function render_lock() {
  const b = $("#lock-btn");
  if (!b) return;
  b.hidden = !LOCK.on || LOCK_OFF || !signed_in();
  b.classList.toggle("open", !locked());
  b.title = locked() ? "Only showing: tap to enter the PIN" : "Lock this screen now";
  b.setAttribute("aria-label", b.title);
  $("#clock").classList.toggle("has-lock", !b.hidden);
}

// ---------------------------------------------------------------------------------------------
// Touches: they keep an open screen open; a keyboard types into the keypad, and nowhere else
// ---------------------------------------------------------------------------------------------

function lock_guard(e) {
  if (e.type === "keydown") {
    if (!pad) return;
    const k = /^[0-9]$/.test(e.key) ? e.key : { Backspace: "⌫", Escape: "✕" }[e.key];
    if (k) pad_key(k);
    e.stopPropagation();
    e.preventDefault();
    return;
  }
  if (LOCK.on && !LOCK_OFF && LOCK.ticket) lock_used();
}

function lock_used() {
  lockUsedAt = Date.now();
  if (LOCK.ticket && lockUsedAt - lockRenewedAt > LOCK_RENEW) {
    lockRenewedAt = lockUsedAt;
    lock_post("touch").then((r) => { if (!r.ok) lock_close(false); });
  }
}

for (const type of ["pointerdown", "keydown"]) window.addEventListener(type, lock_guard, { capture: true, passive: false });

setInterval(() => {
  if (LOCK.on && LOCK.ticket && Date.now() - lockUsedAt > LOCK.idle * 1000) lock_close();
  if (pad && Date.now() - pad.used > PAD_IDLE) pad_end(false);
}, 2000);

// ---------------------------------------------------------------------------------------------
// The keypad
// ---------------------------------------------------------------------------------------------

/** Asks for 4 digits under `title`; `submit(pin)` answers {ok, msg?, wait?}. Resolves true once a
    submit is ok, false when cancelled. A wrong answer shakes the dots and stays open. */
function pad_ask(title, submit) {
  pad_build();
  return new Promise((resolve) => {
    pad = { digits: "", title, submit, resolve, used: Date.now(), waitUntil: 0, busy: false };
    put($("#lock .title"), title);
    put($("#lock .msg"), "");
    pad_dots();
    $("#lock").hidden = false;
  });
}

function pad_end(ok) {
  if (!pad) return;
  const done = pad.resolve;
  pad = null;
  $("#lock").hidden = true;
  done(ok);
}

function pad_build() {
  if ($("#lock")) return;
  const box = el("div", "box");
  box.append(el("p", "title"), el("div", "dots"), el("p", "msg"));
  for (let i = 0; i < 4; i++) box.querySelector(".dots").appendChild(el("i"));
  const keys = el("div", "keys");
  for (const k of ["1", "2", "3", "4", "5", "6", "7", "8", "9", "✕", "0", "⌫"]) {
    const b = el("button", k === "✕" ? "cancel" : k === "⌫" ? "back" : "digit", k);
    b.type = "button";
    b.addEventListener("click", () => pad_key(k));
    keys.appendChild(b);
  }
  box.appendChild(keys);
  const wrap = el("div");
  wrap.id = "lock";
  wrap.hidden = true;
  wrap.appendChild(box);
  document.body.appendChild(wrap);
}

function pad_dots() {
  const n = pad ? pad.digits.length : 0;
  $("#lock .dots").querySelectorAll("i").forEach((d, i) => d.classList.toggle("on", i < n));
}

function pad_key(k) {
  if (!pad) return;
  pad.used = Date.now();
  if (k === "✕") return pad_end(false);
  if (pad.busy || Date.now() < pad.waitUntil) return;
  if (k === "⌫") pad.digits = pad.digits.slice(0, -1);
  else if (pad.digits.length < 4) pad.digits += k;
  pad_dots();
  if (pad.digits.length === 4) pad_send();
}

function pad_send() {
  const p = pad;
  p.busy = true;
  Promise.resolve(p.submit(p.digits)).then((r) => {
    if (pad !== p) return;
    p.busy = false;
    if (r && r.ok) return pad_end(true);
    p.digits = "";
    pad_dots();
    const dots = $("#lock .dots");
    dots.classList.remove("shake");
    void dots.offsetWidth;   // restart the animation
    dots.classList.add("shake");
    if (r && r.wait) {
      p.waitUntil = Date.now() + r.wait * 1000;
      pad_countdown(p);
    } else put($("#lock .msg"), (r && r.msg) || "Wrong PIN");
  });
}

function pad_countdown(p) {
  if (pad !== p) return;
  const left = Math.ceil((p.waitUntil - Date.now()) / 1000);
  if (left <= 0) return put($("#lock .msg"), "");
  put($("#lock .msg"), `Too many wrong PINs: try again in ${left >= 60 ? Math.ceil(left / 60) + " min" : left + " s"}`);
  p.used = Date.now();   // the countdown stays on screen until it ends
  setTimeout(() => pad_countdown(p), 1000);
}

/** The answer of /api/lock/* as the keypad shows it. */
function pad_answer(r) {
  return r.ok ? { ok: true } : { ok: false, wait: r.wait || 0, msg: r.error === "wrong PIN" ? "Wrong PIN" : r.error };
}

/** Resolves true once the screen is open (at once without a lock). */
function lock_unlock() {
  if (!locked()) return Promise.resolve(true);
  return pad_ask("Enter the PIN", (pin) => lock_post("unlock", { pin }).then((r) => {
    if (r.ok) set_ticket(r.ticket);
    return pad_answer(r);
  }));
}

/** A new PIN, twice; resolves the PIN or null. */
async function pad_new_pin() {
  let first = null;
  if (!await pad_ask("New PIN (4 digits)", (pin) => { first = pin; return { ok: true }; })) return null;
  const again = await pad_ask("The new PIN again", (pin) => (pin === first ? { ok: true } : { ok: false, msg: "Not the same: again" }));
  return again ? first : null;
}

/** ⚙: set, change or remove the PIN. */
async function lock_change(remove) {
  lockChanging = true;
  try { await lock_change_steps(remove); } finally { lockChanging = false; }
}

async function lock_change_steps(remove) {
  let old;
  if (LOCK.on) {
    const ok = await pad_ask(remove ? "Current PIN, to remove it" : "Current PIN", (pin) => {
      old = pin;
      return lock_post("unlock", { pin }).then((r) => { if (r.ok) set_ticket(r.ticket); return pad_answer(r); });
    });
    if (!ok) return;
  }
  const pin = remove ? null : await pad_new_pin();
  if (!remove && !pin) return;
  const r = await lock_post("pin", { old, new: pin });
  if (r.ok) {
    set_ticket(r.ticket || "");
    lock_state(r.lock);
    if (typeof show_message === "function") show_message(remove ? "✓ PIN removed" : "✓ PIN set: after " + Math.round(LOCK.idle / 60) + " min without a touch, this screen only shows");
  } else if (typeof show_message === "function") show_message("✕ " + (r.error || "not changed"));
}

/** The section in ⚙ (settings.js calls it with its `section` helper). */
function lock_settings(section) {
  const s = section("Lock", { about: "A PIN that closes signed-in screens after a while without a touch." });
  const button = (label, run) => {
    const b = el("button", "choice", label);
    b.type = "button";
    b.addEventListener("click", run);
    return b;
  };
  const row = el("div", "choices");
  if (!LOCK.settable && !LOCK.on) {
    s.appendChild(el("p", "hint", "No PIN. labhud has no LABHUD_DATA folder to keep one: run init.py pin --hash and put the line in .env."));
    return;
  }
  if (!LOCK.on) row.appendChild(button("Set a PIN", () => lock_change(false)));
  else if (LOCK.settable) row.append(button("Change PIN", () => lock_change(false)), button("Remove PIN", () => lock_change(true)),
    button("Lock now", () => lock_close()));
  else row.appendChild(button("Lock now", () => lock_close()));
  s.appendChild(row);
  s.appendChild(el("p", "hint", LOCK.on
    ? `After ${Math.round(LOCK.idle / 60)} min without a touch ([lock] idle) every signed-in screen only shows: no ⚙, no buttons, until the PIN. The session stays. Forgotten: init.py pin --clear.`
    : "Optional, 4 digits. With a PIN, a signed-in screen goes back to only showing after 2 min without a touch; the PIN opens it again. The same PIN on every screen and in the app."));
  if (LOCK.on && !LOCK.settable) s.appendChild(el("p", "hint", "Set in .env (LABHUD_LOCK_PIN_HASH): change it there."));
}
