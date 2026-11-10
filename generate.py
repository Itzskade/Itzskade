#!/usr/bin/env python3
"""
generate.py — builds the GitHub profile SVGs from profile.yaml.

    python3 generate.py                  # uses the theme set in profile.yaml
    python3 generate.py --theme dracula  # override the theme
    python3 generate.py --list-themes

    python3 generate.py --fetch-stats    # also refresh stats.json from the GitHub API
                                         # (set GITHUB_TOKEN to avoid rate limits)

Output:  assets/profile-vim.svg · assets/whoami.svg · assets/radar.svg
         assets/radar-langs.svg · assets/stats.svg · assets/top-langs.svg
         README.md (from README.template.md)

Requires: PyYAML  (Pillow only if you use .png/.jpg as pixel art)
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pathlib
import sys
import urllib.request
from html import escape

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
FONT = ("'JetBrains Mono','Fira Code','SFMono-Regular',Consolas,"
        "'Liberation Mono','DejaVu Sans Mono',monospace")


# ═════════════════════════════════════════════════════════════
#  helpers
# ═════════════════════════════════════════════════════════════
def e(s) -> str:
    return escape(str(s), quote=True)


def f(n: float) -> str:
    return f"{n:.1f}"


def as_text(v) -> str:
    if isinstance(v, (list, tuple)):
        return " · ".join(map(str, v))
    return str(v)


def dotted(text: str, base: str, dot: str) -> str:
    """Paint the ' · ' separators in another color (tspans)."""
    parts = str(text).split(" · ")
    sep = f'<tspan fill="{dot}"> · </tspan>'
    return f'<tspan fill="{base}">' + sep.join(e(p) for p in parts) + "</tspan>"


# ═════════════════════════════════════════════════════════════
#  shared pieces (defs, window, scanlines)
# ═════════════════════════════════════════════════════════════
def defs(t: dict, sizes=(12, 12, 10), extra_css: str = "", extra: str = "") -> str:
    k, tt, s = sizes
    flat = t.get("flat", False)
    a1, a4, a2, a3 = ((t["accent"],) * 4 if flat else
                      (t["accent"], t["accent4"], t["accent2"], t["accent3"]))
    glow = ""
    if t.get("glow"):
        glow = ('<filter id="glow" x="-30%" y="-30%" width="160%" height="160%">'
                '<feGaussianBlur stdDeviation="2.2" result="b"/>'
                '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
                '</filter>')
    return (
        "<defs><style>"
        f".k{{font:700 {k}px {FONT}}}.t{{font:{tt}px {FONT}}}.s{{font:{s}px {FONT}}}"
        f"{extra_css}</style>"
        # main neon gradient (accent → accent4 → accent2)
        f'<linearGradient id="neon" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{a1}"/>'
        f'<stop offset=".55" stop-color="{a4}"/>'
        f'<stop offset="1" stop-color="{a2}"/></linearGradient>'
        '<pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">'
        '<rect width="4" height="1.3" fill="#000" opacity=".22"/></pattern>'
        f"{glow}{extra}</defs>"
    )


def edge(t: dict) -> str:
    """Outer border stroke: neon gradient or solid color."""
    return t["border"] if t.get("flat") else "url(#neon)"


def gl(t: dict) -> str:
    return ' filter="url(#glow)"' if t.get("glow") else ""


def window(t: dict, w: int, h: int, title: str) -> str:
    """Terminal window. chrome: gnome (buttons on the right) | mac (traffic lights)."""
    o = [f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="14" fill="{t["bg"]}"/>']
    if t.get("chrome", "mac") == "gnome":
        hb = t.get("headerbar", t["bg"])
        o.append(f'<path d="M1 46V15a14 14 0 0 1 14-14h{w-30}a14 14 0 0 1 14 14v31z" fill="{hb}"/>'
                 f'<line x1="1" y1="46" x2="{w-1}" y2="46" stroke="{t["border"]}"/>'
                 f'<text x="{w/2}" y="28" text-anchor="middle" class="s" font-weight="700" '
                 f'fill="{t["text"]}">{e(title)}</text>')
        # minimize · maximize · close
        bx = w - 26
        for i, sym in enumerate(("close", "max", "min")):
            cx = bx - i * 30
            fill = t.get("close", t["accent"]) if sym == "close" else t["border"]
            o.append(f'<circle cx="{cx}" cy="24" r="9" fill="{fill}"/>')
            c = t["text"]
            if sym == "close":
                o.append(f'<path d="M{cx-3.5} 20.5l7 7M{cx+3.5} 20.5l-7 7" stroke="{c}" stroke-width="1.5"/>')
            elif sym == "max":
                o.append(f'<rect x="{cx-3.5}" y="20.5" width="7" height="7" fill="none" stroke="{c}" stroke-width="1.3"/>')
            else:
                o.append(f'<path d="M{cx-3.5} 27.5h7" stroke="{c}" stroke-width="1.5"/>')
    else:
        o.append(f'<circle cx="34" cy="27" r="5.5" fill="{t["accent"]}"/>'
                 f'<circle cx="54" cy="27" r="5.5" fill="{t["accent3"]}"/>'
                 f'<circle cx="74" cy="27" r="5.5" fill="{t["ok"]}"/>'
                 f'<text x="{w/2}" y="31" text-anchor="middle" class="s" fill="{t["muted"]}">{e(title)}</text>')
    o.append(f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="14" fill="none" '
             f'stroke="{edge(t)}" stroke-width="{1.5 if t.get("flat") else 2}"/>')
    return "".join(o)


def scanlines(t: dict, w: int, h: int) -> str:
    if not t.get("scanlines"):
        return ""
    return (f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="14" fill="url(#scan)" '
            f'pointer-events="none"/>')


def panel(t: dict, x, y, w, h) -> str:
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" '
            f'fill="{t["panel"]}" stroke="{t["border"]}"/>')


def cursor(t: dict, x, y, w=8, h=15) -> str:
    return (f'<rect class="cur" x="{x}" y="{y}" width="{w}" height="{h}" fill="{t["accent"]}" opacity="0">'
            '<animate attributeName="opacity" values="0;.9;0" dur="1.1s" repeatCount="indefinite"/></rect>')


def svg_open(w: int, h: int, label: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" role="img" aria-label="{e(label)}">')


# ═════════════════════════════════════════════════════════════
#  pixel-art 1-bit
# ═════════════════════════════════════════════════════════════
ART_W, ART_H = 240, 304


def load_bitmap(path: pathlib.Path) -> list[list[int]]:
    if path.suffix.lower() == ".pbm":
        return load_pbm(path)
    try:
        from PIL import Image, ImageOps
    except ImportError:
        sys.exit(f"[x] {path}: install Pillow to use images (pip install pillow)")
    img = Image.open(path).convert("L")
    img = ImageOps.contain(img, (ART_W, ART_H))
    canvas = Image.new("L", (ART_W, ART_H), 0)            # background = off
    canvas.paste(img, ((ART_W - img.width) // 2, (ART_H - img.height) // 2))
    bw = canvas.convert("1")                               # Floyd–Steinberg
    return [[1 if bw.getpixel((x, y)) else 0 for x in range(ART_W)] for y in range(ART_H)]


def load_pbm(path: pathlib.Path) -> list[list[int]]:
    raw = path.read_bytes()
    if raw[:2] == b"P4":  # binary PBM
        hdr = [l for l in raw.split(b"\n")[1:4] if not l.startswith(b"#")]
        w, h = map(int, hdr[0].split())
        row = (w + 7) // 8
        data = raw[-row * h:]
        return [[(data[y * row + x // 8] >> (7 - x % 8)) & 1 for x in range(w)] for y in range(h)]
    text = "\n".join(l.split("#", 1)[0] for l in raw.decode().splitlines())
    toks = text.split()
    if toks[0] != "P1":
        sys.exit(f"[x] {path}: only PBM P1/P4 is supported")
    w, h = int(toks[1]), int(toks[2])
    bits = "".join(toks[3:])
    if len(bits) < w * h:
        sys.exit(f"[x] {path}: missing pixels ({len(bits)}/{w*h})")
    return [[int(bits[y * w + x]) for x in range(w)] for y in range(h)]


def bitmap_path(bm: list[list[int]], ox: int, oy: int) -> tuple[str, int]:
    """Turn the bitmap into a path of horizontal runs. Returns (d, pixel_count)."""
    d, pts = [], 0
    for y, row in enumerate(bm):
        x, w = 0, len(row)
        while x < w:
            if row[x]:
                s = x
                while x < w and row[x]:
                    x += 1
                n = x - s
                pts += n
                d.append(f"M{ox+s} {oy+y}h{n}v1h-{n}z")
            else:
                x += 1
    return "".join(d), pts


# ═════════════════════════════════════════════════════════════
#  1) profile-vim.svg
# ═════════════════════════════════════════════════════════════
def build_vim(cfg: dict, t: dict) -> str:
    v = cfg["vim"]
    W = 900
    file: dict = v["file"]

    # lines of the "file"
    lines: list[tuple[str, str | None, int]] = []   # (key, value, indent)
    for top, body in file.items():
        if isinstance(body, dict):
            lines.append((top, None, 0))
            for k, val in body.items():
                lines.append((k, as_text(val), 1))
        else:
            lines.append((top, as_text(body), 0))

    n = len(lines)
    step = 17.7
    first_y = 118.0
    text_h = max(17, n) * step
    PH = int(first_y - 56 + text_h + 46)              # panel height
    PH = max(PH, 408)
    H = 56 + PH + 28

    # pixel-art frames
    arts = [ROOT / p for p in v.get("art", [])]
    nf = max(1, len(arts))
    sec = float(v.get("frame_seconds", 3.2))
    total = nf * sec
    p = 100 / nf
    fade = min(2.0, p * 0.1)
    hide = " ".join(f".fr{i}" for i in range(1, nf)) or ".none"
    css = (f".fr{{opacity:0;animation:cyc {total:g}s linear infinite}}.fr0{{opacity:1}}"
           f"@keyframes cyc{{0%{{opacity:0}}{fade:.2f}%{{opacity:1}}{p-fade:.2f}%{{opacity:1}}"
           f"{p:.2f}%{{opacity:0}}100%{{opacity:0}}}}"
           f"@media (prefers-reduced-motion:reduce){{.fr{{animation:none}}{hide}{{display:none}}"
           f".fr0{{opacity:1}}.cur{{display:none}}}}")
    art_top = 56 + (PH - ART_H) // 2 - 4
    extra = (f'<linearGradient id="artg" gradientUnits="userSpaceOnUse" x1="0" y1="{art_top}" '
             f'x2="0" y2="{art_top+ART_H}"><stop offset="0" stop-color="{t["accent"] if t.get("flat") else t["accent3"]}"/>'
             f'<stop offset=".4" stop-color="{t["accent"]}"/>'
             f'<stop offset="1" stop-color="{t["accent"] if t.get("flat") else t["accent4"]}"/></linearGradient>')

    o = [svg_open(W, H, f"vim {v['filename']} — {cfg['whoami']['name']}"),
         defs(t, extra_css=css, extra=extra),
         window(t, W, H, f"vim {v['filename']}")]

    # left panel: pixel art
    o.append(panel(t, 36, 56, 270, PH))
    o.append(f'<text x="52" y="78" class="k" fill="{t["accent2"]}"{gl(t)}>{e(v.get("art_title","VISUAL.MAP"))}</text>')
    o.append(f'<text x="290" y="78" text-anchor="end" class="s" fill="{t["muted"]}">{ART_W}x{ART_H} / 1-BIT</text>')
    ox = 36 + (270 - ART_W) // 2
    for i, ap in enumerate(arts):
        bm = load_bitmap(ap)
        d, pts = bitmap_path(bm, ox, art_top)
        o.append(f'<g class="fr fr{i}" style="animation-delay:{i*sec:.1f}s">'
                 f'<path d="{d}" fill="url(#artg)"/>'
                 f'<text x="52" y="{56+PH-14}" class="s" fill="{t["muted"]}">'
                 f'{e(ap.stem)}.bit · PTS {pts} · FS/SERPENTINE</text></g>')
    # viewfinder corners
    x0, x1, y0, y1 = 46, 296, art_top - 2, art_top + ART_H + 2
    for dpath in (f"M{x0} {y0+12}V{y0}H{x0+12}", f"M{x1} {y0+12}V{y0}H{x1-12}",
                  f"M{x0} {y1-12}V{y1}H{x0+12}", f"M{x1} {y1-12}V{y1}H{x1-12}"):
        o.append(f'<path d="{dpath}" fill="none" stroke="{t["accent2"]}" stroke-width="1.5" stroke-opacity=".8"/>')

    # right panel: editor
    ex, ew = 326, 538
    o.append(panel(t, ex, 56, ew, PH))
    o.append(f'<text x="{ex+16}" y="78" class="k" fill="{t["text"]}">{e(v["filename"])} '
             f'<tspan fill="{t["muted"]}" font-weight="400">[YAML]</tspan></text>')
    handle = "@" + cfg["github"]
    pw = max(110, len(handle) * 8 + 26)
    o.append(f'<rect x="{ex+ew-16-pw}" y="64" width="{pw}" height="22" rx="11" fill="{t["bg"]}" stroke="{t["accent"] if t.get("flat") else "url(#neon)"}"/>'
             f'<text x="{ex+ew-16-pw/2}" y="79" text-anchor="middle" class="k" fill="{t["accent2"]}">{e(handle)}</text>')
    o.append(f'<line x1="{ex}" y1="94" x2="{ex+ew}" y2="94" stroke="{t["border"]}"/>')

    src_bytes = 0
    for i, (k, val, ind) in enumerate(lines):
        y = first_y + i * step
        o.append(f'<text x="{ex+16}" y="{f(y)}" class="s" fill="{t["faint"]}">{i+1:>2}</text>')
        pad = "  " * ind
        if val is None:
            o.append(f'<text x="{ex+44}" y="{f(y)}" class="k" xml:space="preserve" fill="{t["accent2"]}">'
                     f'{e(k)}<tspan fill="{t["muted"]}">:</tspan></text>')
            src_bytes += len(f"{k}:\n".encode())
        else:
            o.append(f'<text x="{ex+44}" y="{f(y)}" class="t" xml:space="preserve" fill="{t["accent"]}">'
                     f'{pad}{e(k)}<tspan fill="{t["muted"]}">:</tspan> {dotted(val, t["value"], t["accent3"])}</text>')
            src_bytes += len(f"{pad}{k}: {val}\n".encode())
            if len(pad + k + val) + 2 > 66:
                print(f"[!] vim: line '{k}' is long and may overflow the panel", file=sys.stderr)

    # cursor at the end of the last line
    cy = first_y + (n - 1) * step
    o.append(cursor(t, ex + 44, f(cy - 10.5), 7, 13))

    # status line
    sy = 56 + PH
    o.append(f'<line x1="{ex}" y1="{sy-34}" x2="{ex+ew}" y2="{sy-34}" stroke="{t["border"]}"/>')
    mode = v.get("mode", "NORMAL")
    o.append(f'<rect x="{ex+14}" y="{sy-26}" width="72" height="18" rx="3" fill="url(#neon)"/>'
             f'<text x="{ex+50}" y="{sy-13}" text-anchor="middle" class="k" fill="{t["bg"]}" '
             f'style="font-size:10.5px">{e(mode)}</text>')
    o.append(f'<text x="{ex+100}" y="{sy-13}" class="k" fill="{t["text"]}" style="font-size:11px">{e(v["filename"])}</text>')
    o.append(f'<text x="{ex+300}" y="{sy-13}" class="s" fill="{t["muted"]}">[utf-8] [unix]</text>')
    o.append(f'<text x="{ex+ew-16}" y="{sy-13}" text-anchor="end" class="s" fill="{t["muted"]}">'
             f'{n}L, {src_bytes}B  <tspan fill="{t["accent3"]}">100%</tspan> {n}:1</text>')

    o.append(scanlines(t, W, H))
    o.append("</svg>")
    return "".join(o)


# ═════════════════════════════════════════════════════════════
#  2) whoami.svg
# ═════════════════════════════════════════════════════════════
def build_whoami(cfg: dict, t: dict) -> str:
    w = cfg["whoami"]
    info: dict = w.get("info", {})
    items: dict = w.get("items", {})
    hl = w.get("highlight")
    W = 900

    y_info0 = 168
    y_sec = y_info0 + 26 * (len(info) - 1) + 34
    y_item0 = y_sec + 28
    y_prompt = y_item0 + 22 * (len(items) - 1) + 26
    PH = y_prompt + 10 - 56
    H = 56 + PH + 28

    css = "@media (prefers-reduced-motion:reduce){.cur{display:none}.mv{display:none}}"
    o = [svg_open(W, H, f"{w['name']} — {w.get('tagline','')}"),
         defs(t, sizes=(17, 14, 13), extra_css=css),
         window(t, W, H, w.get("window_title", "profile shell"))]

    # terminal
    o.append(panel(t, 36, 56, 592, PH))
    o.append(f'<text x="68" y="92" class="k" fill="{t["accent2"]}"{gl(t)}>❯ <tspan fill="{t["text"]}">whoami</tspan></text>')
    o.append(f'<text x="68" y="122" class="t" xml:space="preserve" fill="url(#neon)" font-weight="700" '
             f'style="font-size:16px">{e(w["name"])}</text>')
    name_w = len(w["name"]) * 9.7 + 14
    o.append(f'<text x="{68+name_w:.0f}" y="122" class="t" xml:space="preserve" fill="{t["muted"]}">— '
             f'{dotted(w.get("tagline",""), t["accent3"], t["accent"])}</text>')
    o.append(f'<line x1="68" y1="140" x2="596" y2="140" stroke="{t["border"]}" stroke-dasharray="4 4"/>')

    for i, (k, val) in enumerate(info.items()):
        y = y_info0 + 26 * i
        col = t["ok"] if k == hl else t["value"]
        o.append(f'<text x="68" y="{y}" class="s" fill="{t["accent4"]}">{e(k)}:</text>'
                 f'<text x="168" y="{y}" class="t" xml:space="preserve" fill="{col}">{dotted(as_text(val), col, t["accent"])}</text>')

    o.append(f'<text x="68" y="{y_sec}" class="k" fill="{t["accent2"]}"{gl(t)}>❯ <tspan fill="{t["text"]}">ls '
             f'{e(w.get("section",""))}</tspan></text>')
    for i, (k, val) in enumerate(items.items()):
        y = y_item0 + 22 * i
        o.append(f'<text x="68" y="{y}" class="t" fill="{t["accent"]}">{e(k)}</text>'
                 f'<text x="262" y="{y}" class="t" xml:space="preserve">{dotted(as_text(val), t["value"], t["accent3"])}</text>')

    o.append(f'<text x="68" y="{y_prompt}" class="k" fill="{t["accent2"]}">❯</text>')
    o.append(cursor(t, 90, y_prompt - 13, 9, 16))

    # side panel: sunset + perspective grid
    side = w.get("side", {})
    sx, sw = 644, 220
    o.append(panel(t, sx, 56, sw, PH))
    o.append(f'<clipPath id="sp"><rect x="{sx+1}" y="57" width="{sw-2}" height="{PH-2}" rx="8"/></clipPath>')
    o.append(f'<text x="{sx+20}" y="88" class="s" fill="{t["accent3"]}">● {e(side.get("title","runtime"))}</text>')
    cx = sx + sw / 2
    horizon = 56 + PH * 0.62
    r = 58
    sun_cy = horizon - 24
    o.append(f'<linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0" stop-color="{t["accent3"]}"/><stop offset=".6" stop-color="{t["accent"]}"/>'
             f'<stop offset="1" stop-color="{t["accent4"]}"/></linearGradient>')
    o.append(f'<g clip-path="url(#sp)">')
    # stars
    for (dx, dy, rr) in ((-80, 50, 1.2), (-40, 78, .9), (60, 44, 1.3), (85, 96, .8), (-90, 120, .9), (30, 70, .7)):
        o.append(f'<circle cx="{cx+dx}" cy="{56+dy}" r="{rr}" fill="{t["text"]}">'
                 f'<animate attributeName="opacity" values="1;.2;1" dur="{2+abs(dx)%3}s" repeatCount="indefinite"/></circle>')
    # striped sun
    o.append(f'<circle cx="{cx}" cy="{sun_cy}" r="{r}" fill="url(#sun)"{gl(t)}/>')
    for i, hh in enumerate((3, 4, 5, 6, 8)):
        yy = sun_cy - 6 + i * 7
        o.append(f'<rect x="{cx-r-2}" y="{yy}" width="{2*r+4}" height="{hh}" fill="{t["panel"]}"/>')
    o.append(f'<rect x="{sx}" y="{horizon}" width="{sw}" height="{PH}" fill="{t["panel"]}"/>')
    o.append(f'<line x1="{sx}" y1="{horizon}" x2="{sx+sw}" y2="{horizon}" stroke="{t["accent"]}" stroke-width="2"{gl(t)}/>')
    # perspective grid
    bottom = 56 + PH - 30
    for k in range(-6, 7):
        o.append(f'<line x1="{cx + k*12:.1f}" y1="{horizon}" x2="{cx + k*62:.1f}" y2="{bottom}" '
                 f'stroke="{t["accent4"]}" stroke-opacity=".75"/>')
    gh = bottom - horizon
    o.append('<g class="mv">')
    for j in range(6):
        y0 = horizon + gh * (j / 6) ** 1.8
        y1 = horizon + gh * ((j + 1) / 6) ** 1.8
        o.append(f'<line x1="{sx}" x2="{sx+sw}" y1="{f(y0)}" y2="{f(y0)}" stroke="{t["accent2"]}" stroke-opacity=".8">'
                 f'<animate attributeName="y1" values="{f(y0)};{f(y1)}" dur="1.6s" repeatCount="indefinite"/>'
                 f'<animate attributeName="y2" values="{f(y0)};{f(y1)}" dur="1.6s" repeatCount="indefinite"/></line>')
    o.append("</g>")
    o.append(f'<rect x="{sx}" y="{bottom}" width="{sw}" height="40" fill="{t["panel"]}"/>')
    o.append("</g>")
    o.append(f'<text x="{sx+20}" y="{56+PH-12}" class="s" fill="{t["muted"]}">{e(side.get("status",""))}</text>')

    o.append(scanlines(t, W, H))
    o.append("</svg>")
    return "".join(o)


# ═════════════════════════════════════════════════════════════
#  3) radars (radar.svg / radar-langs.svg)
# ═════════════════════════════════════════════════════════════
def build_radar(block: dict, t: dict, color_key: str, fill_id: str) -> str:
    W, H = 600, 450
    cx, cy, R = 300, 245, 135
    items = block["items"]
    n = len(items)
    vmax = max(i["value"] for i in items)

    def ang(i):
        return math.radians(-90 + 360 * i / n)

    def pt(i, rr):
        return cx + rr * math.cos(ang(i)), cy + rr * math.sin(ang(i))

    col = t[color_key]
    other = t["accent2"] if color_key != "accent2" else t["accent"]
    if t.get("flat"):
        other = col
    grad = (f'<radialGradient id="{fill_id}" cx="50%" cy="50%" r="60%">'
            f'<stop offset="0" stop-color="{other}" stop-opacity=".55"/>'
            f'<stop offset="1" stop-color="{col}" stop-opacity=".35"/></radialGradient>')
    o = [svg_open(W, H, block["title"]), defs(t, sizes=(13, 12, 11), extra=grad)]
    o.append(f'<rect width="{W}" height="{H}" rx="14" fill="{t["panel"]}"/>')
    o.append(f'<rect x="1" y="1" width="{W-2}" height="{H-2}" rx="14" fill="none" stroke="{edge(t)}" stroke-opacity=".8" stroke-width="1.5"/>')
    o.append(f'<text x="24" y="32" class="k" xml:space="preserve" style="font-size:14px" fill="{col}"{gl(t)}>'
             f'<tspan fill="{t["muted"]}">$ cat </tspan>{e(block["title"])}</text>')
    o.append(f'<text x="24" y="50" class="s" fill="{t["muted"]}"># {e(block.get("subtitle",""))}</text>')

    # rings and axes
    for ring in range(1, 5):
        pts = " ".join(f"{f(x)},{f(y)}" for x, y in (pt(i, R * ring / 4) for i in range(n)))
        dash = ' stroke-dasharray="3 4"' if ring < 4 else ""
        o.append(f'<polygon points="{pts}" fill="none" stroke="{t["grid"]}" stroke-opacity=".4"{dash}/>')
    for i in range(n):
        x, y = pt(i, R)
        o.append(f'<line x1="{cx}" y1="{cy}" x2="{f(x)}" y2="{f(y)}" stroke="{t["grid"]}" stroke-opacity=".4"/>')

    # data (log scale)
    def radius(v):
        frac = math.log1p(v) / math.log1p(vmax)
        return R * max(0.12, 0.98 * frac)

    data = [pt(i, radius(it["value"])) for i, it in enumerate(items)]
    pts = " ".join(f"{f(x)},{f(y)}" for x, y in data)
    o.append(f'<polygon points="{pts}" fill="url(#{fill_id})" stroke="{col}" stroke-width="2.5" '
             f'stroke-linejoin="round"{gl(t)}>'
             '<animate attributeName="fill-opacity" values=".6;1;.6" dur="4s" repeatCount="indefinite"/></polygon>')
    for x, y in data:
        o.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="4" fill="{t["panel"]}" stroke="{col}" stroke-width="2"/>')

    # labels
    for i, it in enumerate(items):
        c, s = math.cos(ang(i)), math.sin(ang(i))
        lx, ly = cx + (R + 22) * c, cy + (R + 22) * s
        anchor = "middle" if abs(c) < 0.2 else ("start" if c > 0 else "end")
        y_name = ly - 16 if s < -0.9 else ly - 2
        label = it.get("label") or f'{it["value"]:,} {it.get("unit","")}'.strip()
        o.append(f'<text x="{f(lx)}" y="{f(y_name)}" text-anchor="{anchor}" class="k" fill="{t["text"]}">{e(it["name"])}</text>'
                 f'<text x="{f(lx)}" y="{f(y_name+15)}" text-anchor="{anchor}" class="t" fill="{col}">{e(label)}</text>')

    o.append(scanlines(t, W, H))
    o.append("</svg>")
    return "".join(o)


# ═════════════════════════════════════════════════════════════
#  4) GitHub stats (stats.svg / top-langs.svg)
# ═════════════════════════════════════════════════════════════
API = "https://api.github.com"


def gh(path: str, token: str | None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "profile-generator"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(API + path, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_stats(user: str, exclude: list[str], token: str | None) -> dict:
    """Public data only: owned non-fork repos, stars, languages, commits, PRs, issues."""
    me = gh(f"/users/{user}", token)
    repos, page = [], 1
    while True:
        batch = gh(f"/users/{user}/repos?per_page=100&type=owner&page={page}", token)
        repos += batch
        if len(batch) < 100:
            break
        page += 1
    own = [r for r in repos if not r["fork"]]
    langs: dict[str, int] = {}
    for r in own:
        if r["name"] in exclude:
            continue
        for lang, size in gh(f"/repos/{user}/{r['name']}/languages", token).items():
            langs[lang] = langs.get(lang, 0) + size

    def total(q: str) -> int:
        return gh(f"/search/{q}&per_page=1", token)["total_count"]

    return {
        "user": user,
        "updated": datetime.date.today().isoformat(),
        "stars": sum(r["stargazers_count"] for r in own),
        "commits": total(f"commits?q=author:{user}"),
        "pull_requests": total(f"issues?q=author:{user}+type:pr"),
        "issues": total(f"issues?q=author:{user}+type:issue"),
        "repos": len(own),
        "followers": me.get("followers", 0),
        "languages": dict(sorted(langs.items(), key=lambda kv: -kv[1])),
    }


def card_frame(t: dict, w: int, h: int, cmd: str) -> list[str]:
    return [svg_open(w, h, cmd), defs(t, sizes=(13, 12, 11)),
            f'<rect x="1" y="1" width="{w-2}" height="{h-2}" rx="10" fill="{t["panel"]}" '
            f'stroke="{edge(t)}" stroke-width="1.5"/>',
            f'<text x="20" y="30" class="k" xml:space="preserve" fill="{t["accent"]}"{gl(t)}>'
            f'<tspan fill="{t["accent2"]}">❯</tspan> <tspan fill="{t["text"]}">{e(cmd)}</tspan></text>',
            f'<line x1="20" y1="44" x2="{w-20}" y2="44" stroke="{t["border"]}" stroke-dasharray="4 4"/>']


def build_stats_card(st: dict | None, t: dict, user: str) -> str:
    W, H = 450, 220
    o = card_frame(t, W, H, f"gh api users/{user}")
    st = st or {}
    rows = [("stars", "stars", t["accent3"]), ("commits", "commits", t["accent2"]),
            ("pull requests", "pull_requests", t["accent"]), ("issues", "issues", t["accent4"]),
            ("public repos", "repos", t["accent"]), ("followers", "followers", t["accent3"])]
    for i, (label, key, col) in enumerate(rows):
        y = 74 + i * 22
        val = st.get(key)
        val = f"{val:,}" if isinstance(val, int) else "—"
        o.append(f'<rect x="22" y="{y-9}" width="8" height="8" rx="2" fill="{col}"/>'
                 f'<text x="40" y="{y}" class="t" fill="{t["value"]}">{e(label)}</text>'
                 f'<line x1="{48 + len(label)*7.3:.0f}" y1="{y-3}" x2="{W-80}" y2="{y-3}" '
                 f'stroke="{t["faint"]}" stroke-dasharray="1 4"/>'
                 f'<text x="{W-22}" y="{y}" text-anchor="end" class="k" fill="{t["text"]}">{val}</text>')
    foot = f'updated {st["updated"]}' if st.get("updated") else "waiting for the first GitHub Action run"
    o.append(f'<text x="20" y="{H-16}" class="s" fill="{t["muted"]}"># {e(foot)}</text>')
    o.append(scanlines(t, W, H))
    o.append("</svg>")
    return "".join(o)


def build_langs_card(st: dict | None, t: dict, count: int) -> str:
    W, H = 450, 220
    o = card_frame(t, W, H, "github-linguist --breakdown")
    langs = list((st or {}).get("languages", {}).items())
    total = sum(v for _, v in langs) or 1
    top = langs[:count]
    if len(langs) > count:
        top.append(("Other", sum(v for _, v in langs[count:])))
    palette = [t["accent"], t["accent2"], t["accent3"], t["accent4"], t["value"], t["muted"], t["grid"]]

    def color(i: int, name: str) -> str:
        return t["faint"] if name == "Other" else palette[i % len(palette)]
    # stacked bar
    x, bw = 20.0, W - 40
    o.append(f'<clipPath id="bar"><rect x="20" y="58" width="{bw}" height="10" rx="5"/></clipPath>'
             f'<rect x="20" y="58" width="{bw}" height="10" rx="5" fill="{t["border"]}"/><g clip-path="url(#bar)">')
    for i, (name, v) in enumerate(top):
        w = bw * v / total
        o.append(f'<rect x="{f(x)}" y="58" width="{f(w)}" height="10" fill="{color(i, name)}"/>')
        x += w
    o.append("</g>")
    # two-column list
    for i, (name, v) in enumerate(top):
        col, row = divmod(i, 5)
        cx, y = 22 + col * 215, 96 + row * 22
        o.append(f'<circle cx="{cx+4}" cy="{y-4}" r="4" fill="{color(i, name)}"/>'
                 f'<text x="{cx+16}" y="{y}" class="t" fill="{t["value"]}">{e(name)}</text>'
                 f'<text x="{cx+195}" y="{y}" text-anchor="end" class="k" fill="{t["text"]}">'
                 f'{100*v/total:.1f}%</text>')
    if not top:
        o.append(f'<text x="22" y="100" class="t" fill="{t["muted"]}">no data yet</text>')
    o.append(f'<text x="20" y="{H-16}" class="s" fill="{t["muted"]}"># by bytes of code · public repos · forks excluded</text>')
    o.append(scanlines(t, W, H))
    o.append("</svg>")
    return "".join(o)


# ═════════════════════════════════════════════════════════════
#  README from template
# ═════════════════════════════════════════════════════════════
def build_readme(t: dict) -> str | None:
    tpl = ROOT / "README.template.md"
    if not tpl.exists():
        return None
    out = tpl.read_text(encoding="utf-8")
    for k, v in t.items():
        if isinstance(v, str):
            out = out.replace("{{" + k + "}}", v.lstrip("#"))
    return out


# ═════════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--theme", help="override the theme set in profile.yaml")
    ap.add_argument("--config", default="profile.yaml")
    ap.add_argument("--out", default="assets")
    ap.add_argument("--list-themes", action="store_true")
    ap.add_argument("--fetch-stats", action="store_true", help="refresh stats.json from the GitHub API")
    a = ap.parse_args()

    themes = yaml.safe_load((ROOT / "themes.yaml").read_text(encoding="utf-8"))
    if a.list_themes:
        print("\n".join(themes))
        return
    cfg = yaml.safe_load((ROOT / a.config).read_text(encoding="utf-8"))
    name = a.theme or cfg.get("theme", "synthwave")
    if name not in themes:
        sys.exit(f"[x] theme '{name}' does not exist. Available: {', '.join(themes)}")
    t = themes[name]

    sc = cfg.get("stats", {})
    stats_file = ROOT / "stats.json"
    if a.fetch_stats:
        try:
            data = fetch_stats(cfg["github"], sc.get("exclude_repos", []), os.environ.get("GITHUB_TOKEN"))
            stats_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            print(f"[✓] stats.json  (GitHub API, {len(data['languages'])} languages)")
        except Exception as ex:  # keep the previous stats.json if the API fails
            print(f"[!] could not fetch stats: {ex}", file=sys.stderr)
    stats = json.loads(stats_file.read_text(encoding="utf-8")) if stats_file.exists() else None

    out = ROOT / a.out
    out.mkdir(exist_ok=True)
    files = {
        "profile-vim.svg": build_vim(cfg, t),
        "whoami.svg": build_whoami(cfg, t),
        "radar.svg": build_radar(cfg["signals"], t, "accent", "rf"),
        "radar-langs.svg": build_radar(cfg["languages"], t, "accent2", "rf"),
        "stats.svg": build_stats_card(stats, t, cfg["github"]),
        "top-langs.svg": build_langs_card(stats, t, int(sc.get("langs_count", 7))),
    }
    for fn, svg in files.items():
        (out / fn).write_text(svg, encoding="utf-8")
        print(f"[✓] {a.out}/{fn}  ({len(svg)/1024:.0f} KB)")
    readme = build_readme(t)
    if readme is not None:
        (ROOT / "README.md").write_text(readme, encoding="utf-8")
        print("[✓] README.md  (from README.template.md)")
    print(f"    theme: {name}")


if __name__ == "__main__":
    main()
