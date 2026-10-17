// The search box (🔍 in the top bar, or "/"): every card on every page, the bookmarks, and what
// matches nothing goes to the search engine of [search] url. Enter opens the first hit: a card's
// Web UI, else its page with its panel open; a bookmark in a new tab.

const SEARCH_MAX = 8;

function search_norm(s) {
  return String(s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

/** Everything the box can find: [{label, hint, text, run}]. */
function search_items() {
  const out = [];
  CFG.pages.forEach((p, i) => {
    if (p.games) return;
    for (const g of p.groups) {
      for (const c of CFG.cards[g.id] || []) {
        out.push({ label: c.name, hint: `${p.title} · ${g.title}`, kind: c.url ? "open" : "chevron_right",
          text: search_norm([c.name, c.subtitle, c.id, g.title, p.title, c.url].join(" ")),
          run: () => {
            if (c.url && !LIST_VIEW) return window.open(c.url, "_blank", "noopener");
            go(i);
            if (has_details(c)) setTimeout(() => open_panel(c), 50);
          } });
      }
    }
  });
  for (const b of CFG.bookmarks || []) {
    out.push({ label: b.name, hint: b.group, kind: "link", text: search_norm([b.name, b.group, b.url].join(" ")),
      run: () => window.open(b.url, "_blank", "noopener") });
  }
  return out;
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
    input.placeholder = "Search cards and links";
    input.setAttribute("aria-label", "Search");
    const list = el("div", "search-list");
    panel.append(input, list);
    box.appendChild(panel);
    document.body.appendChild(box);
    box.addEventListener("click", (ev) => { if (ev.target === box) search_close(); });
    let sel = 0, hits = [];
    const draw = () => {
      const q = search_norm(input.value.trim());
      const all = search_items();
      hits = q ? all.filter((x) => q.split(/\s+/).every((w) => x.text.includes(w))).slice(0, SEARCH_MAX) : [];
      const web = CFG.search && CFG.search.url;
      if (q && web) {
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
