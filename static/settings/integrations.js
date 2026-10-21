/* ⚙ > Infrastructure > Integrations: what LabHUD reads (the sources' catalog: the form, Test, Save)
   and Scan my network. The drawing is sources.js's (sources_settings). */
"use strict";

register_section({
  id: "integrations", group: "infra", title: "Integrations", icon: "plug",
  about: "Sources LabHUD reads, and a scan of your network",
  keywords: "sources integrations catalog api key scan network discover proxmox adguard",
  render: (body, ctx) => sources_settings(ctx.section),
});
