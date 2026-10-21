// search_containers (static/search.js, copied out unchanged) with the page's globals stubbed, from {act, signed, rows} on argv:
// [{label, hint, command}] of what it adds. rows: the containers of the host card "pve" as container_rows gives them.
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
const { act, signed, rows } = JSON.parse(process.argv[2]);
const body = `${extract("search_norm")}\n${extract("search_containers")}
  const out = [];
  search_containers(out, act);
  return out.map((x) => ({ label: x.label, hint: x.hint, command: !!x.command }));`;
const CFG = {
  pages: [{ id: "g", title: "G", groups: [{ id: "a", title: "A" }] }],
  cards: { a: [{ id: "pve", name: "PVE", agent: "box" }, { id: "web", name: "Web", agent: "box", agent_target: "docker:web" }] },
};
const fn = new Function("CFG", "act", "signed_in", "container_rows", "action_label", "action_danger", "go", "open_panel",
  "ask_confirmation", "read_logs", body);
process.stdout.write(JSON.stringify(fn(CFG, act, () => signed, () => rows, (a) => a.replace(/^agent-.+-/, "").toUpperCase(), (a) => /stop/.test(a),
  () => {}, () => {}, () => {}, () => {})));
