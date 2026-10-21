// ⚙ > System: the config file as text, and taking it over (managed.py). Only the admin gets
// here (open_settings); labhud checks again on every call (/api/setup/*, _may_manage).
"use strict";

// what is being typed survives a redraw of ⚙ (a choice elsewhere redraws the whole panel)
const ADV = { text: null, saved: null, path: "", managed: false, writable: false, problems: [], msg: "", kind: "" };

function setup_post(op, body) {
  return fetch("/api/setup/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
    .catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

function advanced_settings(section) {
  const where = section("Config file", { wide: true, about: "config.toml: where it is, and whether LabHUD keeps its own copy (Take over) so ⚙ can change it." });
  const state = el("div", "adv-state");
  where.appendChild(state);
  const editor = section("Edit config", { wide: true, about: "The whole file as text. Checked before it is saved; a mistake keeps the last good version." });
  const keys = section("Keys set here", { about: "The secrets in secrets.env (API keys, tokens), by name only." });
  setup_post("status").then((st) => {
    if (!st.ok) return st.error === "forbidden" ? admin_forbidden((m) => state.appendChild(el("p", "hint", m))) : state.appendChild(el("p", "hint", st.error));
    adv_state(state, st);
    adv_keys(keys, st);
  });
  adv_editor(editor);
}

function adv_state(host, st) {
  host.textContent = "";
  if (st.managed) {
    host.appendChild(el("p", null, "Kept by LabHUD in " + st.path + "."));
    host.appendChild(el("p", "hint", "Everything in ⚙ writes this file. The one you mounted at " + st.mounted + " is no longer read."));
    return;
  }
  host.appendChild(el("p", null, "Read from " + st.mounted + (st.includes && st.includes.length ? " and " + st.includes.join(", ") : "") + "."));
  host.appendChild(el("p", "hint", "To change pages, sources and alerts from here, LabHUD keeps its own copy in its data folder: "
    + "Take over copies this file there (the included files merged in), and from then on that copy is the config. "
    + "Nothing is lost: the mounted file stays as it is, as a backup."));
  if (st.problem) { host.appendChild(el("p", "hint warn", st.problem)); return; }
  const row = el("div", "choices");
  const b = el("button", "choice", "Take over");
  b.type = "button";
  b.disabled = !st.can_take_over;
  b.addEventListener("click", () => {
    b.disabled = true;
    put(b, "Taking over…");
    setup_post("take-over").then((r) => {
      if (!r.ok) { b.disabled = false; put(b, "Take over"); host.appendChild(el("p", "hint warn", r.error || "refused")); return; }
      put(b, "LabHUD is restarting…");
      adv_wait_restart();
    });
  });
  row.appendChild(b);
  host.appendChild(row);
}

/** After a restart (take over, a new key): the page reloads once LabHUD answers again. */
function adv_wait_restart() {
  let tries = 0;
  const again = () => {
    tries++;
    fetch("/api/config", { cache: "no-store" }).then((r) => { if (r.ok) location.reload(); else throw 0; })
      .catch(() => { if (tries < 60) setTimeout(again, 1000); });
  };
  setTimeout(again, 2500);
}

function adv_keys(host, st) {
  const names = st.secrets || [];
  host.appendChild(el("p", "hint", names.length
    ? "Kept in secrets.env (only the names are shown): " + names.join(", ") + ". They win over the environment."
    : "None yet. Keys typed into Sources are kept in secrets.env in the data folder, and win over the environment."));
}

function adv_editor(host) {
  const where = el("p", "hint", "");
  const text = el("textarea", "adv-text");
  text.spellcheck = false;
  text.wrap = "off";
  text.setAttribute("autocapitalize", "off");
  text.setAttribute("autocomplete", "off");
  const problems = el("ul", "adv-problems");
  const msg = el("p", "hint");
  const row = el("div", "choices");
  const button = (label, run) => { const b = el("button", "choice", label); b.type = "button"; b.addEventListener("click", run); row.appendChild(b); return b; };
  const say = (m, kind) => { ADV.msg = m; ADV.kind = kind || ""; put(msg, m); msg.className = "hint " + ADV.kind; };
  const show_problems = (list) => { ADV.problems = list || []; problems.replaceChildren(...ADV.problems.map((p) => el("li", null, p))); };
  let timer = null;
  const check = () => setup_post("save-text", { text: text.value, check: true }).then((d) => {
    show_problems(d.problems);
    const changed = text.value !== ADV.saved;
    save.disabled = !d.ok || !changed || !ADV.writable;
    if (d.ok) say(changed ? "Valid. Not saved yet." : "Valid, and the same as the running file.", "ok");
    else if (d.problems) say(d.problems.length + " problem(s): nothing is saved until they are fixed.", "warn");
    else say(d.error || "refused", "warn");
  });
  text.addEventListener("input", () => {
    ADV.text = text.value;
    touched();
    clearTimeout(timer);
    timer = setTimeout(check, 700);
  });
  button("Check", check);
  const save = button("Save", () => {
    save.disabled = true;
    setup_post("save-text", { text: text.value }).then((d) => {
      show_problems(d.problems);
      if (d.ok) { ADV.saved = text.value; say("Saved. The screens take it a few seconds after ⚙ closes.", "ok"); }
      else say(d.error || "Not saved.", "warn");
    });
  });
  button("Revert", () => { text.value = ADV.saved || ""; ADV.text = text.value; show_problems([]); say("Back to the saved file.", ""); save.disabled = true; });
  save.disabled = true;
  host.append(where, text, problems, row, msg);

  const fill = () => {
    put(where, ADV.path + (ADV.writable ? "" : " (read-only: Take over above to edit it here)"));
    text.value = ADV.text ?? ADV.saved ?? "";
    text.readOnly = !ADV.writable;
    show_problems(ADV.problems);
    if (ADV.msg) say(ADV.msg, ADV.kind);
    save.disabled = !ADV.writable || text.value === ADV.saved;
  };
  if (ADV.saved !== null) return fill();
  setup_post("text").then((d) => {
    if (!d.ok) { say(d.error === "forbidden" ? "Refused: sign in again." : d.error || "refused", "warn"); return; }
    Object.assign(ADV, { saved: d.text, path: d.path, managed: d.managed, writable: d.writable });
    fill();
  });
}
