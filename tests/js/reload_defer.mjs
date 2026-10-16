// Runs overlay_open and reload_when_free (copied out of static/app.js, unchanged) against a fake
// page and clock, and prints when the page reloaded: tests/test_reload.py checks it.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");

function extract(name) {
  const start = src.search(new RegExp(`^function ${name}\\(`, "m"));
  if (start < 0) throw new Error(`no function ${name} in app.js`);
  let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}

const out = new Function("page", "log", `
  const nodes = page.nodes, timers = [];
  const $ = (s) => nodes[s] || null;
  const location = { reload: () => log.push("reload@" + page.now) };
  const setInterval = (fn) => { timers.push(fn); return timers.length - 1; };
  const clearInterval = (i) => { timers[i] = null; };
  let reloadPending = false;
  ${extract("overlay_open")}
  ${extract("reload_when_free")}
  return { reload_when_free, tick: () => { page.now++; for (const t of timers) if (t) t(); } };
`);

const steps = JSON.parse(fs.readFileSync(0, "utf8"));
const page = { now: 0, nodes: { "#settings": { hidden: true }, "#panel": { hidden: true }, "#confirm": { hidden: true } } };
const log = [];
const app = out(page, log);
for (const s of steps) {
  if (s === "reload") app.reload_when_free();
  else if (s === "tick") app.tick();
  else { const [id, hidden] = s; page.nodes[id].hidden = hidden; }
}
console.log(JSON.stringify(log));
