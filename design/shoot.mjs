// The screenshots in docs/screenshots, from the demo, through a Chromium's DevTools port (CDP).
//
//   LABHUD_DEMO=1 LABHUD_PORT=28195 LABHUD_HTTP=on LABHUD_DATA=/tmp/demo LABHUD_ADMIN=… python3 server.py
//   brave-origin --headless=new --remote-debugging-port=9444 --user-data-dir=/tmp/shots-profile about:blank
//   node design/shoot.mjs http://127.0.0.1:28195 [admin ticket]
//
// The ticket (from POST /api/admin/login) is only for the ⚙ shots; the wall's pages are shot signed
// out, as a display that only shows. `?static` fetches the data once (no stream, no lock, no tour),
// and the problem toast is hidden: it would cover the middle of every picture. CDP_PORT changes 9444.
import fs from "node:fs";
import path from "node:path";

const [base, ticket] = process.argv.slice(2);
if (!base) { console.error("usage: node design/shoot.mjs <labhud URL> [admin ticket]"); process.exit(2); }
const CDP = `http://127.0.0.1:${process.env.CDP_PORT || 9444}`;
// SHOTS_OUT: another folder (a package's before/after), else docs/screenshots
const OUT = process.env.SHOTS_OUT || path.join(path.dirname(new URL(import.meta.url).pathname), "..", "docs", "screenshots");

const SHOTS = [
  // [file, query and hash, width, height, signed in]
  ["general.png", "?static#general", 800, 360, false],
  ["network.png", "?static#network", 800, 360, false],
  ["security.png", "?static#security", 800, 360, false],
  ["media.png", "?static#media", 800, 360, false],
  ["games.png", "?static#games", 800, 360, false],
  ["portrait.png", "?static#general", 360, 760, false],
  ["desktop.png", "?static#general", 1920, 1080, false],
  ["settings-look.png", "?static&settings=look", 800, 360, true],
  ["settings-alerts.png", "?static&settings=alerts", 800, 360, true],
];
// SHOTS_ALL=1: every demo page at the three sizes (wall, portrait, desktop), for a before/after
// of the cards; SHOTS_ONLY=<regex> keeps only the files it matches.
if (process.env.SHOTS_ALL) {
  for (const page of ["general", "network", "security", "media", "games"])
    for (const [tag, w, h] of [["wall", 800, 360], ["portrait", 360, 760], ["desktop", 1920, 1080]])
      SHOTS.push([`all-${page}-${tag}.png`, `?static#${page}`, w, h, false]);
}
if (process.env.SHOTS_ONLY) {
  const keep = new RegExp(process.env.SHOTS_ONLY);
  SHOTS.splice(0, SHOTS.length, ...SHOTS.filter((s) => keep.test(s[0])));
}

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

async function shoot(file, query, width, height, admin) {
  const tab = await (await fetch(`${CDP}/json/new?about:blank`, { method: "PUT" })).json();
  const ws = new WebSocket(tab.webSocketDebuggerUrl);
  let id = 0;
  const waiting = new Map();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
  };
  await new Promise((r) => (ws.onopen = r));
  const send = (method, params = {}) => new Promise((r) => {
    const i = ++id;
    waiting.set(i, r);
    ws.send(JSON.stringify({ id: i, method, params }));
  });
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
  // the admin's ticket lives in localStorage: set (or clear) it on the origin first
  await send("Page.navigate", { url: `${base}/not-a-page` });
  await pause(800);
  await send("Runtime.evaluate", { expression: admin && ticket
    ? `localStorage.setItem("labhud-admin", ${JSON.stringify(ticket)})` : `localStorage.removeItem("labhud-admin")` });
  await send("Page.navigate", { url: base + "/" + query });
  await pause(5000);
  await send("Runtime.evaluate", { expression: `document.head.insertAdjacentHTML("beforeend", "<style>#toast{display:none!important}</style>")` });
  await pause(1000);
  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(path.join(OUT, file), Buffer.from(shot.result.data, "base64"));
  await fetch(`${CDP}/json/close/${tab.id}`);
  ws.close();
  console.log(`wrote ${path.join(OUT, file)}`);
}

for (const s of SHOTS) {
  if (s[4] && !ticket) { console.log(`skipped ${s[0]}: needs an admin ticket`); continue; }
  await shoot(...s);
}
