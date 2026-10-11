import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import cutscenes, db, evolve, pipeline, writer  # noqa: E402
from app.llm import parse_json  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_a_note_after_the_answer_is_ignored(self):
        self.assertEqual(parse_json('[{"i": 1}]\n\nNote: I kept these short.'), [{"i": 1}])
        self.assertEqual(parse_json('{"a": 1}\n{"b": 2}'), {"a": 1})                       # two values: the first is the answer

    def test_text_before_and_a_code_fence_are_ignored(self):
        self.assertEqual(parse_json('Here you go:\n```json\n[{"i": 2}]\n```\nHope that helps'), [{"i": 2}])
        self.assertEqual(parse_json('Sure.\n{"line": "Easy now."}'), {"line": "Easy now."})

    def test_the_old_behaviours_still_work(self):
        self.assertEqual(parse_json('[{"line": "it\\\'s fine"}]')[0]["line"], "it\\'s fine")      # an invalid escape is tolerated
        self.assertEqual(parse_json('```json\n[1, 2]\n```'), [1, 2])
        self.assertEqual(parse_json('  {"x": "a\\nb"}  '), {"x": "a\nb"})
        self.assertEqual(parse_json("[]"), [])

    def test_no_json_at_all_is_still_an_error_not_a_guess(self):
        for bad in ("no json here", "", "Sorry, I cannot help with that."):
            with self.assertRaises(ValueError):
                parse_json(bad)

    def test_a_broken_value_after_text_is_still_an_error(self):
        with self.assertRaises(ValueError):
            parse_json('Here: [{"i": 1, ')


class WriterRetryTests(unittest.TestCase):
    OPP = {"id": "b1", "t": 20.0, "beat_t": None, "salience": 6.0, "kinds": ["pickup"], "kind": "full", "why": "x", "max_words": 12, "max_duration": 5.0}

    def ask(self):
        return writer._ask("sys", [dict(self.OPP)], [], [], [], [], [], 0, 60)

    def test_an_unreadable_answer_is_asked_for_again_once(self):
        good = json.dumps([{"opportunity": "b1", "persona": "character", "tone": "funny", "emotion": "calm", "line": "Easy now."}])
        with mock.patch("app.writer.claude", side_effect=["Sorry, here is no JSON", good]) as c:
            out = self.ask()
        self.assertEqual(c.call_count, 2)
        self.assertEqual(out[0]["line"], "Easy now.")

    def test_a_note_after_the_answer_does_not_even_need_a_second_try(self):
        good = json.dumps([{"opportunity": "b1", "persona": "character", "tone": "funny", "emotion": "calm", "line": "Easy now."}])
        with mock.patch("app.writer.claude", return_value=good + "\n\nI kept it short.") as c:
            out = self.ask()
        self.assertEqual(c.call_count, 1)
        self.assertEqual(len(out), 1)

    def test_two_unreadable_answers_in_a_row_are_a_real_failure(self):
        with mock.patch("app.writer.claude", return_value="no json") as c, self.assertRaises(ValueError):
            self.ask()
        self.assertEqual(c.call_count, 2)


class EditorPassTests(unittest.TestCase):
    LINES = lambda self: [{"start": 5.0, "line": "Easy now.", "persona": "character", "emotion": "calm", "max_duration": 4.0, "silent": False},
                          {"start": 40.0, "line": "Quiet road.", "persona": "character", "emotion": "calm", "max_duration": 4.0, "silent": False}]

    def test_malformed_scores_and_rewrites_never_crash_the_editor_pass(self):
        with mock.patch("app.evolve.claude", side_effect=['[{"overall": 3}, "junk", {"i": "x"}, {"i": 1, "overall": 3, "fix": "tighter"}]',
                                                           '[{"line": "no index"}, {"i": 9, "line": "out of range"}, "junk", {"i": 1, "line": "Tighter road."}]']):
            out = evolve.refine(self.LINES(), [], [], "sys")
        self.assertEqual([l["line"] for l in out], ["Easy now.", "Tighter road."])

    def test_a_rewrite_that_is_not_a_list_is_ignored(self):
        with mock.patch("app.evolve.claude", side_effect=['[{"i": 0, "overall": 3, "fix": "x"}]', '{"i": 0, "line": "Changed."}']):
            out = evolve.refine(self.LINES(), [], [], "sys")
        self.assertEqual(out[0]["line"], "Easy now.")


class VideoSurvivesTests(unittest.TestCase):
    def test_a_judge_answer_nobody_can_read_does_not_fail_the_video(self):
        """Video 7's regenerate failed with 'JSONDecodeError: Extra data' from the editor pass alone."""
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"rj{time.time_ns()}", "x.mp4", "queued", "western", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps([{"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel"} for t in range(0, 120, 3)]))
        (work / "transcript.json").write_text("[]")
        line = {"start": 30.0, "line": "Easy now.", "persona": "character", "emotion": "calm", "max_duration": 4, "opp_id": "b1",
                "beat_kinds": "hunting", "salience": 6, "silent": False}
        with mock.patch("app.pipeline.scenes.video_length", return_value=120.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.writer.write_lines", return_value=([dict(line)], [])), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=ValueError("Extra data: line 5 column 1")), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop at the voices")):
            pipeline.regenerate(vid)
        row = db.one("SELECT status, error FROM videos WHERE id=?", (vid,))
        self.assertEqual(row["status"], "failed")
        self.assertIn("stop at the voices", row["error"])          # it got as far as the voices: the editor pass did not stop it
        self.assertIn("Easy now.", [r["line"] for r in db.rows("SELECT line FROM lines WHERE video_id=?", (vid,))])


if __name__ == "__main__":
    unittest.main()


class PolishNeverCostsALineTests(unittest.TestCase):
    """Video 8 planned 27 thoughts and ended with 13: the editor pass rewrote the 'weak' ones, the rewrites no longer fit their
    slots, and the guard silently dropped them."""

    def lines(self, n=6, kinds="new_place"):
        return [{"start": 10.0 + 8 * i, "line": f"Short thought number {i}.", "persona": "character", "emotion": "calm", "max_words": 6,
                 "max_duration": 3.5, "opp_id": f"b{i}", "beat_kinds": kinds, "silent": False} for i in range(n)]

    def judge(self, n, score=3):
        return json.dumps([{"i": i, "overall": score, "fix": "make it richer"} for i in range(n)])

    def test_rewrites_that_do_not_fit_are_ignored_and_no_line_is_lost(self):
        too_long = json.dumps([{"i": i, "line": "A much longer and richer rewrite of the thought that cannot possibly fit the slot."} for i in range(6)])
        with mock.patch("app.evolve.claude", side_effect=[self.judge(6), too_long]):
            out = evolve.refine(self.lines(), [], [], "sys")
        self.assertEqual(len(out), 6)
        self.assertEqual([l["line"] for l in out], [f"Short thought number {i}." for i in range(6)])

    def test_rewrites_that_fit_are_used(self):
        short = json.dumps([{"i": i, "line": f"Better {i}."} for i in range(6)])
        with mock.patch("app.evolve.claude", side_effect=[self.judge(6), short]):
            out = evolve.refine(self.lines(), [], [], "sys")
        self.assertEqual([l["line"] for l in out], [f"Better {i}." for i in range(6)])

    def test_a_mix_keeps_every_line(self):
        mix = json.dumps([{"i": 0, "line": "Better zero."}, {"i": 1, "line": "x " * 30}, {"i": 2, "line": "Better two."}])
        with mock.patch("app.evolve.claude", side_effect=[self.judge(6), mix]):
            out = evolve.refine(self.lines(), [], [], "sys")
        self.assertEqual(len(out), 6)
        self.assertEqual(out[0]["line"], "Better zero.")
        self.assertEqual(out[1]["line"], "Short thought number 1.")

    def test_inner_story_thoughts_are_never_rewritten_even_when_the_judge_calls_them_weak(self):
        with mock.patch("app.evolve.claude", side_effect=[self.judge(6)]) as c:
            out = evolve.refine(self.lines(kinds="daydream,travel_story"), [], [], "sys")
        self.assertEqual(c.call_count, 1)                                                   # only the judge was asked
        self.assertEqual(len(out), 6)
        self.assertEqual(out[0]["line"], "Short thought number 0.")

    def test_a_line_the_polish_pushed_into_trouble_comes_back_as_written(self):
        """A rewrite that fits its own slot but is still dropped later must not take the line with it."""
        ok = json.dumps([{"i": 1, "line": "Better one."}])
        real = evolve.guard.enforce
        calls = []

        def picky(lines, *a, **k):
            calls.append(len(lines))
            if len(calls) == 1:
                kept, dropped = real([l for l in lines if l["line"] != "Better one."], *a, **k)
                return kept, [{**l, "reason": "too close to previous line"} for l in lines if l["line"] == "Better one."]
            return real(lines, *a, **k)
        with mock.patch("app.evolve.claude", side_effect=[self.judge(6, 7).replace("7", "3", 1), ok]), mock.patch("app.evolve.guard.enforce", side_effect=picky):
            out = evolve.refine(self.lines(), [], [], "sys")
        self.assertEqual(len(out), 6)
        self.assertEqual(out[1]["line"], "Short thought number 1.")
