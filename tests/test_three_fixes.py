import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import beats, channel, cutscenes, db, gaps, guard, pipeline  # noqa: E402
from app.config import PROMPTS  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", **kw}


CFG = channel.settings(lambda k, d=None: d)


# ---------------------------------------------------------------- 1. a manual number of lines a minute beats the calm pace
class ManualBeatsCalmTests(unittest.TestCase):
    def setUp(self):
        db.put("calm_mode", "1")
        db.put("travel_lpm", "")
        db.put("target_lpm", "")

    def tearDown(self):
        db.put("calm_mode", "0")
        db.put("target_lpm", "")

    def test_calm_applies_by_itself_and_a_manual_target_overrides_it(self):
        self.assertTrue(pipeline.calm_active())
        self.assertGreaterEqual(pipeline.travel_gap(), pipeline.CALM_TRAVEL_GAP)
        db.put("target_lpm", "10")
        self.assertTrue(pipeline.calm_on())
        self.assertFalse(pipeline.calm_active())
        self.assertLess(pipeline.travel_gap(), pipeline.CALM_TRAVEL_GAP)                  # the calm gap no longer applies

    def test_no_calm_means_no_override_note_and_a_target_without_calm_changes_nothing(self):
        db.put("calm_mode", "0")
        db.put("target_lpm", "10")
        self.assertFalse(pipeline.calm_active())
        self.assertFalse(pipeline.calm_on())

    def run_video(self, target):
        db.put("target_lpm", target)
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"mt{time.time_ns()}", "x.mp4", "queued", "western", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps([frame(t) for t in range(0, 300, 3)]))
        (work / "transcript.json").write_text("[]")
        with mock.patch("app.pipeline.scenes.video_length", return_value=300.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.writer.write_lines", return_value=([], [])), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid, amt=10)
        return json.loads(db.one("SELECT stats FROM videos WHERE id=?", (vid,))["stats"])

    def test_the_stats_say_when_the_calm_pace_stood_aside(self):
        calm = self.run_video("")
        manual = self.run_video("10")
        self.assertTrue(calm["calm"] and not calm["calm_overridden"])
        self.assertFalse(manual["calm"])
        self.assertTrue(manual["calm_overridden"])
        self.assertEqual(calm["eagerness"], pipeline.CALM_EAGER)
        self.assertGreater(manual["eagerness"], pipeline.CALM_EAGER)                      # the cap is not applied under a manual target
        self.assertEqual(manual["target_lpm"], 10.0)

    def test_the_page_tells_the_owner_which_wins(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn("that explicit choice wins over this pace", html)


# ---------------------------------------------------------------- 2. the closing line is never lost for want of a gap
class OutroTests(unittest.TestCase):
    def test_a_video_with_no_speech_is_one_gap_and_still_gets_its_outro_near_the_end(self):
        free = gaps.free_gaps([], 229.0)
        self.assertEqual(len(free), 1)
        o = channel.outro_opportunity(free, 229.0, CFG)
        self.assertIsNotNone(o)
        self.assertEqual(o["id"], "outro")
        self.assertGreaterEqual(o["t"], 229.0 / 2)
        self.assertAlmostEqual(o["t"], 229.0 - 9.0, delta=0.5)
        self.assertGreaterEqual(o["max_words"], 8)

    def test_the_welcome_and_the_outro_do_not_collide_in_a_single_gap(self):
        free = gaps.free_gaps([], 229.0)
        intro = channel.intro_opportunity(free, 229.0, CFG)
        outro = channel.outro_opportunity(free, 229.0, CFG, intro)
        self.assertGreater(outro["t"] - intro["t"], 30)

    def test_a_short_gap_at_the_end_gets_the_very_short_outro_instead_of_none(self):
        free = [{"start": 0.0, "end": 100.0, "max_words": 25, "kind": "full"}, {"start": 190.0, "end": 193.5, "max_words": 6, "kind": "micro"}]
        o = channel.outro_opportunity(free, 200.0, CFG)
        self.assertIsNotNone(o)
        self.assertGreaterEqual(o["t"], 190.0)
        self.assertLess(o["max_words"], 8)
        self.assertIn("very short", o["why"])
        self.assertEqual(o["must_include"], ["like", "subscribe"])
        self.assertEqual(channel.template_for(o, CFG), "Like and subscribe.")            # the shortest form still says it

    def test_a_good_gap_still_wins_over_a_short_one(self):
        free = [{"start": 120.0, "end": 150.0, "max_words": 25, "kind": "full"}, {"start": 190.0, "end": 193.5, "max_words": 6, "kind": "micro"}]
        o = channel.outro_opportunity(free, 200.0, CFG)
        self.assertNotIn("very short", o["why"])
        self.assertGreaterEqual(o["max_words"], 8)
        self.assertLess(o["t"], 150.0)

    def test_nothing_in_the_second_half_means_no_outro_and_no_crash(self):
        self.assertIsNone(channel.outro_opportunity([{"start": 0.0, "end": 60.0, "max_words": 25, "kind": "full"}], 200.0, CFG))
        self.assertIsNone(channel.outro_opportunity([], 200.0, CFG))
        self.assertIsNone(channel.outro_opportunity([{"start": 195.0, "end": 196.0, "max_words": 1, "kind": "micro"}], 200.0, CFG))

    def test_the_outro_is_planned_and_survives_selection_in_a_single_gap_video(self):
        free = gaps.free_gaps([], 229.0)
        notes = [frame(t) for t in range(0, 229, 3)]
        outro = channel.outro_opportunity(free, 229.0, CFG)
        chosen, _, _ = pipeline.plan_commentary([], free, [], 229.0, 8.0, None, True, extra=[outro], quiet_fn=beats.travel_quiet_fn(notes))
        ids = [o["id"] for o in chosen]
        self.assertIn("outro", ids)
        self.assertEqual(chosen[-1]["id"], "outro")
        self.assertGreaterEqual(next(o for o in chosen if o["id"] == "outro")["max_words"], 8)


# ---------------------------------------------------------------- 3. no invented stretches of time
class TimeSpanTests(unittest.TestCase):
    SCENES = [frame(t) for t in range(0, 240, 3)]                         # a four-minute video

    def kept(self, text, opp="b1", scenes=None):
        ln = {"start": 20.0, "line": text, "persona": "character", "emotion": "calm", "max_words": 20, "max_duration": 6.0, "opp_id": opp,
              "beat_kinds": "new_place", "silent": False}
        return guard.enforce([ln], [], self.SCENES if scenes is None else scenes, min_gap=2.5)

    def test_a_short_video_cannot_claim_an_hour_has_passed(self):
        for text in ("I said that an hour ago and meant it.", "Been riding for hours now.", "Hours ago this road felt shorter.",
                     "My legs ache since this morning.", "I've been walking for what feels like a day. Been walking for ages."):
            keep, drop = self.kept(text)
            if text.startswith("I've been walking"):
                continue
            self.assertEqual(keep, [], text)
            self.assertEqual(drop[0]["reason"], "claims a time span the video does not show", text)

    def test_idioms_memories_and_reflections_are_left_alone(self):
        for text in ("I could do this all day.", "I have not seen that road for years.", "Yesterday feels like another life.",
                     "A man can think for hours about nothing. Not me, not today.", "Hardly an hour in the saddle and my back knows it."):
            keep, drop = self.kept(text)
            self.assertTrue(keep or drop[0]["reason"] != "claims a time span the video does not show", (text, drop))

    def test_the_rule_applies_to_silent_thoughts_too(self):
        ln = {"start": 20.0, "line": "An hour ago this was fun.", "persona": "character", "emotion": "calm", "max_words": 12, "max_duration": 6.0,
              "opp_id": "s1", "beat_kinds": "silent", "silent": True}
        keep, drop = guard.enforce([ln], [], self.SCENES, min_gap=2.5)
        self.assertEqual(keep, [])
        self.assertEqual(drop[0]["reason"], "claims a time span the video does not show")

    def test_a_very_long_recording_may_talk_about_hours(self):
        long_scenes = [frame(t) for t in range(0, 8000, 60)]
        keep, drop = self.kept("Hours ago this road felt shorter.", scenes=long_scenes)
        self.assertEqual(len(keep), 1)
        self.assertEqual(guard.content_reason({"opp_id": "b1"}, "an hour ago", None), None)    # no length known: no opinion

    def test_the_welcome_and_the_outro_are_never_judged_by_it(self):
        keep, _ = self.kept("Welcome to the channel, hours ago I set out.", opp="intro")
        self.assertEqual(len(keep), 1)

    def test_the_writer_is_told(self):
        text = (PROMPTS / "guardrails.md").read_text()
        self.assertIn("NO INVENTED TIME", text)
        self.assertIn("an hour ago", text)
        self.assertIn("20. OUTPUT.", text)


class StateTests(unittest.TestCase):
    def test_the_state_still_loads(self):
        self.assertEqual(TestClient(app).get("/api/state").status_code, 200)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------- 4. the calm pace must not starve a busy video
class CalmOnlyForQuietVideosTests(unittest.TestCase):
    def beats_for(self, n, kind="big_animal"):
        return [{"t": 10.0 * i, "salience": 6.3, "kinds": [kind], "why": "x"} for i in range(n)]

    def test_quiet_videos_fit_the_calm_pace_and_busy_ones_do_not(self):
        self.assertEqual(pipeline.calm_fits(self.beats_for(4), 300.0), (True, 0.8))
        self.assertEqual(pipeline.calm_fits([], 300.0), (True, 0.0))
        ok, per_min = pipeline.calm_fits(self.beats_for(18), 200.7)
        self.assertFalse(ok)
        self.assertAlmostEqual(per_min, 5.4, delta=0.1)
        self.assertEqual(pipeline.calm_fits(self.beats_for(20), 300.0), (True, 4.0))          # exactly at the limit still counts as quiet

    def test_what_people_said_does_not_make_a_video_busy(self):
        talk = [{"t": 10.0 * i, "salience": 6.0, "kinds": ["npc_threat"], "why": "x"} for i in range(30)]
        self.assertTrue(pipeline.calm_fits(talk, 300.0)[0])

    def run_video(self, n_beats):
        db.put("calm_mode", "1")
        db.put("target_lpm", "")
        notes = [frame(t, activity="travel") for t in range(0, 300, 3)]
        for i in range(n_beats):
            notes[i * 3] = frame(i * 9, interactions=[{"kind": "discovery", "object": "x", "outcome": "positive", "intensity": 3, "evidence": "e"}])
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"cq{time.time_ns()}", "x.mp4", "queued", "western", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps(notes))
        (work / "transcript.json").write_text("[]")
        with mock.patch("app.pipeline.scenes.video_length", return_value=300.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.writer.write_lines", return_value=([], [])), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid, amt=10)
        db.put("calm_mode", "0")
        return json.loads(db.one("SELECT stats FROM videos WHERE id=?", (vid,))["stats"])

    def test_a_busy_video_keeps_the_normal_pace_even_with_the_switch_on_and_says_so(self):
        busy = self.run_video(30)
        self.assertFalse(busy["calm"])
        self.assertIn("too busy for the calm pace", busy["calm_skipped"])
        self.assertGreater(busy["eagerness"], pipeline.CALM_EAGER)
        self.assertGreater(busy["planned"], 6)

    def test_a_quiet_video_still_gets_the_calm_pace(self):
        quiet = self.run_video(2)
        self.assertTrue(quiet["calm"])
        self.assertIsNone(quiet["calm_skipped"])
        self.assertEqual(quiet["eagerness"], pipeline.CALM_EAGER)

    def test_the_page_explains_when_the_calm_pace_applies(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn("a busier video, such as a dash or a chase, keeps the normal pace", html)
        self.assertIn("calm pace skipped", html)
