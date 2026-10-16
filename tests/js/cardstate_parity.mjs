// Runs the display's own card functions (copied out of static/app.js, unchanged) on a config and
// a snapshot read from stdin, and prints what they say about every card: tests/test_cardstate.py
// compares it with cardstate.py.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const NAMES = ["get", "bytes", "used_of", "rate", "duration", "format", "bad_pressure", "severity",
  "raw_severity", "maintenance_until", "quiet_off", "off_label", "clock", "card_status", "raw_status",
  "card_bad", "is_guest", "summary", "detail_row", "when", "short_time", "action_label", "useful_actions",
  "action_verb", "action_rank", "action_danger", "order_actions", "game_buttons", "game_card"];

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

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const body = `
  let D = input.data, CFG = input.cfg;
  const nf = new Intl.NumberFormat("en-US");
  const nf1 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const LAST_SEV = {};
  const SEV_RANK = { "": 0, warn: 1, crit: 2 };
  ${src.match(/^const AGENT_VERBS = [^;]+;/m)[0]}
  ${src.match(/^const ACTION_RANK = [^;]+;/m)[0]}
  const MONTHS = ${JSON.stringify(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])};
  ${NAMES.map(extract).join("\n")}
  const rounds = [];
  for (const snap of input.snapshots) {
    D = snap;
    const out = {};
    rounds.push(out);
    for (const cards of Object.values(CFG.cards)) for (const c of cards) {
      out[c.id] = {
        status: card_status(c), bad: card_bad(c), summary: summary(c),
        metrics: (c.metrics || []).map(([p, , f]) => [format(get(p), f), severity(p, f, get(p))]),
        actions: useful_actions(c).map((a) => [a, action_label(a), action_danger(a)]),
      };
    }
    // the games pages: each server's tile, as game_card() makes it in mount_games()
    for (const p of CFG.pages) if (p.games) (get(p.games) || []).forEach((sv, i) => {
      const g = game_card(sv, i);
      out["games:" + p.id + ":" + i] = { title: g.title, line: g.line, running: g.check[1],
        actions: useful_actions(g).map((a) => [a, action_label(a), action_danger(a)]) };
    });
  }
  return rounds;`;
process.stdout.write(JSON.stringify(new Function("input", body)(input)));
