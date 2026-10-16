// The display's action order (static/app.js, copied out unchanged) for the list on argv:
// tests/test_cardstate.py ActionOrder compares it with cardstate.py.
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
const body = `${src.match(/^const ACTION_RANK = [^;]+;/m)[0]}
  ${["action_verb", "action_rank", "action_danger", "order_actions"].map(extract).join("\n")}
  const o = order_actions(list);
  return [o, o.map(action_danger)];`;
process.stdout.write(JSON.stringify(new Function("list", body)(JSON.parse(process.argv[2]))));
