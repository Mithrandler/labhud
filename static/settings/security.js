/* ⚙ > System > Security: the admin (password and authenticator), the PIN of the screens, the
   browsers signed in (account.js, lock.js); single sign-on, how long a session and an unlock last,
   and the networks (api/security.py, api/oidc.py). Also the "Sign in with single sign-on" button of
   the sign-in dialog, and its way back (/?oidc=<r>). */
"use strict";

register_section({
  id: "security", group: "system", title: "Security", icon: "security",
  about: "The admin, the PIN, single sign-on, sessions, networks",
  keywords: "admin password totp authenticator pin lock sessions sign out oidc sso authentik single sign-on timeout idle network proxy cidr",
  render: (body, ctx) => {
    admin_settings(ctx.section);
    lock_settings(ctx.section);
    const sso = ctx.section("Single sign-on", { wide: true, about: "Sign in with Authentik (or another OpenID Connect provider). The password and authenticator stay as the way back in." });
    const time = ctx.section("Session timeout", { about: "How long a browser stays signed in, and how long a screen stays open without the PIN." });
    const nets = ctx.section("Trusted networks", { wide: true, about: "The proxies whose X-Forwarded-For is believed, and the only networks the admin may sign in and act from." });
    for (const s of [sso, time, nets]) s.appendChild(el("p", "hint", "Loading…"));
    security_post("get").then((r) => {
      for (const s of [sso, time, nets]) s.querySelector(".hint:last-child")?.remove();
      if (!r.ok) {
        const say = (m) => sso.appendChild(el("p", "hint", m));
        return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not load.");
      }
      sso_settings(sso, r, ctx);
      timeout_settings(time, r, ctx);
      network_settings(nets, r, ctx);
    });
  },
});

function security_post(op, body) {
  return fetch("/api/security/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
    .catch(() => ({ ok: false, error: "Could not reach LabHUD." }));
}

/** A labelled input in ⚙'s form style (the same as Integrations'). */
function sec_field(host, label, value, opts) {
  const row = el("label", "src-field");
  const input = el("input");
  input.value = value || "";
  input.spellcheck = false; input.autocapitalize = "none"; input.autocomplete = "off";
  Object.assign(input, opts || {});
  row.append(el("span", "lbl", label), input);
  host.appendChild(row);
  return input;
}

const sec_list = (text) => String(text || "").split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

/** Save: a button and the line that says how it went; `body()` gives what to send. */
function sec_save(host, r, label, body, extra) {
  const row = el("div", "choices");
  const said = el("p", "hint");
  const go = el("button", "choice on", label || "Save");
  go.type = "button";
  go.disabled = !r.kept;
  go.addEventListener("click", () => {
    go.disabled = true;
    put(said, "Saving…");
    security_post("save", body()).then((x) => {
      go.disabled = false;
      put(said, x.ok ? "✓ Saved. Every screen takes it in a moment." : "✕ " + (x.error === "forbidden" ? "refused" : x.error || "not saved"));
    });
  });
  row.appendChild(go);
  for (const b of extra || []) row.appendChild(b);
  host.append(row, said);
  if (!r.kept) said.textContent = "Take over the config first (⚙ > System), so LabHUD can write it.";
  return said;
}

function sso_settings(s, r, ctx) {
  const o = r.oidc || {};
  s.appendChild(el("p", "hint", r.oidc ? `On: anyone the provider says is ${[...(o.allow || []), ...(o.allow_groups || []).map((g) => "in " + g)].join(", ")} signs in as the admin.`
    : "Off. In Authentik: a new OAuth2/OpenID provider (confidential, signing key RS256) and an application for it; then fill in what it shows."));
  const cb = el("p", "hint");
  cb.append("Redirect URI to give the provider: ", el("code", null, o.redirect_uri || r.callback));
  s.appendChild(cb);
  const issuer = sec_field(s, "Issuer", o.issuer, { placeholder: "https://auth.example/application/o/labhud/" });
  const client = sec_field(s, "Client ID", o.client_id);
  const secret = sec_field(s, "Client secret", "", { type: "password", placeholder: r.secret_set ? "kept (type to replace)" : "from the provider" });
  const allow = sec_field(s, "Users", (o.allow || []).join(", "), { placeholder: "user names or emails" });
  const groups = sec_field(s, "Groups", (o.allow_groups || []).join(", "), { placeholder: "e.g. labhud admins" });
  const redirect = sec_field(s, "Redirect URI", o.redirect_uri, { placeholder: "this address (" + r.callback + ")" });
  const test = el("button", "choice", "Test");
  test.type = "button";
  const said = sec_save(s, r, "Save", () => ({ oidc: { issuer: issuer.value.trim(), client_id: client.value.trim(), secret: secret.value,
    allow: sec_list(allow.value), allow_groups: sec_list(groups.value), redirect_uri: redirect.value.trim() } }), [test]);
  test.addEventListener("click", () => {
    put(said, "Asking the provider…");
    security_post("oidc-test", { issuer: issuer.value.trim() }).then((x) => put(said, x.ok
      ? `✓ The provider answers: ${x.keys} signing key${x.keys === 1 ? "" : "s"}.` : "✕ " + (x.error || "no answer")));
  });
  if (r.oidc && r.kept) {
    const off = el("button", "choice danger", "Turn off");
    off.type = "button";
    off.addEventListener("click", () => security_post("save", { oidc: null }).then((x) => {
      if (x.ok) setTimeout(() => ctx.redraw(), 800); else put(said, "✕ " + (x.error || "not saved"));
    }));
    said.previousSibling.appendChild(off);
  }
}

function timeout_settings(s, r) {
  const pick = (label, value, options) => {
    const row = el("label", "src-field");
    const sel = el("select");
    for (const [v, text] of options) {
      const o = el("option", null, text);
      o.value = String(v);
      if (v === value) o.selected = true;
      sel.appendChild(o);
    }
    if (!options.some(([v]) => v === value)) { const o = el("option", null, String(value)); o.value = String(value); o.selected = true; sel.prepend(o); }
    row.append(el("span", "lbl", label), sel);
    s.appendChild(row);
    return sel;
  };
  const hours = pick("Signed in for", r.session_hours, [[1, "1 hour"], [4, "4 hours"], [12, "12 hours"], [24, "1 day"], [72, "3 days"],
    [168, "7 days"], [336, "14 days"], [720, "30 days"]]);
  const pin = pick("Open without PIN", r.pin_minutes, [[1, "1 min"], [2, "2 min"], [5, "5 min"], [10, "10 min"], [15, "15 min"],
    [30, "30 min"], [60, "1 hour"], [120, "2 hours"]]);
  s.appendChild(el("p", "hint", "Both count from the last use. " + (r.pin_on ? "" : "The PIN time matters once a PIN is set (Lock).")));
  sec_save(s, r, "Save", () => ({ session_hours: Number(hours.value), pin_minutes: Number(pin.value) }));
}

function network_settings(s, r) {
  const you = el("p", "hint");
  you.append("This browser comes from ", el("b", null, r.your_ip), r.via_proxy ? ` (through the proxy ${r.peer}).` : ".");
  s.appendChild(you);
  const proxies = sec_field(s, "Trusted proxies", (r.trusted_proxies || []).join(", "), { placeholder: "e.g. 172.18.0.0/16 (none: X-Forwarded-For is ignored)" });
  const nets = sec_field(s, "Admin networks", (r.admin_networks || []).join(", "), { placeholder: "e.g. 192.168.1.0/24, 10.6.0.0/24 (none: anywhere)" });
  s.appendChild(el("p", "hint", "Outside the admin networks nobody signs in, and a signed-in browser or phone only shows. A change that would leave this browser outside is refused."));
  sec_save(s, r, "Save", () => ({ trusted_proxies: sec_list(proxies.value), admin_networks: sec_list(nets.value) }));
}

// ---------------------------------------------------------------------------------------------
// The sign-in dialog's button, and the way back from the provider
// ---------------------------------------------------------------------------------------------

const OIDC_BIND = "labhud-oidc-bind";

/** The button above the password form (account.js open_signin, when /api/config says oidc). */
function oidc_signin_button(said) {
  const wrap = el("div", "signin-sso");
  const b = el("button", "go sso", "Sign in with single sign-on");
  b.type = "button";
  b.addEventListener("click", () => {
    b.disabled = true;
    fetch("/api/admin/oidc/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "web" }) })
      .then((r) => r.json()).then((r) => {
        if (!r.ok) throw new Error(r.error === "forbidden" ? "refused here" : r.error);
        try { sessionStorage.setItem(OIDC_BIND, r.bind); } catch (e) { throw new Error("this browser keeps nothing (private mode?)"); }
        location.assign(r.url);
      }).catch((e) => { b.disabled = false; put(said, "✕ " + (e.message || "Could not reach LabHUD.")); });
  });
  wrap.append(b, el("p", "hint", "or with the password and a code:"));
  return wrap;
}

/** Back from the provider: /?oidc=<r> (or ?oidc_error=…) — finish with this tab's bind. */
function oidc_return() {
  const q = new URLSearchParams(location.search);
  const r = q.get("oidc"), error = q.get("oidc_error");
  if (!r && !error) return;
  q.delete("oidc"); q.delete("oidc_error");
  history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "") + location.hash);
  let bind = "";
  try { bind = sessionStorage.getItem(OIDC_BIND) || ""; sessionStorage.removeItem(OIDC_BIND); } catch (e) { /* none */ }
  const say = (m) => (typeof show_message === "function" ? show_message(m) : null);
  const ready = (n) => (typeof CFG !== "undefined" && CFG) || n <= 0 ? Promise.resolve() : new Promise((ok) => setTimeout(ok, 200)).then(() => ready(n - 1));
  if (error) {
    const why = { denied: "the provider said no", refused: "that user may not be the admin here", network: "not from an admin network", off: "single sign-on is off" };
    return ready(50).then(() => say("✕ Single sign-on: " + (why[error] || "it did not work (see LabHUD's log)")));
  }
  fetch("/api/admin/oidc/finish", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ r, bind }) })
    .then((x) => x.json()).then((x) => ready(50).then(() => {
      if (!x.ok) return say("✕ Single sign-on: expired or started in another tab. Try again.");
      ADMIN_IDLE = x.idle || ADMIN_IDLE;
      set_admin(x.ticket, x.user);
      check_admin();
      say("✓ Signed in as " + x.user + " (single sign-on: " + x.sso + ")");
    })).catch(() => say("Could not reach LabHUD."));
}

window.addEventListener("load", oidc_return);
