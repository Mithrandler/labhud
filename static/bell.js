// The bell, top right: what was pressed (TRACKS, app.js: followed until it really happened) and
// the alerts, in one list that opens on a tap. Nothing stays over the cards: a toast says it for
// four seconds at the bottom, then it is only in the bell. Before 0.2.16 both were bars under the
// top bar that could not be closed while an action ran (up to 5 minutes).
"use strict";

const TOAST_MS = 4000;
const BELL_KEEP = 20;          // finished actions kept in the bell (this tab only)
let BELL_DONE = [];            // finished tracks, newest first
let BELL_SEEN = Date.now();    // a failure after this makes the badge red until the bell is opened
const ALERTS_TOASTED = new Set();
let TOASTS = [];               // [{text, kind}] waiting; one shows at a time
let toastTimer = null;

// ---------------------------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------------------------

/** A short message at the bottom for 4 s ("ok", "bad" or ""): ✓ and ✕ at the start say which. */
function toast(text, kind) {
  if (!kind) kind = /^✓/.test(text) ? "ok" : /^✕/.test(text) ? "bad" : "";
  if (TOASTS.some((x) => x.text === text)) return;
  TOASTS.push({ text, kind });
  if (TOASTS.length > 3) TOASTS.shift();
  if (!toastTimer) next_toast();
}

function next_toast() {
  const box = $("#toast");
  const t = TOASTS.shift();
  if (!t) { box.hidden = true; toastTimer = null; return; }
  box.textContent = "";
  box.className = t.kind;
  box.appendChild(el("span", "txt", t.text));
  const x = el("button", "x", "✕");
  x.type = "button";
  x.setAttribute("aria-label", "Close");
  x.addEventListener("click", (e) => { e.stopPropagation(); clearTimeout(toastTimer); next_toast(); });
  box.appendChild(x);
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(next_toast, TOAST_MS);
}

/** The messages of ⚙, sign in, maintenance…: a toast now (was the alert banner for 6 s). */
function show_message(text) { toast(text); }

// ---------------------------------------------------------------------------------------------
// What the bell holds
// ---------------------------------------------------------------------------------------------

/** The config's own alert list ([alerts] path) and what the [[alert]] rules find (alerting.py). */
function alerts_now() {
  const dis = dismissed();
  const own = (CFG && CFG.alerts && get(CFG.alerts.path)) || [];
  return [...own, ...(get("alerting.active") || [])].filter((a) => a && a.id && !dis[a.id]);
}

function dismiss_alert(id) {
  const x = dismissed(); x[id] = Date.now();
  try { localStorage.setItem(DISMISSED_KEY, JSON.stringify(x)); } catch (err) { /* private mode */ }
  render_bell();
  mark_menu_alerts();
}

function track_text(t, now) {
  const secs = Math.round(((t.end || now) - t.started) / 1000);
  const time = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  if (t.phase === "sending") return [`${t.label}`, "sending…", time];
  if (t.phase === "waiting") return [`${t.label}`, `${t.verb}…`, time];
  if (t.phase === "done") return [`${t.label}`, t.doneText, time];
  if (t.phase === "timeout") return [`${t.label}`, `no change after ${ACTION_TIMEOUT / 60} min`, time];
  return [`${t.label}`, t.error || "refused", time];
}

/** Called by app.js whenever a followed action changes, and every second while one runs. */
function render_tracks() {
  const now = Date.now();
  for (const t of TRACKS) {
    if (!t.toasted) { t.toasted = true; toast(`${t.label} · ${t.verb ? t.verb + "…" : "sent"}`, ""); }
    if (t.end) {
      const [label, what] = track_text(t, now);
      toast(t.phase === "done" ? `✓ ${label} ${what}` : `✕ ${label}: ${what}`);
      BELL_DONE = [t, ...BELL_DONE.filter((x) => x.name !== t.name || x.started !== t.started)].slice(0, BELL_KEEP);
    }
  }
  TRACKS = TRACKS.filter((t) => !t.end);
  const active = TRACKS.length > 0;
  if (active && !trackTimer) trackTimer = setInterval(() => { check_tracks(); render_tracks(); }, 1000);
  if (!active && trackTimer) { clearInterval(trackTimer); trackTimer = null; }
  render_bell();
}

/** Called on every data update: a new alert gets a toast once; the bell shows them all. */
function render_alerts() {
  for (const a of alerts_now()) {
    if (!ALERTS_TOASTED.has(a.id)) { ALERTS_TOASTED.add(a.id); toast("⚠ " + a.text, "warn"); }
  }
  render_bell();
}

// ---------------------------------------------------------------------------------------------
// The bell and its list
// ---------------------------------------------------------------------------------------------

function render_bell() {
  const b = $("#bell");
  if (!b) return;
  const alerts = alerts_now();
  const failed = BELL_DONE.some((t) => t.phase !== "done" && t.end > BELL_SEEN);
  const n = TRACKS.length + alerts.length;
  const badge = b.querySelector(".badge");
  put(badge, n ? String(n) : failed ? "!" : "");
  badge.hidden = !n && !failed;
  cls(b, "hot", alerts.length > 0 || failed);
  cls(b, "busy", TRACKS.length > 0);
  b.setAttribute("aria-label", `Notifications: ${TRACKS.length} running, ${alerts.length} alert(s)`);
  if (!$("#bell-menu").hidden) fill_bell_menu();
}

function open_bell() {
  const m = $("#bell-menu");
  if (!m.hidden) return close_bell();
  if (typeof close_account_menu === "function") close_account_menu();
  BELL_SEEN = Date.now();
  m.hidden = false;
  $("#bell").setAttribute("aria-expanded", "true");
  fill_bell_menu();
  render_bell();
  touched();
}

function close_bell() {
  const m = $("#bell-menu");
  if (m.hidden) return;
  m.hidden = true;
  $("#bell").setAttribute("aria-expanded", "false");
}

function fill_bell_menu() {
  const m = $("#bell-menu");
  const now = Date.now();
  m.textContent = "";
  const alerts = alerts_now();
  if (alerts.length) {
    m.appendChild(el("h4", null, "Alerts"));
    for (const a of alerts) {
      const r = el("div", "row alert");
      r.appendChild(el("span", "txt", a.text));
      const ok = el("button", "ok", "OK");
      ok.type = "button";
      ok.addEventListener("click", (e) => { e.stopPropagation(); dismiss_alert(a.id); });
      r.appendChild(ok);
      m.appendChild(r);
    }
  }
  const tracks = [...TRACKS, ...BELL_DONE];
  m.appendChild(el("h4", null, "Actions"));
  if (!tracks.length) m.appendChild(el("p", "empty", "Nothing pressed yet."));
  for (const t of tracks) {
    const [label, what, time] = track_text(t, now);
    const r = el("div", "row track " + t.phase);
    r.appendChild(el("span", "mark", t.phase === "done" ? "✓" : t.end ? "✕" : "●"));
    const txt = el("span", "txt");
    txt.append(el("b", null, label), el("span", "what", what));
    r.appendChild(txt);
    r.appendChild(el("span", "time", time));
    if (t.end) {
      const x = el("button", "x", "✕");
      x.type = "button";
      x.setAttribute("aria-label", "Remove");
      x.addEventListener("click", (e) => { e.stopPropagation(); BELL_DONE = BELL_DONE.filter((y) => y !== t); render_bell(); });
      r.appendChild(x);
    } else {
      // fills over the expected time and waits near the end: progress, not a promise
      const fill = el("i");
      const secs = (now - t.started) / 1000;
      fill.style.width = (100 * (t.phase === "sending" ? 0.05 : Math.min(0.95, 0.1 + secs / 120))).toFixed(1) + "%";
      r.appendChild(fill);
    }
    m.appendChild(r);
  }
  if (BELL_DONE.length) {
    const clear = el("button", "clear", "Clear");
    clear.type = "button";
    clear.addEventListener("click", (e) => { e.stopPropagation(); BELL_DONE = []; render_bell(); });
    m.appendChild(clear);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("#bell").addEventListener("click", (e) => { e.stopPropagation(); open_bell(); });
});
// a tap anywhere else closes the list
document.addEventListener("pointerdown", (e) => {
  const m = $("#bell-menu");
  if (m && !m.hidden && !m.contains(e.target) && !$("#bell").contains(e.target)) close_bell();
}, { passive: true });
