import unittest
from fractions import Fraction
from pathlib import Path

from tabwriter import Song, parse
from tabwriter.layout import layout_song
from tabwriter.editing import CHORD_SHAPES, Cursor, TabEditor
from tabwriter.serialize import serialize

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def measures(ed):
    return list(ed.song.measures())


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.ed = TabEditor(Song(grid=4))      # quarter-note slots keep these tests short

    def test_new_song_has_one_blank_measure(self):
        ms = measures(self.ed)
        self.assertEqual(len(ms), 1)
        self.assertEqual(len(ms[0].events), 4)
        self.assertTrue(all(e.rest for e in ms[0].events))
        self.assertEqual(self.ed.song.problems, [])

    def test_typing_frets_and_two_digit_frets(self):
        ed = self.ed
        ed.set_cursor(0, 0, 5)
        ed.set_fret(3)
        self.assertEqual(ed.note().fret, 3)
        ed.set_cursor(0, 1, 2)
        ed.set_fret(1)
        ed.set_fret(2)              # typed quickly -> 12
        self.assertEqual(ed.note().fret, 12)
        ed.mute()
        self.assertIsNone(ed.note().fret)

    def test_shorter_duration_fills_with_rest_and_longer_eats_rests(self):
        ed = self.ed
        ed.set_cursor(0, 0, 5)
        ed.set_fret(0)
        ed.set_duration(base=8)
        evs = measures(ed)[0].events
        self.assertEqual([e.base for e in evs], [8, 8, 4, 4, 4])
        self.assertEqual(measures(ed)[0].actual, Fraction(4))
        ed.set_duration(base=2)
        self.assertEqual([e.base for e in measures(ed)[0].events], [2, 4, 4])
        self.assertEqual(measures(ed)[0].actual, Fraction(4))

    def test_delete_note_then_beat(self):
        ed = self.ed
        ed.set_cursor(0, 1, 3)
        ed.set_fret(2)
        ed.delete()
        self.assertTrue(measures(ed)[0].events[1].rest)
        ed.delete()                 # empty beat -> removed
        self.assertEqual(len(measures(ed)[0].events), 3)

    def test_undo_redo(self):
        ed = self.ed
        ed.set_cursor(0, 0, 1)
        ed.set_fret(5)
        ed.toggle_strum("down")
        ed.undo()
        self.assertIsNone(ed.event().strum)
        ed.undo()
        self.assertTrue(ed.event().rest)
        ed.redo()
        self.assertEqual(ed.note().fret, 5)

    def test_chord_shape_labels_only_on_change(self):
        ed = self.ed
        ed.set_cursor(0, 0, 1)
        ed.apply_shape("G", CHORD_SHAPES["G"])
        ed.repeat_beat()
        ed.set_cursor(0, 2, 1)
        ed.apply_shape("G", CHORD_SHAPES["G"])
        ed.set_cursor(0, 3, 1)
        ed.apply_shape("C", CHORD_SHAPES["C"])
        evs = measures(ed)[0].events
        self.assertEqual([e.chord for e in evs], ["G", None, None, "C"])
        self.assertEqual(len(evs[0].notes), 6)
        self.assertEqual(evs[1].notes, evs[0].notes)

    def test_move_note_between_strings_and_beats(self):
        ed = self.ed
        ed.set_cursor(0, 0, 5)
        ed.set_fret(3)
        ed.move_note(Cursor(0, 0, 5), Cursor(0, 2, 4))
        evs = measures(ed)[0].events
        self.assertTrue(evs[0].rest)
        self.assertEqual([(n.string, n.fret) for n in evs[2].notes], [(4, 3)])
        ed.move_note(Cursor(0, 2, 4), Cursor(0, 3, 4), copy_note=True)
        self.assertEqual(len(evs[2].notes), 1)
        ed.move_note_to_string(1)

    def test_measures_sections_time_and_breaks(self):
        ed = self.ed
        ed.add_measure()
        ed.add_measure()
        self.assertEqual(len(measures(ed)), 3)
        ed.set_time(3, 4)            # from measure 3 on
        self.assertEqual([m.time for m in measures(ed)], [(4, 4), (4, 4), (3, 4)])
        self.assertEqual(len(measures(ed)[2].events), 3)
        ed.set_cursor(0, 0, 1)
        ed.toggle_line_break()
        self.assertEqual([len(r) for r in ed.song.sections[0].rows], [1, 2])
        ed.set_cursor(1, 0, 1)
        ed.split_section("Verse")
        self.assertEqual([s.title for s in ed.song.sections], ["Intro", "Verse"])
        ed.copy_measure()
        ed.paste_measure()
        ed.delete_measure()
        self.assertEqual(len(measures(ed)), 3)
        self.assertEqual([m.number for m in measures(ed)], [1, 2, 3])

    def test_moving_past_the_end_adds_a_measure(self):
        ed = self.ed
        ed.set_cursor(0, 3, 1)
        ed.move_beat(1)              # last measure is all rests: stay
        self.assertEqual(len(measures(ed)), 1)
        ed.set_fret(0)
        ed.move_beat(1)
        self.assertEqual(len(measures(ed)), 2)
        self.assertEqual((ed.cursor.m, ed.cursor.e), (1, 0))


class MeasureRangeTests(unittest.TestCase):
    def setUp(self):
        self.ed = TabEditor(Song(grid=4))
        for _ in range(4):
            self.ed.add_measure()
        for m in range(5):                         # mark each measure with its own fret
            self.ed.set_cursor(m, 0, 1)
            self.ed.set_fret_value(m)

    def frets(self):
        return [m.events[0].notes[0].fret if m.events[0].notes else None for m in measures(self.ed)]

    def test_delete_range_and_undo(self):
        self.ed.delete_measure(1, 3)
        self.assertEqual(self.frets(), [0, 4])
        self.assertEqual([m.number for m in measures(self.ed)], [1, 2])
        self.ed.undo()
        self.assertEqual(self.frets(), [0, 1, 2, 3, 4])

    def test_delete_everything_keeps_one_empty_measure(self):
        self.ed.delete_measure(0, 4)
        self.assertEqual(len(measures(self.ed)), 1)
        self.assertTrue(all(e.rest for e in measures(self.ed)[0].events))

    def test_copy_paste_duplicate_clear_ranges(self):
        self.assertEqual(self.ed.copy_measure(0, 1), 2)
        self.ed.set_cursor(4, 0, 1)
        self.ed.paste_measure()
        self.assertEqual(self.frets(), [0, 1, 2, 3, 4, 0, 1])
        self.ed.duplicate_measure(2, 3)
        self.assertEqual(self.frets(), [0, 1, 2, 3, 2, 3, 4, 0, 1])
        self.ed.clear_measures(0, 1)
        self.assertEqual(self.frets()[:3], [None, None, 2])


class GridAndLayoutTests(unittest.TestCase):
    def test_defaults_eighth_grid_and_two_per_line(self):
        ed = TabEditor()
        self.assertEqual(ed.song.grid, 8)
        self.assertEqual([e.base for e in measures(ed)[0].events], [8] * 8)
        for _ in range(4):
            ed.add_measure()
        self.assertEqual(len(ed.song.sections[0].rows), 1)          # one row in the model…
        systems = layout_song(ed.song)[0].systems
        self.assertEqual([s.last - s.first + 1 for s in systems], [2, 2, 1])   # …wrapped 2 per line

    def test_per_line_zero_fits_by_width(self):
        ed = TabEditor(Song(grid=4, per_line=0))
        for _ in range(2):                  # three short measures fit on one line
            ed.add_measure()
        self.assertEqual(len(layout_song(ed.song)[0].systems), 1)

    def test_set_grid_redivides_rests_only(self):
        ed = TabEditor(Song(grid=4))
        ed.set_cursor(0, 1, 3)
        ed.set_fret(2)                      # beat 2 holds a note
        ed.set_grid(8)
        evs = measures(ed)[0].events
        self.assertEqual([(e.base, e.rest) for e in evs],
                         [(8, True), (8, True), (4, False), (8, True), (8, True), (8, True), (8, True)])
        ed.set_grid(2)                      # coarser: the rests after beat 2 merge on the half-note grid
        evs = measures(ed)[0].events
        self.assertEqual([(e.base, e.rest) for e in evs], [(4, True), (4, False), (2, True)])
        ed.add_measure()
        self.assertEqual([e.base for e in measures(ed)[1].events], [2, 2])

    def test_grid_and_per_line_saved(self):
        text = serialize(TabEditor(Song(grid=4, per_line=3)).song)
        self.assertIn("grid: 4", text)
        self.assertIn("per_line: 3", text)
        song = parse(text)
        self.assertEqual((song.grid, song.per_line), (4, 3))


class SerializeTests(unittest.TestCase):
    def roundtrip(self, song):
        text = serialize(song)
        again = parse(text)
        self.assertEqual(again.problems, [], text)
        self.assertEqual([(m.time, m.events) for m in song.measures()],
                         [(m.time, m.events) for m in again.measures()])
        self.assertEqual([(s.title, [len(r) for r in s.rows]) for s in song.sections],
                         [(s.title, [len(r) for r in s.rows]) for s in again.sections])
        return text

    def test_examples_roundtrip(self):
        for path in EXAMPLES.glob("*.tab"):
            with self.subTest(path.name):
                self.roundtrip(parse(path.read_text(encoding="utf-8")))

    def test_edited_song_roundtrip(self):
        ed = TabEditor()
        ed.set_cursor(0, 0, 1)
        ed.apply_shape("Am", CHORD_SHAPES["Am"])
        ed.set_duration(base=8, dotted=True)
        ed.toggle_strum("up")
        ed.add_measure()
        ed.set_time(6, 8)
        ed.duplicate_measure()
        ed.set_field("capo", 2)
        text = self.roundtrip(ed.song)
        self.assertIn("capo: 2", text)
        self.assertIn("@6/8", text)
        self.assertIn("x02210^", text)
        self.assertIn("%", text)


if __name__ == "__main__":
    unittest.main()
