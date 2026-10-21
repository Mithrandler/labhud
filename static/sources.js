// ⚙ > Integrations: what LabHUD reads (onboard.catalog), set up from the page: the form, Test, Save
// (the keys go to secrets.env, a card to the page, LabHUD restarts), and Scan my network (scan.py).
"use strict";

const SRC = { catalog: null, open: "", values: {}, page: "", test: null, filter: "", managed: null, scan: null, cidr: "" };

function sources_settings(section) {
  const box = section("Sources", { wide: true, about: "What LabHUD reads: pick one, fill in its address and key, Test, Save." });
  const host = el("div", "src");
  box.appendChild(host);
  const scanBox = section("Scan my network", { wide: true, about: "Looks for known services on your network and offers them as sources." });
  const scanHost = el("div", "src-scan");
  scanBox.appendChild(scanHost);
  if (SRC.catalog) { draw_sources(host); draw_scan(scanHost); return; }
  host.appendChild(el("p", "hint", "Reading what LabHUD can show…"));
  Promise.all([setup_post("status"), setup_post("catalog"), setup_post("scan-status")]).then(([st, cat, sc]) => {
    SRC.managed = !!(st.ok && st.managed);
    if (!cat.ok) { host.textContent = ""; host.appendChild(el("p", "hint warn", cat.error || "refused")); return; }
    SRC.catalog = cat.sources;
    SRC.scan = sc.ok ? sc : null;
    SRC.cidr = SRC.cidr || (sc.ok ? sc.cidr || sc.default : "");
    draw_sources(host);
    draw_scan(scanHost);
    if (SRC.scan && SRC.scan.running) poll_scan();
  });
}

function redraw_sources() {
  const h = $("#settings .src");
  if (h) draw_sources(h);
}

function monogram(title) {
  const m = el("span", "src-mono", title.replace(/[^A-Za-z0-9]/g, "").slice(0, 2).toUpperCase());
  let hue = 0;
  for (const ch of title) hue = (hue * 31 + ch.charCodeAt(0)) % 360;
  m.style.setProperty("--hue", String(hue));
  return m;
}

function draw_sources(host) {
  host.textContent = "";
  if (!SRC.managed) {
    host.appendChild(el("p", "hint", "Sources set up here are kept by LabHUD (keys in its data folder, a card on a page): "
      + "⚙ > System > Take over, once, first. Below: what is set up now."));
  }
  if (SRC.open) host.appendChild(source_form(SRC.catalog.find((c) => c.name === SRC.open)));
  const find = el("input", "src-find");
  find.placeholder = "Find a source…";
  find.value = SRC.filter;
  find.addEventListener("input", () => {
    SRC.filter = find.value;
    for (const t of host.querySelectorAll(".src-tile")) t.hidden = !t.dataset.k.includes(SRC.filter.toLowerCase());
  });
  host.appendChild(find);
  const grid = el("div", "src-grid");
  for (const c of SRC.catalog) {
    const t = el("button", "src-tile" + (c.configured ? " on" : "") + (SRC.open === c.name ? " open" : ""));
    t.type = "button";
    t.dataset.k = (c.title + " " + c.name + " " + c.about).toLowerCase();
    t.hidden = !t.dataset.k.includes(SRC.filter.toLowerCase());
    const txt = el("span", "src-txt");
    txt.append(el("b", null, c.title), el("span", "about", c.about || ""));
    t.append(monogram(c.title), txt);
    if (c.configured) t.appendChild(el("span", "src-ok", "✓"));
    t.addEventListener("click", () => {
      SRC.open = SRC.open === c.name ? "" : c.name;
      SRC.values = {}; SRC.test = null;
      redraw_sources();
      const f = $("#settings .src-form");
      if (f) f.scrollIntoView({ block: "nearest" });
    });
    grid.appendChild(t);
  }
  host.appendChild(grid);
}

function source_form(c) {
  const f = el("div", "src-form");
  const head = el("div", "src-form-head");
  head.append(monogram(c.title), el("b", null, c.title));
  const x = el("button", "lay-more", "✕");
  x.type = "button";
  x.addEventListener("click", () => { SRC.open = ""; redraw_sources(); });
  head.appendChild(x);
  f.appendChild(head);
  if (c.about) f.appendChild(el("p", "hint", c.about));
  if (c.with && c.with.length) {
    f.appendChild(el("p", "hint", `Comes on with ${c.with.join(", ")}: set that one up and this one follows.`));
    return f;
  }
  for (const fd of c.fields) {
    const row = el("label", "src-field");
    row.appendChild(el("span", "lbl", fd.label));
    const input = el("input");
    input.type = fd.secret ? "password" : "text";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = SRC.values[fd.name] || "";
    if (fd.secret && c.configured) input.placeholder = "set: leave empty to keep it";
    else if (fd.hint) input.placeholder = fd.hint;
    input.addEventListener("input", () => { SRC.values[fd.name] = input.value; SRC.test = null; put_test(f); });
    row.appendChild(input);
    f.appendChild(row);
  }
  if (SRC.managed) {
    const pages = CFG.pages.filter((p) => !p.games);
    if (!SRC.page || !pages.some((p) => p.id === SRC.page)) SRC.page = pages.length ? pages[0].id : "";
    const row = el("label", "src-field");
    row.appendChild(el("span", "lbl", "Its card on"));
    const sel = el("select");
    for (const p of pages) { const o = el("option", null, p.title); o.value = p.id; sel.appendChild(o); }
    sel.value = SRC.page;
    sel.addEventListener("change", () => { SRC.page = sel.value; });
    row.appendChild(sel);
    f.appendChild(row);
  }
  const btns = el("div", "choices");
  const button = (label, run, cls) => { const b = el("button", "choice" + (cls ? " " + cls : ""), label); b.type = "button"; b.addEventListener("click", run); btns.appendChild(b); return b; };
  button("Test", () => {
    SRC.test = { pending: true };
    put_test(f);
    setup_post("try", { name: c.name, values: SRC.values }).then((r) => { SRC.test = r; put_test(f); });
  });
  if (SRC.managed) {
    button("Save", () => {
      SRC.test = { pending: true, saving: true };
      put_test(f);
      setup_post("source-save", { name: c.name, values: SRC.values, page: SRC.page }).then((r) => {
        if (!r.ok) { SRC.test = r; put_test(f); return; }
        SRC.test = { ok: true, saved: true };
        put_test(f);
        if (r.restart) { toast("✓ " + c.title + " saved: LabHUD restarts"); adv_wait_restart(); }
        else { toast("✓ " + c.title + " saved"); SRC.catalog = null; }
      });
    }, "on");
    if (c.configured) {
      button("Forget", () => {
        put($("#confirm-text"), `Forget ${c.title}? Its keys and its card go; LabHUD restarts.`);
        ask([["FORGET", () => setup_post("source-forget", { name: c.name }).then((r) => {
          if (!r.ok) return toast("✕ " + (r.error || "refused"));
          toast("✓ " + c.title + " forgotten" + (r.restart ? ": LabHUD restarts" : ""));
          if (r.restart) adv_wait_restart();
        }), true]]);
      }, "danger");
    }
  }
  f.appendChild(btns);
  f.appendChild(el("p", "hint src-test"));
  put_test(f);
  if (SRC.managed) f.appendChild(el("p", "hint", "Save tests it first. The keys are kept in LabHUD's data folder (secrets.env, "
    + "only LabHUD can read it) and never come back to this page."));
  return f;
}

function put_test(f) {
  const p = f.querySelector(".src-test");
  if (!p) return;
  const t = SRC.test;
  p.className = "hint src-test" + (!t || t.pending ? "" : t.ok ? " ok" : " warn");
  put(p, !t ? "" : t.pending ? (t.saving ? "Testing, then saving…" : "Testing…")
    : t.saved ? "✓ Saved." : t.ok ? `✓ It answers (${(t.keys || []).slice(0, 6).join(", ")}${(t.keys || []).length > 6 ? "…" : ""}).`
    : "✕ " + (t.error || "refused"));
}

// ---------------------------------------------------------------------------------------------
// Scan my network
// ---------------------------------------------------------------------------------------------

let scanTimer = null;

function draw_scan(host) {
  host.textContent = "";
  host.appendChild(el("p", "hint", "Looks for the services LabHUD knows on one subnet, only when you ask, slowly "
    + "(16 connections at most, about a minute for a /24) so an IDS does not take it for an attack. "
    + "The agents' addresses are added. A service counts only when its page names it, not because a port is open."));
  const row = el("div", "lay-bar");
  const input = el("input", "src-find");
  input.value = SRC.cidr;
  input.placeholder = "192.168.1.0/24";
  input.addEventListener("input", () => { SRC.cidr = input.value.trim(); });
  const go = el("button", "choice on", SRC.scan && SRC.scan.running ? "Scanning…" : "Scan");
  go.type = "button";
  go.disabled = !!(SRC.scan && SRC.scan.running);
  go.addEventListener("click", () => {
    go.disabled = true;
    setup_post("scan", { cidr: SRC.cidr }).then((r) => {
      if (!r.ok) { go.disabled = false; toast("✕ " + (r.error || "refused")); return; }
      SRC.scan = r;
      draw_scan(host);
      poll_scan();
    });
  });
  row.append(input, go);
  host.appendChild(row);
  const sc = SRC.scan;
  if (!sc || !sc.total) return;
  const bar = el("div", "src-progress");
  const fill = el("i");
  fill.style.width = (100 * sc.done / sc.total).toFixed(1) + "%";
  bar.appendChild(fill);
  host.appendChild(bar);
  host.appendChild(el("p", "hint", `${sc.cidr}: ${sc.done} of ${sc.total} addresses${sc.running ? "…" : ", done"}`
    + `. ${sc.found.length} service(s) found.` + (sc.error ? " " + sc.error : "")));
  for (const it of sc.found) {
    const c = SRC.catalog.find((x) => x.name === it.source);
    const r = el("div", "src-found");
    r.append(monogram(c ? c.title : it.source), el("b", null, c ? c.title : it.source), el("span", "url", it.url));
    if (it.configured || (c && c.configured)) r.appendChild(el("span", "src-ok", "✓ set up"));
    else if (c) {
      const b = el("button", "choice small", "Set up");
      b.type = "button";
      b.addEventListener("click", () => {
        SRC.open = c.name; SRC.test = null;
        const url = c.fields.find((fd) => /_URL$/.test(fd.name));
        SRC.values = url ? { [url.name]: it.url } : {};
        redraw_sources();
        const f = $("#settings .src-form");
        if (f) f.scrollIntoView({ block: "start" });
      });
      r.appendChild(b);
    }
    host.appendChild(r);
  }
}

function poll_scan() {
  clearTimeout(scanTimer);
  scanTimer = setTimeout(() => {
    const host = $("#settings .src-scan");
    if (!host || $("#settings").hidden) return;
    setup_post("scan-status").then((r) => {
      if (!r.ok) return;
      SRC.scan = r;
      draw_scan(host);
      if (r.running) poll_scan();
      else toast(`✓ Scan done: ${r.found.length} service(s)`);
    });
  }, 1500);
}
