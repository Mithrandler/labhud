// The config editor (editmode.py): the code from `init.py edit` is in the link's fragment, which
// never reaches a server or a log. Checked as you type; saved only when the check passes.
"use strict";
const $ = (id) => document.getElementById(id);
let code = "";
let timer = null;
let saved = "";

function msg(text, kind = "") { $("msg").textContent = text || ""; $("msg").className = "msg " + kind; }

async function api(step, body = {}) {
  const r = await fetch("/edit/api/" + step, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Labhud-Edit": code }, body: JSON.stringify(body),
  });
  let d = {};
  try { d = await r.json(); } catch (e) { d = { ok: false, error: "no answer (" + r.status + ")" }; }
  if (r.status === 403) { msg(d.error || "refused", "bad"); $("save").disabled = true; }
  return d;
}

function problems(list) {
  $("problems").replaceChildren(...(list || []).map((p) => { const li = document.createElement("li"); li.textContent = p; return li; }));
}

async function check() {
  const d = await api("check", { text: $("text").value });
  problems(d.problems);
  const changed = $("text").value !== saved;
  $("save").disabled = !d.ok || !changed;
  if (d.ok) msg(changed ? "Valid. Not saved yet." : "Valid, and the same as the running file.", "ok");
  else if (d.problems) msg(d.problems.length + " problem(s): nothing is saved until they are fixed.", "warn");
}

$("text").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(check, 700); });
$("check").addEventListener("click", check);
$("save").addEventListener("click", async () => {
  $("save").disabled = true;
  const d = await api("save", { text: $("text").value });
  problems(d.problems);
  if (d.ok) { saved = $("text").value; msg("Saved. The display reloads it in a few seconds.", "ok"); }
  else msg("Not saved.", "bad");
});
window.addEventListener("beforeunload", (ev) => { if ($("text").value !== saved) ev.preventDefault(); });

(async () => {
  const m = location.hash.match(/^#([A-Za-z2-9]{4}-[A-Za-z2-9]{4})$/);
  if (m) { code = m[1].toUpperCase(); try { sessionStorage.setItem("labhud-edit", code); } catch (e) { /* ignore */ } }
  else { try { code = sessionStorage.getItem("labhud-edit") || ""; } catch (e) { /* ignore */ } }
  history.replaceState(null, "", location.pathname);
  if (!code) return msg("Run `python3 init.py edit` where labhud runs, and open the link it prints.", "warn");
  const d = await api("load");
  if (!d.ok) return;
  saved = d.text;
  $("text").value = d.text;
  $("text").hidden = false;
  $("where").textContent = d.path + (d.writable ? "" : " (not writable: saving will fail)");
  msg("Loaded. Every change is checked as you type.", "ok");
})();
