// The setup page (setupmode.py). Plain JS, no build step, like the display. Every call carries the
// setup code; nothing from the services is ever put in the page as HTML (textContent only).
"use strict";

const $ = (id) => document.getElementById(id);
const ORDER = ["code", "proxmox", "guests", "services", "display", "review"];
const S = {
  code: "",
  catalog: [],
  tried: new Set(),
  pve: null,        // what Proxmox answered (no secret)
  include: new Set(), // "node/vmid"
  weather: null,    // [lat, lon, tz, city]
};

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v;
    else if (k === "class") e.className = v;
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function msg(node, text, kind = "") {
  node.textContent = text || "";
  node.className = "msg " + kind;
}

async function api(step, body = {}) {
  const r = await fetch("/setup/api/" + step, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Labhud-Setup": S.code },
    body: JSON.stringify(body),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { data = { ok: false, error: "no answer (" + r.status + ")" }; }
  if (r.status === 403 && step !== "hello") {
    sessionStorage.removeItem("labhud-setup");
    show("code");
    msg($("code-msg"), data.error || "the code is no longer accepted", "bad");
  }
  return data;
}

function show(step) {
  for (const id of ORDER) $(id).hidden = id !== step;
  const at = ORDER.indexOf(step);
  document.querySelectorAll("#steps li").forEach((li, i) => {
    li.className = i === at ? "on" : i < at ? "done" : "";
  });
  window.scrollTo(0, 0);
}

async function busy(button, fn) {
  button.disabled = true;
  try { return await fn(); } finally { button.disabled = false; }
}

// -- 1. the code ----------------------------------------------------------------------------

$("code-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  S.code = $("code-input").value.trim().toUpperCase();
  const b = ev.submitter || $("code-form").querySelector("button");
  await busy(b, start);
});

async function start() {
  const r = await api("hello");
  if (!r.ok) return msg($("code-msg"), r.error || "wrong code", "bad");
  try { sessionStorage.setItem("labhud-setup", S.code); } catch (e) { /* private mode */ }
  S.catalog = r.catalog;
  S.tried = new Set(r.tried || []);
  S.writable = r.writable;
  S.folder = r.folder;
  $("d-hosts").value = defaultHosts();
  $("find-hosts").value = location.hostname;
  renderCatalog();
  if (r.proxmox) { S.pve = r.proxmox; showPve(); }
  show(S.jump || "proxmox");
}

function defaultHosts() {
  const port = location.port || (location.protocol === "https:" ? "443" : "80");
  return [...new Set([location.hostname + ":" + port, "localhost:" + port])].join(",");
}

// -- 2. Proxmox -----------------------------------------------------------------------------

$("pve-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const b = ev.submitter || $("pve-form").querySelector("button");
  msg($("pve-result"), "connecting…");
  const r = await busy(b, () => api("proxmox", {
    url: $("pve-url").value, token_id: $("pve-id").value, secret: $("pve-secret").value,
  }));
  if (!r.ok) { S.pve = null; $("pve-next").hidden = true; return msg($("pve-result"), r.error, "bad"); }
  $("pve-secret").value = "";
  S.pve = r;
  S.include = new Set(Object.entries(r.guests).flatMap(([n, gs]) => gs.map((g) => n + "/" + g[0])));
  showPve();
});

function showPve() {
  const r = S.pve;
  const total = Object.values(r.guests).reduce((a, g) => a + g.length, 0);
  let text = `Proxmox ${r.version}: ${r.nodes.length} node(s) (${r.nodes.join(", ")}), ${total} guest(s). `;
  let kind = "ok";
  if (r.extra.length) {
    text += `Warning: this token can do more than read (${r.extra.join(", ")}). labhud only needs PVEAuditor; a leaked .env could change your lab.`;
    kind = "warn";
  } else text += "The token can only read. Good.";
  msg($("pve-result"), text, kind);
  $("pve-url").value = r.url;
  $("pve-id").value = r.token_id;
  $("pve-pin-row").hidden = !r.fingerprint;
  $("pve-fp").textContent = r.fingerprint ? r.fingerprint.toUpperCase().match(/../g).join(":") : "";
  $("pve-next").hidden = false;
  try { $("find-hosts").value = [...new Set([new URL(r.url).hostname, location.hostname])].join(","); } catch (e) { /* keep */ }
  if (!S.include.size) S.include = new Set(Object.entries(r.guests).flatMap(([n, gs]) => gs.map((g) => n + "/" + g[0])));
  renderGuests();
}

$("pve-next").addEventListener("click", () => show("guests"));
$("pve-skip").addEventListener("click", async () => {
  await api("forget-proxmox");
  S.pve = null;
  show("services");
});

// -- 3. guests ------------------------------------------------------------------------------

function renderGuests() {
  const list = $("guest-list");
  list.replaceChildren();
  for (const node of S.pve.nodes) {
    const box = el("div", { class: "guests" });
    for (const [vmid, name, kind] of S.pve.guests[node] || []) {
      const key = node + "/" + vmid;
      const cb = el("input", { type: "checkbox", checked: S.include.has(key) });
      cb.addEventListener("change", () => { cb.checked ? S.include.add(key) : S.include.delete(key); });
      box.append(el("label", { class: "check" }, cb,
        el("span", {}, el("span", { text: `${vmid} ${name} ` }), el("span", { class: "kind", text: kind === "lxc" ? "LXC" : "VM" }))));
    }
    if (!box.childElementCount) box.append(el("span", { class: "small", text: "no guests" }));
    list.append(el("div", { class: "node" }, el("h3", { text: node }), box));
  }
}

function setAll(pick) {
  S.include = new Set();
  for (const [node, gs] of Object.entries(S.pve.guests)) for (const g of gs) if (pick(node, g[0])) S.include.add(node + "/" + g[0]);
  renderGuests();
}
$("g-all").addEventListener("click", () => setAll(() => true));
$("g-none").addEventListener("click", () => setAll(() => false));
$("g-running").addEventListener("click", () => {
  const on = new Set((S.pve.running || []).map(([n, v]) => n + "/" + v));
  setAll((n, v) => on.has(n + "/" + v));
});
$("guests-next").addEventListener("click", () => show("services"));

// -- 4. services ----------------------------------------------------------------------------

function renderCatalog() {
  const box = $("catalog");
  box.replaceChildren();
  const derived = [];
  for (const src of S.catalog) {
    if (!src.fields.length) { derived.push(src); continue; }
    const state = el("span", { class: "state" });
    const setState = () => {
      if (S.tried.has(src.name)) { state.textContent = "✓ works"; state.className = "state ok"; }
      else if (src.configured) { state.textContent = "set in the environment"; state.className = "state ok"; }
      else { state.textContent = ""; }
    };
    setState();
    const form = el("form");
    for (const f of src.fields) {
      const input = el("input", { name: f.name, type: f.secret ? "password" : "text", autocomplete: "off",
        placeholder: f.secret && S.tried.has(src.name) ? "kept from the last try" : (f.hint.startsWith("http") ? f.hint.split(/[ ,]/)[0] : "") });
      form.append(el("label", {}, el("span", { text: f.label }), input,
        f.hint ? el("span", { class: "hint", text: f.name + " · " + f.hint }) : el("span", { class: "hint", text: f.name })));
    }
    const out = el("p", { class: "msg" });
    const tryB = el("button", { text: "Try" });
    const forget = el("button", { type: "button", class: "ghost", text: "Remove", hidden: !S.tried.has(src.name) });
    form.append(el("div", { class: "row" }, tryB, forget), out);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const values = {};
      for (const i of form.querySelectorAll("input")) values[i.name] = i.value;
      msg(out, "asking " + src.title + "…");
      const r = await busy(tryB, () => api("try", { name: src.name, values }));
      if (r.ok) {
        S.tried.add(src.name);
        msg(out, "It answered: " + (r.keys || []).join(", "), "ok");
        for (const i of form.querySelectorAll("input[type=password]")) { i.value = ""; i.placeholder = "kept from the last try"; }
        forget.hidden = false;
      } else msg(out, r.error, "bad");
      setState();
    });
    forget.addEventListener("click", async () => {
      await api("forget", { name: src.name });
      S.tried.delete(src.name);
      forget.hidden = true;
      msg(out, "removed");
      setState();
    });
    box.append(el("details", { class: "svc", "data-source": src.name },
      el("summary", {}, el("b", { text: src.title }), el("span", { class: "about", text: src.about }), state), form));
  }
  if (derived.length) {
    box.append(el("p", { class: "small", text: "Turn on by themselves: " +
      derived.map((d) => `${d.title} (with ${d.with.join(" or ")})`).join(", ") + "." }));
  }
}
$("services-next").addEventListener("click", () => show("display"));

$("find-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const hosts = $("find-hosts").value.split(",").map((h) => h.trim()).filter(Boolean);
  msg($("find-msg"), "looking…");
  $("find-list").replaceChildren();
  const r = await busy(ev.submitter || $("find-form").querySelector("button"), () => api("suggest", { hosts }));
  if (!r.ok) return msg($("find-msg"), r.error, "bad");
  msg($("find-msg"), r.found.length ? `Found ${r.found.length}:` : "Nothing known found on those hosts.", r.found.length ? "ok" : "warn");
  for (const f of r.found) {
    const src = S.catalog.find((c) => c.name === f.source);
    const use = el("button", { type: "button", class: "ghost", text: "Use" });
    use.addEventListener("click", () => {
      const box = document.querySelector(`#catalog details[data-source="${f.source}"]`);
      if (!box) return;
      box.open = true;
      const url = box.querySelector("input[name$='_URL']");
      if (url) { url.value = f.url; url.focus(); }
      box.scrollIntoView({ block: "start" });
    });
    $("find-list").append(el("div", { class: "row" }, el("span", { text: `${src ? src.title : f.source} at ${f.url}` }), use));
  }
});

// -- 5. display -----------------------------------------------------------------------------

$("d-find").addEventListener("click", async (ev) => {
  const r = await busy(ev.target, () => api("weather", { city: $("d-city").value }));
  if (!r.ok) { S.weather = null; return msg($("d-weather"), r.error, "bad"); }
  S.weather = r.weather;
  msg($("d-weather"), r.weather ? `${r.weather[3]}: ${r.weather[0]}, ${r.weather[1]}` : "not found", r.weather ? "ok" : "warn");
});

function plan() {
  return {
    title: $("d-title").value,
    hosts: $("d-hosts").value.split(",").map((h) => h.trim()).filter(Boolean),
    weather: $("d-city").value.trim() ? S.weather : null,
    pin: $("pve-pin").checked,
    include: [...S.include].map((k) => { const i = k.lastIndexOf("/"); return [k.slice(0, i), Number(k.slice(i + 1))]; }),
    services: [...S.tried],
  };
}

$("display-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if ($("d-city").value.trim() && !S.weather) $("d-find").click();
  const r = await api("preview", plan());
  $("r-where").textContent = S.writable
    ? `labhud will write config.toml and .env to ${S.folder}, then start as the display.`
    : `${S.folder} is not writable: mount it read-write (docs/install.md), or copy config.toml from below.`;
  $("r-where").className = S.writable ? "" : "warn";
  $("r-problems").replaceChildren(...(r.problems || []).map((p) => el("li", { text: p })));
  $("r-config").textContent = r.config || "";
  $("r-env").textContent = (r.env || []).map((n) => n + "=…").join("\n");
  $("r-write").disabled = !r.ok || !S.writable;
  show("review");
});

$("r-write").addEventListener("click", async (ev) => {
  const r = await busy(ev.target, () => api("finish", plan()));
  if (!r.ok) {
    $("r-problems").replaceChildren(...(r.problems || [r.error]).map((p) => el("li", { text: p })));
    return;
  }
  ev.target.disabled = true;
  try { sessionStorage.removeItem("labhud-setup"); } catch (e) { /* ignore */ }
  msg($("r-msg"), "Written. labhud is starting as the display…", "ok");
  for (let i = 0; i < 30; i++) {
    await new Promise((ok) => setTimeout(ok, 1000));
    try {
      const c = await fetch("/api/config", { cache: "no-store" });
      if (c.ok) { location.href = "/"; return; }
    } catch (e) { /* still restarting */ }
  }
  msg($("r-msg"), "It did not come back in 30 s: look at labhud's log.", "bad");
});

// -- start ----------------------------------------------------------------------------------

(async () => {
  let saved = "";
  try { saved = sessionStorage.getItem("labhud-setup") || ""; } catch (e) { /* private mode */ }
  // http://host:port/#ABCD-EFGH (the link in the log): the fragment never reaches a server or a log.
  // An optional ":<step>" opens that step, for screenshots.
  const m = location.hash.match(/^#([A-Za-z2-9]{4}-[A-Za-z2-9]{4})(?::([a-z]+))?$/);
  if (m) {
    saved = m[1].toUpperCase();
    history.replaceState(null, "", location.pathname);
    if (ORDER.includes(m[2])) S.jump = m[2];
  }
  if (saved) {
    S.code = saved;
    $("code-input").value = saved;
    const r = await api("hello");
    if (r.ok) { sessionStorage.setItem("labhud-setup", saved); return start(); }
  }
  show("code");
})();
