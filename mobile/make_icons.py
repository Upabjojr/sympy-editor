#!/usr/bin/env python3
"""Build the app's icons from SymPy's logo and a pencil drawn over it.

    python mobile/make_icons.py

The mark is `mobile/icon/sympy-mark.svg`: SymPy's own logo (the snake and the
cube) with the wordmark taken off, since an icon has no room for text.  Its
author permits its free use on SymPy's terms - see the `<desc>` in the file.
The pencil is drawn here, in a flat style that survives being shrunk to 48px:
it says the app is for *editing* the mathematics, not for reading it.

What comes out:

* `mobile/icon/icon.svg`             the master, background and all;
* `mobile/icon/icon-foreground.svg`  the art alone, in the 108dp box an
  adaptive icon wants, with everything inside the central 72dp the launcher
  is guaranteed to show;
* `android/app/src/main/res/mipmap-*/`  the PNGs the Android app ships;
* `ios/SymPyEditor/Assets.xcassets/AppIcon.appiconset/`  the iOS app icon,
  the single 1024x1024 Xcode has wanted since 14;
* `mobile/icon/icon-512.png`         Google Play's listing icon;
* `mobile/icon/icon-1024.png`        the same at App Store size.

Needs `rsvg-convert` (librsvg) for the PNGs; without it the SVGs are still
written and it says so.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON = HERE / "icon"
RES = HERE / "android/app/src/main/res"
IOS_ICONS = HERE / "ios/SymPyEditor/Assets.xcassets/AppIcon.appiconset"

#: The mark's own drawing, in the source SVG's 750x750 user units (measured
#: once from a render of mobile/icon/sympy-mark.svg: the tail reaches far to
#: the left and the head to the right, so the mark is much wider than tall).
MARK_BOX = (35.6, 60.0, 729.4, 515.6)

#: Densities Android asks for, as (directory suffix, scale).
DENSITIES = [("mdpi", 1), ("hdpi", 1.5), ("xhdpi", 2), ("xxhdpi", 3), ("xxxhdpi", 4)]

BACKGROUND = "#f7f3e6"      # parchment, out of the cube's own palette
BACKGROUND_EDGE = "#efe8d3"


def mark_group(box: tuple[float, float, float, float], indent: str = "  ") -> str:
    """The SymPy mark, scaled and centred inside `box` (x, y, w, h)."""
    x, y, w, h = box
    mx0, my0, mx1, my1 = MARK_BOX
    scale = min(w / (mx1 - mx0), h / (my1 - my0))
    tx = x + (w - (mx1 - mx0) * scale) / 2 - mx0 * scale
    ty = y + (h - (my1 - my0) * scale) / 2 - my0 * scale
    inner = (ICON / "sympy-mark.svg").read_text(encoding="utf-8")
    inner = inner.split("<svg", 1)[1].split(">", 1)[1].rsplit("</svg>", 1)[0]
    body = "\n".join(indent + "  " + line.strip() for line in inner.splitlines() if line.strip())
    return f'{indent}<g transform="translate({tx:.3f},{ty:.3f}) scale({scale:.5f})">\n{body}\n{indent}</g>'


def pencil(cx: float, cy: float, length: float, angle: float = -38, indent: str = "  ") -> str:
    """A pencil, tip down-left, drawn across the mark.

    Flat shapes and one light outline: at 48px the outline is what keeps it
    from dissolving into the snake behind it.
    """
    L, h = length, length * 0.115
    tip, ferrule, eraser = L * 0.22, L * 0.10, L * 0.11
    body0, body1 = eraser + ferrule, L - tip
    o = f'{indent}<g transform="translate({cx:.2f},{cy:.2f}) rotate({angle}) translate({-L / 2:.2f},0)" ' \
        f'stroke="#ffffff" stroke-width="{h * 0.30:.2f}" stroke-linejoin="round">\n'
    p = indent + "  "
    # eraser, its band, the wooden body with a darker facet, then the point
    o += f'{p}<path d="M{eraser:.2f},{-h:.2f} H{eraser * 0.45:.2f} a{eraser * 0.55:.2f},{h:.2f} 0 0 0 0,{2 * h:.2f} ' \
         f'H{eraser:.2f} Z" fill="#f28b82"/>\n'
    o += f'{p}<rect x="{eraser:.2f}" y="{-h:.2f}" width="{ferrule:.2f}" height="{2 * h:.2f}" fill="#b9bec7"/>\n'
    o += f'{p}<rect x="{body0:.2f}" y="{-h:.2f}" width="{body1 - body0:.2f}" height="{2 * h:.2f}" fill="#f4b53f"/>\n'
    o += f'{p}<rect x="{body0:.2f}" y="{h * 0.15:.2f}" width="{body1 - body0:.2f}" height="{h * 0.85:.2f}" ' \
         f'fill="#d9932b" stroke="none"/>\n'
    o += f'{p}<path d="M{body1:.2f},{-h:.2f} L{L:.2f},0 L{body1:.2f},{h:.2f} Z" fill="#e8c79a"/>\n'
    o += f'{p}<path d="M{L - tip * 0.34:.2f},{-h * 0.34:.2f} L{L:.2f},0 L{L - tip * 0.34:.2f},{h * 0.34:.2f} Z" ' \
         f'fill="#3a3f45" stroke="none"/>\n'
    return o + f"{indent}</g>\n"


def foreground_svg() -> str:
    """The art alone, in the 108dp box: everything that must be seen lives in
    the central 72dp, since a launcher may mask away the rest."""
    art = mark_group((19.0, 23.0, 70.0, 46.0), indent="  ")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="108" height="108" viewBox="0 0 108 108">\n'
            "  <title>SymPy editor</title>\n"
            f"{art}\n"
            f"{pencil(57.0, 61.0, 68.0)}"
            "</svg>\n")


def master_svg(size: int = 512, round_shape: bool = False) -> str:
    """The whole icon, background and all: the store's listing and the PNGs
    for launchers that do not do adaptive icons.  `round_shape` draws the
    background as a circle, for the round variant those launchers ask for."""
    s = size
    art = mark_group((0.08 * s, 0.14 * s, 0.84 * s, 0.54 * s), indent="  ")
    shape = (f'  <circle cx="{s / 2:.1f}" cy="{s / 2:.1f}" r="{s / 2:.1f}" fill="url(#bg)"/>' if round_shape
             else f'  <rect width="{s}" height="{s}" rx="{s * 0.22:.1f}" fill="url(#bg)"/>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{s}" height="{s}" viewBox="0 0 {s} {s}">\n'
            "  <title>SymPy editor</title>\n"
            "  <defs><linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">"
            f'<stop offset="0" stop-color="{BACKGROUND}"/>'
            f'<stop offset="1" stop-color="{BACKGROUND_EDGE}"/></linearGradient></defs>\n'
            f"{shape}\n"
            f"{art}\n"
            f"{pencil(0.58 * s, 0.64 * s, 0.78 * s)}"
            "</svg>\n")


#: The adaptive icon: a launcher masks the foreground to whatever shape it
#: likes, over a flat background of its own.
ADAPTIVE = ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
            '    <background android:drawable="@color/ic_launcher_background"/>\n'
            '    <foreground android:drawable="@mipmap/ic_launcher_foreground"/>\n'
            "</adaptive-icon>\n")

COLOURS = ('<?xml version="1.0" encoding="utf-8"?>\n'
           "<resources>\n"
           f'    <color name="ic_launcher_background">{BACKGROUND}</color>\n'
           "</resources>\n")

#: Xcode 14 and later take one 1024x1024 image for every place an iOS icon is
#: shown, and make the rest themselves.  It must be opaque, with no alpha.
IOS_CONTENTS = """{
  "images" : [
    {
      "filename" : "icon-1024.png",
      "idiom" : "universal",
      "platform" : "ios",
      "size" : "1024x1024"
    }
  ],
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
"""

ASSETS_CONTENTS = """{
  "info" : {
    "author" : "xcode",
    "version" : 1
  }
}
"""


def flatten(png: Path, background: str = BACKGROUND) -> None:
    """Drop the alpha channel: the App Store refuses an icon that has one.
    Does nothing without Pillow, and says so."""
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is not installed:", png, "keeps its alpha channel (the App Store wants none)")
        return
    image = Image.open(png)
    if image.mode != "RGBA":
        return
    flat = Image.new("RGB", image.size, background)
    flat.paste(image, mask=image.split()[-1])
    flat.save(png)


def render(svg: Path, png: Path, size: int) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), str(svg), "-o", str(png)], check=True)


def main() -> int:
    ICON.mkdir(parents=True, exist_ok=True)
    (ICON / "icon.svg").write_text(master_svg(), encoding="utf-8")
    (ICON / "icon-round.svg").write_text(master_svg(round_shape=True), encoding="utf-8")
    (ICON / "icon-foreground.svg").write_text(foreground_svg(), encoding="utf-8")
    print("Wrote", ICON / "icon.svg", "and", ICON / "icon-foreground.svg")
    if not shutil.which("rsvg-convert"):
        print("rsvg-convert not found: install librsvg2-bin to build the PNGs")
        return 1
    for suffix, scale in DENSITIES:
        out = RES / f"mipmap-{suffix}"
        render(ICON / "icon.svg", out / "ic_launcher.png", round(48 * scale))
        render(ICON / "icon-round.svg", out / "ic_launcher_round.png", round(48 * scale))
        render(ICON / "icon-foreground.svg", out / "ic_launcher_foreground.png", round(108 * scale))
    adaptive = RES / "mipmap-anydpi-v26"
    adaptive.mkdir(parents=True, exist_ok=True)
    (adaptive / "ic_launcher.xml").write_text(ADAPTIVE, encoding="utf-8")
    (adaptive / "ic_launcher_round.xml").write_text(ADAPTIVE, encoding="utf-8")
    (RES / "values").mkdir(parents=True, exist_ok=True)
    (RES / "values/ic_launcher_background.xml").write_text(COLOURS, encoding="utf-8")
    render(ICON / "icon.svg", ICON / "icon-512.png", 512)       # Google Play's listing icon
    render(ICON / "icon.svg", ICON / "icon-1024.png", 1024)     # the App Store's
    IOS_ICONS.mkdir(parents=True, exist_ok=True)
    render(ICON / "icon.svg", IOS_ICONS / "icon-1024.png", 1024)
    flatten(IOS_ICONS / "icon-1024.png")                        # iOS rejects an icon with alpha
    (IOS_ICONS / "Contents.json").write_text(IOS_CONTENTS, encoding="utf-8")
    (IOS_ICONS.parent / "Contents.json").write_text(ASSETS_CONTENTS, encoding="utf-8")
    print("Wrote the mipmaps under", RES, ", the iOS icon under", IOS_ICONS,
          "and", ICON / "icon-512.png", "/", ICON / "icon-1024.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
