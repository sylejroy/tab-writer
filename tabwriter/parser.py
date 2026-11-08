"""Parser for the .tab text notation. See README.md for the full syntax.

Quick reference::

    title: Song name          settings: title subtitle artist capo time tempo tuning page
    # Intro                   starts a section (printed as a red bar)
    def am = [Am] :4 0.5^ ... defines a macro, used as $am
    | [Am] :4 0.5^ :8 (2.3 2.4 x.5)^ 1.2v | % |

Tokens inside a measure:
    :4  :8  :16  :4.  :8t     set the duration of the following events (sticky)
    3.5                       fret 3 on string 5 (string 1 = high e)
    x.5                       muted string 5
    (2.3 2.4 x.5)             several strings struck together
    x02210                    chord shape, one char per string from low E to high e ("-" = not played)
    ^  v                      suffix: up / down strum arrow, e.g. 0.5^  (2.3 2.4)v  x02210v
    [Am]                      chord name above the next event
    r                         rest
    @3/4                      time signature change starting with this measure
    ~12.34                    this measure starts 12.34 s into the audio (written by the YouTube import)
    %                         a whole measure: repeat the previous measure
"""
from __future__ import annotations

import copy
import re
from fractions import Fraction

from .model import Event, Measure, Note, Problem, Section, Song

KNOWN_KEYS = {"title", "subtitle", "artist", "capo", "time", "tempo", "tuning", "page", "per_line", "grid"}

KEY_RE = re.compile(r"^([A-Za-z][A-Za-z_]+):(.*)$")
DEF_RE = re.compile(r"^def\s+([A-Za-z_]\w*)\s*=\s*(.*)$")
MACRO_RE = re.compile(r"\$([A-Za-z_]\w*)")
TOKEN_RE = re.compile(r"\[[^\]]*\]|\([^)]*\)\S*|\S+")
DUR_RE = re.compile(r"^:(1|2|4|8|16|32)(\.?)(t?)$")
NOTE_RE = re.compile(r"^(\d{1,2}|[xX])\.(\d{1,2})$")
SHAPE_RE = re.compile(r"^[0-9xX\-]+$")
TIME_RE = re.compile(r"^(\d{1,2})\s*/\s*(1|2|4|8|16|32)$")
AUDIO_TIME_RE = re.compile(r"^~(\d+(?:\.\d+)?)$")
INLINE_TIME_RE = re.compile(r"^@(\d{1,2})/(1|2|4|8|16|32)$")
PITCH_RE = re.compile(r"[A-Ga-g][#b]?")
STRUMS = {"^": "up", "v": "down"}


def parse(text: str) -> Song:
    return _Parser().parse(text)


def _beats(quarters: Fraction, den: int) -> str:
    value = quarters * den / 4
    if value.denominator == 1:
        return str(value.numerator)
    return f"{float(value):.3g}"


class _Parser:
    def __init__(self) -> None:
        self.song = Song()
        self.macros: dict[str, str] = {}
        self.section: Section | None = None
        self.time = (4, 4)
        self.time_pending = True          # show the time signature on the next measure
        self.base, self.dotted, self.triplet = 4, False, False
        self.number = 0
        self.last: Measure | None = None
        self.pending_at: float | None = None

    # -- helpers -----------------------------------------------------------
    def problem(self, line: int, message: str, level: str = "error") -> None:
        self.song.problems.append(Problem(line, message, level))

    def expand(self, text: str, line: int) -> str:
        for _ in range(20):
            if "$" not in text:
                return text

            def sub(m: re.Match) -> str:
                name = m.group(1)
                if name not in self.macros:
                    self.problem(line, f"Unknown macro ${name} (define it first with: def {name} = ...)")
                    return ""
                return f" {self.macros[name]} "

            text = MACRO_RE.sub(sub, text)
        return text

    # -- main loop ---------------------------------------------------------
    def parse(self, text: str) -> Song:
        for lineno, raw in enumerate(text.splitlines(), 1):
            line = raw.split("//", 1)[0].strip()
            if not line:
                continue
            if line.startswith("#"):
                self.section = Section(line.lstrip("#").strip() or None)
                self.song.sections.append(self.section)
                continue
            m = DEF_RE.match(line)
            if m:
                self.macros[m.group(1)] = self.expand(m.group(2), lineno)
                continue
            m = KEY_RE.match(line)
            if m:
                self.setting(m.group(1).lower(), m.group(2).strip(), lineno)
                continue
            self.music(line, lineno)
        self.check()
        return self.song

    def setting(self, key: str, value: str, line: int) -> None:
        song = self.song
        if key in ("title", "subtitle", "artist"):
            setattr(song, key, value)
        elif key == "capo":
            digits = re.search(r"\d+", value)
            if digits:
                song.capo = int(digits.group())
            elif value.lower() in ("", "none", "no", "0"):
                song.capo = 0
            else:
                self.problem(line, f"capo should be a fret number, got '{value}'")
        elif key == "tempo":
            digits = re.search(r"\d+", value)
            if digits:
                song.tempo = int(digits.group())
            else:
                self.problem(line, f"tempo should be a number of beats per minute, got '{value}'")
        elif key == "time":
            m = TIME_RE.match(value)
            if not m:
                self.problem(line, f"time should look like 4/4, 3/4 or 6/8, got '{value}'")
                return
            new = (int(m.group(1)), int(m.group(2)))
            if new[0] == 0:
                self.problem(line, "time signature needs at least one beat")
                return
            if new != self.time or self.number == 0:
                self.time_pending = True
            self.time = new
        elif key == "tuning":
            if self.number:
                self.problem(line, "tuning must be set before the first measure")
                return
            pitches = PITCH_RE.findall(value)
            if not 3 <= len(pitches) <= 10:
                self.problem(line, f"tuning needs 3–10 strings listed low to high, e.g. 'E A D G B E', got '{value}'")
                return
            song.tuning = [p[0].upper() + p[1:] for p in pitches]
        elif key == "per_line":
            if value.isdigit() and int(value) <= 16:
                song.per_line = int(value)
            else:
                self.problem(line, f"per_line should be a number of measures (0 = as many as fit), got '{value}'")
        elif key == "grid":
            if value.strip() in ("1", "2", "4", "8", "16"):
                song.grid = int(value)
            else:
                self.problem(line, f"grid should be 1, 2, 4, 8 or 16, got '{value}'")
        elif key == "page":
            if value.lower() in ("a4", "letter"):
                song.page = "letter" if value.lower() == "letter" else "A4"
            else:
                self.problem(line, f"page should be A4 or letter, got '{value}'")
        else:
            song.meta[key.replace("_", " ").title()] = value

    def music(self, line: str, lineno: int) -> None:
        if self.section is None:
            self.section = Section(None)
            self.song.sections.append(self.section)
        text = self.expand(line, lineno)
        row: list[Measure] = []
        for segment in text.split("|"):
            segment = segment.strip()
            if not segment:
                continue
            measure = self.measure(segment, lineno)
            if measure is not None:
                row.append(measure)
        if row:
            self.section.rows.append(row)

    def measure(self, segment: str, lineno: int) -> Measure | None:
        if segment == "%":
            if self.last is None:
                self.problem(lineno, "'%' repeats the previous measure, but there is none yet")
                return None
            events = copy.deepcopy(self.last.events)
        else:
            events = self.events(segment, lineno)
        self.number += 1
        m = Measure(self.number, self.time, events, show_time=self.time_pending, line=lineno,
                    at=self.pending_at)
        self.pending_at = None
        self.time_pending = False
        self.last = m
        return m

    def events(self, segment: str, lineno: int) -> list[Event]:
        events: list[Event] = []
        chord: str | None = None
        for tok in TOKEN_RE.findall(segment):
            if tok.startswith("["):
                if chord is not None:
                    self.problem(lineno, f"Chord [{chord}] is not followed by a note", "warning")
                chord = tok[1:-1].strip() or None
                continue
            if tok.startswith("~"):
                m = AUDIO_TIME_RE.match(tok)
                if not m:
                    self.problem(lineno, f"Bad audio time '{tok}' — use e.g. ~12.5 (seconds)")
                else:
                    self.pending_at = float(m.group(1))
                continue
            if tok.startswith("@"):
                m = INLINE_TIME_RE.match(tok)
                if not m or int(m.group(1)) == 0:
                    self.problem(lineno, f"Bad time signature '{tok}' — use e.g. @3/4 at the start of a measure")
                elif events:
                    self.problem(lineno, f"'{tok}' must be the first thing in a measure")
                else:
                    new = (int(m.group(1)), int(m.group(2)))
                    if new != self.time:
                        self.time, self.time_pending = new, True
                continue
            if tok.startswith(":"):
                m = DUR_RE.match(tok)
                if not m:
                    self.problem(lineno, f"Bad duration '{tok}' — use :1 :2 :4 :8 :16 :32, add '.' for dotted or 't' for triplet")
                    continue
                self.base, self.dotted, self.triplet = int(m.group(1)), bool(m.group(2)), bool(m.group(3))
                continue

            body, strum = tok, None
            if len(body) > 1 and body[-1] in STRUMS:
                body, strum = body[:-1], STRUMS[body[-1]]

            if body.lower() == "r":
                events.append(self.event([], strum=None, chord=chord, rest=True))
                chord = None
                continue

            notes = self.notes(body, tok, lineno)
            if notes is None:
                continue
            events.append(self.event(notes, strum=strum, chord=chord))
            chord = None
        if chord is not None:
            self.problem(lineno, f"Chord [{chord}] at the end of a measure has no note to sit on", "warning")
        return events

    def event(self, notes: list[Note], *, strum, chord, rest: bool = False) -> Event:
        return Event(notes=notes, base=self.base, dotted=self.dotted, triplet=self.triplet,
                     strum=strum, chord=chord, rest=rest)

    def notes(self, body: str, tok: str, lineno: int) -> list[Note] | None:
        n = self.song.string_count
        if body.startswith("("):
            if not body.endswith(")"):
                self.problem(lineno, f"'{tok}': missing ')'")
                return None
            parts = body[1:-1].split()
            if not parts:
                self.problem(lineno, "Empty chord '()'")
                return None
            notes = []
            for part in parts:
                note = self.note(part, lineno)
                if note is None:
                    return None
                if any(x.string == note.string for x in notes):
                    self.problem(lineno, f"'{tok}' uses string {note.string} twice")
                    return None
                notes.append(note)
            return sorted(notes, key=lambda x: x.string)
        if SHAPE_RE.match(body) and "." not in body:
            if len(body) != n:
                self.problem(lineno, f"Unrecognized token '{tok}'. A single note is fret.string (e.g. 3.5); "
                                     f"a chord shape needs exactly {n} characters, low string first (e.g. x02210)")
                return None
            notes = []
            for i, ch in enumerate(body):
                if ch == "-":
                    continue
                notes.append(Note(string=n - i, fret=None if ch in "xX" else int(ch)))
            if not notes:
                self.problem(lineno, f"Chord shape '{tok}' plays no strings")
                return None
            return sorted(notes, key=lambda x: x.string)
        note = self.note(body, lineno, tok)
        return None if note is None else [note]

    def note(self, text: str, lineno: int, tok: str | None = None) -> Note | None:
        m = NOTE_RE.match(text)
        if not m:
            self.problem(lineno, f"Unrecognized token '{tok or text}'. Notes are fret.string, e.g. 3.5 or x.6")
            return None
        string = int(m.group(2))
        if not 1 <= string <= self.song.string_count:
            self.problem(lineno, f"'{tok or text}': string {string} does not exist (1–{self.song.string_count})")
            return None
        fret = None if m.group(1) in "xX" else int(m.group(1))
        return Note(string=string, fret=fret)

    def check(self) -> None:
        self.song.problems.extend(check_measures(self.song))


def check_measures(song: Song) -> list[Problem]:
    """Warnings for measures whose length doesn't match their time signature."""
    out = []
    for m in song.measures():
        if m.events and m.actual != m.expected:
            num, den = m.time
            out.append(Problem(m.line, f"Measure {m.number} has {_beats(m.actual, den)} beats, "
                                       f"expected {num} in {num}/{den}", "warning", m.number))
    return out
