// ring_plan, half_dash and guest_headline (static/app.js, copied out unchanged) on {bars, rest} from argv: which rings a machine gets.
// tests/test_make_rings.py compares it with the app's gaugesOf (RingsTest.kt / GaugesTest.kt say the same).
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
const a = JSON.parse(process.argv[2]);
const body = `${["ring_kind", "ring_plan", "half_dash", "bad_pressure", "guest_headline"].map(extract).join("\n")}
  if (a.guests) return a.guests.map((g) => guest_headline(g));
  if (a.dash) return a.dash.map((d) => half_dash(d.len, d.share, d.from_end));
  const r = ring_plan(a.bars, a.rest);
  return r.plan.map((p) => ({ label: p.label, a: p.a[0], b: p.b ? p.b[0] : null, temp: p.temp ? p.temp[0] : null }));`;
process.stdout.write(JSON.stringify(new Function("a", body)(a)));
