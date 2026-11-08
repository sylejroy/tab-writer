"""Editing operations on a Song, independent of any GUI toolkit.

The editor keeps a cursor (measure, event, string). Event index == len(events) is the "append" slot
after the last event of a measure. Every mutating operation is undoable.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from fractions import Fraction
from functools import wraps

from .model import STANDARD_TUNING, Event, Measure, Note, Section, Song
from .parser import check_measures

# Common open chord shapes, lowest string first ("x" = muted).
CHORD_SHAPES = {
    "C": "x32010", "D": "xx0232", "E": "022100", "F": "133211", "G": "320003", "A": "x02220", "B": "x24442",
    "Am": "x02210", "Bm": "x24432", "Dm": "xx0231", "Em": "022000", "F#m": "244222",
    "A7": "x02020", "B7": "x21202", "C7": "x32310", "D7": "xx0212", "E7": "020100", "G7": "320001",
    "Am7": "x02010", "Dm7": "xx0211", "Em7": "022030",
    "Cmaj7": "x32000", "Dmaj7": "xx0222", "Fmaj7": "xx3210",
    "Asus2": "x02200", "Asus4": "x02230", "Dsus2": "xx0230", "Dsus4": "xx0233",
    "Esus4": "022200", "Cadd9": "x32030", "G/B": "x20003",
}

BASES = (1, 2, 4, 8, 16, 32)
TIME_SIGNATURES = ("4/4", "3/4", "2/4", "6/8", "9/8", "12/8", "2/2", "5/4", "7/8")
TWO_DIGIT_WINDOW = 0.9   # seconds in which a second digit extends the fret (1 then 2 -> 12)


@dataclass
class Cursor:
    m: int = 0      # measure index in song order
    e: int = 0      # event index; == len(events) means "after the last event"
    s: int = 1      # string, 1 = high e


@dataclass
class Ref:
    si: int
    ri: int
    mi: int
    section: Section
    row: list
    measure: Measure


def rests_for(length: Fraction) -> list[Event]:
    """Fill a length (in quarter notes) with as few plain rests as possible."""
    out = []
    for base in BASES:
        unit = Fraction(4, base)
        while length >= unit:
            out.append(Event(rest=True, base=base))
            length -= unit
    return out


GRIDS = (1, 2, 4, 8)


def grid_rests(start: Fraction, end: Fraction, grid: int) -> list[Event]:
    """Rests covering start..end, split on grid lines (grid 8 = one rest per eighth)."""
    unit = Fraction(4, grid)
    out: list[Event] = []
    t = start
    lead = (-t) % unit
    if lead:
        step = min(lead, end - t)
        out += rests_for(step)
        t += step
    while t + unit <= end:
        out.append(Event(rest=True, base=grid))
        t += unit
    if t < end:
        out += rests_for(end - t)
    return out


def blank_events(time_sig: tuple[int, int], grid: int = 8) -> list[Event]:
    """Empty slots (rests) on the grid, ready to be overwritten with notes."""
    num, den = time_sig
    return grid_rests(Fraction(0), Fraction(4 * num, den), grid)


def shape_notes(shape: str, strings: int) -> list[Note]:
    notes = []
    for i, ch in enumerate(shape[-strings:]):
        if ch == "-":
            continue
        notes.append(Note(string=strings - i, fret=None if ch in "xX" else int(ch)))
    return sorted(notes, key=lambda n: n.string)


def _op(merge: str | None = None):
    """Wrap a mutating operation: snapshot for undo, renumber and validate afterwards.

    The wrapped function returns False when nothing changed. `merge` groups rapid repeats
    (e.g. typing a title) into one undo step.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(self, *args, **kwargs):
            before = (copy.deepcopy(self.song), copy.copy(self.cursor))
            result = fn(self, *args, **kwargs)
            if result is False:
                self.song, self.cursor = before
                return False
            now = time.monotonic()
            # set_field merges per field name; other merging ops (chord/section names) merge per op
            field_name = args[0] if fn.__name__ == "set_field" and args else None
            key = (merge, field_name) if merge else None
            if not (key and key == self._last_merge and now - self._last_time < 2):
                self.undo_stack.append(before)
                del self.undo_stack[:-200]
                self.redo_stack.clear()
            self._last_merge, self._last_time = key, now
            self.changed()
            return True
        return wrapper
    return deco


class TabEditor:
    def __init__(self, song: Song | None = None):
        self.song = song or Song()
        self.cursor = Cursor()
        self.base, self.dotted, self.triplet = 4, False, False
        self.clipboard: list[Measure] = []
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.dirty = False
        self._last_merge = None
        self._last_time = 0.0
        self._last_digit = (0.0, None)
        if not any(True for _ in self.song.measures()):
            if not self.song.sections:
                self.song.sections.append(Section("Intro"))
            self.song.sections[0].rows.append([Measure(1, (4, 4), blank_events((4, 4), self.song.grid))])
        self.song.renumber()
        self.clamp()
        self.sync_duration()

    # -- bookkeeping -------------------------------------------------------------
    def changed(self) -> None:
        self.dirty = True
        self.song.renumber()
        errors = [p for p in self.song.problems if p.level == "error" and p.measure is None]
        self.song.problems = errors + check_measures(self.song)
        self.clamp()

    def refs(self) -> list[Ref]:
        out = []
        for si, sec in enumerate(self.song.sections):
            for ri, row in enumerate(sec.rows):
                for mi, m in enumerate(row):
                    out.append(Ref(si, ri, mi, sec, row, m))
        return out

    def clamp(self) -> None:
        refs = self.refs()
        c = self.cursor
        if not refs:
            c.m = c.e = 0
            return
        c.m = max(0, min(c.m, len(refs) - 1))
        c.e = max(0, min(c.e, len(refs[c.m].measure.events)))
        c.s = max(1, min(c.s, self.song.string_count))

    def ref(self) -> Ref | None:
        refs = self.refs()
        return refs[self.cursor.m] if refs else None

    def event(self) -> Event | None:
        r = self.ref()
        if r is None or self.cursor.e >= len(r.measure.events):
            return None
        return r.measure.events[self.cursor.e]

    def note(self) -> Note | None:
        ev = self.event()
        return next((n for n in ev.notes if n.string == self.cursor.s), None) if ev else None

    def sync_duration(self) -> None:
        """Make the 'current duration' follow the event under the cursor."""
        ev = self.event()
        if ev is not None:
            self.base, self.dotted, self.triplet = ev.base, ev.dotted, ev.triplet

    def string_name(self, s: int) -> str:
        tuning = self.song.tuning
        name = tuning[len(tuning) - s]
        return name.lower() if s == 1 and tuning == STANDARD_TUNING else name

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.redo_stack.append((copy.deepcopy(self.song), copy.copy(self.cursor)))
        self.song, self.cursor = self.undo_stack.pop()
        self._last_merge = None
        self.changed()
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        self.undo_stack.append((copy.deepcopy(self.song), copy.copy(self.cursor)))
        self.song, self.cursor = self.redo_stack.pop()
        self._last_merge = None
        self.changed()
        return True

    def load(self, song: Song) -> None:
        self.__init__(song)

    # -- navigation (not undoable) -----------------------------------------------
    def set_cursor(self, m: int, e: int, s: int) -> None:
        self.cursor = Cursor(m, e, s)
        self.clamp()
        self.sync_duration()

    def move_string(self, d: int) -> None:
        self.cursor.s = max(1, min(self.song.string_count, self.cursor.s + d))

    def move_beat(self, d: int) -> None:
        refs = self.refs()
        c = self.cursor
        e = c.e + d
        if e < 0:
            if c.m > 0:
                c.m -= 1
                c.e = max(0, len(refs[c.m].measure.events) - 1)
            else:
                c.e = 0
        elif e >= len(refs[c.m].measure.events):
            if c.m + 1 < len(refs):
                c.m, c.e = c.m + 1, 0
            elif any(not ev.rest for ev in refs[c.m].measure.events):
                self.add_measure()          # typing past the end of the song grows it
            else:
                c.e = max(0, len(refs[c.m].measure.events) - 1)
        else:
            c.e = e
        self.sync_duration()

    def move_measure(self, d: int) -> None:
        self.cursor.m += d
        self.cursor.e = 0
        self.clamp()
        self.sync_duration()

    # -- notes -------------------------------------------------------------------
    def _ensure_event(self) -> Event:
        r = self.ref()
        ev = self.event()
        if ev is None:
            ev = Event(rest=True, base=self.base, dotted=self.dotted, triplet=self.triplet)
            r.measure.events.append(ev)
            self.cursor.e = len(r.measure.events) - 1
        return ev

    @staticmethod
    def _set_note(ev: Event, string: int, fret: int | None) -> None:
        ev.notes = sorted([n for n in ev.notes if n.string != string] + [Note(string, fret)], key=lambda n: n.string)
        ev.rest = False

    @staticmethod
    def _remove_note(ev: Event, string: int) -> None:
        ev.notes = [n for n in ev.notes if n.string != string]
        if not ev.notes:
            ev.rest, ev.strum = True, None

    @_op()
    def set_fret(self, digit: int):
        now = time.monotonic()
        where = (self.cursor.m, self.cursor.e, self.cursor.s)
        last_time, last_where = self._last_digit
        existing = self.note()
        fret = digit
        if (last_where == where and now - last_time < TWO_DIGIT_WINDOW and existing
                and existing.fret is not None and 0 < existing.fret < 10 and existing.fret * 10 + digit <= 30):
            fret = existing.fret * 10 + digit
        ev = self._ensure_event()
        self._set_note(ev, self.cursor.s, fret)
        self._last_digit = (now, (self.cursor.m, self.cursor.e, self.cursor.s))

    @_op()
    def set_fret_value(self, fret: int | None):
        ev = self._ensure_event()
        note = self.note()
        if note is not None and note.fret == fret:
            return False
        self._set_note(ev, self.cursor.s, fret)

    @_op()
    def replace_song(self, song: Song):
        """Swap in a song parsed from edited text (undoable)."""
        self.song = song

    @_op()
    def mute(self):
        ev = self._ensure_event()
        self._set_note(ev, self.cursor.s, None)

    @_op()
    def delete(self):
        """Remove the note under the cursor; on an empty beat, remove the beat."""
        ev = self.event()
        if ev is None:
            return False
        if self.note() is not None:
            self._remove_note(ev, self.cursor.s)
        elif ev.rest:
            return self._delete_beat()
        else:
            return False

    @_op()
    def delete_beat(self):
        return self._delete_beat()

    def _delete_beat(self):
        r = self.ref()
        if self.event() is None:
            return False
        del r.measure.events[self.cursor.e]
        if not r.measure.events:
            r.measure.events = blank_events(r.measure.time, self.song.grid)
        self.cursor.e = min(self.cursor.e, len(r.measure.events) - 1)
        return True

    @_op()
    def move_note_to_string(self, d: int):
        ev, note = self.event(), self.note()
        target = self.cursor.s + d
        if note is None or not 1 <= target <= self.song.string_count:
            return False
        self._remove_note(ev, note.string)
        self._set_note(ev, target, note.fret)
        self.cursor.s = target

    @_op()
    def move_note(self, src: Cursor, dst: Cursor, copy_note: bool = False):
        refs = self.refs()
        try:
            sev = refs[src.m].measure.events[src.e]
        except IndexError:
            return False
        note = next((n for n in sev.notes if n.string == src.s), None)
        if note is None or (src.m, src.e, src.s) == (dst.m, dst.e, dst.s):
            return False
        dm = refs[dst.m].measure
        if dst.e >= len(dm.events):
            dm.events.append(Event(rest=True, base=sev.base, dotted=sev.dotted, triplet=sev.triplet))
            dst = Cursor(dst.m, len(dm.events) - 1, dst.s)
        dev = dm.events[dst.e]
        strum = sev.strum
        if not copy_note:
            self._remove_note(sev, src.s)
        self._set_note(dev, dst.s, note.fret)
        if dev is sev:
            dev.strum = strum
        self.cursor = Cursor(dst.m, dst.e, dst.s)

    # -- beats -------------------------------------------------------------------
    @_op()
    def set_duration(self, base: int | None = None, dotted: bool | None = None, triplet: bool | None = None):
        if base is not None:
            self.base = base
        if dotted is not None:
            self.dotted = dotted
        if triplet is not None:
            self.triplet = triplet
        ev = self.event()
        if ev is None:
            return False
        old = ev.duration
        ev.base, ev.dotted, ev.triplet = self.base, self.dotted, self.triplet
        self._rebalance(self.ref().measure.events, self.cursor.e, old - ev.duration)

    @staticmethod
    def _rebalance(events: list[Event], i: int, freed: Fraction) -> None:
        """Keep the following beats in place: fill freed time with rests, or eat into following rests."""
        if freed > 0:
            events[i + 1:i + 1] = rests_for(freed)
            return
        need = -freed
        j = i + 1
        while need > 0 and j < len(events) and events[j].rest:
            length = events[j].duration
            if length <= need:
                del events[j]
                need -= length
            else:
                events[j:j + 1] = rests_for(length - need)
                need = 0

    @_op()
    def toggle_strum(self, direction: str):
        ev = self.event()
        if ev is None or ev.rest:
            return False
        ev.strum = None if ev.strum == direction else direction

    @_op()
    def make_rest(self):
        ev = self.event()
        if ev is None or ev.rest:
            return False
        ev.notes, ev.rest, ev.strum = [], True, None

    @_op(merge="chord")
    def set_chord_name(self, name: str):
        ev = self.event()
        name = name.strip() or None
        if ev is None or ev.chord == name:
            return False
        ev.chord = name

    def _chord_in_effect(self) -> str | None:
        refs = self.refs()
        for m in range(self.cursor.m, -1, -1):
            events = refs[m].measure.events
            start = self.cursor.e - 1 if m == self.cursor.m else len(events) - 1
            for e in range(start, -1, -1):
                if events[e].chord:
                    return events[e].chord
        return None

    @_op()
    def apply_shape(self, name: str, shape: str):
        """Fill the current beat with a chord shape; label it unless the same chord is already in effect."""
        ev = self._ensure_event()
        ev.notes = shape_notes(shape, self.song.string_count)
        ev.rest = False
        if ev.chord is None and self._chord_in_effect() != name:
            ev.chord = name
        elif ev.chord is not None:
            ev.chord = name

    @_op()
    def insert_beat(self):
        r = self.ref()
        r.measure.events.insert(self.cursor.e, Event(rest=True, base=self.base, dotted=self.dotted,
                                                     triplet=self.triplet))

    @_op()
    def repeat_beat(self):
        """Copy the current beat's notes and strum onto the next beat and move there."""
        src = self.event()
        if src is None:
            return False
        refs = self.refs()
        m, e = self.cursor.m, self.cursor.e + 1
        if e >= len(refs[m].measure.events):
            if m + 1 >= len(refs):
                self._insert_measure_after(m)
                refs = self.refs()
            m, e = m + 1, 0
        dst = refs[m].measure.events[e]
        dst.notes = copy.deepcopy(src.notes)
        dst.rest, dst.strum = src.rest, src.strum
        self.cursor.m, self.cursor.e = m, e

    @_op()
    def swap_beat(self, d: int):
        events = self.ref().measure.events
        i, j = self.cursor.e, self.cursor.e + d
        if self.event() is None or not 0 <= j < len(events):
            return False
        events[i], events[j] = events[j], events[i]
        self.cursor.e = j

    # -- measures ----------------------------------------------------------------
    def _insert_measure_after(self, m: int, measure: Measure | None = None) -> None:
        r = self.refs()[m]
        new = measure or Measure(0, r.measure.time, blank_events(r.measure.time, self.song.grid))
        r.row.insert(r.mi + 1, new)

    @_op()
    def add_measure(self):
        self._insert_measure_after(self.cursor.m)
        self.cursor.m, self.cursor.e = self.cursor.m + 1, 0

    def _span(self, first: int | None, last: int | None) -> tuple[int, int]:
        """Measure range to act on: the given one, or just the cursor measure."""
        if first is None:
            first = last = self.cursor.m
        if last is None:
            last = first
        first, last = sorted((first, last))
        count = len(self.refs())
        return max(0, min(first, count - 1)), max(0, min(last, count - 1))

    @_op()
    def duplicate_measure(self, first: int | None = None, last: int | None = None):
        first, last = self._span(first, last)
        copies = [copy.deepcopy(r.measure) for r in self.refs()[first:last + 1]]
        for m in copies:
            m.at = None
        for i, m in enumerate(copies):
            self._insert_measure_after(last + i, m)
        self.cursor.m, self.cursor.e = last + 1, 0

    def copy_measure(self, first: int | None = None, last: int | None = None) -> int:
        """Copy measures to the clipboard; returns how many were copied."""
        first, last = self._span(first, last)
        self.clipboard = [copy.deepcopy(r.measure) for r in self.refs()[first:last + 1]]
        for m in self.clipboard:
            m.at = None
        return len(self.clipboard)

    @_op()
    def paste_measure(self):
        """Insert the copied measures after the cursor measure."""
        if not self.clipboard:
            return False
        for i, m in enumerate(self.clipboard):
            self._insert_measure_after(self.cursor.m + i, copy.deepcopy(m))
        self.cursor.m, self.cursor.e = self.cursor.m + 1, 0

    @_op()
    def clear_measures(self, first: int | None = None, last: int | None = None):
        """Replace the contents of measures with empty grid slots."""
        first, last = self._span(first, last)
        for r in self.refs()[first:last + 1]:
            r.measure.events = blank_events(r.measure.time, self.song.grid)
        self.cursor.e = 0

    @_op()
    def delete_measure(self, first: int | None = None, last: int | None = None):
        """Delete measures (the cursor measure by default). The song always keeps one measure."""
        first, last = self._span(first, last)
        if last - first + 1 >= len(self.refs()):          # everything: keep one empty measure
            keep = self.refs()[0].measure
            keep.events = blank_events(keep.time, self.song.grid)
            first = 1
        for m in range(last, first - 1, -1):
            r = self.refs()[m]
            del r.row[r.mi]
            if not r.row:
                del r.section.rows[r.ri]
            if not r.section.rows and len(self.song.sections) > 1:
                del self.song.sections[r.si]
        self.cursor.m = min(first, len(self.refs()) - 1)
        self.cursor.e = 0

    @_op()
    def set_time(self, num: int, den: int, from_start: bool = False):
        """Change the time signature from this measure until the next change."""
        refs = self.refs()
        start = 0 if from_start else self.cursor.m
        old = refs[start].measure.time
        if old == (num, den):
            return False
        for r in refs[start:]:
            if r.measure.time != old:
                break
            only_rests = all(ev.rest for ev in r.measure.events)
            r.measure.time = (num, den)
            if only_rests:
                r.measure.events = blank_events((num, den), self.song.grid)

    def line_break_after(self) -> bool:
        """True if the cursor measure ends its line of tab."""
        r = self.ref()
        return r.mi == len(r.row) - 1

    @_op()
    def toggle_line_break(self):
        r = self.ref()
        if r.mi == len(r.row) - 1:
            if r.ri + 1 >= len(r.section.rows):
                return False
            r.row.extend(r.section.rows.pop(r.ri + 1))
        else:
            r.section.rows.insert(r.ri + 1, r.row[r.mi + 1:])
            del r.row[r.mi + 1:]

    # -- sections ----------------------------------------------------------------
    @_op(merge="section")
    def rename_section(self, title: str):
        sec = self.ref().section
        title = title.strip() or None
        if sec.title == title:
            return False
        sec.title = title

    @_op()
    def split_section(self, title: str = "New section"):
        """Start a new section at the cursor measure (or rename if it already starts one)."""
        r = self.ref()
        if r.ri == 0 and r.mi == 0:
            r.section.title = title
            return True
        tail_rows = [r.row[r.mi:]] + r.section.rows[r.ri + 1:]
        del r.row[r.mi:]
        del r.section.rows[r.ri + 1:]
        if not r.row:
            del r.section.rows[r.ri]
        self.song.sections.insert(r.si + 1, Section(title, tail_rows))
        self.cursor.e = 0

    @_op()
    def add_section(self, title: str = "New section"):
        time_sig = self.refs()[-1].measure.time if self.refs() else (4, 4)
        self.song.sections.append(Section(title, [[Measure(0, time_sig, blank_events(time_sig, self.song.grid))]]))
        self.cursor = Cursor(len(self.refs()) - 1, 0, self.cursor.s)

    @_op()
    def set_grid(self, grid: int):
        """Use this grid for new measures and re-divide the empty stretches of the current measure."""
        changed = self.song.grid != grid
        self.song.grid = grid
        r = self.ref()
        events, out, run, t, run_start = r.measure.events, [], [], Fraction(0), Fraction(0)

        def flush():
            if not run:
                return
            if any(e.triplet for e in run):
                out.extend(run)
            else:
                rests = grid_rests(run_start, t, grid)
                rests[0].chord = next((e.chord for e in run if e.chord), None)
                out.extend(rests)
            run.clear()

        for ev in events:
            if ev.rest:
                if not run:
                    run_start = t
                run.append(ev)
            else:
                flush()
                out.append(ev)
            t += ev.duration
        flush()
        if [(e.base, e.dotted) for e in out] != [(e.base, e.dotted) for e in events]:
            r.measure.events = out
            self.cursor.e = min(self.cursor.e, len(out) - 1)
            changed = True
        return changed or False

    # -- song settings -----------------------------------------------------------
    @_op(merge="field")
    def set_field(self, name: str, value):
        if getattr(self.song, name) == value:
            return False
        setattr(self.song, name, value)
