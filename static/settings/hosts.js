/* ⚙ > Infrastructure > Hosts & agents: the paired agents, one per host (agents.py), what each may
   do and on what, the code that adds one, and the agents' own updates. The drawing is settings.js's
   (agents_settings); this file puts it in ⚙. */
"use strict";

register_section({
  id: "hosts", group: "infra", title: "Hosts & agents", icon: "server",
  about: "Agents on your hosts, what each may do, adding a host",
  keywords: "agents add system code capabilities targets wake wol proxmox systemd docker update",
  render: (body, ctx) => {
    agents_settings(ctx.section("Agents", { wide: true, about: "An agent runs on a host and acts only through its own APIs. "
      + "Nothing is allowed until you tick it: first what it may do, then on which containers, guests, services or servers." }));
  },
});
