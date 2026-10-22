// uptime_day_shares (static/app.js, copied out unchanged) on {values} from argv: one share a day.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const start = src.search(/^function uptime_day_shares\(/m);
let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
for (; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}" && --depth === 0) break;
}
const fn = src.slice(start, i + 1);
const { values } = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(new Function("values", `${fn}\nreturn uptime_day_shares(values);`)(values)));
