// search_rank (static/search.js, copied out unchanged) on {items, q} from argv: the labels, best first.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/search.js", import.meta.url), "utf8");
function extract(name) {
  const start = src.search(new RegExp(`^function ${name}\\(`, "m"));
  if (start < 0) throw new Error(`no function ${name} in search.js`);
  let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const body = `${extract("search_norm")}\n${extract("search_rank")}
  return search_rank(items, q).map((x) => x.label);`;
const { items, q } = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(new Function("items", "q", body)(items, q)));
