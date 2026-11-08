"""Data model for a tab: song -> sections -> rows -> measures -> events -> notes."""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

STANDARD_TUNING = ["E", "A", "D", "G", "B", "E"]  # lowest string first


@dataclass
class Note:
    string: int        # 1 = highest-pitched string (top line of the staff)
    fret: int | None   # None = muted / dead note, drawn as "X"

    @property
    def label(self) -> str:
        return "X" if self.fret is None else str(self.fret)


@dataclass
class Event:
    """Something that happens at one point in time: a note, a chord, a strum or a rest."""

    notes: list[Note] = field(default_factory=list)
    base: int = 4                 # 1 whole, 2 half, 4 quarter, 8 eighth, 16, 32
    dotted: bool = False
    triplet: bool = False
    strum: str | None = None      # "up" | "down"
    chord: str | None = None      # chord name printed above the staff
    rest: bool = False

    @property
    def duration(self) -> Fraction:
        """Length in quarter notes."""
        d = Fraction(4, self.base)
        if self.dotted:
            d *= Fraction(3, 2)
        if self.triplet:
            d *= Fraction(2, 3)
        return d


@dataclass
class Measure:
    number: int
    time: tuple[int, int]
    events: list[Event] = field(default_factory=list)
    show_time: bool = False       # draw the time signature at the start of this measure
    line: int = 0                 # source line, for error messages
    at: float | None = None       # where this bar starts in the song's audio (seconds), for playback

    @property
    def expected(self) -> Fraction:
        return Fraction(4 * self.time[0], self.time[1])

    @property
    def actual(self) -> Fraction:
        return sum((e.duration for e in self.events), Fraction(0))


@dataclass
class Section:
    title: str | None
    rows: list[list[Measure]] = field(default_factory=list)   # one row per source line


@dataclass
class Problem:
    line: int
    message: str
    level: str = "error"          # "error" | "warning"
    measure: int | None = None    # measure number the problem refers to, if any


@dataclass
class Song:
    title: str = ""
    subtitle: str = ""
    artist: str = ""
    capo: int = 0
    tempo: int | None = None
    tuning: list[str] = field(default_factory=lambda: list(STANDARD_TUNING))
    page: str = "A4"
    per_line: int = 2             # measures per line of tab (0 = as many as fit)
    grid: int = 8                 # slot size of new empty measures: 1, 2, 4 or 8
    meta: dict[str, str] = field(default_factory=dict)   # any other "key: value" lines
    sections: list[Section] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def string_count(self) -> int:
        return len(self.tuning)

    def measures(self):
        for section in self.sections:
            for row in section.rows:
                yield from row

    def renumber(self) -> None:
        """Recompute measure numbers and where time signatures are shown (after editing)."""
        prev = None
        for i, m in enumerate(self.measures(), 1):
            m.number = i
            m.show_time = prev is None or m.time != prev
            prev = m.time

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]
