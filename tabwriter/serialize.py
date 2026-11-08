"""Turn a Song back into .tab text, so visual edits are saved in the same readable format."""
from __future__ import annotations

import re

from .model import STANDARD_TUNING, Event, Song


def _clean(text: str) -> str:
    return " ".join(str(text).replace("//", "/").split())


def _duration(ev: Event) -> str:
    return f":{ev.base}{'.' if ev.dotted else ''}{'t' if ev.triplet else ''}"


def _event(ev: Event, strings: int) -> str:
    if ev.rest or not ev.notes:
        return "r"
    if len(ev.notes) >= 4 and all(n.fret is None or n.fret <= 9 for n in ev.notes):
        # chord shape, lowest string first: x32010
        by_string = {n.string: n for n in ev.notes}
        body = "".join("-" if s not in by_string else ("x" if by_string[s].fret is None else str(by_string[s].fret))
                       for s in range(strings, 0, -1))
        return body + {"up": "^", "down": "v"}.get(ev.strum or "", "")
    notes = [f"{'x' if n.fret is None else n.fret}.{n.string}" for n in sorted(ev.notes, key=lambda n: n.string)]
    body = notes[0] if len(notes) == 1 else f"({' '.join(notes)})"
    return body + {"up": "^", "down": "v"}.get(ev.strum or "", "")


def _same(a, b) -> bool:
    return a.time == b.time and a.events == b.events


def serialize(song: Song) -> str:
    lines: list[str] = []
    for key in ("title", "subtitle", "artist"):
        if getattr(song, key):
            lines.append(f"{key}: {_clean(getattr(song, key))}")
    if song.capo:
        lines.append(f"capo: {song.capo}")
    measures = list(song.measures())
    time = measures[0].time if measures else (4, 4)
    lines.append(f"time: {time[0]}/{time[1]}")
    if song.tempo:
        lines.append(f"tempo: {song.tempo}")
    if song.tuning != STANDARD_TUNING:
        lines.append(f"tuning: {' '.join(song.tuning)}")
    if song.per_line != 2:
        lines.append(f"per_line: {song.per_line}")
    if song.grid != 8:
        lines.append(f"grid: {song.grid}")
    if song.page == "letter":
        lines.append("page: letter")
    for key, value in song.meta.items():
        name = re.sub(r"[^A-Za-z_]", "_", key.strip().lower().replace(" ", "_")) or "info"
        if len(name) < 2:
            name += "_"
        lines.append(f"{name}: {_clean(value)}")

    state = (4, False, False)        # the parser's initial duration
    prev = None
    for section in song.sections:
        lines.append("")
        lines.append(f"# {_clean(section.title)}" if section.title else "#")
        for row in section.rows:
            parts = []
            for m in row:
                if prev is not None and _same(m, prev):
                    parts.append("%")
                    continue
                toks = []
                if prev is not None and m.time != prev.time:
                    toks.append(f"@{m.time[0]}/{m.time[1]}")
                for ev in m.events:
                    dur = (ev.base, ev.dotted, ev.triplet)
                    if dur != state:
                        toks.append(_duration(ev))
                        state = dur
                    if ev.chord:
                        toks.append(f"[{_clean(ev.chord).replace(']', '').replace('[', '')}]")
                    toks.append(_event(ev, song.string_count))
                if not m.events:                       # keep empty measures as a full-measure rest
                    toks.append("r")
                parts.append(" ".join(toks))
                prev = m
            if parts:
                lines.append("| " + " | ".join(parts) + " |")
    return "\n".join(lines) + "\n"
