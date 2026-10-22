// glance_pack (static/app.js, copied out unchanged) on {counts, titles, cols, t, gap, avail} from argv: the page of each tile.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const start = src.search(/^function glance_pack\(/m);
let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
for (; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}" && --depth === 0) break;
}
const fn = src.slice(start, i + 1);
const a = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(new Function("counts", "titles", "cols", "t", "gap", "avail", `${fn}\nreturn glance_pack(counts, titles, cols, t, gap, avail);`)(a.counts, a.titles, a.cols, a.t, a.gap, a.avail)));
