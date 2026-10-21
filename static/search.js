// The search box (🔍 in the top bar, or "/"): every card on every page, the bookmarks, and what
// matches nothing goes to the search engine of [search] url. Enter opens the first hit: a card's
// Web UI, else its page with its panel open; a bookmark in a new tab. It is also the command
// palette: pages, the sections of ⚙ and the cards' buttons (RESTART · name), the last two only
// for who may (signed in as the admin, or the token/role the admin gave; the PIN still holds, and
// a button asks as in the card's panel). "> text" looks only among the commands.

const SEARCH_MAX = 12;

function search_norm(s) {
  return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** The items whose text has every word of `q`, best first: a label that starts with the query, then
    one with a word that does, then the rest, each in the order given. `q` is already normalised. */
function search_rank(items, q) {
  const words = q.split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const scored = [];
  items.forEach((x, i) => {
    if (!words.every((w) => x.text.includes(w))) return;
    const label = search_norm(x.label);
    const rank = label.startsWith(words[0]) ? 0 : label.split(/[\s·\-_.]+/).some((t) => t.startsWith(words[0])) ? 1 : 2;
    scored.push([rank, i, x]);
  });
  return scored.sort((a, b) => a[0] - b[0] || a[1] - b[1]).map((r) => r[2]);
}

/** Everything the box can find: [{label, hint, text, run, command?}]. `command` ones (a card's button,
    a section of ⚙, a page) are the commands: "> restart" lists only them. A card's buttons are the
    admin's, as in its panel: only when signed in and unlocked (may_act), and they ask as the panel does. */
function search_items() {
  const out = [];
  const act = typeof may_act === "function" && may_act();
  CFG.pages.forEach((p, i) => {
    if (p.games) return;
    out.push({ label: p.title, hint: "page", kind: "chevron_right", command: true, text: search_norm("page go " + p.title), run: () => go(i) });
    for (const g of p.groups) {
      for (const c of CFG.cards[g.id] || []) {
        out.push({ label: c.name, hint: `${p.title} · ${g.title}`, kind: c.url ? "open" : "chevron_right",
          text: search_norm([c.name, c.subtitle, c.id, g.title, p.title, c.url].join(" ")),
          run: () => {
            if (c.url && !LIST_VIEW) return window.open(c.url, "_blank", "noopener");
            go(i);
            if (has_details(c)) setTimeout(() => open_panel(c), 50);
          } });
        if (!act) continue;
        for (const a of useful_actions(c)) {
          const word = action_label(a);
          out.push({ label: `${word.toLowerCase()} · ${c.name}`, hint: action_danger(a) ? "asks first" : "action", kind: "chevron_right",
            command: true, text: search_norm(`${word} ${c.name} ${c.id} ${p.title}`), run: () => ask_confirmation(c, a) });
        }
      }
    }
  });
  search_containers(out, act);
  for (const b of CFG.bookmarks || []) {
    out.push({ label: b.name, hint: b.group, kind: "link", text: search_norm([b.name, b.group, b.url].join(" ")),
      run: () => window.open(b.url, "_blank", "noopener") });
  }
  if (typeof signed_in === "function" && signed_in()) {
    if (typeof locked === "function" && locked()) {
      out.push({ label: "Unlock", hint: "PIN", kind: "chevron_right", command: true, text: "unlock pin", run: () => lock_unlock() });
    }
    for (const [group, list] of settings_groups()) {
      for (const s of list) {
        out.push({ label: s.title, hint: "⚙ " + group, kind: "chevron_right", command: true,
          text: search_norm(`settings ${s.title} ${group} ${s.about || ""}`), run: () => settings_go(s.id) });
      }
    }
  }
  return out;
}

/** The containers an agent reports, found by name: Enter opens the panel of the host card that lists them. Their
    buttons and LOGS are the rows' own (agent_containers in app.js): only when the row has buttons, and as the admin. */
function search_containers(out, act) {
  const seen = new Set();
  const cards = [];
  CFG.pages.forEach((p, i) => {
    if (p.games) return;
    for (const g of p.groups) for (const c of CFG.cards[g.id] || []) {
      cards.push([i, p, g, c]);
      const t = /^docker:(.+)$/.exec(c.agent_target || "");
      if (t && c.agent) seen.add(`${c.agent}/${t[1]}`);   // a container with a card of its own is found as that card
    }
  });
  for (const [i, p, g, c] of cards) {
    if (!c.agent || c.agent_target || c.game) continue;
    for (const x of container_rows(c)) {
      const key = `${c.agent}/${x.name}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const open = () => { go(i); setTimeout(() => open_panel(c), 50); };
      out.push({ label: x.name, hint: `container · ${c.agent}`, kind: "chevron_right", text: search_norm(`container docker ${x.name} ${c.agent} ${p.title}`), run: open });
      if (!act || !signed_in() || !x.card.actions.length) continue;
      for (const a of x.actions) {
        const word = action_label(a);
        out.push({ label: `${word.toLowerCase()} · ${x.name}`, hint: action_danger(a) ? "asks first" : "action", kind: "chevron_right", command: true,
          text: search_norm(`${word} ${x.name} container ${c.agent}`), run: () => ask_confirmation(x.card, a) });
      }
      out.push({ label: `logs · ${x.name}`, hint: "action", kind: "chevron_right", command: true, text: search_norm(`logs ${x.name} container ${c.agent}`),
        run: () => { read_logs(c.agent, x.name); open(); } });
    }
  }
}

function search_open() {
  let box = $("#search");
  if (!box) {
    box = el("div", null);
    box.id = "search";
    box.hidden = true;
    const panel = el("div", "search-box");
    const input = el("input", "search-input");
    input.type = "search";
    input.placeholder = "Search cards and links, > for commands";
    input.setAttribute("aria-label", "Search");
    const list = el("div", "search-list");
    panel.append(input, list);
    box.appendChild(panel);
    document.body.appendChild(box);
    box.addEventListener("click", (ev) => { if (ev.target === box) search_close(); });
    let sel = 0, hits = [];
    const draw = () => {
      // ">" first: only the commands (buttons of the cards, pages, ⚙)
      const raw = input.value.trim();
      const cmd = raw.startsWith(">");
      const q = search_norm(cmd ? raw.slice(1).trim() : raw);
      const all = search_items().filter((x) => !cmd || x.command);
      hits = cmd && !q ? all.filter((x) => x.hint === "action" || x.hint === "asks first" || x.hint === "PIN").slice(0, SEARCH_MAX)
        : search_rank(all, q).slice(0, SEARCH_MAX);
      const web = CFG.search && CFG.search.url;
      if (q && web && !cmd) {
        hits.push({ label: `Search the web for “${input.value.trim()}”`, hint: new URL(web.replace("%s", "")).host, kind: "search",
          run: () => window.open(web.replace("%s", encodeURIComponent(input.value.trim())), "_blank", "noopener") });
      }
      sel = Math.min(sel, Math.max(0, hits.length - 1));
      list.textContent = "";
      hits.forEach((h, i) => {
        const r = el("button", "search-hit" + (i === sel ? " on" : ""));
        r.type = "button";
        r.append(icon_svg(h.kind, 16), el("span", "l", h.label), el("span", "h", h.hint || ""));
        r.addEventListener("click", () => { search_close(); h.run(); });
        list.appendChild(r);
      });
      if (q && !hits.length) list.appendChild(el("p", "hint", "Nothing matches."));
    };
    input.addEventListener("input", () => { sel = 0; draw(); });
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape") return search_close();
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        sel = (sel + (ev.key === "ArrowDown" ? 1 : -1) + hits.length) % Math.max(1, hits.length);
        draw();
      }
      if (ev.key === "Enter" && hits[sel]) { const h = hits[sel]; search_close(); h.run(); }
    });
    box._draw = draw;
  }
  const input = box.querySelector("input");
  input.value = "";
  box._draw();
  box.hidden = false;
  input.focus();
}

function search_close() {
  const box = $("#search");
  if (box) box.hidden = true;
}

// The menu keeps clear of the tools beside the clock, however many of them are shown.
(() => {
  const tools = document.getElementById("tools");
  if (!tools || !window.ResizeObserver) return;
  new ResizeObserver(() => document.documentElement.style.setProperty("--tools-w", tools.offsetWidth + 8 + "px")).observe(tools);
})();

document.getElementById("search-btn")?.addEventListener("click", (ev) => { ev.stopPropagation(); search_open(); });

document.addEventListener("keydown", (ev) => {
  const t = ev.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable);
  if (ev.key === "/" && !typing && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
    ev.preventDefault();
    search_open();
  }
});
