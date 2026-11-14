"""Frame guide: grey out the parts of the picture a 16:9 or 9:16 export would crop away.

Drawn as an mpv OSD overlay (ASS vector drawing), so it sits on the video without touching decoding.
Crop maths matches the Korea render: 4:3 -> 16:9 keeps the full width and takes a 16:9 band at
vertical position y (0 = top, 0.5 = middle, 1 = bottom); 9:16 keeps the full height, centred.
"""
from __future__ import annotations

MODES = [
    ("off", "Frame guide: off"),
    ("16:9", "16:9 landscape (1080p)"),
    ("9:16", "9:16 vertical (Shorts / TikTok)"),
    ("both", "Both 16:9 + 9:16"),
]
SHADE = r"{\an7\pos(0,0)\bord0\shad0\1c&H202020&\1a&H%s&\p1}"
SHADE_ALPHA = ["38", "90"]  # first format: heavy grey; second (in "both"): light grey, so overlaps read darker
LINE = r"{\an7\pos(0,0)\bord0\shad0\1c&H%s&\1a&H10&\p1}"
COL = {"16:9": "50D7FF", "9:16": "E6C84F"}  # ASS colours are BGR: amber, cyan-blue
LABEL = {"16:9": "16:9 · 1080p", "9:16": "9:16 · Shorts / TikTok"}


def crop_rect(sw: float, sh: float, aspect: float, y: float = 0.5, x: float = 0.5):
    """Largest rect of the given aspect inside sw x sh. Returns (left, top, width, height) in source px."""
    if sw / sh > aspect:
        cw = sh * aspect
        return (sw - cw) * x, 0.0, cw, sh
    ch = sw / aspect
    return 0.0, (sh - ch) * y, sw, ch


def _box(x1, y1, x2, y2):
    return f"m {x1:.0f} {y1:.0f} l {x2:.0f} {y1:.0f} l {x2:.0f} {y2:.0f} l {x1:.0f} {y2:.0f} "


def ass(mode: str, osd: dict, sw: float, sh: float, y: float = 0.5) -> str:
    """ASS events for the guide, in OSD pixel coordinates. '' when there is nothing to draw."""
    if mode == "off" or not osd or not sw or not sh:
        return ""
    W, H = osd["w"], osd["h"]
    vx, vy = osd["ml"], osd["mt"]
    vw, vh = W - osd["ml"] - osd["mr"], H - osd["mt"] - osd["mb"]
    if vw <= 0 or vh <= 0:
        return ""
    k = vw / sw
    fmts = ["16:9", "9:16"] if mode == "both" else [mode]
    out, shaded = [], 0
    for f in fmts:
        a = 16 / 9 if f == "16:9" else 9 / 16
        cx, cy, cw, ch = crop_rect(sw, sh, a, y=y if f == "16:9" else 0.5)
        x1, y1 = vx + cx * k, vy + cy * vh / sh
        x2, y2 = x1 + cw * k, y1 + ch * vh / sh
        full = abs(cw - sw) < 1 and abs(ch - sh) < 1
        if not full:  # grey out what this format loses
            shapes = ""
            if y1 - vy > 0.5:
                shapes += _box(vx, vy, vx + vw, y1)
            if vy + vh - y2 > 0.5:
                shapes += _box(vx, y2, vx + vw, vy + vh)
            if x1 - vx > 0.5:
                shapes += _box(vx, y1, x1, y2)
            if vx + vw - x2 > 0.5:
                shapes += _box(x2, y1, vx + vw, y2)
            out.append(SHADE % SHADE_ALPHA[min(shaded, 1)] + shapes + "{\\p0}")
            shaded += 1
        t = 3
        frame = _box(x1, y1, x2, y1 + t) + _box(x1, y2 - t, x2, y2) + _box(x1, y1, x1 + t, y2) + _box(x2 - t, y1, x2, y2)
        out.append(LINE % COL[f] + frame + "{\\p0}")
        out.append(rf"{{\an7\pos({x1 + 8:.0f},{y1 + 6:.0f})\fs{max(18, int(H / 30))}\bord1\shad0"
                   rf"\1c&H{COL[f]}&\3c&H000000&}}{LABEL[f]}" + ("  (whole picture)" if full else ""))
    return "\n".join(out)
