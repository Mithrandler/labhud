#!/usr/bin/env python3
"""Fetches the game icons into static/icons/svc/ from Steam's own store art (the same art the Steam store
shows for each game): <slug>.png (96 px square, a centred crop of the portrait capsule, for the Android
app) and <slug>.svg (the same picture in an SVG, for the wall). dashboard-icons has no logo for these
games. Add a game to APPS (slug: Steam app id), also to GAME_ICONS in cardtype.py and static/app.js,
then run it. The files are committed, so labhud never asks Steam for an icon.

    python3 design/game_icons.py            # fetch the missing ones
    python3 design/game_icons.py --all      # fetch every one again
"""

import base64
import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "icons", "svc")
BASE = "https://cdn.cloudflare.steamstatic.com/steam/apps"
SIZE = 96

APPS = {
    "valheim": 892970, "terraria": 105600, "factorio": 427520, "counter-strike": 730, "palworld": 1623730,
    "ark": 346110, "satisfactory": 526870, "7-days-to-die": 251570, "enshrouded": 1203620,
    "subnautica": 264710, "garrys-mod": 4000, "dayz": 221100, "conan-exiles": 440900,
    "left-4-dead": 550, "half-life": 70, "team-fortress-2": 440, "project-zomboid": 108600,
    "unturned": 304930, "starbound": 211820, "v-rising": 1604030, "arma-3": 107410,
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def main(argv):
    os.makedirs(OUT, exist_ok=True)
    again = "--all" in argv
    for slug, app in APPS.items():
        svg, png = os.path.join(OUT, slug + ".svg"), os.path.join(OUT, slug + ".png")
        if not again and os.path.exists(svg) and os.path.exists(png):
            continue
        try:
            raw = fetch(f"{BASE}/{app}/library_600x900.jpg")
        except OSError as e:
            print(f"{slug}: {e}", file=sys.stderr)
            continue
        # the middle square of the portrait art, then the icon's size
        subprocess.run(["convert", "-", "-gravity", "center", "-crop", "100%x66.67%+0+0", "+repage",
                        "-resize", f"{SIZE}x{SIZE}", png], input=raw, check=True)
        with open(png, "rb") as f:
            data = base64.b64encode(f.read()).decode()
        with open(svg, "w", encoding="utf-8") as f:
            f.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">'
                    f'<image width="{SIZE}" height="{SIZE}" href="data:image/png;base64,{data}"/></svg>\n')
        print(slug)


main(sys.argv[1:])
