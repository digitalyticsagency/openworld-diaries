import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import guard, transcript  # noqa: E402
from app.evolve import combined  # noqa: E402

SEGS = [{"start": 20, "end": 25, "speaker": "player", "text": "hello"}]
WIN = guard.speech_windows(SEGS)  # (18.5, 26.5)
SCENES = [{"t": 40, "interactions": [{"kind": "damage_taken"}]}, {"t": 60, "interactions": []}]


def L(start, text="A short quiet thought.", **kw):
    return {"start": start, "max_duration": 10, "persona": "player", "emotion": "calm",
            "trigger_t": None, "line": text, **kw}


class GuardTests(unittest.TestCase):
    def test_window_has_buffer_and_merges(self):
        w = guard.speech_windows([{"start": 10, "end": 12}, {"start": 13, "end": 15}])
        self.assertEqual(w, [(8.5, 16.5)])

    def test_line_inside_speech_dropped(self):
        kept, dropped = guard.enforce([L(21)], WIN, SCENES)
        self.assertEqual(kept, [])
        self.assertEqual(dropped[0]["reason"], "overlaps speech")

    def test_line_running_into_speech_dropped(self):
        kept, dropped = guard.enforce([L(16, "This line is long enough to run into the player talking soon")], WIN, SCENES)
        self.assertEqual(kept, [])

    def test_line_after_speech_kept(self):
        kept, _ = guard.enforce([L(30)], WIN, SCENES)
        self.assertEqual(len(kept), 1)

    def test_max_duration_clamped_to_next_speech(self):
        kept, _ = guard.enforce([L(10, "Short.")], WIN, SCENES)
        self.assertLessEqual(kept[0]["max_duration"], 18.5 - 10 - 0.3 + 1e-6)

    def test_min_gap(self):
        kept, dropped = guard.enforce([L(30), L(33)], WIN, SCENES)
        self.assertEqual(len(kept), 1)
        self.assertEqual(dropped[0]["reason"], "too close to previous line")

    def test_too_long(self):
        kept, dropped = guard.enforce([L(30, " ".join(["word"] * 30))], WIN, SCENES)
        self.assertEqual(dropped[0]["reason"], "too long")

    def test_pain_needs_event(self):
        ungrounded = L(50, emotion="pain", trigger_t=60)
        grounded = L(50, emotion="pain", trigger_t=40)
        self.assertEqual(guard.enforce([ungrounded], WIN, SCENES)[0], [])
        self.assertEqual(len(guard.enforce([grounded], WIN, SCENES)[0]), 1)

    def test_pain_without_trigger_dropped(self):
        self.assertEqual(guard.enforce([L(50, emotion="joy")], WIN, SCENES)[0], [])

    def test_calm_needs_no_event(self):
        self.assertEqual(len(guard.enforce([L(50, emotion="awe")], WIN, SCENES)[0]), 1)

    def test_clip_fits(self):
        self.assertFalse(guard.clip_fits(15, 5, WIN))
        self.assertTrue(guard.clip_fits(27, 5, WIN))


class OtherTests(unittest.TestCase):
    def test_parse_srt(self):
        srt = "1\n00:00:01,000 --> 00:00:03,500\nHey there\n\n2\n00:01:00,000 --> 00:01:02,000\n<i>Whoa</i>\n"
        segs = transcript.parse_subtitles(srt)
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in segs],
                         [(1.0, 3.5, "Hey there"), (60.0, 62.0, "Whoa")])

    def test_parse_vtt(self):
        vtt = "WEBVTT\n\n00:02.000 --> 00:04.000\nHi\n"
        self.assertEqual(transcript.parse_subtitles(vtt)[0]["start"], 2.0)

    def test_combined_weights(self):
        self.assertEqual(combined(human=10, yt=None, self_=0), round((0.5 * 10 + 0.2 * 0) / 0.7, 3))
        self.assertIsNone(combined())
        self.assertEqual(combined(self_=7), 7)


if __name__ == "__main__":
    unittest.main()
