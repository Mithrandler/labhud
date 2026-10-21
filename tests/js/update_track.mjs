// track_goal (static/app.js, copied out unchanged) for a container's UPDATE: it ends with the
// agent's event about that container, not with the first event of another one, and a failed
// update ends in red with the agent's words: tests/test_cardstate.py UpdateTrack.
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
  let EV = [];
  const get = (p) => p === "events.recent" ? EV : null;
  const card_status = () => true, quiet_off = () => false;
  ${["track_goal", "update_goal", "game_goal"].map(extract).join("\n")}
  const now = Date.now() / 1000;
  const card = { id: "vm:jellyfin", name: "jellyfin", agent: "n100", agent_target: "docker:jellyfin" };
  const out = [];
  for (const end of [{ name: "n100: jellyfin updated (a1b2c3 → d4e5f6)", bad: false },
                     { name: "n100: jellyfin: update failed: pull jellyfin/jellyfin:latest: denied", bad: true }]) {
    const t = track_goal("agent-ab12cd34-docker-jellyfin-update", card);
    EV = [{ name: "n100: jellyfin-old updated (x → y)", t: now, bad: false },
          { name: "n100: jellyfin updated (old)", t: now - 600, bad: false }];
    const before = [t.want(), t.failed()];
    EV = [Object.assign({ t: now + 1 }, end), ...EV];
    out.push({ verb: t.verb, timeout: t.timeout, before, after: [t.want(), t.failed()] });
  }
  return out;`;
process.stdout.write(JSON.stringify(new Function(body)()));
