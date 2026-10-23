// host_node, host_line and tile_headline (static/tiles.js, copied out unchanged) on {card, data, marks} from argv.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/tiles.js", import.meta.url), "utf8");
function extract(name) {
  const start = src.search(new RegExp(`^function ${name}\\(`, "m"));
  if (start < 0) throw new Error(`no function ${name} in tiles.js`);
  let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const names = ["tiles_rows", "tiles_pages", "host_node", "host_line", "tile_headline", "tile_trouble", "card_items", "names_line"];
const body = `${names.map(extract).join("\n")}
  return { node: host_node(card), line: host_line(card), head: tile_headline(card), trouble: tile_trouble(card), items: card_items(card), line2: names_line(items, 2) };`;
const { card, data, items, widths, cols, rows, game, group } = JSON.parse(process.argv[2]);
if (group) {
  const pure = new Function(`${["names_line", "group_tile_text"].map(extract).join("\n")}\n return group_tile_text(${group.count}, ${JSON.stringify(group.what)});`);
  process.stdout.write(JSON.stringify(pure()));
  process.exit(0);
}
if (game) {
  const pure = new Function(`${extract("game_tile_text")}\n return game_tile_text(${JSON.stringify(game.on)}, ${JSON.stringify(game.q)});`);
  process.stdout.write(JSON.stringify(pure()));
  process.exit(0);
}
if (widths) {
  const pure = new Function(`${["tiles_rows", "tiles_pages"].map(extract).join("\n")}
    return { rows: tiles_rows(${JSON.stringify(widths)}, ${cols}), pages: tiles_pages(${JSON.stringify(widths)}, ${cols}, ${rows}) };`);
  process.stdout.write(JSON.stringify(pure()));
  process.exit(0);
}
const stubs = {
  get: (path) => data[path],
  format: (v) => `${Math.round(v)}%`,
  severity: (path, fmt, v) => (v >= 92 ? "crit" : v >= 80 ? "warn" : ""),
  SEV_RANK: { "": 0, warn: 1, crit: 2 },
  is_guest: () => false,
  card_bad: (c) => ((c.metrics || []).some(([p]) => data[p] >= 92) ? true : false),
  card_status: () => true,
  guest_headline: () => ({}),
  detail_row: (d) => [String(d.name ?? ""), String(d.value ?? ""), d.bad === true],
};
const run = new Function(...Object.keys(stubs), "card", "items", body);
process.stdout.write(JSON.stringify(run(...Object.values(stubs), card, items || [])));
