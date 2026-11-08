"""Page geometry shared by the SVG/HTML export and the desktop editor.

`layout_song` turns a Song into sections of systems (lines of tab). Each system holds a list of
drawing primitives in its own coordinate space (WIDTH units wide) plus click regions ("hits"):

    ("line",   cls, x1, y1, x2, y2)
    ("rect",   cls, x, y, w, h)             filled rectangle
    ("poly",   cls, [x, y, ...])            filled polygon
    ("pline",  cls, [x, y, ...])            stroked polyline
    ("circle", cls, cx, cy, r)              filled circle
    ("text",   cls, x, baseline_y, text, size_or_None, anchor)    anchor: "start" | "middle"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .model import Event, Measure, Song

WIDTH = 760.0
GAP = 11.0              # distance between two string lines
CLEF_W = 30.0           # room for the "TAB" clef at the start of every system
PAD_L = 11.0            # space after a barline
PAD_R = 4.0             # space before a barline
ARROW_ROOM = 12.0       # space left of a note for its strum arrow
EMPTY_W = 60.0          # width of a measure without events
STRETCH_FROM = 0.55     # systems shorter than this share of the line are only stretched a little
MAX_SOFT_STRETCH = 1.35
FRET_SIZE = 10.5
CHORD_SIZE = 13.0
CHORD_Y = 14.0
BEAM_H = 3.2
BEAM_GAP = 2.2

SANS, SERIF = "sans", "serif"
# Text classes: family, size, bold, italic, colour.
TEXT_STYLES = {
    "fret": (SANS, FRET_SIZE, False, False, "#111"),
    "chord": (SANS, CHORD_SIZE, True, False, "#111"),
    "mnum": (SERIF, 10.0, False, False, "#333"),
    "mnum bad": (SERIF, 10.0, True, False, "#d0021b"),
    "capo": (SERIF, 12.0, False, False, "#111"),
    "clef": (SANS, 14.0, True, False, "#111"),
    "ts": (SERIF, 30.0, True, False, "#111"),
    "tup": (SERIF, 10.0, False, True, "#111"),
}


def text_width(s: str, size: float, bold: bool = False) -> float:
    """Rough Helvetica advance width, good enough for spacing decisions."""
    w = 0.0
    for ch in s:
        if ch in "1iljtfrI.:,'|/() ":
            w += 0.33
        elif ch in "mwMW#" or ch.isupper():
            w += 0.70
        else:
            w += 0.56
    return w * size * (1.08 if bold else 1.0)


def _cubic(p0, p1, p2, p3, n: int = 8) -> list[float]:
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t * t, t ** 3
        pts += [a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]]
    return pts


def _quad(p0, p1, p2, n: int = 6) -> list[float]:
    pts = []
    for i in range(n + 1):
        t = i / n
        a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        pts += [a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]]
    return pts


@dataclass
class Hit:
    """Clickable area of one event (e is its index) or of a whole measure (e is None)."""
    m: int          # measure index, 0-based in song order
    e: int | None
    x: float
    w: float
    xn: float       # x of the notes (for a measure: where an appended event would go)


@dataclass
class SystemLayout:
    height: float
    staff_top: float
    strings: int
    first: int
    last: int
    items: list[tuple] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)

    def string_y(self, s: int) -> float:
        return self.staff_top + (s - 1) * GAP


@dataclass
class SectionLayout:
    index: int
    title: str | None
    systems: list[SystemLayout]


# -- horizontal layout ----------------------------------------------------------------

def _slot_width(ev: Event) -> float:
    w = 20 + 26 * float(ev.duration) ** 0.65
    if ev.notes:
        widest = max(text_width(n.label, FRET_SIZE) for n in ev.notes)
        w = max(w, ARROW_ROOM + widest + (16 if ev.strum else 12))
    return w


def _time_sig_size(strings: int) -> float:
    return (strings - 1) * GAP * 0.56


@dataclass
class _MeasureLayout:
    measure: Measure
    slots: list[float]
    ts_w: float

    @property
    def fixed(self) -> float:
        return PAD_L + PAD_R + self.ts_w

    @property
    def flex(self) -> float:
        return sum(self.slots) if self.slots else EMPTY_W

    @property
    def natural(self) -> float:
        return self.fixed + self.flex


def _layout_measure(m: Measure, strings: int) -> _MeasureLayout:
    evs = m.events
    slots = [_slot_width(e) for e in evs]
    # Chord names may overhang following events, but must not collide with the next chord name.
    marks = [i for i, e in enumerate(evs) if e.chord]
    for a, b in zip(marks, marks[1:] + [len(evs)]):
        need = text_width(evs[a].chord, CHORD_SIZE, bold=True) + 10
        have = sum(slots[a:b])
        if have < need:
            extra = (need - have) / (b - a)
            for i in range(a, b):
                slots[i] += extra
    ts_w = 0.0
    if m.show_time:
        size = _time_sig_size(strings)
        ts_w = max(text_width(str(v), size, bold=True) for v in m.time) + 10
    return _MeasureLayout(m, slots, ts_w)


def _break_rows(rows: list[list[_MeasureLayout]], avail: float, per_line: int = 0) -> list[list[_MeasureLayout]]:
    """Each source row starts a new system; rows wrap after `per_line` measures or when too wide."""
    systems = []
    for row in rows:
        cur: list[_MeasureLayout] = []
        width = 0.0
        for ml in row:
            if cur and (width + ml.natural > avail or (per_line and len(cur) >= per_line)):
                systems.append(cur)
                cur, width = [], 0.0
            cur.append(ml)
            width += ml.natural
        if cur:
            systems.append(cur)
    return systems


# -- drawing --------------------------------------------------------------------------

def _arrow(out: list, x: float, top: float, bottom: float, up: bool) -> None:
    head, half = 6.5, 2.8
    if up:
        out.append(("line", "arrow", x, top + 4, x, bottom))
        out.append(("poly", "ink", [x, top, x - half, top + head, x + half, top + head]))
    else:
        out.append(("line", "arrow", x, top, x, bottom - 4))
        out.append(("poly", "ink", [x, bottom, x - half, bottom - head, x + half, bottom - head]))


def _time_signature(out: list, cx: float, top: float, h: float, time: tuple[int, int], strings: int) -> None:
    size = _time_sig_size(strings)
    for value, cy in ((time[0], top + h * 0.27), (time[1], top + h * 0.75)):
        s = str(value)
        tw = text_width(s, size, bold=True)
        out.append(("rect", "ko", cx - tw / 2 - 1, cy - size * 0.37, tw + 2, size * 0.74))
        out.append(("text", "ts", cx, cy + size * 0.35, s, size, "middle"))


def _event(out: list, ev: Event, x: float, string_y) -> None:
    if ev.chord:
        out.append(("text", "chord", x - 4, CHORD_Y, ev.chord, None, "start"))
    if ev.rest or not ev.notes:
        return
    widest = 0.0
    for note in ev.notes:
        y = string_y(note.string)
        tw = text_width(note.label, FRET_SIZE)
        widest = max(widest, tw)
        out.append(("rect", "ko", x - tw / 2 - 1.2, y - 5.2, tw + 2.4, 10.4))
        out.append(("text", "fret", x, y + FRET_SIZE * 0.36, note.label, None, "middle"))
    if ev.strum:
        ys = [string_y(n.string) for n in ev.notes]
        _arrow(out, x - widest / 2 - 4.5, min(ys) - 7, max(ys) + 7, ev.strum == "up")


def _levels(base: int) -> int:
    """Number of beams/flags: eighth 1, sixteenth 2, thirty-second 3."""
    return {8: 1, 16: 2, 32: 3}.get(base, 0)


def _rest(out: list, ev: Event, x: float, mid: float) -> None:
    if ev.base == 1:
        out.append(("rect", "ink", x - 3.5, mid - 4, 7, 3))
    elif ev.base == 2:
        out.append(("rect", "ink", x - 3.5, mid - 1, 7, 3))
    elif ev.base == 4:
        pts = [x - 1.5, mid - 9, x + 2.5, mid - 4.5, x - 1.5, mid - 0.5]
        pts += _cubic((x + 2.5, mid + 4), (x - 2, mid + 2), (x - 3, mid + 6), (x + 0.5, mid + 9))
        out.append(("pline", "flag", pts))
    else:
        n = _levels(ev.base)
        out.append(("line", "flag", x + 3, mid - 6, x - 1 - n, mid + 6 + (n - 1) * 3))
        for i in range(n):
            bx, by = x - 2.2 - i * 1.2, mid - 5 + i * 4.2
            out.append(("circle", "ink", bx, by, 1.7))
            out.append(("pline", "flag", _quad((bx, by + 0.6), (bx + 3, by + 1.5), (x + 3 - i * 1.2, by - 1))))
    if ev.dotted:
        out.append(("circle", "ink", x + 5.5, mid - 2, 1.2))


def _beat_length(time: tuple[int, int]) -> Fraction:
    num, den = time
    if den == 8 and num % 3 == 0:
        return Fraction(3, 2)          # compound time: beam eighths in threes
    return Fraction(4, den) if den <= 4 else Fraction(1)


def _rhythm(out: list, pos: list[tuple[Event, float, Fraction]], time, top: float, bot: float) -> None:
    beat = _beat_length(time)
    mid = (top + bot) / 2

    # Group consecutive eighths/sixteenths that start in the same beat.
    groups: list[list[tuple[Event, float, Fraction]]] = []
    cur: list = []
    cur_beat = None
    for item in pos:
        ev, _, t = item
        if not ev.rest and _levels(ev.base):
            b = t // beat
            if cur and b == cur_beat:
                cur.append(item)
            else:
                if cur:
                    groups.append(cur)
                cur, cur_beat = [item], b
        elif cur:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)

    for ev, x, _ in pos:
        if ev.rest:
            _rest(out, ev, x, mid)
            continue
        end = bot
        if ev.base == 2:
            end = top + (bot - top) * 0.55
        if ev.base != 1:
            out.append(("line", "stem", x, top, x, end))
        if ev.dotted:
            out.append(("circle", "ink", x + 3.8, end - 8 if ev.base >= 4 else end - 2, 1.2))

    for g in groups:
        levels = [_levels(ev.base) for ev, _, _ in g]
        if len(g) == 1:
            x = g[0][1]
            for i in range(levels[0]):
                yb = bot - i * 5
                out.append(("pline", "flag", _cubic((x, yb), (x + 1, yb - 4), (x + 7, yb - 5), (x + 6, yb - 11))))
            continue
        xs = [x for _, x, _ in g]
        for lvl in range(1, max(levels) + 1):
            y = bot - BEAM_H - (lvl - 1) * (BEAM_H + BEAM_GAP)
            i = 0
            while i < len(g):
                if levels[i] < lvl:
                    i += 1
                    continue
                j = i
                while j + 1 < len(g) and levels[j + 1] >= lvl:
                    j += 1
                if j > i:
                    out.append(("rect", "ink", xs[i] - 0.55, y, xs[j] - xs[i] + 1.1, BEAM_H))
                else:   # lone short note inside a beam group gets a stub
                    stub = 6.0
                    x0 = xs[i] - stub if i > 0 else xs[i] - 0.55
                    out.append(("rect", "ink", x0, y, stub + 0.55, BEAM_H))
                i = j + 1

    # Triplet numbers under every three consecutive triplet events.
    run: list[float] = []

    def flush() -> None:
        for k in range(0, len(run) - len(run) % 3, 3):
            chunk = run[k:k + 3]
            out.append(("text", "tup", sum(chunk) / 3, bot + 10, "3", None, "middle"))
        run.clear()

    for ev, x, _ in pos:
        if ev.triplet:
            run.append(x)
        else:
            flush()
    flush()


def _layout_system(mls: list[_MeasureLayout], song: Song, *, show_capo: bool, last_number: int) -> SystemLayout:
    n = song.string_count
    h = (n - 1) * GAP
    avail = WIDTH - CLEF_W
    fixed = sum(ml.fixed for ml in mls)
    flex = sum(ml.flex for ml in mls)
    natural = fixed + flex
    k = 1.0
    if flex > 0:
        k = (avail - fixed) / flex
        if natural < avail * STRETCH_FROM:
            k = min(k, MAX_SOFT_STRETCH)

    staff_top = 47.0 if show_capo else 33.0
    staff_bot = staff_top + h
    stem_top = staff_bot + 9
    stem_bot = stem_top + 20
    tuplets = any(e.triplet for ml in mls for e in ml.measure.events)
    height = stem_bot + (17 if tuplets else 7)
    sys = SystemLayout(height, staff_top, n, mls[0].measure.number, mls[-1].measure.number)
    string_y = sys.string_y

    back: list[tuple] = []
    out: list[tuple] = []

    size = max(8.0, h * 0.27)
    for i, ch in enumerate("TAB"):
        cy = staff_top + h * (0.2 + 0.3 * i)
        out.append(("text", "clef", CLEF_W * 0.45, cy + size * 0.36, ch, size, "middle"))

    bars: list[tuple[float, bool]] = [(0.5, False)]
    x = CLEF_W
    for idx, ml in enumerate(mls):
        m = ml.measure
        mi = m.number - 1
        x0 = x
        cls = "mnum bad" if m.events and m.actual != m.expected else "mnum"
        if idx == 0:
            out.append(("text", cls, 1, staff_top - 5, str(m.number), None, "start"))
        else:
            out.append(("text", cls, x, staff_top - 5, str(m.number), None, "middle"))
        cur = x + PAD_L
        if m.show_time:
            _time_signature(out, cur + ml.ts_w / 2, staff_top, h, m.time, n)
            cur += ml.ts_w
        if show_capo and idx == 0:
            out.append(("text", "capo", cur + 24, staff_top - 15, f"Capo fret {song.capo}", None, "start"))
        if not m.events:
            cur += EMPTY_W * k
        pos = []
        slots = []
        t = Fraction(0)
        for ev, w in zip(m.events, ml.slots):
            pos.append((ev, cur + ARROW_ROOM, t))
            slots.append((cur, w * k))
            t += ev.duration
            cur += w * k
        for ev, xn, _ in pos:
            _event(out, ev, xn, string_y)
        _rhythm(out, pos, m.time, stem_top, stem_bot)
        x = cur + PAD_R
        bars.append((x, m.number == last_number))

        # Click regions: each event's slot; the space after the last note appends a new event.
        if pos:
            last_note = pos[-1][1]
            append_from = min(last_note + 14, x - 6)
        else:
            append_from = x0 + PAD_L
        sys.hits.append(Hit(mi, None, x0, x - x0, (append_from + x) / 2))
        for ei, ((sx, sw), (_, xn, _)) in enumerate(zip(slots, pos)):
            right = sx + sw if ei < len(pos) - 1 else append_from
            sys.hits.append(Hit(mi, ei, sx, max(right - sx, 4.0), xn))

    end = bars[-1][0]
    for s in range(1, n + 1):
        back.append(("line", "staff", 0, string_y(s), end, string_y(s)))
    for bx, final in bars:
        if final:
            back.append(("line", "bar", bx - 4.5, staff_top, bx - 4.5, staff_bot))
            back.append(("rect", "ink", bx - 2.6, staff_top - 0.35, 2.6, h + 0.7))
        else:
            back.append(("line", "bar", bx, staff_top, bx, staff_bot))
    sys.items = back + out
    return sys


def layout_song(song: Song) -> list[SectionLayout]:
    n = song.string_count
    last_number = max((m.number for m in song.measures()), default=0)
    sections = []
    first_system = True
    for index, section in enumerate(song.sections):
        rows = [[_layout_measure(m, n) for m in row] for row in section.rows]
        systems = []
        for group in _break_rows(rows, WIDTH - CLEF_W, song.per_line):
            systems.append(_layout_system(group, song, show_capo=first_system and song.capo > 0,
                                          last_number=last_number))
            first_system = False
        sections.append(SectionLayout(index, section.title, systems))
    return sections
