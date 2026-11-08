import json
import tempfile
import unittest
from pathlib import Path

from tabwriter import parse
from tabwriter.serialize import serialize
from tabwriter.player import timeline, video_id
from tabwriter.transcriber import Transcriber, split_title, song_from_analysis


def write_analysis(folder: Path, capo: bool = False) -> Path:
    """A tiny guitar-transcriber result: G for a bar of eighth strums, then C ringing into the next bar."""
    out = folder / "output"
    out.mkdir(parents=True)
    g = [3, 2, 0, 0, 0, 3]                      # low string first
    c = [None, 3, 2, 0, 1, 0]
    grid = [
        {"start_cell": 4, "end_cell": 20, "strikes": [4, 6, 8, 10, 12, 14, 16, 18]},
        {"start_cell": 20, "end_cell": 40, "strikes": [20, 23]},     # 23 is beat 1 "a"; rounds to beat 2
    ]
    detail = [
        {"chord": "G", "frets": g, "sure": True, "plain": True},
        {"chord": "C", "frets": c, "sure": False, "plain": True},
    ]
    result = {
        "title": "Some Band - Some Song (Official Video)",
        "bars": [0.5, 2.5, 4.5],                 # bar k starts at cell 4 + 16k
        "report": {"first_bar_cell": 4, "tempo": [96.4, 96.4], "key": {"key": "G major"}},
        "events": detail,
        "capo": {"fret": 2, "events": [{"shape": "F", "frets": [1, 3, 3, 2, 1, 1]},
                                       {"shape": "Bb", "frets": [None, 1, 3, 3, 3, 1]}]} if capo else None,
    }
    (out / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (out / "events.json").write_text(json.dumps(grid), encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps({"id": "abcdefghijk", "title": result["title"],
                                                  "channel": "Some Band - Topic", "duration": 200}), encoding="utf-8")
    return folder


class TranscriberImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bars_rhythm_chords_and_arrows(self):
        song = song_from_analysis(write_analysis(self.root / "songs" / "abcdefghijk"))
        self.assertEqual((song.title, song.artist, song.tempo), ("Some Song", "Some Band", 96))
        self.assertEqual(song.meta["Source"], "youtube.com/watch?v=abcdefghijk")
        measures = list(song.measures())
        self.assertEqual(len(measures), 3)
        self.assertTrue(all(m.actual == m.expected for m in measures))
        bar1 = measures[0].events
        self.assertEqual([e.base for e in bar1], [8] * 8)
        self.assertEqual(bar1[0].chord, "G")
        self.assertEqual([e.strum for e in bar1[:4]], ["down", "up", "down", "up"])
        self.assertEqual([(n.string, n.fret) for n in bar1[0].notes],
                         [(1, 3), (2, 0), (3, 0), (4, 0), (5, 2), (6, 3)])
        bar2 = measures[1].events
        self.assertEqual(bar2[0].chord, "C?")                 # unsure chords are marked
        self.assertEqual([(e.base, e.dotted) for e in bar2], [(4, False), (2, True)])  # beat 1, beat 2 + ring
        bar3 = measures[2].events                              # C still ringing: repeated without an arrow
        self.assertIsNone(bar3[0].strum)
        self.assertFalse(bar3[0].rest)

    def test_sixteenths_keep_offbeat_stroke(self):
        song = song_from_analysis(write_analysis(self.root / "s"), rhythm=16)
        bar2 = list(song.measures())[1].events
        self.assertEqual([(e.base, e.dotted, e.rest) for e in bar2],
                         [(8, True, False), (2, True, False), (16, False, True)])

    def test_capo_version(self):
        song = song_from_analysis(write_analysis(self.root / "s", capo=True))
        self.assertEqual(song.capo, 2)
        self.assertEqual(list(song.measures())[0].events[0].chord, "F")
        plain = song_from_analysis(self.root / "s", use_capo=False)
        self.assertEqual((plain.capo, list(plain.measures())[0].events[0].chord), (0, "G"))

    def test_no_arrows_and_roundtrip(self):
        song = song_from_analysis(write_analysis(self.root / "s"), arrows=False)
        self.assertTrue(all(e.strum is None for m in song.measures() for e in m.events))
        again = parse(serialize(song))
        self.assertEqual(again.problems, [])
        self.assertEqual([m.events for m in again.measures()], [m.events for m in song.measures()])

    def test_library_reads_folders(self):
        write_analysis(self.root / "songs" / "abcdefghijk")
        (self.root / "src" / "guitartab").mkdir(parents=True)
        tr = Transcriber(self.root)
        self.assertEqual([e["slug"] for e in tr.library()], ["abcdefghijk"])
        self.assertEqual(tr.slug_for("abcdefghijk"), "abcdefghijk")
        self.assertFalse(tr.available())                      # no .venv here

    def test_bar_times_timeline_and_roundtrip(self):
        song = song_from_analysis(write_analysis(self.root / "s"))
        self.assertEqual([m.at for m in song.measures()], [0.5, 2.5, 4.5])
        self.assertEqual(video_id(song), "abcdefghijk")
        tl = timeline(song)
        self.assertEqual(tl.times[:3], [0.5, 0.75, 1.0])      # eighths at 2 s per bar
        self.assertEqual(tl.slots[tl.index_at(2.6)], (1, 0))
        self.assertEqual(tl.time_of(1, 1), 3.0)
        again = parse(serialize(song))
        self.assertEqual([m.at for m in again.measures()], [0.5, 2.5, 4.5])

    def test_timeline_interpolates_measures_without_time(self):
        song = parse("| ~10 :1 0.1 | :1 0.1 | ~14 :1 0.1 | :1 0.1 |")
        tl = timeline(song)
        self.assertEqual(tl.times, [10.0, 12.0, 14.0, 16.0])
        self.assertIsNone(timeline(parse("| :1 0.1 |")))

    def test_copied_measures_lose_their_audio_time(self):
        from tabwriter.editing import TabEditor
        ed = TabEditor(parse("| ~10 :1 0.1 | ~12 :1 0.2 |"))
        ed.duplicate_measure(0, 1)
        self.assertEqual([m.at for m in ed.song.measures()], [10.0, 12.0, None, None])

    def test_split_title(self):
        self.assertEqual(split_title("Oasis - Wonderwall (Official Video)"), ("Wonderwall", "Oasis"))
        self.assertEqual(split_title("Vance Joy - 'Riptide' Official Video"), ("Riptide", "Vance Joy"))
        self.assertEqual(split_title("Devil Town", "Cavetown - Topic"), ("Devil Town", "Cavetown"))
        self.assertEqual(split_title("Remix Song (Mixtape)", ""), ("Remix Song (Mixtape)", ""))


if __name__ == "__main__":
    unittest.main()
