"""Play a transcribed song's audio and work out which beat of the tab is sounding.

Audio plays through the Windows Media Control Interface (winmm), which can seek, pause and report the
position of a WAV file without any extra packages. Timing comes from the per-measure `at` anchors
(seconds into the audio) that the YouTube import writes; measures without one are interpolated.
"""
from __future__ import annotations

import bisect
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .model import Song
from .transcriber import Transcriber, TranscriberError, _no_window

TRACKS = {"mix": "Full song", "guitar": "Guitar only"}


class PlayerError(RuntimeError):
    pass


# -- timeline ---------------------------------------------------------------------------

@dataclass
class Timeline:
    times: list[float]                 # start time of every event, in song order
    slots: list[tuple[int, int]]       # (measure index, event index) for each time
    end: float

    def index_at(self, t: float) -> int:
        """Index of the event sounding at time t (-1 before the first)."""
        return bisect.bisect_right(self.times, t) - 1

    def time_of(self, m: int, e: int) -> float | None:
        for i, slot in enumerate(self.slots):
            if slot[0] == m and slot[1] >= e or slot[0] > m:
                return self.times[i]
        return None


def has_timing(song: Song) -> bool:
    return any(m.at is not None for m in song.measures())


def timeline(song: Song) -> Timeline | None:
    measures = list(song.measures())
    anchors = [m.at for m in measures]
    known = [(i, a) for i, a in enumerate(anchors) if a is not None]
    if not known:
        return None
    steps = [(b - a) / (j - i) for (i, a), (j, b) in zip(known, known[1:]) if j > i and b > a]
    bar = sorted(steps)[len(steps) // 2] if steps else 2.0
    starts: list[float] = []
    for i, a in enumerate(anchors):
        if a is not None:
            starts.append(a)
            continue
        before = next(((p, v) for p, v in reversed(known) if p < i), None)
        after = next(((n, v) for n, v in known if n > i), None)
        if before and after:
            (p, vp), (n, vn) = before, after
            starts.append(vp + (vn - vp) * (i - p) / (n - p))
        elif before:
            starts.append(before[1] + (i - before[0]) * bar)
        else:
            starts.append(after[1] - (after[0] - i) * bar)
    times, slots = [], []
    for i, m in enumerate(measures):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(starts) else start + bar
        length = max(m.actual, m.expected) or 1
        pos = 0
        for e, ev in enumerate(m.events):
            times.append(start + (end - start) * float(pos / length))
            slots.append((i, e))
            pos += ev.duration
    total_end = starts[-1] + bar if starts else 0.0
    # Keep times increasing even if anchors were edited out of order.
    for k in range(1, len(times)):
        times[k] = max(times[k], times[k - 1])
    return Timeline(times, slots, total_end)


# -- finding the audio -----------------------------------------------------------------------

def video_id(song: Song) -> str | None:
    source = song.meta.get("Source", "")
    return source.rsplit("v=", 1)[-1][:11] if "v=" in source else None


def audio_file(song: Song, track: str = "mix", transcriber_dir: str | None = None) -> Path:
    """The WAV to play for a transcribed song. The guitar stem is converted to 16-bit once and cached."""
    vid = video_id(song)
    tr = Transcriber.find(transcriber_dir)
    if not vid or tr is None:
        raise PlayerError("This tab has no recording to play. Playback works for songs imported from YouTube "
                          "while guitar-transcriber's files are still on this computer.")
    slug = tr.slug_for(vid)
    if not slug:
        raise PlayerError("The analysis for this song is no longer in guitar-transcriber's songs folder.")
    folder = tr.song_dir(slug)
    if track == "mix":
        path = folder / "mix.wav"
        if not path.exists():
            raise PlayerError(f"{path} is missing.")
        return path
    stem = folder / "stems" / f"{track}.wav"
    if not stem.exists():
        raise PlayerError(f"The separated {track} track is missing ({stem}).")
    cached = tr.root / ".cache" / "tabwriter" / f"{slug}-{track}.wav"
    if cached.exists() and cached.stat().st_mtime >= stem.stat().st_mtime:
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    code = ("import sys, soundfile as sf\n"
            "data, rate = sf.read(sys.argv[1], dtype='float32')\n"
            "sf.write(sys.argv[2], data.clip(-1, 1), rate, subtype='PCM_16')\n")
    done = subprocess.run([str(tr.python), "-I", "-c", code, str(stem), str(cached)], capture_output=True,
                          timeout=180, **_no_window())
    if done.returncode != 0 or not cached.exists():
        raise PlayerError("Could not prepare the guitar track for playback.")
    return cached


# -- playing ---------------------------------------------------------------------------------

class Player:
    """Windows MCI wave player: open, play from a position, pause, report position."""

    ALIAS = "tabwriter_audio"

    def __init__(self):
        if sys.platform != "win32":
            raise PlayerError("Playback is only available on Windows for now.")
        import ctypes
        self._winmm = ctypes.windll.winmm
        self._buffer = ctypes.create_unicode_buffer(256)
        self.path: Path | None = None
        self.playing = False

    def _send(self, command: str) -> str:
        error = self._winmm.mciSendStringW(command, self._buffer, 255, 0)
        if error:
            import ctypes
            text = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(error, text, 255)
            raise PlayerError(text.value or f"Audio error {error}")
        return self._buffer.value

    def load(self, path: Path) -> None:
        if self.path == path:
            return
        self.close()
        self._send(f'open "{path}" type waveaudio alias {self.ALIAS}')
        self._send(f"set {self.ALIAS} time format milliseconds")
        self.path = path

    @property
    def length(self) -> float:
        return int(self._send(f"status {self.ALIAS} length") or 0) / 1000 if self.path else 0.0

    def play(self, start: float) -> None:
        ms = max(0, int(start * 1000))
        self._send(f"play {self.ALIAS} from {min(ms, max(0, int(self.length * 1000) - 1))}")
        self.playing = True

    def pause(self) -> None:
        if self.path:
            self._send(f"pause {self.ALIAS}")
        self.playing = False

    def position(self) -> float:
        if not self.path:
            return 0.0
        return int(self._send(f"status {self.ALIAS} position") or 0) / 1000

    def finished(self) -> bool:
        return bool(self.path) and self._send(f"status {self.ALIAS} mode") == "stopped"

    def close(self) -> None:
        if self.path:
            try:
                self._send(f"close {self.ALIAS}")
            except PlayerError:
                pass
        self.path = None
        self.playing = False


__all__ = ["Player", "PlayerError", "Timeline", "timeline", "has_timing", "audio_file", "TRACKS",
           "TranscriberError"]
