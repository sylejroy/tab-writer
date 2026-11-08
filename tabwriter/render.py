"""Render a Song as a printable HTML page; each line of tab is an inline SVG."""
from __future__ import annotations

from html import escape

from .layout import WIDTH, SystemLayout, layout_song
from .model import STANDARD_TUNING, Song


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _pts(pts: list[float]) -> str:
    return " ".join(_f(p) for p in pts)


def _svg_item(item: tuple) -> str:
    kind, cls = item[0], item[1]
    if kind == "line":
        _, _, x1, y1, x2, y2 = item
        return f'<line class="{cls}" x1="{_f(x1)}" y1="{_f(y1)}" x2="{_f(x2)}" y2="{_f(y2)}"/>'
    if kind == "rect":
        _, _, x, y, w, h = item
        return f'<rect class="{cls}" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}"/>'
    if kind == "poly":
        return f'<polygon class="{cls}" points="{_pts(item[2])}"/>'
    if kind == "pline":
        return f'<polyline class="{cls}" points="{_pts(item[2])}"/>'
    if kind == "circle":
        _, _, cx, cy, r = item
        return f'<circle class="{cls}" cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(r)}"/>'
    if kind == "text":
        _, _, x, y, s, size, anchor = item
        style = f' style="font-size:{_f(size)}px"' if size else ""
        klass = cls + (" mid" if anchor == "middle" else "")
        return f'<text class="{klass}" x="{_f(x)}" y="{_f(y)}"{style}>{escape(s)}</text>'
    raise ValueError(kind)


def system_svg(sys: SystemLayout) -> str:
    label = f"Measure {sys.first}" if sys.first == sys.last else f"Measures {sys.first}–{sys.last}"
    return (f'<svg class="system" viewBox="0 0 {_f(WIDTH)} {_f(sys.height)}" role="img" aria-label="{label}" '
            f'xmlns="http://www.w3.org/2000/svg">' + "".join(_svg_item(i) for i in sys.items) + "</svg>")


CSS = """
@page { size: %(page)s; margin: 13mm 14mm 15mm; }
* { box-sizing: border-box; }
html { background: #e6e6e9; }
body { margin: 0; color: #111; font-family: Helvetica, Arial, sans-serif;
  -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.sheet { background: #fff; max-width: %(width)s; min-height: %(height)s; margin: 24px auto; padding: 14mm 14mm 16mm;
  box-shadow: 0 1px 3px rgba(0,0,0,.12), 0 10px 30px rgba(0,0,0,.08); }
@media (max-width: 820px) { .sheet { margin: 0; padding: 24px 16px; min-height: 0; } }
.song-head { text-align: center; margin-bottom: 6mm; }
.song-head h1 { font-size: 22pt; margin: 0; letter-spacing: .01em; }
.song-head .sub { font-size: 12pt; color: #444; margin-top: 1.5mm; }
.song-head .meta { font-size: 9.5pt; color: #555; margin-top: 2mm; }
.song-head .meta span + span::before { content: "\\00b7"; margin: 0 .6em; color: #999; }
.section-title { background: #b5001b; color: #fff; font: bold 14pt Helvetica, Arial, sans-serif; text-align: center;
  margin: 7mm 0 2mm; padding: 1.4mm 0; letter-spacing: .02em; }
.tab-section:first-of-type .section-title { margin-top: 0; }
.keep { break-inside: avoid; page-break-inside: avoid; }
svg.system { display: block; width: 100%%; height: auto; margin: 0 0 4.5mm; overflow: visible;
  break-inside: avoid; page-break-inside: avoid; }
.system text { fill: #111; }
.system .fret { font: 10.5px Helvetica, Arial, sans-serif; }
.system .chord { font: bold 13px Helvetica, Arial, sans-serif; }
.system .mnum { font: 10px "Times New Roman", Times, serif; fill: #333; }
.system .mid { text-anchor: middle; }
.system .capo { font: 12px "Times New Roman", Times, serif; }
.system .clef { font-family: Helvetica, Arial, sans-serif; font-weight: bold; }
.system .ts { font-family: "Times New Roman", Times, serif; font-weight: bold; }
.system .tup { font: italic 10px "Times New Roman", Times, serif; }
.system .ko { fill: #fff; }
.system .ink { fill: #111; }
.system .staff { stroke: #8f8f8f; stroke-width: .7; }
.system .bar { stroke: #222; stroke-width: 1; }
.system .stem { stroke: #111; stroke-width: 1.1; }
.system .arrow { stroke: #111; stroke-width: 1.3; }
.system .flag { fill: none; stroke: #111; stroke-width: 1.3; stroke-linecap: round; stroke-linejoin: round; }
.empty { color: #888; text-align: center; padding: 30mm 0; font-size: 11pt; }
@media print {
  html { background: #fff; }
  .sheet { margin: 0; padding: 0; max-width: none; min-height: 0; box-shadow: none; }
}
"""


def header_lines(song: Song) -> tuple[str, str, list[str]]:
    """Title, subtitle line and meta items, shared with the desktop editor."""
    sub = " — ".join(s for s in (song.subtitle, song.artist) if s)
    meta = []
    if song.tempo:
        meta.append(f"♩ = {song.tempo}")
    if song.tuning != STANDARD_TUNING:
        meta.append(f"Tuning: {' '.join(song.tuning)}")
    meta += [f"{k}: {v}" for k, v in song.meta.items()]
    return song.title, sub, meta


def _header(song: Song) -> str:
    title, sub, meta = header_lines(song)
    if not (title or sub or meta):
        return ""
    parts = ['<header class="song-head">']
    if title:
        parts.append(f"<h1>{escape(title)}</h1>")
    if sub:
        parts.append(f'<div class="sub">{escape(sub)}</div>')
    if meta:
        parts.append('<div class="meta">' + "".join(f"<span>{escape(m)}</span>" for m in meta) + "</div>")
    parts.append("</header>")
    return "".join(parts)


def render_body(song: Song) -> str:
    parts = [_header(song)]
    for section in layout_song(song):
        svgs = [system_svg(s) for s in section.systems]
        if not svgs and not section.title:
            continue
        parts.append('<section class="tab-section">')
        if section.title:
            first = svgs.pop(0) if svgs else ""
            parts.append(f'<div class="keep"><h2 class="section-title">{escape(section.title)}</h2>{first}</div>')
        parts.extend(svgs)
        parts.append("</section>")
    if not any(True for _ in song.measures()):
        parts.append('<p class="empty">No measures yet.</p>')
    return "\n".join(p for p in parts if p)


def render_html(song: Song, *, auto_print: bool = False) -> str:
    letter = song.page == "letter"
    css = CSS % {"page": "letter" if letter else "A4",
                 "width": "216mm" if letter else "210mm",
                 "height": "279mm" if letter else "297mm"}
    title = escape(song.title or "Tab")
    script = "<script>addEventListener('load',()=>setTimeout(()=>print(),200))</script>" if auto_print else ""
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{title}</title><style>{css}</style>{script}</head>"
            f"<body><main class=\"sheet\">\n{render_body(song)}\n</main></body></html>\n")
