// ⚙ > Alerts and ⚙ > Notifications, LabHUD's part: the [[alert]] rules on hosts (alerting.py) and where the news goes
// ([notify] and the ntfy webhook, notify.py). Kept by LabHUD for every screen and the app.
"use strict";

const ALR = { data: null, rules: null, notify: null, dirty: false, ntfy: { url: "", token: "" }, test: null };
const ALR_UNIT = { cpu: "%", mem: "%", disk: "%", temp: "°C" };
const ALR_DEFAULT = { cpu: 90, mem: 90, disk: 90, temp: 85 };

/** Reads the rules once, then draws: the parts of ⚙ that show them call it with their own drawer. */
function alerts_ready(host, draw) {
  if (ALR.data) return draw();
  host.appendChild(el("p", "hint", "Reading the rules…"));
  setup_post("alerts").then((r) => {
    host.textContent = "";
    if (!r.ok) {
      const say = (m) => host.appendChild(el("p", "hint warn", m));
      if (r.error === "forbidden") admin_forbidden(say); else say(r.error || "refused");
      return;
    }
    alerts_loaded(r);
    redraw_alerts();
  });
}

/** ⚙ > Alerts: the rules on hosts, and the quiet hours. */
function alert_settings(section) {
  const rules = section("Alert rules", { wide: true, about: "When a host's numbers stay past a mark long enough, an alert goes to the bell, the history and your phone." });
  const rulesHost = el("div", "alr");
  rules.appendChild(rulesHost);
  const quiet = section("Quiet hours", { about: "Only the urgent alerts are sent in these hours, by LabHUD's clock; the rest wait for the morning." });
  const quietHost = el("div", "alr-quiet");
  quiet.appendChild(quietHost);
  alerts_ready(rulesHost, () => { draw_rules(rulesHost); draw_quiet(quietHost); });
}

/** ⚙ > Notifications: where the alerts go (ntfy, the app). */
function notify_settings(section) {
  const where = section("ntfy and the app", { wide: true, about: "Where LabHUD sends the alerts: an ntfy topic, the LabHUD app, or both." });
  const whereHost = el("div", "alr-notify");
  where.appendChild(whereHost);
  alerts_ready(whereHost, () => draw_notify(whereHost));
}

function alerts_loaded(r) {
  ALR.data = r;
  ALR.rules = r.rules.map((x) => ({ ...x }));
  ALR.notify = { via: r.notify.via, quiet: r.notify.quiet ? [...r.notify.quiet] : null };
  ALR.dirty = false;
}

function redraw_alerts() {
  const a = $("#settings .alr"), n = $("#settings .alr-notify"), q = $("#settings .alr-quiet");
  if (a) draw_rules(a);
  if (q) draw_quiet(q);
  if (n) draw_notify(n);
}

/** Save / Discard while something is changed (the rules, the quiet hours and "via" are saved together). */
function alr_save_row(host) {
  if (!ALR.data.managed || !ALR.dirty) return;
  const btns = el("div", "choices");
  const save = el("button", "choice on", "Save");
  save.type = "button";
  save.addEventListener("click", alerts_save);
  const discard = el("button", "choice", "Discard");
  discard.type = "button";
  discard.addEventListener("click", () => { alerts_loaded(ALR.data); redraw_alerts(); });
  btns.append(save, discard);
  host.appendChild(el("p", "hint warn", "Not saved yet."));
  host.appendChild(btns);
}

function alr_changed() { ALR.dirty = true; redraw_alerts(); }

function alr_select(options, value, pick) {
  const s = el("select");
  for (const [v, label] of options) { const o = el("option", null, label); o.value = String(v); s.appendChild(o); }
  s.value = String(value);
  s.addEventListener("change", () => pick(s.value));
  return s;
}

function alr_number(value, min, max, pick) {
  const i = el("input");
  i.type = "number"; i.inputMode = "numeric"; i.min = String(min); i.max = String(max);
  i.value = String(value);
  i.addEventListener("change", () => {
    const n = Math.round(Number(i.value));
    if (!Number.isFinite(n) || n < min || n > max) { i.value = String(value); return; }
    pick(n);
  });
  return i;
}

/** offline and failed take minutes only (server: alerting.NO_MARK). */
const alr_no_mark = (what) => (ALR.data.no_mark || ["offline"]).includes(what);

function draw_rules(host) {
  host.textContent = "";
  const d = ALR.data;
  const names = d.hosts.map((h) => h.name).join(", ") || "none yet";
  host.appendChild(el("p", "hint", `A host's numbers come from its agent. "All hosts" = every card with an agent of its own: ${names}. `
    + "A host's own rule wins over \"All hosts\" for the same thing."));
  if (!d.managed) host.appendChild(el("p", "hint warn", "⚙ > System > Take over first: then LabHUD keeps the rules and this page changes them."));
  const list = el("div", "alr-list");
  ALR.rules.forEach((r, i) => {
    const row = el("div", "alr-row");
    row.appendChild(alr_select([["*", "All hosts"], ...d.hosts.map((h) => [h.id, h.name])], r.on, (v) => { r.on = v; alr_changed(); }));
    row.appendChild(alr_select(Object.entries(d.what), r.what, (v) => {
      r.what = v;
      if (alr_no_mark(v)) delete r.above; else if (r.above == null) r.above = ALR_DEFAULT[v];
      if (r.minutes == null) r.minutes = 5;
      alr_changed();
    }));
    if (!alr_no_mark(r.what)) {
      row.append(el("span", "alr-w", "above"), alr_number(r.above, 1, r.what === "temp" ? 150 : 100, (n) => { r.above = n; alr_changed(); }),
        el("span", "alr-w", ALR_UNIT[r.what]));
    }
    row.append(el("span", "alr-w", alr_no_mark(r.what) ? "for" : "for at least"),
      alr_number(r.minutes, 0, 1440, (n) => { r.minutes = n; alr_changed(); }), el("span", "alr-w", "min"));
    if (d.urgent.includes(r.what)) row.appendChild(el("span", "alr-tag", "urgent"));
    const x = el("button", "lay-more", "✕");
    x.type = "button";
    x.setAttribute("aria-label", "Remove this rule");
    x.addEventListener("click", () => { ALR.rules.splice(i, 1); alr_changed(); });
    row.appendChild(x);
    list.appendChild(row);
  });
  if (!ALR.rules.length) list.appendChild(el("p", "hint", "No rules: only the history and the marks on the cards."));
  host.appendChild(list);
  const btns = el("div", "choices");
  const button = (label, run, c) => { const b = el("button", "choice" + (c ? " " + c : ""), label); b.type = "button"; b.addEventListener("click", run); btns.appendChild(b); return b; };
  button("+ Rule", () => { ALR.rules.push({ on: "*", what: "cpu", above: 90, minutes: 10 }); alr_changed(); });
  if (d.managed) {
    const save = button("Save", alerts_save, "on");
    save.disabled = !ALR.dirty;
    if (ALR.dirty) button("Discard", () => { alerts_loaded(ALR.data); redraw_alerts(); });
  }
  host.appendChild(btns);
  if (d.active && d.active.length) {
    host.appendChild(el("p", "hint", "Wrong now:"));
    for (const a of d.active) host.appendChild(el("p", "alr-now", "⚠ " + a.text));
  }
  host.appendChild(el("p", "hint", "Urgent ones (offline, disk, temperature) are sent in the quiet hours too; "
    + "CPU and RAM wait for the morning, in one message. Every alert is in the bell and the history."));
}

function draw_notify(host) {
  host.textContent = "";
  const d = ALR.data, n = ALR.notify;
  host.appendChild(el("p", "hint", "Send the alerts through:"));
  const via = el("div", "choices");
  for (const [v, label] of [["ntfy", "ntfy"], ["app", "The LabHUD app"], ["both", "Both"]]) {
    const b = el("button", "choice" + (n.via === v ? " on" : ""), label);
    b.type = "button";
    b.disabled = !d.managed;
    b.addEventListener("click", () => { n.via = v; alr_changed(); });
    via.appendChild(b);
  }
  host.appendChild(via);
  host.appendChild(el("p", "hint", n.via === "ntfy" ? "The app shows no notification of its own; ntfy's app does."
    : "The app (0.3 and later) shows them itself, from LabHUD, while it can reach it (home network or VPN)."));

  alr_save_row(host);

  const f = el("div", "src-form");
  const head = el("div", "src-form-head");
  head.append(el("b", null, "ntfy"));
  f.appendChild(head);
  const t = d.ntfy;
  f.appendChild(el("p", "hint", t.set
    ? `Set: ${t.host || "?"} (${t.format})${t.problem ? " · " + t.problem : ""} · ${t.sent} sent since the start`
      + (t.last_error ? ` · last error: ${t.last_error}` : "")
    : "Not set. The topic's address on your ntfy server, and its token if it asks for one."));
  const field = (label, key, type, ph) => {
    const row = el("label", "src-field");
    row.appendChild(el("span", "lbl", label));
    const i = el("input");
    i.type = type; i.autocomplete = "off"; i.spellcheck = false;
    i.value = ALR.ntfy[key]; i.placeholder = ph;
    i.addEventListener("input", () => { ALR.ntfy[key] = i.value; ALR.test = null; put_alr_test(f); });
    row.appendChild(i);
    f.appendChild(row);
  };
  field("Topic address", "url", "text", t.set ? "set: leave empty to keep it" : "https://ntfy.example.com/labhud");
  field("Token", "token", "password", t.set ? "set: leave empty to keep it" : "tk_… (empty if the topic is open)");
  const btns = el("div", "choices");
  const button = (label, run, c) => { const b = el("button", "choice" + (c ? " " + c : ""), label); b.type = "button"; b.addEventListener("click", run); btns.appendChild(b); return b; };
  button("Test", () => {
    ALR.test = { pending: true }; put_alr_test(f);
    setup_post("notify-test", { url: ALR.ntfy.url, token: ALR.ntfy.token }).then((r) => { ALR.test = r; put_alr_test(f); });
  });
  if (d.managed) {
    button("Save", () => {
      const v = {};
      if (ALR.ntfy.url.trim()) { v.LABHUD_NOTIFY_URL = ALR.ntfy.url.trim(); v.LABHUD_NOTIFY_FORMAT = "ntfy"; }
      if (ALR.ntfy.token.trim()) v.LABHUD_NOTIFY_AUTH = /^(bearer|basic) /i.test(ALR.ntfy.token.trim()) ? ALR.ntfy.token.trim() : "Bearer " + ALR.ntfy.token.trim();
      if (!Object.keys(v).length) { ALR.test = { ok: false, error: "nothing typed" }; put_alr_test(f); return; }
      ALR.test = { pending: true }; put_alr_test(f);
      setup_post("secrets", { values: v }).then((r) => {
        if (!r.ok) { ALR.test = r; put_alr_test(f); return; }
        ALR.ntfy = { url: "", token: "" };
        if (r.restart) { toast("✓ ntfy saved: LabHUD restarts"); adv_wait_restart(); }
      });
    }, "on");
    if (t.set) {
      button("Forget", () => {
        put($("#confirm-text"), "Forget the ntfy address and token? LabHUD restarts.");
        ask([["FORGET", () => setup_post("secrets", { values: { LABHUD_NOTIFY_URL: "", LABHUD_NOTIFY_AUTH: "", LABHUD_NOTIFY_FORMAT: "" } })
          .then((r) => {
            if (!r.ok) return toast("✕ " + (r.error || "refused"));
            if (r.restart) { toast("✓ ntfy forgotten: LabHUD restarts"); adv_wait_restart(); }
          }), true]]);
      }, "danger");
    }
  }
  f.appendChild(btns);
  f.appendChild(el("p", "alr-test"));
  put_alr_test(f);
  host.appendChild(f);
}

function draw_quiet(host) {
  host.textContent = "";
  const d = ALR.data, n = ALR.notify;
  const q = el("div", "alr-row");
  const hours = Array.from({ length: 24 }, (_, h) => [h, String(h).padStart(2, "0") + ":00"]);
  q.appendChild(alr_select([["off", "Off"], ["on", "On"]], n.quiet ? "on" : "off", (v) => { n.quiet = v === "on" ? [23, 7] : null; alr_changed(); }));
  if (n.quiet) {
    q.append(el("span", "alr-w", "from"), alr_select(hours, n.quiet[0], (v) => { n.quiet[0] = Number(v); alr_changed(); }),
      el("span", "alr-w", "to"), alr_select(hours, n.quiet[1], (v) => { n.quiet[1] = Number(v); alr_changed(); }));
  }
  for (const s of q.querySelectorAll("select")) s.disabled = !d.managed;
  host.appendChild(q);
  alr_save_row(host);

}

function put_alr_test(f) {
  const p = f.querySelector(".alr-test");
  if (!p) return;
  const t = ALR.test;
  p.className = "alr-test hint" + (t && !t.pending && !t.ok ? " warn" : "");
  p.textContent = !t ? "" : t.pending ? "Sending…" : t.ok ? "✓ Sent: look at your phone." : "✕ " + (t.error || "failed");
}

function alerts_save() {
  setup_post("alerts-save", { rules: ALR.rules, notify: ALR.notify }).then((r) => {
    if (!r.ok) { toast("✕ " + (r.error || "not saved"), "warn"); return; }
    toast("✓ Alert rules saved");
    ALR.data = null;   // read again: what LabHUD applied
    open_settings();
  });
}

/** ⚙ > Alerts > Thresholds: [display] warn/crit, where CPU, RAM and disk bars turn orange and red
    on every screen and in the app (api/display.py). A metric's own warn/crit in config.toml wins. */
function marks_settings(section) {
  const s = section("Thresholds", { about: "Where the bars for CPU, RAM and disk turn orange and red, on every screen and in the app. "
    + "A metric's own warn/crit in config.toml wins." });
  const d = (typeof CFG !== "undefined" && CFG && CFG.display) || {};
  const marks = { warn: d.warn ?? 80, crit: d.crit ?? 92 };
  const row = el("div", "marks");
  const said = el("p", "hint");
  const save = el("button", "choice on", "Save");
  save.type = "button";
  save.disabled = true;
  const changed = () => { save.disabled = !(marks.warn < marks.crit); put(said, marks.warn < marks.crit ? "Not saved yet." : "Orange must come before red."); };
  const mark = (cls, label, value, min, max, pick) => {
    const r = el("label", "alr-row");
    r.append(el("span", "sev " + cls), el("span", "alr-w", label), alr_number(value, min, max, pick), el("span", "alr-w", "%"));
    return r;
  };
  row.append(mark("warn", "Orange from", marks.warn, 1, 99, (n) => { marks.warn = n; changed(); }),
    mark("crit", "Red from", marks.crit, 2, 100, (n) => { marks.crit = n; changed(); }), save);
  save.addEventListener("click", () => {
    save.disabled = true;
    fetch("/api/display/marks", { method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(marks) })
      .then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
      .then((r) => {
        if (!r.ok) { put(said, "✕ " + (r.error === "forbidden" ? "refused" : r.error || "not saved")); save.disabled = false; return; }
        put(said, `✓ Saved: orange from ${r.warn}%, red from ${r.crit}%. The screens take it in a moment.`);
      })
      .catch(() => { put(said, "✕ Could not reach LabHUD."); save.disabled = false; });
  });
  s.append(row, said);
}
