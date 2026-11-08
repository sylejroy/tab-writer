import unittest
from fractions import Fraction
from pathlib import Path

from tabwriter import parse, render_html

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class ParserTests(unittest.TestCase):
    def test_settings(self):
        song = parse("title: T\nartist: A\ncapo: fret 5\ntime: 6/8\ntempo: 90\nkey: G\n| :8 0.5 0.5 0.5 0.5 0.5 0.5 |")
        self.assertEqual((song.title, song.artist, song.capo, song.tempo), ("T", "A", 5, 90))
        self.assertEqual(song.meta, {"Key": "G"})
        m = next(song.measures())
        self.assertEqual(m.time, (6, 8))
        self.assertTrue(m.show_time)
        self.assertEqual(song.problems, [])

    def test_notes_chords_strums(self):
        song = parse("| [Am] :4 0.5^ :8 (2.3 2.4 x.5)^ 1.2v :4. x02210v |")
        ev = next(song.measures()).events
        self.assertEqual(ev[0].chord, "Am")
        self.assertEqual(ev[0].strum, "up")
        self.assertEqual([(n.string, n.fret) for n in ev[1].notes], [(3, 2), (4, 2), (5, None)])
        self.assertEqual(ev[2].strum, "down")
        self.assertEqual(ev[2].duration, Fraction(1, 2))
        self.assertEqual(ev[3].duration, Fraction(3, 2))
        # shape x02210 is low E -> high e
        self.assertEqual([(n.string, n.fret) for n in ev[3].notes],
                         [(1, 0), (2, 1), (3, 2), (4, 2), (5, 0), (6, None)])

    def test_duration_is_sticky_across_measures(self):
        song = parse("| :8 0.1 0.1 0.1 0.1 0.1 0.1 0.1 0.1 | 0.1 0.1 0.1 0.1 0.1 0.1 0.1 0.1 |")
        self.assertEqual(song.problems, [])

    def test_macros_and_repeat(self):
        song = parse("def p = [G] :2 3.6 3.6\n| $p | % |")
        measures = list(song.measures())
        self.assertEqual(len(measures), 2)
        self.assertEqual(measures[1].events[0].chord, "G")
        self.assertEqual([m.number for m in measures], [1, 2])

    def test_time_change_shows_signature(self):
        song = parse("| :1 0.1 |\ntime: 3/4\n| :2. 0.1 | % |")
        self.assertEqual([m.show_time for m in song.measures()], [True, True, False])

    def test_triplets(self):
        song = parse("| :8t 0.1 0.1 0.1 0.1 0.1 0.1 :2 0.1 |")
        self.assertEqual(song.problems, [])

    def test_errors_and_warnings(self):
        song = parse("| 3 9.7 :5 $nope [C] |\n| :4 0.1 |")
        messages = " ".join(p.message for p in song.problems)
        self.assertIn("Unrecognized token '3'", messages)
        self.assertIn("string 7 does not exist", messages)
        self.assertIn("Bad duration", messages)
        self.assertIn("Unknown macro $nope", messages)
        self.assertIn("has 1 beats, expected 4", messages)
        self.assertTrue(all(p.line in (1, 2) for p in song.problems))

    def test_custom_tuning_sets_string_count(self):
        song = parse("tuning: E A D G\n| :1 (0.4 2.1) |")
        self.assertEqual(song.string_count, 4)
        self.assertEqual(song.problems, [])
        self.assertTrue(parse("tuning: EADG\n| 0.5 |").errors)


class RenderTests(unittest.TestCase):
    def test_examples_render_without_problems(self):
        for path in EXAMPLES.glob("*.tab"):
            with self.subTest(path.name):
                song = parse(path.read_text(encoding="utf-8"))
                self.assertEqual(song.problems, [])
                html = render_html(song)
                self.assertIn("<svg", html)
                self.assertIn("section-title", html)

    def test_intro_contents(self):
        html = render_html(parse((EXAMPLES / "intro.tab").read_text(encoding="utf-8")))
        self.assertIn("Capo fret 5", html)
        self.assertIn("Csus2/B", html)
        self.assertEqual(html.count('class="system"'), 4)      # four lines of tab
        self.assertIn("size: A4", html)

    def test_escapes_user_text(self):
        html = render_html(parse("title: <b>x</b>\n| [A<i>] :1 0.5 |"))
        self.assertNotIn("<b>x</b>", html)
        self.assertNotIn("A<i>", html)

    def test_empty_song(self):
        self.assertIn("No measures yet", render_html(parse("")))


if __name__ == "__main__":
    unittest.main()
