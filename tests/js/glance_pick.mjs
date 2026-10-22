// glance_kind + glance_pick (static/app.js, copied out unchanged) on {metrics} from argv: [{kind, index}].
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
function extract(re, name) {
  const start = src.search(re);
  if (start < 0) throw new Error(`no ${name} in app.js`);
  let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const order = src.match(/^const GLANCE_ORDER = .*;$/m)[0];
const body = `${order}\n${extract(/^function glance_kind\(/m, "glance_kind")}\n${extract(/^function glance_pick\(/m, "glance_pick")}
  return glance_pick(metrics);`;
const { metrics } = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(new Function("metrics", body)(metrics)));
