#!/usr/bin/env python3
"""Makes labhud's look from design/tokens.json, so the wall, ⚙, the setup pages and the Android app
cannot drift apart:

- the colours (and, in app.css, the sizes: design:scale), as CSS variables, between the `design:colors` markers of every page that has them
  (static/app.css, static/setup/setup.css, static/status.html, static/public.html);
- static/icons.js: the icons for the wall and ⚙ (thin lines, drawn with currentColor);
- with --android <labhud-android checkout>: the app's Tokens.kt, the same colours (`Hud`, the app's HUD theme) and the
  same icons as ImageVectors (`LabIcons`).

    python3 design/build.py                          # write the files in this repo
    python3 design/build.py --android ../labhud-android
    python3 design/build.py --check                  # exit 1 if a file is not what tokens.json makes
    python3 design/build.py --png                    # the app icons (PNG) from static/icon.svg

An icon is SVG markup on a 24×24 grid: <path>, <circle>, <ellipse> and <rect>, stroked with
`icon_stroke`, round caps and joins; fill="currentColor" also fills it (dots). For the app every
shape becomes a path, since an ImageVector only has paths.
"""

import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS = os.path.join(ROOT, "design", "tokens.json")
PAGES = ["static/app.css", "static/setup/setup.css", "static/status.html", "static/public.html"]
ICONS_JS = "static/icons.js"
KOTLIN = "app/src/main/java/io/github/mithrandler/labhud/ui/Tokens.kt"
BLOCK = re.compile(r"(?P<indent>[ \t]*)/\* design:colors[^*]*\*/\n.*?/\* design:end \*/", re.S)
GREY = re.compile(r"(?P<indent>[ \t]*)/\* design:grey[^*]*\*/\n.*?/\* design:end \*/", re.S)
SETTINGS_JS = "static/settings.js"
SERVERS_JS = "static/servers.js"
BASEVARS = re.compile(r"(?P<indent>[ \t]*)/\* design:basevars[^*]*\*/\n.*?/\* design:end \*/", re.S)
SCALE = re.compile(r"(?P<indent>[ \t]*)/\* design:scale[^*]*\*/\n.*?/\* design:end \*/", re.S)
SHAPE = re.compile(r"<(path|circle|ellipse|rect)\b([^>]*?)/>")
ATTR = re.compile(r'([a-z-]+)="([^"]*)"')


def load():
    with open(TOKENS, encoding="utf-8") as f:
        return json.load(f)


def digest():
    with open(TOKENS, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def css_var(name):
    return "--" + name.replace("_", "-")


def colors_block(tokens, indent):
    lines = [f"{indent}/* design:colors (design/tokens.json, written by design/build.py: edit there) */"]
    for name, (value, _) in tokens["colors"].items():
        lines.append(f"{indent}{css_var(name)}: {value};")
    lines.append(f"{indent}/* design:end */")
    return "\n".join(lines)


def scale_block(tokens, indent):
    lines = [f"{indent}/* design:scale (design/tokens.json, written by design/build.py: edit there) */"]
    for name, (value, _) in tokens["scale"].items():
        lines.append(f"{indent}{css_var(name)}: {value}px;")
    lines.append(f"{indent}/* design:end */")
    return "\n".join(lines)


def page(text, tokens, name):
    """The page with its colours (always) and its sizes (where it has a design:scale block)."""
    if not BLOCK.search(text):
        raise SystemExit(f"{name}: no /* design:colors */ … /* design:end */ block")
    text = BLOCK.sub(lambda m: colors_block(tokens, m.group("indent")), text)
    return SCALE.sub(lambda m: scale_block(tokens, m.group("indent")), text)


def icons_js(tokens):
    icons = json.dumps(tokens["icons"], indent=2, ensure_ascii=False)
    return f"""/* The icons of the wall and ⚙. Written by design/build.py from design/tokens.json: edit there.
   Thin lines on a 24×24 grid, in currentColor; the Android app draws the same ones. */

const ICONS = {icons};

/** An <svg> of the named icon, sized by CSS (or `size` px), stroked in currentColor. */
function icon_svg(name, size) {{
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "{tokens["icon_stroke"]}");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  if (size) {{ s.setAttribute("width", size); s.setAttribute("height", size); }}
  s.innerHTML = ICONS[name] || "";
  return s;
}}

// <button data-icon="bell" data-size="18"> in the page: the icon goes in first, before anything else is drawn
for (const b of document.querySelectorAll("[data-icon]")) b.prepend(icon_svg(b.dataset.icon, b.dataset.size));
"""


def num(x):
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def as_path(kind, a):
    """One SVG shape as path data: an ImageVector has nothing but paths."""
    f = lambda k, d=0.0: float(a.get(k, d))
    if kind == "path":
        return a["d"]
    if kind in ("circle", "ellipse"):
        cx, cy = f("cx"), f("cy")
        rx = f("r") if kind == "circle" else f("rx")
        ry = f("r") if kind == "circle" else f("ry")
        return (f"M{num(cx - rx)} {num(cy)}a{num(rx)} {num(ry)} 0 1 0 {num(2 * rx)} 0"
                f"a{num(rx)} {num(ry)} 0 1 0 {num(-2 * rx)} 0Z")
    x, y, w, h, r = f("x"), f("y"), f("width"), f("height"), f("rx")
    if not r:
        return f"M{num(x)} {num(y)}h{num(w)}v{num(h)}h{num(-w)}Z"
    arc = f"A{num(r)} {num(r)} 0 0 1"
    return (f"M{num(x + r)} {num(y)}H{num(x + w - r)}{arc} {num(x + w)} {num(y + r)}V{num(y + h - r)}"
            f"{arc} {num(x + w - r)} {num(y + h)}H{num(x + r)}{arc} {num(x)} {num(y + h - r)}"
            f"V{num(y + r)}{arc} {num(x + r)} {num(y)}Z")


def shapes(markup):
    out = []
    for kind, attrs in SHAPE.findall(markup):
        a = dict(ATTR.findall(attrs))
        out.append((as_path(kind, a), a.get("fill") == "currentColor"))
    if not out or SHAPE.sub("", markup).strip():
        raise SystemExit(f"an icon has something other than path, circle, ellipse or rect: {markup[:60]}…")
    return out


def camel(name):
    head, *rest = name.split("_")
    return head + "".join(p.title() for p in rest)


def grey_block(tokens, indent):
    pairs = ", ".join(f'"{css_var(n)}": "{v}"' for n, (v, _) in tokens["board_grey"].items())
    lines = [f"{indent}/* design:grey (design/tokens.json, written by design/build.py: edit there) */",
             f"{indent}const GREY_VARS = {{ {pairs} }};"]
    # the other bases: every colour a page uses, whether it is a light ground, and the font it asks for
    for name, base in tokens["board_bases"].items():
        vars_ = ", ".join(f'"{css_var(n)}": "{v[0]}"' for n, v in base.items() if not n.startswith("_"))
        font = f', font: "{base["_font"]}"' if base.get("_font") else ""
        light = "true" if base.get("_light") else "false"
        lines.append(f'{indent}const {name.upper()}_BASE = {{ label: "{base["_label"]}", light: {light}{font}, vars: {{ {vars_} }} }};')
    lines.append(f"{indent}/* design:end */")
    return "\n".join(lines)


def basevars_block(tokens, indent):
    """Every base's variables for the Servers page, which does not load settings.js: oled (nothing), grey, then the bases of tokens.json."""
    def vars_of(d):
        return "{ " + ", ".join(f'"{css_var(n)}": "{v[0] if isinstance(v, list) else v}"' for n, v in d.items() if not n.startswith("_")) + " }"
    rows = [f"{indent}  oled: {{}},", f"{indent}  grey: {vars_of(tokens['board_grey'])},"]
    rows += [f"{indent}  {name}: {vars_of(b)}," for name, b in tokens["board_bases"].items()]
    light = ", ".join(f'"{n}"' for n, b in tokens["board_bases"].items() if b.get("_light"))
    return (f"{indent}/* design:basevars (design/tokens.json, written by design/build.py: edit there) */\n"
            f"{indent}const BASE_VARS = {{\n" + "\n".join(rows) + f"\n{indent}}};\n{indent}const LIGHT_BASES = [{light}];\n{indent}/* design:end */")


def argb(value):
    """#rrggbb or #rrggbbaa as the 0xAARRGGBB of a Kotlin Color."""
    h = value.lstrip("#").upper()
    return h[6:] + h[:6] if len(h) == 8 else "FF" + h


def kotlin(tokens):
    colors = "\n".join(f"    /** {note} */\n    val {camel(n)} = Color(0xFF{v.lstrip('#').upper()})"
                       for n, (v, note) in tokens["colors"].items())
    scale = "\n".join(f"    /** {note} */\n    val {camel(n)} = {value}{'.sp' if n.startswith('text_') else '.dp'}"
                      for n, (value, note) in tokens["scale"].items())
    def scheme(key):
        return "\n".join(f"    val {k} = Color(0xFF{v.lstrip('#')})" for k, v in tokens[key].items())
    light_status = "\n".join(f"    val {camel(n)} = Color(0xFF{v.lstrip('#')})" for n, (v, _) in tokens["light_status"].items())
    grey = "\n".join(f"    /** {note} */\n    val {camel(n)} = Color(0xFF{v.lstrip('#').upper()})"
                      for n, (v, note) in tokens["board_grey"].items())
    bases = "\n\n".join(
        f"/** The \"{b['_label']}\" board: every colour the app asks of a base. */\nobject Hud{name.title()} {{\n"
        + "\n".join(f"    val {camel(n)} = Color(0x{argb(v[0])})" for n, v in b.items() if not n.startswith("_"))
        + "\n}"
        for name, b in tokens["board_bases"].items())
    icons = "\n".join(
        f"    val {camel(n)}: ImageVector by lazy {{ icon(\"{n}\", "
        + ", ".join(f"\"{d}\" to {'true' if fill else 'false'}" for d, fill in shapes(m)) + ") }"
        for n, m in tokens["icons"].items())
    return f"""// Written by labhud's design/build.py from its design/tokens.json ({digest()}): edit there, not here.
package io.github.mithrandler.labhud.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The colours of the wall and ⚙: the HUD theme. The app's `Lab` (Theme.kt) is these or, in another theme, others. */
object Hud {{
{colors}
}}

/** The "Dark grey" board's ground, cards and edges (the wall's settings.js says the same). */
object HudGrey {{
{grey}
}}

{bases}

/** The fallback Material schemes (no wallpaper colours to take, before Android 12): labhud's blue on neutral greys. */
object FallbackDark {{
{scheme("material_dark")}
}}

object FallbackLight {{
{scheme("material_light")}
}}

/** Status colours on the light theme (the HUD's are for a dark ground). */
object LightStatus {{
{light_status}
}}

/** The sizes of the wall and ⚙: gaps, corners, type, a meter's bar. */
object LabScale {{
{scale}
}}

/** The icons of the wall and ⚙: thin lines on a 24×24 grid, tinted by Icon() like any other. */
object LabIcons {{
{icons}

    private fun icon(name: String, vararg paths: Pair<String, Boolean>): ImageVector =
        ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {{
            for ((d, filled) in paths) addPath(
                pathData = addPathNodes(d),
                fill = if (filled) SolidColor(Color.Black) else null,
                stroke = SolidColor(Color.Black),
                strokeLineWidth = {tokens["icon_stroke"]}f,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
            )
        }}.build()
}}
"""


def outputs(android=None):
    """{path: text} of every file tokens.json makes."""
    tokens = load()
    out = {}
    for rel in PAGES:
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as f:
            out[path] = page(f.read(), tokens, rel)
    out[os.path.join(ROOT, ICONS_JS)] = icons_js(tokens)
    sp = os.path.join(ROOT, SETTINGS_JS)
    with open(sp, encoding="utf-8") as f:
        out[sp] = GREY.sub(lambda m: grey_block(tokens, m.group("indent")), f.read())
    vp = os.path.join(ROOT, SERVERS_JS)
    with open(vp, encoding="utf-8") as f:
        out[vp] = BASEVARS.sub(lambda m: basevars_block(tokens, m.group("indent")), f.read())
    if android:
        out[os.path.join(android, KOTLIN)] = kotlin(tokens)
    return out


def stale(android=None):
    """The files that are not what tokens.json makes."""
    bad = []
    for path, text in outputs(android).items():
        try:
            with open(path, encoding="utf-8") as f:
                if f.read() == text:
                    continue
        except OSError:
            pass
        bad.append(path)
    return bad


# The app icons, drawn from the one SVG of the mark (static/icon.svg) by ImageMagick: --png
PNGS = [("static/icon-512.png", 512), ("static/icon-192.png", 192), ("static/apple-touch-icon.png", 180)]


def pngs():
    import subprocess
    src = os.path.join(ROOT, "static", "icon.svg")
    for rel, size in PNGS:
        subprocess.run(["magick", "-background", "none", "-density", str(max(96, size // 4)), src,
                        "-resize", f"{size}x{size}", "-strip", "PNG32:" + os.path.join(ROOT, rel)], check=True)
        print(f"wrote {rel}")


def main(argv):
    if "--png" in argv:
        pngs()
        return 0
    android = argv[argv.index("--android") + 1] if "--android" in argv else None
    if "--check" in argv:
        bad = stale(android)
        for p in bad:
            print(f"not made from design/tokens.json: {os.path.relpath(p)} (run python3 design/build.py)")
        return 1 if bad else 0
    for path, text in outputs(android).items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {os.path.relpath(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
