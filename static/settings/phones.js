/* ⚙ > System > Phones: pairing the LabHUD Android app with a QR code, and the paired phones
   (devices.py). The drawing is settings.js's (phones_settings). */
"use strict";

register_section({
  id: "phones", group: "system", title: "Phones", icon: "phone",
  about: "Pair the Android app with a QR code; the paired phones",
  keywords: "phone app android pair qr code device remove",
  render: (body, ctx) => phones_settings(ctx),
});
