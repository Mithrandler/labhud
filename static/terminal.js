// A container's live log and a terminal in it (POST /api/setup/stream-*, agents.py). Both are a
// window over the page, opened by the admin from the container's row. The page polls the server,
// which holds each poll until there is something; what comes is shown and not kept: the log keeps
// the last LOG_LINES lines on screen, the terminal what xterm.js keeps of its own scrollback. The
// terminal needs the PIN (the server refuses it otherwise) and a tick of its own in ⚙ > Hosts &
// agents > Terminal. xterm.js (static/vendor/xterm, MIT) is loaded when a terminal is first opened.
"use strict";

const LOG_LINES = 1000;

function b64_bytes(text) {
  const bin = atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function bytes_b64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 4096) bin += String.fromCharCode(...bytes.subarray(i, i + 4096));
  return btoa(bin);
}

/** A window over the page: a title, a body, Close. `closed` runs once when it goes. */
function stream_window(title, closed) {
  const wrap = el("div", "stream-window");
  const head = el("div", "stream-head");
  head.appendChild(el("span", "stream-title", title));
  const status = el("span", "hint", "opening…");
  const shut = el("button", "choice small", "Close");
  shut.type = "button";
  const body = el("div", "stream-body");
  head.append(status, shut);
  wrap.append(head, body);
  document.body.appendChild(wrap);
  let done = false;
  const close = () => {
    if (done) return;
    done = true;
    wrap.remove();
    closed();
  };
  shut.addEventListener("click", close);
  wrap.addEventListener("keydown", (e) => { if (e.key === "Escape" && !wrap.querySelector(".xterm")) close(); });
  return { body, status, close, alive: () => !done };
}

/** Reads a stream until it ends or `alive()` says no; `data(bytes)` for each piece, `end(reason)` once. */
async function stream_follow(id, alive, data, end) {
  let after = 0;
  let fails = 0;
  while (alive()) {
    const r = await setup_post("stream-read", { id, after });
    if (!alive()) return;
    if (!r.ok) {
      if (++fails > 3 || r.error === "no such stream") return end(r.error || "lost");
      await new Promise((ok) => setTimeout(ok, 1000));
      continue;
    }
    fails = 0;
    after = r.next;
    if (r.data) data(b64_bytes(r.data));
    if (r.end) return end(r.end);
  }
}

/** The container's log, live. */
function live_log(agent, name) {
  let id = null;
  const w = stream_window(`${agent} · ${name} · live log`, () => { if (id) setup_post("stream-close", { id }); });
  const pre = el("pre", "logs-text stream-log");
  w.body.appendChild(pre);
  const dec = new TextDecoder();
  let lines = [""];
  setup_post("stream-open", { agent, kind: "logs", target: name }).then((r) => {
    if (!r.ok) return put(w.status, "✕ " + (r.error || "failed"));
    id = r.id;
    put(w.status, "live");
    stream_follow(id, w.alive, (bytes) => {
      const parts = dec.decode(bytes, { stream: true }).split("\n");
      lines[lines.length - 1] += parts[0];
      lines.push(...parts.slice(1));
      if (lines.length > LOG_LINES) lines = lines.slice(-LOG_LINES);
      const stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
      pre.textContent = lines.join("\n");
      if (stick) pre.scrollTop = pre.scrollHeight;
    }, (why) => { put(w.status, "ended: " + why); id = null; });
  });
}

let XTERM = null;   // the promise of xterm.js being loaded

function load_xterm() {
  if (XTERM) return XTERM;
  const script = (src) => new Promise((ok, no) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = ok;
    s.onerror = () => no(new Error("could not load " + src));
    document.head.appendChild(s);
  });
  const css = document.createElement("link");
  css.rel = "stylesheet";
  css.href = "/vendor/xterm/xterm.css";
  document.head.appendChild(css);
  XTERM = script("/vendor/xterm/xterm.js").then(() => script("/vendor/xterm/addon-fit.js"));
  XTERM.catch(() => { XTERM = null; });
  return XTERM;
}

/** A shell in the container (the server asks for the PIN, the agent for a tick of its own). */
function shell_window(agent, name) {
  let id = null;
  let term = null;
  const w = stream_window(`${agent} · ${name} · terminal`, () => {
    if (id) setup_post("stream-close", { id });
    if (term) term.dispose();
  });
  w.body.classList.add("stream-term");
  load_xterm().then(() => {
    if (!w.alive()) return;
    term = new Terminal({ cursorBlink: true, fontSize: 13, scrollback: 2000, theme: { background: "#000000" } });
    const fit = new FitAddon.FitAddon();
    term.loadAddon(fit);
    term.open(w.body);
    fit.fit();
    return setup_post("stream-open", { agent, kind: "shell", target: name, cols: term.cols, rows: term.rows }).then((r) => {
      if (!r.ok) return put(w.status, "✕ " + (r.error || "failed"));
      id = r.id;
      put(w.status, "open · closes after 3 minutes without a key");
      let pending = [];
      let timer = 0;
      const send = () => {   // keys typed within a few ms go together
        timer = 0;
        const all = pending.join("");
        pending = [];
        if (all) setup_post("stream-input", { id, data: bytes_b64(new TextEncoder().encode(all)) });
      };
      term.onData((d) => { pending.push(d); if (!timer) timer = setTimeout(send, 15); });
      term.onResize(({ cols, rows }) => { if (id) setup_post("stream-resize", { id, cols, rows }); });
      window.addEventListener("resize", () => { if (w.alive()) fit.fit(); });
      term.focus();
      stream_follow(id, w.alive, (bytes) => term.write(bytes), (why) => {
        put(w.status, "ended: " + why);
        term.write(`\r\n\x1b[2m[${why}]\x1b[0m\r\n`);
        id = null;
      });
    });
  }).catch((e) => put(w.status, "✕ " + e.message));
}

/** The LIVE and SHELL buttons of a container's row. SHELL only where the container is ticked for it. */
function stream_buttons(agent, name) {
  const out = [];
  const live = el("button", "", "LIVE");
  live.type = "button";
  live.title = "its log, as it is written";
  live.addEventListener("click", () => live_log(agent, name));
  out.push(live);
  if (((get(`agents.${agent}.terminal`) || [])).includes(name)) {
    const sh = el("button", "", "SHELL");
    sh.type = "button";
    sh.title = "a terminal in the container (needs the PIN)";
    sh.addEventListener("click", () => shell_window(agent, name));
    out.push(sh);
  }
  return out;
}
