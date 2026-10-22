// Runs a pure function of static/app.js, copied out unchanged: node call_fn.mjs <name> '<json array of arguments>'
import fs from "fs";

const [name, argsJson] = process.argv.slice(2);
const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
const start = src.search(new RegExp(`^function ${name}\\(`, "m"));
if (start < 0) throw new Error(`no function ${name}`);
let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
for (; i < src.length; i++) {
  if (src[i] === "{") depth++;
  else if (src[i] === "}" && --depth === 0) break;
}
const fn = src.slice(start, i + 1);
const args = JSON.parse(argsJson || "[]");
process.stdout.write(JSON.stringify(new Function("args", `${fn}\nreturn ${name}(...args);`)(args)));
