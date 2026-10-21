#!/usr/bin/env python3
"""Fetches the service icons named in design/icons.txt from homarr-labs/dashboard-icons into
static/icons/svc/: <name>.svg for the wall and <name>.png (96 px, made by ImageMagick from
their PNG) for the Android app. Run it after adding a name; the files are committed, so labhud
never asks a CDN for an icon.

    python3 design/icons.py            # fetch the missing ones
    python3 design/icons.py --all      # fetch every one again
"""

import os
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "design", "icons.txt")
OUT = os.path.join(ROOT, "static", "icons", "svc")
BASE = "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main"
SIZE = 96


def names():
    with open(LIST, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


def main(argv):
    os.makedirs(OUT, exist_ok=True)
    again = "--all" in argv
    for name in names():
        svg, png = os.path.join(OUT, name + ".svg"), os.path.join(OUT, name + ".png")
        if not again and os.path.exists(svg) and os.path.exists(png):
            continue
        data = fetch(f"{BASE}/svg/{name}.svg")
        if not data.lstrip().startswith((b"<svg", b"<?xml")):
            raise SystemExit(f"{name}: not an SVG")
        if b"<script" in data.lower():   # shown only as <img>, where scripts never run; still, no
            raise SystemExit(f"{name}: has a <script>")
        with open(svg, "wb") as f:
            f.write(data)
        big = fetch(f"{BASE}/png/{name}.png")
        subprocess.run(["magick", "-", "-resize", f"{SIZE}x{SIZE}", "-strip", "PNG32:" + png],
                       input=big, check=True)
        print("fetched", name)
    with open(os.path.join(OUT, "LICENSE"), "wb") as f:
        f.write(fetch(f"{BASE}/LICENSE"))


if __name__ == "__main__":
    main(sys.argv[1:])
