/* ⚙ > System > System: config.toml (Take over, the editor, the keys: advanced.js), Backup and restore
   (api/backup.py), About, and the Danger zone (New keys and Remove every phone: api/security.py): what cannot be undone, each behind a typed word. */
"use strict";

register_section({
  id: "system", group: "system", title: "System", icon: "setup",
  about: "config.toml, backup, about, danger zone",
  keywords: "config toml take over edit keys secrets backup restore about version danger reset",
  render: (body, ctx) => {
    advanced_settings(ctx.section);
    backup_settings(ctx.section("Backup and restore", { wide: true, about: "config.toml and LabHUD's data in one file; "
      + "the secrets only encrypted, with a password. A daily backup keeps the last few." }));
    about_settings(ctx.section("About", { about: "This LabHUD, and where the project lives." }));
    ctx.danger("Each needs its word typed first. None of them can be undone.", [
      ["Sign out every browser", "Every browser signed in as the admin, this one too, needs the password and a code again.",
        "SIGN OUT", () => admin_post("logout-all").then(() => { set_admin(""); show_message("Signed out everywhere"); })],
      ["Remove every phone", "Every paired LabHUD app stops working until it is paired again.",
        "REMOVE", () => danger_post("remove-phones", "REMOVE", (r) => `✓ ${r.phones} phone${r.phones === 1 ? "" : "s"} removed`)],
      ["Restore a backup", "Replaces config.toml and LabHUD's data with the backup picked above (what is there now is backed up first), "
        + "then LabHUD restarts.", "RESTORE", () => backup_restore()],
      ["New keys", "New keys for the agents and the phones: each has to be added again.", "NEW KEYS",
        () => danger_post("new-keys", "NEW KEYS", (r) => `✓ New keys: add the ${r.agents} agents and pair the ${r.phones} phones again`)],
    ]);
  },
});

/** The logo, the version and the links (GET /api/brand/about, api/brand.py). */
function about_settings(s) {
  const box = el("div", "about");
  const logo = el("img", "about-logo");
  logo.src = "/brand/lockup.svg"; logo.alt = "LabHUD";
  const facts = el("div", "about-facts");
  box.append(logo, facts);
  s.appendChild(box);
  fetch("/api/brand/about").then((r) => r.json()).then((r) => {
    facts.appendChild(el("p", "about-version", "Version " + (r.version || "?")));
    const names = { source: "Source", app: "Android app", agent: "Agent" };
    const links = el("p", "about-links");
    for (const [k, url] of Object.entries(r.links || {})) {
      const a = el("a", null, names[k] || k);
      a.href = url; a.target = "_blank"; a.rel = "noopener";
      links.appendChild(a);
    }
    facts.appendChild(links);
  }).catch(() => facts.appendChild(el("p", "hint", "Could not reach LabHUD.")));
}

/** Danger zone > Remove every phone and New keys (package F, api/security.py): the typed word goes too. */
function danger_post(op, word, done) {
  security_post(op, { confirm: word }).then((r) => show_message(r.ok ? done(r) : "✕ " + (r.error === "forbidden" ? "refused" : r.error || "not done")));
}

// --- Backup and restore (api/backup.py) ---

const BKP = { data: null, pick: "", password: "", secrets: false, busy: "" };

function backup_post(op, body) {
  return fetch("/api/backup/" + op, {
    method: "POST", headers: admin_headers(lock_headers({ "Content-Type": "application/json" })), body: JSON.stringify(body || {}),
  }).then((r) => r.json().catch(() => ({ ok: false, error: "no answer (" + r.status + ")" })))
    .catch(() => ({ ok: false, error: "could not reach LabHUD" }));
}

function backup_settings(s) {
  const host = el("div", "bkp");
  s.appendChild(host);
  host.appendChild(el("p", "hint", "Reading…"));
  backup_post("list").then((r) => {
    host.textContent = "";
    if (!r.ok) {
      const say = (m) => host.appendChild(el("p", "hint warn", m));
      return r.error === "forbidden" ? admin_forbidden(say) : say(r.error || "refused");
    }
    BKP.data = r;
    if (BKP.pick && !r.backups.some((b) => b.name === BKP.pick)) BKP.pick = "";
    draw_backup(host);
  });
}

function backup_again() { BKP.busy = ""; open_settings(); }

function backup_size(n) { return n >= 1 << 20 ? (n / (1 << 20)).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }

function draw_backup(host) {
  const d = BKP.data;
  if (d.problem) return host.appendChild(el("p", "hint warn", d.problem));
  const last = d.last;
  if (last) {
    const when = new Date(last.t * 1000).toLocaleString();
    host.appendChild(el("p", "hint" + (last.ok ? "" : " warn"), last.ok ? `Last backup: ${last.name} (${when})` : `✕ Last backup failed (${when}): ${last.error}`));
  }

  // one now
  const now = el("div", "src-form");
  const head = el("div", "src-form-head");
  head.appendChild(el("b", null, "Back up now"));
  now.appendChild(head);
  const sec = el("label", "bkp-check");
  const box = el("input");
  box.type = "checkbox"; box.checked = BKP.secrets; box.disabled = !d.encryption;
  box.addEventListener("change", () => { BKP.secrets = box.checked; });
  sec.append(box, el("span", null, " With the secrets (secrets.env, the HTTPS key): encrypted"));
  now.appendChild(sec);
  now.appendChild(backup_password_field("Password", d.password_set ? "empty: the saved backup password" : "at least 8 characters; empty: not encrypted",
    () => BKP.password, (v) => { BKP.password = v; }));
  if (!d.encryption) now.appendChild(el("p", "hint", "Encryption needs the Python package cryptography (it is in the Docker image)."));
  const row = el("div", "choices");
  const make = el("button", "choice on", BKP.busy === "create" ? "Backing up…" : "Back up");
  make.type = "button"; make.disabled = !!BKP.busy;
  make.addEventListener("click", () => {
    BKP.busy = "create"; put(make, "Backing up…"); make.disabled = true;
    backup_post("create", { secrets: BKP.secrets, password: BKP.password }).then((r) => {
      if (!r.ok) { toast("✕ " + (r.error || "not made"), "warn"); return backup_again(); }
      BKP.password = ""; BKP.pick = r.name;
      toast("✓ " + r.name);
      backup_again();
    });
  });
  const up = el("button", "choice", BKP.busy === "upload" ? "Uploading…" : "Upload a backup…");
  up.type = "button"; up.disabled = !!BKP.busy;
  const file = el("input");
  file.type = "file"; file.accept = ".gz,.enc"; file.hidden = true;
  file.addEventListener("change", () => { if (file.files[0]) backup_upload(file.files[0]); });
  up.addEventListener("click", () => file.click());
  row.append(make, up, file);
  now.appendChild(row);
  host.appendChild(now);

  // the list
  if (!d.backups.length) host.appendChild(el("p", "hint", "No backup yet."));
  else host.appendChild(el("p", "hint", "Pick one to restore it (the Danger zone below), or download it: a backup kept only here goes with this disk."));
  const list = el("div", "bkp-list");
  for (const b of d.backups) {
    const r = el("label", "bkp-row" + (BKP.pick === b.name ? " on" : ""));
    const pick = el("input");
    pick.type = "radio"; pick.name = "bkp-pick"; pick.checked = BKP.pick === b.name;
    pick.addEventListener("change", () => { BKP.pick = b.name; for (const x of list.children) x.classList.toggle("on", x === r); });
    const text = el("span", "bkp-name");
    text.append(el("b", null, (b.encrypted ? "🔒 " : "") + b.name), el("span", "hint", ` ${backup_size(b.size)} · ${new Date(b.t * 1000).toLocaleString()}`));
    const dl = el("button", "choice small", "Download");
    dl.type = "button";
    dl.addEventListener("click", (e) => { e.preventDefault(); backup_download(b.name); });
    const rm = el("button", "choice small danger", "Delete");
    rm.type = "button";
    rm.addEventListener("click", (e) => {
      e.preventDefault();
      put($("#confirm-text"), `Delete ${b.name}?`);
      ask([["DELETE", () => backup_post("delete", { name: b.name }).then((x) => { if (!x.ok) toast("✕ " + (x.error || "refused"), "warn"); backup_again(); }), true]]);
    });
    r.append(pick, text, dl, rm);
    list.appendChild(r);
  }
  host.appendChild(list);
  if (d.backups.some((b) => b.encrypted)) {
    host.appendChild(backup_password_field("Its password", "to restore an encrypted backup", () => BKP.restorePw || "", (v) => { BKP.restorePw = v; }));
  }

  // daily
  const st = d.settings;
  const daily = el("div", "src-form");
  const dh = el("div", "src-form-head");
  dh.appendChild(el("b", null, "Daily backup"));
  daily.appendChild(dh);
  const want = { ...st };
  const field = (label, control) => { const l = el("label", "src-field"); l.append(el("span", "lbl", label), control); daily.appendChild(l); };
  const on = el("select");
  for (const [v, t] of [["true", "On"], ["false", "Off"]]) { const o = el("option", null, t); o.value = v; on.appendChild(o); }
  on.value = String(st.daily);
  on.addEventListener("change", () => { want.daily = on.value === "true"; });
  field("Daily", on);
  const at = el("select");
  for (let h = 0; h < 24; h++) { const o = el("option", null, String(h).padStart(2, "0") + ":00"); o.value = String(h); at.appendChild(o); }
  at.value = String(st.at);
  at.addEventListener("change", () => { want.at = Number(at.value); });
  field("At", at);
  const keep = el("input");
  keep.type = "number"; keep.min = 1; keep.max = 90; keep.value = st.keep;
  keep.addEventListener("input", () => { want.keep = Number(keep.value); });
  field("Keep the last", keep);
  daily.appendChild(el("p", "hint", d.password_set ? "A backup password is saved: the daily backups hold the secrets too, encrypted."
    : "No backup password saved: the daily backups leave the secrets out."));
  let pw = "";
  daily.appendChild(backup_password_field("Backup password", d.password_set ? "set: type a new one to change it" : "at least 8 characters (kept in secrets.env)",
    () => pw, (v) => { pw = v; }));
  const drow = el("div", "choices");
  const save = el("button", "choice on", "Save");
  save.type = "button";
  save.addEventListener("click", () => {
    const body = { daily: want.daily, at: want.at, keep: want.keep };
    if (pw) body.password = pw;
    backup_post("settings", body).then((r) => {
      if (!r.ok) return toast("✕ " + (r.error || "not saved"), "warn");
      toast("✓ Daily backup saved");
      backup_again();
    });
  });
  drow.appendChild(save);
  if (d.password_set) {
    const forget = el("button", "choice danger", "Forget the password");
    forget.type = "button";
    forget.addEventListener("click", () => backup_post("settings", { forget_password: true }).then((r) => {
      if (!r.ok) return toast("✕ " + (r.error || "refused"), "warn");
      toast("✓ Backup password forgotten");
      backup_again();
    }));
    drow.appendChild(forget);
  }
  daily.appendChild(drow);
  host.appendChild(daily);
}

function backup_password_field(label, ph, get, set) {
  const l = el("label", "src-field");
  const i = el("input");
  i.type = "password"; i.autocomplete = "new-password"; i.spellcheck = false;
  i.value = get(); i.placeholder = ph;
  i.addEventListener("input", () => set(i.value));
  l.append(el("span", "lbl", label), i);
  return l;
}

/** The file through the browser: the route answers JSON, so it comes as base64. */
function backup_download(name) {
  backup_post("download", { name }).then((r) => {
    if (!r.ok) return toast("✕ " + (r.error || "refused"), "warn");
    const raw = atob(r.data), bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const a = el("a");
    a.href = URL.createObjectURL(new Blob([bytes], { type: "application/octet-stream" }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  });
}

/** A file from this computer, in parts of 768 KiB (each part one small JSON request). */
function backup_upload(file) {
  const PART = 768 * 1024;
  BKP.busy = "upload"; open_settings();
  const send = (i, id) => {
    const piece = file.slice(i * PART, (i + 1) * PART);
    return piece.arrayBuffer().then((buf) => {
      let bin = "";
      const b = new Uint8Array(buf);
      for (let k = 0; k < b.length; k += 0x8000) bin += String.fromCharCode.apply(null, b.subarray(k, k + 0x8000));
      const last = (i + 1) * PART >= file.size;
      return backup_post("upload", { id, part: i, last, data: btoa(bin) }).then((r) => {
        if (!r.ok) throw new Error(r.error || "refused");
        return last ? r : send(i + 1, r.id);
      });
    });
  };
  send(0, null).then((r) => { BKP.pick = r.name; toast("✓ Uploaded: " + r.name); backup_again(); })
    .catch((e) => { toast("✕ " + e.message, "warn"); backup_again(); });
}

/** Danger zone > Restore a backup: the one picked above. */
function backup_restore() {
  if (!BKP.pick) return toast("✕ Pick a backup first, in Backup and restore", "warn");
  const enc = BKP.pick.endsWith(".enc");
  if (enc && !BKP.restorePw) return toast("✕ It is encrypted: type its password under the list", "warn");
  toast("Restoring " + BKP.pick + "…");
  backup_post("restore", { name: BKP.pick, password: enc ? BKP.restorePw : "", word: "RESTORE" }).then((r) => {
    if (!r.ok) return toast("✕ " + (r.error || "refused"), "warn");
    BKP.restorePw = "";
    toast("✓ Restored (what was there: " + r.before + "). LabHUD restarts.");
    adv_wait_restart();
  });
}
