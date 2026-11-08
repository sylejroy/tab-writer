"""Bridge to guitar-transcriber: YouTube song -> chords and pick strokes -> an editable Song.

guitar-transcriber (separate project) splits the guitar out of a recording, finds the chords, where
they are played and when each stroke lands. It needs PyTorch and a GPU, so it keeps its own Python
environment; this module only talks to it through subprocesses and the JSON files it writes:

    <root>/songs/<slug>/meta.json             video id, title, channel, duration
    <root>/songs/<slug>/output/result.json    chords, fret shapes, capo version, tempo, key
    <root>/songs/<slug>/output/events.json    the same events on a 16th-note grid, with pick strokes
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

from .editing import rests_for
from .model import Event, Measure, Note, Section, Song

CELLS_PER_BAR = 16                     # the transcriber's grid: one cell is a sixteenth note
DEFAULT_ROOTS = (Path("D:/guitar-transcriber"),)
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# (length in sixteenths, note value, dotted), longest first
NOTE_LENGTHS = ((16, 1, False), (12, 2, True), (8, 2, False), (6, 4, True), (4, 4, False),
                (3, 8, True), (2, 8, False), (1, 16, False))


class TranscriberError(RuntimeError):
    """Something the person should be told, in plain words."""


# -- locating the project -------------------------------------------------------------

class Transcriber:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    @classmethod
    def find(cls, configured: str | None = None) -> "Transcriber | None":
        candidates = [os.environ.get("TABWRITER_TRANSCRIBER"), configured, *DEFAULT_ROOTS]
        for c in candidates:
            if c and cls(c).available():
                return cls(c)
        return None

    @property
    def python(self) -> Path:
        win = self.root / ".venv" / "Scripts" / "python.exe"
        return win if win.exists() else self.root / ".venv" / "bin" / "python"

    def available(self) -> bool:
        return (self.root / "src" / "guitartab").is_dir() and self.python.exists()

    def song_dir(self, slug: str) -> Path:
        return self.root / "songs" / slug

    # -- songs already analysed -------------------------------------------------------
    def library(self) -> list[dict]:
        """Finished analyses, newest first: slug, id, title, channel, duration. Reads files only."""
        items = []
        for result in (self.root / "songs").glob("*/output/result.json"):
            folder = result.parent.parent
            entry: dict = {}
            try:
                if (folder / "meta.json").exists():
                    entry = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
                if not entry.get("title"):     # analysed from the command line: no meta.json
                    head = json.loads(result.read_text(encoding="utf-8"))
                    entry.update(title=head.get("title") or folder.name, duration=int(head.get("duration") or 0))
            except (OSError, ValueError):
                continue
            entry["slug"] = folder.name
            if not entry.get("id") and VIDEO_ID.match(folder.name):
                entry["id"] = folder.name
            items.append((result.stat().st_mtime, entry))
        return [e for _, e in sorted(items, key=lambda pair: pair[0], reverse=True)]

    def slug_for(self, video_id: str) -> str | None:
        """Folder of an analysed video, or None if it has not been analysed."""
        for entry in self.library():
            if entry.get("id") == video_id:
                return entry["slug"]
        return None

    # -- talking to the transcriber's environment ---------------------------------------
    def _env(self) -> dict:
        cache = self.root / ".cache"
        return os.environ | {"TORCH_HOME": str(cache / "torch"), "TMP": str(cache / "tmp"),
                             "TEMP": str(cache / "tmp"), "PYTHONIOENCODING": "utf-8"}

    def search(self, query: str) -> list[dict]:
        """YouTube results (or the one linked video): id, title, channel, duration, too_long."""
        code = ("import json, sys\nfrom guitartab import youtube as yt\n"
                "try:\n    print(json.dumps(yt.search(sys.argv[1])))\n"
                "except yt.YouTubeError as e:\n    print(json.dumps({'error': str(e)}))\n")
        try:
            done = subprocess.run([str(self.python), "-I", "-X", "utf8", "-c", code, query[:200]],
                                  capture_output=True, timeout=60, cwd=self.root, env=self._env(),
                                  **_no_window())
        except subprocess.TimeoutExpired as exc:
            raise TranscriberError("YouTube took too long to answer.") from exc
        try:
            data = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
        except (ValueError, IndexError):
            raise TranscriberError("Searching failed: " + _last_line(done.stderr)) from None
        if isinstance(data, dict):
            raise TranscriberError(data.get("error", "Searching failed."))
        return [d for d in data if isinstance(d, dict) and VIDEO_ID.match(str(d.get("id", "")))]

    def analyse(self, item: dict, on_stage=None, on_process=None) -> str:
        """Run the full analysis for a video (minutes, GPU). Returns the song folder name.

        `on_stage(name)` hears progress; `on_process(popen)` gets the process so it can be stopped.
        """
        video_id = str(item.get("id", ""))
        if not VIDEO_ID.match(video_id):
            raise TranscriberError("That is not a YouTube video id.")
        if item.get("too_long"):
            raise TranscriberError("That video is over 10 minutes, which is too long to analyse.")
        slug = self.slug_for(video_id) or video_id
        folder = self.song_dir(slug)
        folder.mkdir(parents=True, exist_ok=True)
        meta = {k: item[k] for k in ("id", "title", "channel", "duration", "too_long") if k in item}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        log_path = self.root / "logs" / f"{slug}.log"
        log_path.parent.mkdir(exist_ok=True)
        title = str(item.get("title") or slug)
        command = [str(self.python), "-I", "-X", "utf8", "-m", "guitartab.cli", slug,
                   f"--source=https://www.youtube.com/watch?v={video_id}", f"--title={title}"]
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=self.root,
                                    env=self._env(), encoding="utf-8", errors="replace", **_no_window())
            if on_process:
                on_process(proc)
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if line.startswith("STAGE ") and on_stage:
                    on_stage(line.split()[1])
            proc.wait()
        if proc.returncode != 0:
            raise TranscriberError(f"The analysis stopped with an error. Details are in {log_path}.")
        if not (folder / "output" / "result.json").exists():
            raise TranscriberError("The analysis finished without a result.")
        return slug


def _no_window() -> dict:
    """Keep console windows from flashing up on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _last_line(raw: bytes) -> str:
    lines = raw.decode("utf-8", "replace").strip().splitlines()
    return lines[-1] if lines else "no details"


# -- turning an analysis into a Song -----------------------------------------------------

def _note_value(cells: int) -> tuple[int, int, bool]:
    """The longest plain or dotted note that fits in `cells` sixteenths: (cells used, base, dotted)."""
    for length, base, dotted in NOTE_LENGTHS:
        if length <= cells:
            return length, base, dotted
    return 1, 16, False


def _rests(cells: int) -> list[Event]:
    return rests_for(Fraction(cells, 4))


def _strum(offset: int, strike_offsets: list[int]) -> str:
    """Guess the strum direction: down on the beat, up in between (eighth or sixteenth feel)."""
    if all(o % 2 == 0 for o in strike_offsets):
        return "down" if offset % 4 == 0 else "up"
    return "down" if offset % 2 == 0 else "up"


NOISE = re.compile(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|upgrade|\bmix\b|\b4k\b|\bhd\b)"
                   r"[^\)\]]*[\)\]]", re.IGNORECASE)
TRAILING = re.compile(r"\s+(official\s+)?(music\s+)?(video|audio|lyrics?)\s*$", re.IGNORECASE)


def split_title(raw: str, channel: str = "") -> tuple[str, str]:
    """'Oasis - Wonderwall (Official Video)' -> ('Wonderwall', 'Oasis')."""
    title = TRAILING.sub("", NOISE.sub("", raw)).strip(" -|")
    channel = channel.removesuffix(" - Topic").removesuffix("VEVO").strip()
    for sep in (" - ", " – ", " — ", " | "):
        if sep in title:
            artist, rest = title.split(sep, 1)
            return rest.strip(" '\""), artist.strip()
    return title.strip(" '\""), channel


def _cell_clock(bar_times: list[float], first_cell: int):
    """Seconds at a grid cell, from the bar start times the transcriber measured (beat tracking)."""
    times = [float(t) for t in bar_times]
    if len(times) < 2:
        return lambda cell: None
    base = first_cell % CELLS_PER_BAR         # bar_times[k] is the time of cell base + 16k

    def at(cell: int) -> float:
        k = (cell - base) / CELLS_PER_BAR
        i = min(max(int(k // 1), 0), len(times) - 2)
        return times[i] + (times[i + 1] - times[i]) * (k - i)

    return at


def song_from_analysis(folder: str | os.PathLike, *, use_capo: bool = True, arrows: bool = True,
                       rhythm: int = 8, per_line: int = 2) -> Song:
    """Build an editable Song from a guitar-transcriber song folder (or its output folder).

    rhythm=8 rounds pick strokes to eighth notes (easier to read); rhythm=16 keeps sixteenths.
    """
    folder = Path(folder)
    out = folder if (folder / "result.json").exists() else folder / "output"
    try:
        result = json.loads((out / "result.json").read_text(encoding="utf-8"))
        grid_events = json.loads((out / "events.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TranscriberError(f"No finished analysis in {folder}.") from exc
    meta = {}
    if (out.parent / "meta.json").exists():
        try:
            meta = json.loads((out.parent / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

    report = result.get("report", {})
    detail = result.get("events", [])
    capo = result.get("capo") if use_capo else None
    if capo and len(capo.get("events", [])) != len(grid_events):
        capo = None
    if len(detail) != len(grid_events):
        raise TranscriberError("The analysis files do not match each other; try analysing the song again.")

    first = int(report.get("first_bar_cell", 0))
    strings = 6
    cell_time = _cell_clock(result.get("bars") or [], first)

    # Every stroke on the grid: cell -> (notes, label). Plus which event covers each cell.
    strokes: dict[int, tuple[list[Note], str | None]] = {}
    covering: list[tuple[int, int, list[Note]]] = []
    last_label = None
    for i, (ev, info) in enumerate(zip(grid_events, detail)):
        frets = (capo["events"][i].get("frets") if capo else info.get("frets"))
        if not frets:
            continue
        notes = [Note(strings - idx, int(f)) for idx, f in enumerate(frets) if f is not None]
        if not notes:
            continue
        notes.sort(key=lambda n: n.string)
        name = capo["events"][i].get("shape") if capo else info.get("chord")
        label = None
        if name and info.get("plain"):
            label = name + ("" if info.get("sure", True) else "?")
        start, end = int(ev["start_cell"]), int(ev["end_cell"])
        covering.append((start, end, notes))
        cells = sorted(set(ev.get("strikes") or [start]))
        if rhythm == 8:                     # round to the nearest eighth note of the bar
            cells = sorted({first + (c - first + 1) // 2 * 2 for c in cells})
        for k, cell in enumerate(cells):
            strokes[cell] = (notes, label if k == 0 and label != last_label else None)
        if label:
            last_label = label

    if not strokes:
        raise TranscriberError("The analysis found no playable chords in this song.")

    first_bar = (min(strokes) - first) // CELLS_PER_BAR
    last_cell = max(end for _, end, _ in covering)
    last_bar = (last_cell - 1 - first) // CELLS_PER_BAR

    measures: list[Measure] = []
    for bar in range(first_bar, last_bar + 1):
        c0 = first + bar * CELLS_PER_BAR
        offsets = sorted(c - c0 for c in strokes if c0 <= c < c0 + CELLS_PER_BAR)
        real = list(offsets)
        bar_strokes = {o: strokes[c0 + o] for o in offsets}
        # A chord still ringing from the previous bar is shown again at the bar line, without an arrow.
        if 0 not in bar_strokes:
            ringing = next((notes for s, e, notes in covering if s < c0 < e), None)
            if ringing:
                bar_strokes[0] = (ringing, None)
                offsets = [0] + offsets
        events: list[Event] = []
        if not offsets:
            events = _rests(CELLS_PER_BAR)
        else:
            if offsets[0] > 0:
                events += _rests(offsets[0])
            for j, o in enumerate(offsets):
                nxt = offsets[j + 1] if j + 1 < len(offsets) else CELLS_PER_BAR
                used, base, dotted = _note_value(nxt - o)
                notes, label = bar_strokes[o]
                strum = _strum(o, real) if arrows and o in real else None
                events.append(Event(notes=[Note(n.string, n.fret) for n in notes], base=base, dotted=dotted,
                                    strum=strum, chord=label))
                if nxt - o > used:
                    events += _rests(nxt - o - used)
        at = cell_time(c0)
        measures.append(Measure(0, (4, 4), events, at=round(at, 3) if at is not None and at >= 0 else None))

    title, artist = split_title(str(result.get("title") or meta.get("title") or folder.name),
                                str(meta.get("channel") or ""))
    song = Song(title=title, artist=artist, per_line=per_line, grid=8 if rhythm == 8 else 4)
    tempo = report.get("tempo")
    if isinstance(tempo, list) and tempo:
        song.tempo = int(round(float(tempo[0])))
    if capo:
        song.capo = int(capo.get("fret") or 0)
    key = (report.get("key") or {}).get("key") if isinstance(report.get("key"), dict) else None
    if capo and key:
        song.meta["Key"] = f"{key} (sounding)"
    elif key:
        song.meta["Key"] = key
    video_id = meta.get("id") or (out.parent.name if VIDEO_ID.match(out.parent.name) else None)
    if video_id:
        song.meta["Source"] = f"youtube.com/watch?v={video_id}"
    song.subtitle = "Transcribed — check by ear"
    song.sections.append(Section("Transcription", [measures]))
    song.renumber()
    return song
