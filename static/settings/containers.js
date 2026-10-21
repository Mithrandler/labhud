/* ⚙ > Infrastructure > Containers: every agent's ticked containers in one place: updating on their own (in one window),
   webhooks for CI, cleaning unused images (settings.js, containers_settings); the private registries labhud signs in to when an agent pulls,
   and the image scans (api/registries.py). */
"use strict";

/** POST a JSON body to /api/registries/<op> with admin + lock headers. */
function registries_post(op, body) {
  return fetch("/api/registries/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: r.ok }))).catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

register_section({
  id: "containers", group: "infra", title: "Containers", icon: "container",
  about: "Updates on their own, webhooks, clean-up, on every host",
  keywords: "docker container image update auto window webhook ci prune clean registry scan cve trivy vulnerability critical high grype login token",
  render: (body, ctx) => {
    containers_settings(ctx);
    const reg = ctx.section("Registries", { wide: true, about: "Private registries labhud signs in to when an agent pulls or updates one of their images. The token stays in labhud (secrets.env) and goes, sealed, only to the agent that pulls an image of that registry." });
    const scan = ctx.section("Vulnerability scan", { wide: true, about: "Critical and high CVEs of each ticked container's image, from trivy or grype on its host (Image scan, ticked in Hosts & agents)." });
    const wait = el("p", "hint", "Reading…");
    reg.appendChild(wait);
    registries_post("list").then((r) => {
      wait.remove();
      if (!r.ok) { const say = (m) => reg.appendChild(el("p", "hint", m)); return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "Could not read the registries."); }
      registries_rows(reg, r, ctx);
      scan_rows(scan, r, ctx);
    });
  },
});

/** Render the list of known registries and, when labhud may, the form to add or change one. */
function registries_rows(box, r, ctx) {
  if (!r.registries || r.registries.length === 0) {
    box.appendChild(el("p", "hint", "None yet: images are pulled with the host's own logins (docker login)."));
  }
  for (const g of r.registries || []) {
    const row = el("div", "reg-row");
    row.appendChild(el("b", null, g.host));
    row.appendChild(el("span", "reg-user", g.user));
    row.appendChild(el("span", "reg-token" + (g.token ? "" : " missing"), g.token ? "token set" : "no token"));
    row.appendChild(el("span", "reg-used", g.containers + (g.containers === 1 ? " container" : " containers")));
    const rm = el("button", "choice small", "Remove");
    rm.type = "button";
    rm.addEventListener("click", () => {
      put($("#confirm-text"), `Remove ${g.host} and its token?`);
      ask([["REMOVE", () => registries_post("remove", { host: g.host }).then((x) => { if (!x.ok) show_message(x.error || "Refused."); ctx.redraw(); }), true]]);
    });
    row.appendChild(rm);
    box.appendChild(row);
  }
  if (r.managed === false) {
    box.appendChild(el("p", "hint", "Take over the config (System) to add a registry."));
    return;
  }
  const form = el("form", "src-form reg-form");
  const field = (label, name, type, placeholder) => {
    const row = el("label", "src-field");
    row.appendChild(el("span", "lbl", label));
    const input = el("input");
    input.name = name;
    input.type = type;
    input.placeholder = placeholder;
    input.autocomplete = "off";
    input.spellcheck = false;
    row.appendChild(input);
    form.appendChild(row);
    return input;
  };
  const host = field("Registry", "host", "text", "ghcr.io");
  const user = field("User", "user", "text", "your user");
  const token = field("Token", "token", "password", "a token that may pull (read-only)");
  token.autocomplete = "new-password";
  const save = el("button", "choice", "Save registry");
  save.type = "submit";
  form.appendChild(save);
  form.appendChild(el("p", "hint", "Saving a registry that exists changes its user; an empty token keeps the one it has."));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const body = { host: host.value.trim(), user: user.value.trim() };
    if (token.value) body.token = token.value;
    save.disabled = true;
    registries_post("save", body).then((x) => {
      save.disabled = false;
      if (!x.ok) return show_message(x.error || "Refused.");
      token.value = "";
      ctx.redraw();
    });
  });
  box.appendChild(form);
}

/** Render the scanner availability, the hold-back switch, and the per-image CVE rows. */
function scan_rows(box, r, ctx) {
  const scanners = r.scanners || [];
  if (scanners.length === 0) {
    box.appendChild(el("p", "hint", "No agent has found trivy or grype on its host. Install one there (the agent does not download it), restart the agent, then tick Image scan in Hosts & agents."));
  } else {
    box.appendChild(el("p", "hint", "Can scan: " + scanners.map((s) => s.agent + (s.on ? "" : " (not ticked)")).join(", ") + "."));
  }
  const hold = el("button", "choice" + (r.hold_critical ? " on" : ""), "Hold back an update with new critical CVEs");
  hold.type = "button";
  hold.title = "An update labhud starts on its own (Update window) is kept out when the new image has more critical CVEs than the running one. Buttons and webhooks still update.";
  hold.addEventListener("click", () => {
    hold.classList.add("pending");
    registries_post("hold", { on: !r.hold_critical }).then((x) => {
      if (!x.ok) show_message(x.error || "Refused.");
      ctx.redraw();
    });
  });
  const row = el("div", "choices");
  row.appendChild(hold);
  box.appendChild(row);
  box.appendChild(el("p", "hint", "Only on hosts with Image scan ticked; elsewhere an update goes ahead as before."));
  const rows = r.scans || [];
  if (rows.length) {
    const list = el("div", "scan-list");
    for (const x of rows) {
      const line = el("div", "scan-row" + (x.critical ? " bad" : ""));
      line.appendChild(el("b", null, x.name));
      line.appendChild(el("span", "scan-host", x.agent));
      line.appendChild(el("span", "scan-image", x.image));
      line.appendChild(el("span", "scan-count", scan_counts(x)));
      line.appendChild(el("span", "scan-when", x.t ? "scanned " + ago(x.t) : ""));
      list.appendChild(line);
    }
    box.appendChild(list);
  }
}

/** Short human label for a scan row's critical and high CVE counts. */
function scan_counts(x) {
  if (!x.critical && !x.high) return "no critical or high";
  return [x.critical ? x.critical + " critical" : "", x.high ? x.high + " high" : ""].filter(Boolean).join(" · ");
}
