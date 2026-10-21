/* ⚙ > Monitoring > Notifications: where the alerts go. ntfy and the app (alerts.js), then email, a
   webhook, Discord and Gotify (api/notifications.py, notify.py), each with Test, and Events: which
   channel gets which kind of news. Secrets are typed here and never shown again: only "set". */
"use strict";

const NTF = { data: null, form: {}, test: {} };

function notify_post(op, body) {
  return fetch("/api/notify/" + op, {
    method: "POST",
    headers: admin_headers(lock_headers({ "Content-Type": "application/json" })),
    body: JSON.stringify(body || {})
  }).then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
    .catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

const NTF_CHANNELS = [
  ["email", "Email", "Alerts by SMTP. The password is kept in secrets.env, never shown again.", [
    ["host", "SMTP server", "text", "smtp.example.com", false],
    ["port", "Port", "number", "587 (STARTTLS) or 465 (SSL)", false],
    ["security", "Security", "select", "", false],
    ["user", "User", "text", "labhud@example.com", false],
    ["from", "From", "text", "the user, if empty", false],
    ["to", "To", "text", "you@example.com, other@example.com", false],
    ["password", "Password", "password", "", true]]],
  ["webhook", "Webhook", "A POST with the event as JSON ({title, text, bad, time, event, card}) to any address.", [
    ["url", "Address", "password", "https://n8n.example.com/webhook/…", true],
    ["auth", "Authorization", "password", "Bearer … (empty if none)", true]]],
  ["discord", "Discord", "A channel's webhook (Server settings > Integrations > Webhooks).", [
    ["url", "Webhook address", "password", "https://discord.com/api/webhooks/…", true]]],
  ["gotify", "Gotify", "A Gotify server and an application's token.", [
    ["url", "Server", "text", "https://gotify.example.com", false],
    ["token", "App token", "password", "", true]]],
];

register_section({
  id: "notifications",
  group: "monitoring",
  title: "Notifications",
  icon: "bell",
  about: "ntfy, the app, email, webhooks; where each kind of event goes",
  keywords: "notifications ntfy app push email smtp webhook discord gotify channel test events matrix",
  render: (body, ctx) => {
    notify_settings(ctx.section);
    const cards = {};
    for (const [id, title, about] of NTF_CHANNELS) cards[id] = ctx.section(title, { about });
    const events = ctx.section("Events", { wide: true, about: "Which channel gets what. A channel that is not set is greyed out." });
    const draw = () => { for (const [id] of NTF_CHANNELS) ntf_channel(cards[id], id); ntf_events(events); };
    if (NTF.data) return draw();
    for (const s of Object.values(cards)) s.appendChild(el("p", "hint", "Reading…"));
    notify_post("channels").then((r) => {
      if (!r.ok) {
        for (const s of [...Object.values(cards), events]) s.querySelectorAll("p.hint").forEach((p) => p.remove());
        const say = (m) => events.appendChild(el("p", "hint warn", m));
        if (r.error === "forbidden") admin_forbidden(say); else say(r.error || "refused");
        return;
      }
      NTF.data = r;
      draw();
    });
  },
});

function ntf_reload() {
  NTF.data = null;
  notify_post("channels").then((r) => {
    if (r.ok) { NTF.data = r; }
    if (typeof open_settings === "function") open_settings();
  });
}

function ntf_channel(s, id) {
  [...s.children].forEach((c) => { if (c.tagName !== "H3" && !c.classList.contains("set-desc")) c.remove(); });
  const d = NTF.data, c = d.channels[id], spec = NTF_CHANNELS.find((x) => x[0] === id), form = NTF.form[id] || (NTF.form[id] = {});

  const statusCls = "hint" + (c.problem || c.last_error ? " warn" : "");
  let statusText;
  if (c.set) {
    statusText = "Set" + (c.host ? ": " + c.host : "") + (id === "gotify" && c.url ? ": " + c.url : "")
      + " · " + (c.sent || 0) + " sent since the start"
      + (c.last_error ? " · last error: " + c.last_error : "");
  } else if (c.problem) {
    statusText = "✕ " + c.problem;
  } else {
    statusText = "Not set.";
  }
  s.appendChild(el("p", statusCls, statusText));

  if ((id === "email" || id === "gotify") && !d.managed) {
    s.appendChild(el("p", "hint", "Take over the config first (⚙ > System): its settings are kept in config.toml."));
  }

  const f = el("div", "src-form");
  for (const [key, label, type, ph, secret] of spec[3]) {
    const row = el("label", "src-field");
    row.appendChild(el("span", "lbl", label));
    if (type === "select") {
      const sel = el("select");
      for (const [val, text] of [["starttls", "STARTTLS"], ["ssl", "SSL"], ["none", "None (no password)"]]) {
        const o = el("option", null, text);
        o.value = val;
        sel.appendChild(o);
      }
      sel.value = form[key] ?? c[key] ?? "starttls";
      sel.addEventListener("change", () => { form[key] = sel.value; });
      row.appendChild(sel);
    } else {
      const inp = el("input");
      inp.type = type === "password" ? "password" : type;
      inp.autocomplete = "off";
      inp.spellcheck = false;
      if (secret) {
        inp.value = form[key] || "";
        inp.placeholder = (c[key] === true || (key === "url" && c.set && id !== "gotify")) ? "set: leave empty to keep it" : ph;
      } else {
        let v = form[key] ?? c[key] ?? "";
        if (Array.isArray(v)) v = v.join(", ");
        inp.value = v;
        inp.placeholder = ph;
      }
      inp.addEventListener("input", () => { form[key] = inp.value; NTF.test[id] = null; ntf_test_line(f, id); });
      row.appendChild(inp);
    }
    f.appendChild(row);
  }

  const btns = el("div", "choices");
  const button = (label, cls, run) => {
    const b = el("button", "choice" + (cls ? " " + cls : ""), label);
    b.type = "button";
    b.addEventListener("click", run);
    btns.appendChild(b);
    return b;
  };

  button("Test", "", () => {
    NTF.test[id] = { pending: true };
    ntf_test_line(f, id);
    notify_post("test", ntf_body(id)).then((r) => { NTF.test[id] = r; ntf_test_line(f, id); });
  });

  const saveBtn = button("Save", "on", () => {
    NTF.test[id] = { pending: true };
    ntf_test_line(f, id);
    notify_post("save", ntf_body(id)).then((r) => {
      if (!r.ok) { NTF.test[id] = r; ntf_test_line(f, id); return; }
      NTF.form[id] = {};
      NTF.test[id] = null;
      toast("✓ " + spec[1] + " saved");
      ntf_reload();
    });
  });
  if ((id === "email" || id === "gotify") && !d.managed) saveBtn.disabled = true;

  if (c.set || c.password || c.token || c.host) {
    button("Forget", "danger", () => {
      notify_post("save", { channel: id, forget: true }).then((r) => {
        if (!r.ok) return toast("✕ " + (r.error || "refused"), "warn");
        toast("✓ " + spec[1] + " forgotten");
        ntf_reload();
      });
    });
  }

  f.appendChild(btns);
  f.appendChild(el("p", "alr-test"));
  ntf_test_line(f, id);
  s.appendChild(f);
}

function ntf_body(id) {
  const config = {}, secrets = {};
  const spec = NTF_CHANNELS.find((x) => x[0] === id);
  for (const [key, , type, , secret] of spec[3]) {
    const v = (NTF.form[id] || {})[key];
    if (secret) {
      if (v) secrets[key] = v;   // empty: what is saved stays
    } else {
      if (key === "port" && v !== "" && v != null) config[key] = Number(v);
      else if (key === "to" && Array.isArray(v)) config[key] = v.join(", ");
      else config[key] = v !== undefined ? v : (NTF.data.channels[id][key] ?? "");
    }
  }
  const body = { channel: id, config, secrets };
  if (id === "webhook" || id === "discord") delete body.config;
  return body;
}

function ntf_test_line(f, id) {
  const p = f.querySelector(".alr-test");
  if (!p) return;
  const t = NTF.test[id];
  p.className = "alr-test hint" + (t && !t.pending && !t.ok ? " warn" : t && t.ok ? " ok" : "");
  p.textContent = !t ? "" : t.pending ? "Working…" : t.ok ? "✓ Done: a test message was sent, look for it." : "✕ " + (t.error || "failed");
}

function ntf_events(s) {
  [...s.children].forEach((c) => { if (c.tagName !== "H3" && !c.classList.contains("set-desc")) c.remove(); });
  const d = NTF.data;
  const m = {};
  for (const [ev] of d.events) m[ev] = [...(d.matrix[ev] || [])];

  const chNames = { app: "App", ntfy: "ntfy", email: "Email", webhook: "Webhook", discord: "Discord", gotify: "Gotify" };
  const table = el("table", "ntf-matrix");

  const thead = el("thead");
  const hrow = el("tr");
  hrow.appendChild(el("th"));
  for (const ch of d.order) {
    const th = el("th", null, chNames[ch] || ch);
    if (!d.channels[ch].set) th.classList.add("off");
    hrow.appendChild(th);
  }
  thead.appendChild(hrow);
  table.appendChild(thead);

  const tbody = el("tbody");
  let save = null;
  for (const [ev, label] of d.events) {
    const tr = el("tr");
    tr.appendChild(el("th", null, label)).setAttribute("scope", "row");
    for (const ch of d.order) {
      const td = el("td");
      if (!d.channels[ch].set) td.classList.add("off");
      const cb = el("input");
      cb.type = "checkbox";
      cb.checked = m[ev].includes(ch);
      cb.disabled = !d.managed;
      cb.setAttribute("aria-label", label + " → " + ch);
      cb.addEventListener("change", () => {
        if (cb.checked) { if (!m[ev].includes(ch)) m[ev].push(ch); } else { m[ev] = m[ev].filter((x) => x !== ch); }
        if (save) save.disabled = false;
      });
      td.appendChild(cb);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  const box = el("div", "ntf-matrix-box");
  box.appendChild(table);
  s.appendChild(box);

  s.appendChild(el("p", "hint", d.explicit ? "Saved in config.toml as [notify.events]." : "Not saved yet: this is what LabHUD does by default (bad news where ⚙ > Alerts says, and to every channel that is set). Updates and sign-ins go nowhere until ticked."));
  s.appendChild(el("p", "hint", "Recoveries (a host back up, an alert gone) follow their event when LABHUD_NOTIFY_ALL is on. Urgent news is sent in the quiet hours too."));

  const btns = el("div", "choices");
  save = el("button", "choice on", "Save events");
  save.type = "button";
  save.disabled = true;
  save.addEventListener("click", () => {
    save.disabled = true;
    notify_post("events", { events: m }).then((r) => {
      if (!r.ok) { save.disabled = false; return toast("✕ " + (r.error || "not saved"), "warn"); }
      toast("✓ Events saved");
      ntf_reload();
    });
  });
  btns.appendChild(save);
  s.appendChild(btns);

  if (!d.managed) s.appendChild(el("p", "hint", "Take over the config first (⚙ > System) to change them."));
}
