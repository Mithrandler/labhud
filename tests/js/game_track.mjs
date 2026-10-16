// track_goal (static/app.js, copied out unchanged) for a game server started from its tile, while
// the list is sorted again under it: tests/test_cardstate.py GameTrack.
import fs from "fs";

const src = fs.readFileSync(new URL("../../static/app.js", import.meta.url), "utf8");
function extract(name) {
  const start = src.search(new RegExp(`^function ${name}\\(`, "m"));
  let i = src.indexOf("{", src.indexOf(")", start)), depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`unbalanced ${name}`);
}
const body = `
  let LIST = [{ id: "7d", state: "offline" }, { id: "cs", state: "offline" }];
  const CFG = { pages: [{ id: "games", games: "live.games.servers" }] };
  const get = () => LIST;
  const card_status = (c) => c.check[1], quiet_off = () => false;
  ${["track_goal", "game_goal"].map(extract).join("\n")}
  const tile = { game: LIST[1], check: ["known", false] };        // the CS tile, pressed
  const t = track_goal("agent-ab12cd34-ptero-cs-start", tile);
  const before = t.want();
  // CS is up and now sorted first: the slot it was in shows 7 Days (offline)
  LIST = [{ id: "cs", state: "running" }, { id: "7d", state: "offline" }];
  Object.assign(tile, { game: LIST[1], check: ["known", false] });
  return { verb: t.verb, before, after: t.want() };`;
process.stdout.write(JSON.stringify(new Function(body)()));
