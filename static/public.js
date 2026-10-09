// The public status page: one fetch a minute, text only (textContent, never HTML).
"use strict";
const LABEL = { up: "up", down: "down", maintenance: "maintenance", scheduled: "off on schedule", "on-demand": "off" };

function render(d) {
  document.title = d.title;
  document.getElementById("title").textContent = d.title;
  const sum = document.getElementById("summary");
  sum.textContent = d.all_up ? "All systems up" : "Something is down";
  sum.className = d.all_up ? "up" : "down";
  const box = document.getElementById("groups");
  box.textContent = "";
  for (const g of d.groups) {
    box.appendChild(document.createElement("h2")).textContent = g.title;
    const ul = box.appendChild(document.createElement("ul"));
    for (const i of g.items) {
      const li = ul.appendChild(document.createElement("li"));
      li.appendChild(document.createElement("span")).textContent = i.name;
      const st = li.appendChild(document.createElement("span"));
      st.className = "st " + i.state;
      st.textContent = LABEL[i.state] || i.state;
    }
  }
  document.getElementById("updated").textContent = "Updated " + new Date(d.now * 1000).toLocaleTimeString();
}

function load() {
  fetch("/api/public", { cache: "no-store" }).then((r) => r.json()).then((d) => { if (!d.error) render(d); })
    .catch(() => { document.getElementById("updated").textContent = "Not reachable right now."; });
}
load();
setInterval(load, 60000);
