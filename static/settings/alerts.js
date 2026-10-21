/* ⚙ > Monitoring > Alerts: the thresholds of the bars, the alert rules on hosts, the quiet hours
   (alerts.js, LabHUD's for every screen), and what this screen does when a problem is new. */
"use strict";

register_section({
  id: "alerts", group: "monitoring", title: "Alerts", icon: "warning",
  about: "Thresholds, rules on hosts, quiet hours, this screen's signal",
  keywords: "alerts rules thresholds warn crit orange red cpu ram disk temperature offline quiet hours flash beep awake",
  render: (body, ctx) => {
    marks_settings(ctx.section);
    alert_settings(ctx.section);
    screen_signal_settings(ctx);
  },
});
