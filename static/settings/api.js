/* ⚙ > System > API & audit: tokens for scripts (api/tokens.py) and the audit log (api/audit.py).
   A token is shown once, when it is made; the log is read with GET /api/v1/audit, newest first. */
"use strict";

function tokens_post(op, body) {
  return fetch("/api/tokens/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
    .catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

const AUD = { who: "", action: "", ok: "", from: "", to: "", offset: 0, limit: 50 };
const TOK = { shown: "" };

register_section({
  id: "api", group: "system", title: "API & audit", icon: "key",
  about: "Tokens for scripts, and who did what",
  keywords: "api token bearer scope audit log history who",
  admin: true,
  render: (body, ctx) => { api_tokens(ctx); api_audit(ctx); },
});

function api_tokens(ctx) {
  const s = ctx.section("API tokens", { wide: true, about: "For scripts and other tools: Authorization: Bearer lh_…. read sees the state, act also presses the cards' buttons, admin also changes settings. Shown once, when made." });
  const made = el("div", "tok-made");
  const list = el("div", "tok-list");
  const form = el("div", "tok-form");
  const input = el("input");
  input.type = "text"; input.placeholder = "Name (e.g. home-assistant)"; input.maxLength = 40; input.className = "tok-name";
  const scope = el("select", "tok-scope");
  for (const [v, label] of [["read", "viewer"], ["act", "operator"], ["admin", "admin"]]) { const o = el("option"); o.value = v; o.textContent = label; scope.appendChild(o); }
  const days = el("select", "tok-days");
  for (const [v, label] of [["", "never expires"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"]]) { const o = el("option"); o.value = v; o.textContent = label; days.appendChild(o); }
  const btn = el("button", "choice on", "Make token"); btn.type = "button";
  btn.addEventListener("click", () => {
    const name = input.value.trim();
    if (!name) { show_message("Give the token a name."); return; }
    tokens_post("create", { name, scope: scope.value, days: days.value ? Number(days.value) : null }).then((r) => {
      if (!r.ok) { r.error === "forbidden" ? admin_forbidden(show_message) : show_message(r.error); return; }
      TOK.shown = r.token; input.value = ""; draw_made(made); load_tokens(list);
    });
    touched();
  });
  form.append(input, scope, days, btn);
  s.append(form, made, list);
  draw_made(made);
  load_tokens(list);
}

function draw_made(host) {
  host.textContent = "";
  if (!TOK.shown) return;
  host.appendChild(el("p", "hint ok", "Copy it now: LabHUD keeps only its hash and will not show it again."));
  host.appendChild(copy_line(TOK.shown, "tok-copy"));
  const b = el("button", "choice small", "Done"); b.type = "button";
  b.addEventListener("click", () => { TOK.shown = ""; draw_made(host); });
  host.appendChild(b);
}

function load_tokens(host) {
  tokens_post("list").then((r) => {
    host.textContent = "";
    if (!r.ok) { host.appendChild(el("p", "hint", r.error === "forbidden" ? "Refused: sign in again, or unlock the screen." : r.error)); return; }
    if (!r.enabled) { host.appendChild(el("p", "hint", "Tokens need LABHUD_DATA (a folder LabHUD can write).")); return; }
    if (!r.tokens.length) { host.appendChild(el("p", "hint", "No token yet.")); return; }
    for (const t of r.tokens) {
      const row = el("div", "device tok" + (t.expired ? " expired" : ""));
      const text = el("div", "device-text");
      const nameSpan = el("span", "device-name");
      nameSpan.appendChild(document.createTextNode(t.name + " "));
      nameSpan.appendChild(el("span", "tok-scope-tag " + t.scope, t.role || t.scope));
      const parts = ["made " + ago(t.made), t.last ? "used " + ago(t.last) + (t.ip ? " from " + t.ip : "") : "never used", t.expires ? (t.expired ? "expired" : "expires " + new Date(t.expires * 1000).toLocaleDateString()) : "no expiry"];
      text.append(nameSpan, el("span", "device-seen", parts.join(" · ")));
      const b = el("button", "choice danger", "Revoke"); b.type = "button";
      b.addEventListener("click", () => {
        put($("#confirm-text"), "Revoke " + t.name + "? Whatever uses it stops working at once.");
        ask([["REVOKE", () => tokens_post("revoke", { id: t.id }).then((x) => { if (!x.ok) show_message(x.error); load_tokens(host); }), true]]);
      });
      row.append(text, b);
      host.appendChild(row);
    }
  });
}

function api_audit(ctx) {
  const s = ctx.section("Audit log", { wide: true, about: "Who did what, when and from where: sign-ins, buttons pressed, changes in ⚙, refusals." });
  const keep = el("div", "aud-bar");
  const days = el("input"); days.type = "number"; days.min = 7; days.max = 730; days.className = "aud-keep";
  const kb = el("button", "choice", "Save"); kb.type = "button";
  const kmsg = el("span", "device-seen", "");
  keep.append(el("span", "device-seen", "Keep entries for (days)"), days, kb, kmsg);
  s.appendChild(keep);
  fetch("/api/v1/audit?limit=1", { headers: lock_headers() }).then((r) => r.json()).then((r) => { if (r.keep_days) days.value = r.keep_days; });
  kb.addEventListener("click", () => {
    fetch("/api/audit/settings", { method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify({ keep_days: Number(days.value) }) })
      .then((r) => r.json()).then((r) => { kmsg.textContent = r.ok ? "Saved" : (r.error || "Not saved"); });
  });
  const bar = el("div", "aud-bar");
  const who = el("input"); who.type = "text"; who.className = "aud-who"; who.placeholder = "who"; who.value = AUD.who;
  const act = el("input"); act.type = "text"; act.className = "aud-action"; act.placeholder = "action"; act.value = AUD.action;
  const okSel = el("select", "aud-okf");
  for (const [v, label] of [["", "all"], ["1", "ok"], ["0", "refused"]]) { const o = el("option"); o.value = v; o.textContent = label; okSel.appendChild(o); }
  okSel.value = AUD.ok;
  const from = el("input"); from.type = "date"; from.className = "aud-from"; from.value = AUD.from;
  const to = el("input"); to.type = "date"; to.className = "aud-to"; to.value = AUD.to;
  const fbtn = el("button", "choice on", "Filter"); fbtn.type = "button";
  const table = el("div", "aud-table");
  const pager = el("div", "choices aud-pager");
  const apply = () => {
    AUD.who = who.value; AUD.action = act.value; AUD.ok = okSel.value; AUD.from = from.value; AUD.to = to.value;
    AUD.offset = 0; load_audit(table, pager);
  };
  who.addEventListener("change", apply); act.addEventListener("change", apply); okSel.addEventListener("change", apply);
  from.addEventListener("change", apply); to.addEventListener("change", apply);
  who.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  act.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
  fbtn.addEventListener("click", apply);
  bar.append(who, act, okSel, from, to, fbtn);
  s.append(bar, table, pager);
  load_audit(table, pager);
}

function load_audit(table, pager) {
  const q = new URLSearchParams();
  if (AUD.who) q.set("who", AUD.who);
  if (AUD.action) q.set("action", AUD.action);
  if (AUD.ok) q.set("ok", AUD.ok);
  if (AUD.from) q.set("from", AUD.from);
  if (AUD.to) q.set("to", AUD.to);
  q.set("offset", String(AUD.offset));
  q.set("limit", String(AUD.limit));
  fetch("/api/v1/audit?" + q, { headers: lock_headers() }).then((r) => r.json()).then((r) => {
    table.textContent = ""; pager.textContent = "";
    if (!r.ok) { table.appendChild(el("p", "hint", r.error === "forbidden" ? "Refused: sign in again." : (r.error || "Could not read the log."))); return; }
    if (!r.enabled) { table.appendChild(el("p", "hint", "The log needs LABHUD_DATA (a folder LabHUD can write).")); return; }
    if (!r.rows.length) table.appendChild(el("p", "hint", AUD.offset ? "No more entries." : "Nothing matches."));
    else audit_rows(table, r.rows);
    if (AUD.offset > 0) { const b = el("button", "choice", "‹ Newer"); b.type = "button"; b.addEventListener("click", () => { AUD.offset = Math.max(0, AUD.offset - AUD.limit); load_audit(table, pager); }); pager.appendChild(b); }
    if (r.more) { const b = el("button", "choice", "Older ›"); b.type = "button"; b.addEventListener("click", () => { AUD.offset += AUD.limit; load_audit(table, pager); }); pager.appendChild(b); }
  }).catch(() => { table.textContent = ""; table.appendChild(el("p", "hint", "Could not reach LabHUD.")); });
}

/** The log as a table: a header row, then one row per entry, refusals marked. */
function audit_rows(table, rows) {
  const head = el("div", "aud-row aud-head");
  for (const label of ["When", "Who", "Action", "Target", "From", ""]) head.appendChild(el("span", null, label));
  table.appendChild(head);
  for (const e of rows) {
    const row = el("div", "aud-row" + (e.ok ? "" : " bad"));
    const when = el("span", "aud-when", new Date(e.ts).toLocaleString()); when.title = String(e.ts);
    row.append(when, el("span", "aud-who", e.who), el("span", "aud-action", e.action), el("span", "aud-target", e.target));
    const ipSpan = el("span", "aud-ip", e.ip); ipSpan.title = e.ua || "";
    row.append(ipSpan, el("span", "aud-ok", e.ok ? "ok" : "refused"));
    table.appendChild(row);
  }
}
