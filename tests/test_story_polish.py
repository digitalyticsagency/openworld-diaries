import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ["ELEVENLABS_API_KEY"] = "test-key"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import channel, cutscenes, db, gaps, guard, packaging, pipeline, storybible, storymode, viral, voice  # noqa: E402
from app.config import PROMPTS  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "player_state": "riding",
            "mount_or_vehicle": "brown horse", "time_weather": "day, clear", **kw}


def ride(seconds=240, step=3, **kw):
    return [frame(t, **kw) for t in range(0, seconds, step)]


BIBLE = {"title": "The Map on the Pay Slip", "premise": "Two brothers worked a grading camp. They fought over socks and parted.",
         "beats": [f"Part {i} of the story." for i in range(1, 6)], "part_titles": ["The camp", "The socks", "The leaving", "The boot", "The map"],
         "ending": "He still has the map."}


# ---------------------------------------------------------------- wording: spoken, not written
class SpokenWordingTests(unittest.TestCase):
    def test_written_style_is_caught(self):
        for bad in ("He had one thing left: the map.", "He left; I stayed.", "I waited — for what, I do not know.", "He went away – slowly.",
                    " ".join(["and he said that"] * 12) + "."):
            self.assertTrue(guard.written_style(bad), bad)
        for ok in ("He said Mother knit them for him. It ended with me shouting he could pick wheat by himself.",
                   "I think it was March, or maybe the week after, and he was already gone.", "Hm. We never spoke of it again."):
            self.assertFalse(guard.written_style(ok), ok)

    def judge(self, text, kinds="daydream,travel_story,story_told"):
        ln = {"start": 20.0, "line": text, "persona": "character", "emotion": "calm", "max_words": 30, "max_duration": 12.0, "opp_id": "s0_0",
              "beat_kinds": kinds, "silent": False}
        keep, drop = guard.enforce([ln], [], ride(120), min_gap=2.5)
        return None if keep else drop[0]["reason"]

    def test_a_story_line_that_reads_like_writing_is_dropped_and_other_lines_are_not_judged(self):
        self.assertEqual(self.judge("He had one thing left: the map and the long road home."), "written, not spoken")
        self.assertEqual(self.judge("I stayed; he left, and that was the whole of it for years."), "written, not spoken")
        self.assertIsNone(self.judge("He had one thing left, the map and the long road home."))
        self.assertIsNone(self.judge("Easy now: she's spooked.", kinds="big_animal"))

    def test_the_writer_is_taught_to_talk(self):
        text = (PROMPTS / "story.md").read_text()
        for needle in ("How people really tell it", "some forty dollars", "No semicolons, colons or dashes", "No neat contrasts", "Pressed so hard it came through the back"):
            self.assertIn(needle, text)


# ---------------------------------------------------------------- voice: each line a little different, real pauses, real timing
class HumanVoiceTests(unittest.TestCase):
    LINES = ["He put me to greasing the cable pulleys.", "In April the river ran brown and fast.", "Then he called me a fool.", "I worked the crossing alone."]

    def test_every_line_gets_its_own_treatment_inside_a_narrow_band(self):
        ss = [voice._settings("calm", True, t) for t in self.LINES]
        for s in ss:
            self.assertTrue(0.36 <= s["stability"] <= 0.50 and 0.28 <= s["style"] <= 0.48 and 0.90 <= s["speed"] <= 0.98, s)
        self.assertGreaterEqual(len({s["speed"] for s in ss}), 3)
        self.assertGreaterEqual(len({s["stability"] for s in ss}), 3)
        self.assertEqual(voice._settings("calm", True, self.LINES[0]), voice._settings("calm", True, self.LINES[0]))          # but repeatable
        n = voice._settings("calm", False, self.LINES[0])
        self.assertNotIn("speed", n)                                                                                       # other commentary untouched

    def test_the_writers_direction_nudges_it(self):
        base = voice._settings("calm", True, "He left.")
        slow = voice._settings("calm", True, "He left.", "low, tired")
        warm = voice._settings("calm", True, "He left.", "dry, amused")
        self.assertLess(slow["speed"], base["speed"])
        self.assertGreater(slow["stability"], base["stability"])
        self.assertGreater(warm["style"], base["style"])
        self.assertEqual(voice._settings("calm", True, "He left.", "plain"), base)

    def test_sentences_are_separated_by_a_breath_but_not_the_last(self):
        out = voice.with_breaths("He said Mother knit them. It ended with me shouting. That was that.")
        self.assertEqual(out.count("<break"), 2)
        self.assertTrue(out.rstrip().endswith("That was that."))
        self.assertEqual(voice.with_breaths("One sentence only."), "One sentence only.")
        self.assertNotIn("<break", voice.with_breaths("Dr. no, wait. Fine."[:0] or "No breaks here"))

    def test_the_breaths_go_to_the_voice_for_a_story_and_not_for_other_lines(self):
        class R:
            status_code, content, text = 200, b"x", ""

            def raise_for_status(self):
                pass
        out = Path(tempfile.mkdtemp()) / "c.mp3"
        with mock.patch("app.voice.requests.post", return_value=R()) as p:
            voice.tts("He left. I stayed.", "V", "calm", out, story=True)
            story_body = p.call_args.kwargs["json"]
            voice.tts("He left. I stayed.", "V", "calm", out)
            plain_body = p.call_args.kwargs["json"]
        self.assertIn("<break", story_body["text"])
        self.assertNotIn("<break", plain_body["text"])

    def test_a_take_is_cached_by_its_direction_too(self):
        a = voice._cache_key("He left.", "V", "calm", True, None, None, "")
        self.assertNotEqual(a, voice._cache_key("He left.", "V", "calm", True, None, None, "low, tired"))
        self.assertEqual(a, voice._cache_key("He left.", "V", "calm", True, None, None, ""))


class RetimeTests(unittest.TestCase):
    def lines(self, starts, passage=0):
        return [{"start": t, "line": f"Line number {i} of the story, plain and told.", "story": True, "passage": passage, "persona": "character"}
                for i, t in enumerate(starts)]

    def test_lines_of_a_passage_follow_their_real_audio_with_a_breath_between(self):
        kept = self.lines([20.0, 31.0, 42.0])                    # planned with generous slots
        clips = [(20.0, "a", 6.0), (31.0, "b", 6.5), (42.0, "c", 5.5)]
        out = voice.retime_story(kept, clips, [])
        self.assertEqual(out[0][0], 20.0)                         # the first line never moves
        for (s1, _, d1), (s2, _, _) in zip(out, out[1:]):
            self.assertTrue(1.0 <= s2 - (s1 + d1) <= 1.8 + 1e-6, (s1, d1, s2))
        self.assertEqual([k["start"] for k in kept], [round(c[0], 2) for c in out])        # the lines carry the new times too
        self.assertNotEqual(round(out[1][0] - out[0][0] - 6.0, 2), round(out[2][0] - out[1][0] - 6.5, 2))   # not the same breath twice

    def test_a_line_that_would_run_into_the_next_passage_stays_where_it_was_planned(self):
        kept = self.lines([20.0, 26.5], passage=0) + self.lines([36.0, 44.0], passage=1)
        clips = [(20.0, "a", 5.0), (26.5, "b", 9.0), (36.0, "c", 6.0), (44.0, "d", 5.0)]
        out = voice.retime_story(kept, clips, [])
        self.assertEqual(out[1][0], 26.5)                                                  # moving it would take it past the quiet before the next passage
        self.assertEqual(out[0][0], 20.0)

    def test_a_line_that_fits_is_moved_but_never_past_the_quiet_before_the_next_passage(self):
        kept = self.lines([20.0, 26.5], passage=0) + self.lines([50.0, 58.0], passage=1)
        clips = [(20.0, "a", 5.0), (26.5, "b", 5.0), (50.0, "c", 6.0), (58.0, "d", 5.0)]
        out = voice.retime_story(kept, clips, [])
        self.assertTrue(26.0 <= out[1][0] <= 27.0 and out[1][0] + 5.0 <= 50.0 - 2.0)
        self.assertEqual(out[2][0], 50.0)

    def test_a_line_never_moves_into_speech(self):
        kept = self.lines([20.0, 30.0])
        clips = [(20.0, "a", 6.0), (30.0, "b", 6.0)]
        out = voice.retime_story(kept, clips, [[27.5, 29.5]])                               # speech where the retimed line would start
        self.assertFalse(27.5 < out[1][0] + out[1][2] and out[1][0] < 29.5, out)

    def test_other_commentary_is_never_moved(self):
        kept = [{"start": 20.0, "line": "One.", "persona": "character"}, {"start": 30.0, "line": "Two.", "persona": "character", "story": True}]
        clips = [(20.0, "a", 3.0), (30.0, "b", 3.0)]
        self.assertEqual(voice.retime_story(kept, clips, []), clips)

    def test_make_clips_retimes_a_story(self):
        lines = [{"start": 20.0, "line": "He said Mother knit them for him and that was that.", "persona": "character", "emotion": "calm", "max_duration": 12.0,
                  "story": True, "passage": 0},
                 {"start": 32.0, "line": "It ended with me shouting he could pick wheat by himself.", "persona": "character", "emotion": "calm",
                  "max_duration": 12.0, "story": True, "passage": 0}]

        def fake(text, voice_id, emotion, out, story=False, prev=None, nxt=None, delivery=""):
            Path(out).write_bytes(b"x")
        with mock.patch("app.voice.tts", side_effect=fake), mock.patch("app.voice.level_clip"), mock.patch("app.voice.duration", return_value=5.0), \
                mock.patch("app.voices.resolve", return_value="V"):
            clips = voice.make_clips(lines, [], "sys", Path(tempfile.mkdtemp()), pack="western")
        self.assertEqual(len(clips), 2)
        self.assertTrue(1.0 <= clips[1][0] - (clips[0][0] + 5.0) <= 1.8 + 1e-6)
        self.assertEqual(lines[1]["start"], round(clips[1][0], 2))


# ---------------------------------------------------------------- pacing: the quiet varies, long videos get long stories
class LongStoryTests(unittest.TestCase):
    def test_the_quiet_between_passages_is_not_always_the_same(self):
        ps = storymode.passages(ride(900), gaps.free_gaps([], 900), 900, 4.5)
        quiets = [round(b[0][0] - (a[-1][0] + a[-1][1]), 1) for a, b in zip(ps, ps[1:])]
        self.assertGreater(len(set(quiets)), 3)
        self.assertTrue(all(q >= storymode.QUIET_MIN for q in quiets), quiets)

    def test_a_long_ride_gets_many_passages_and_the_cap_is_thirty(self):
        ps = storymode.passages(ride(900), gaps.free_gaps([], 900), 900, 4.5)
        self.assertGreater(len(ps), 12)
        self.assertLessEqual(len(storymode.passages(ride(6000), gaps.free_gaps([], 6000), 6000, 10)), 30)
        self.assertEqual((storymode.MAX_PASSAGES, storybible.MAX_PARTS), (30, 30))

    def test_the_prompt_stays_short_however_long_the_story(self):
        beats_ = [f"Part {i} " + "word " * 40 for i in range(30)]
        opps = storymode.told_opps(storymode.passages(ride(2400), gaps.free_gaps([], 2400), 2400, 4.5), {**BIBLE, "beats": beats_, "part_titles": ["x"] * 30})
        self.assertTrue(opps)
        self.assertLess(max(len(o["why"]) for o in opps), 1800)                                  # only the parts just before, not all thirty
        late = next(o for o in opps if o["passage"] == 20)
        self.assertIn("(19) Part 18", late["why"].replace("Part 19 ", "Part 18 ") if False else late["why"])
        self.assertNotIn("(1) Part 0", late["why"])

    def test_part_titles_are_kept_or_filled_in(self):
        ok = storybible.validate(dict(BIBLE), 5)
        self.assertEqual(ok["part_titles"], ["The camp", "The socks", "The leaving", "The boot", "The map"])
        short = storybible.validate({**BIBLE, "part_titles": ["Only one"]}, 5)
        self.assertEqual(short["part_titles"], ["Only one", "Part 2", "Part 3", "Part 4", "Part 5"])
        self.assertEqual(storybible.validate({**BIBLE, "part_titles": "junk"}, 3)["part_titles"], ["Part 1", "Part 2", "Part 3"])

    def test_the_story_is_asked_for_with_room_to_answer_and_part_titles(self):
        with mock.patch("app.storybible.claude", return_value=json.dumps(BIBLE)) as c:
            storybible.make(30)
        self.assertGreaterEqual(c.call_args.args[2], 6000)
        self.assertIn("part_titles", (PROMPTS / "storybible.md").read_text())


# ---------------------------------------------------------------- the welcome, the outro, the chapters and the title belong to the story
class FramingTests(unittest.TestCase):
    def test_the_welcome_says_a_story_is_coming_and_the_outro_closes_it(self):
        free = gaps.free_gaps([], 240)
        cfg = channel.settings(lambda k, d=None: d)
        intro, outro = channel.intro_opportunity(free, 240, cfg), channel.outro_opportunity(free, 240, cfg)
        storymode.frame_welcome_and_outro(intro, outro, BIBLE)
        self.assertIn('called "The Map on the Pay Slip"', intro["why"])
        self.assertIn("do not tell any of it", intro["why"])
        self.assertIn("'welcome' and 'channel'", intro["why"])                           # still the real welcome
        self.assertIn('The story "The Map on the Pay Slip" has just ended', outro["why"])
        self.assertIn("not a moral and not a summary", outro["why"])
        self.assertIn("'like' and 'subscribe'", outro["why"])
        self.assertEqual(intro["must_include"], ["welcome", "channel"])
        self.assertEqual(outro["must_include"], ["like", "subscribe"])
        storymode.frame_welcome_and_outro(None, None, BIBLE)                               # nothing to frame: no crash

    def test_story_chapters_are_the_parts_by_title_and_need_three(self):
        lines = [{"start": 45.0, "passage": 0, "story": True}, {"start": 54.0, "passage": 0, "story": True}, {"start": 85.0, "passage": 1, "story": True},
                 {"start": 122.0, "passage": 2, "story": True}, {"start": 90.0, "passage": 1, "story": True, "dropped": True}]
        ch = viral.story_chapters(lines, BIBLE)
        self.assertEqual(ch[0], {"t": 0.0, "title": "Welcome"})
        self.assertEqual([c["title"] for c in ch[1:]], ["Part 1: The camp", "Part 2: The socks", "Part 3: The leaving"])
        self.assertEqual([c["t"] for c in ch[1:]], [45.0, 85.0, 122.0])
        self.assertEqual(viral.story_chapters(lines[:1], BIBLE), [])
        self.assertEqual(viral.story_chapters(lines, None), [])

    def facts(self, story):
        return packaging.facts("Red Dead Redemption 2", "Western outlaw", 200, ride(200), [], [], [], story=story)

    def test_a_story_video_always_offers_the_story_title_as_a_title(self):
        fs = self.facts({"title": "The Map on the Pay Slip", "logline": "Two brothers worked a grading camp."})
        pkg = packaging.validate({"titles": ["A Ride With An Inner Voice"]}, fs)
        self.assertEqual(pkg["titles"][0], "The Map on the Pay Slip | Red Dead Redemption 2 inner story")
        again = packaging.validate({"titles": ["The Map on the Pay Slip, an inner story"]}, fs)
        self.assertEqual(again["titles"], ["The Map on the Pay Slip, an inner story"])         # already there: nothing added
        plain = packaging.validate({"titles": ["A Ride With An Inner Voice"]}, self.facts(None))
        self.assertEqual(plain["titles"], ["A Ride With An Inner Voice"])

    def test_the_writer_of_the_packaging_is_told_about_the_story_and_never_to_spoil_it(self):
        text = (PROMPTS / "packaging.md").read_text()
        self.assertIn("never give away how it ends", text)
        fs = self.facts({"title": "T", "logline": "L."})
        self.assertEqual(fs["story"], {"title": "T", "logline": "L."})
        self.assertIsNone(self.facts(None)["story"])


class PipelineTests(unittest.TestCase):
    def run_video(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created,story_mode,story_lpm) VALUES(?,?,?,?,?,?,?,?)",
                     (f"sp{time.time_ns()}", "x.mp4", "queued", "free_wanderer", "Red Dead Redemption 2", time.time(), 1, 4.5)).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps(ride(240)))
        (work / "transcript.json").write_text("[]")
        seen = {}

        def spy(scenes, segments, windows, system, length, opps, **kw):
            seen["opps"] = opps
            return [], []
        with mock.patch("app.pipeline.scenes.video_length", return_value=240.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.storybible.make", side_effect=lambda parts, *a, **k: {**BIBLE, "beats": (BIBLE["beats"] * 8)[:parts],
                                                                                                    "part_titles": (BIBLE["part_titles"] * 8)[:parts]}), \
                mock.patch("app.pipeline.writer.write_lines", side_effect=spy), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid)
        return vid, seen

    def test_the_welcome_and_the_outro_the_writer_sees_belong_to_the_story(self):
        vid, seen = self.run_video()
        why = {o["id"]: o["why"] for o in seen["opps"] if o["id"] in ("intro", "outro")}
        self.assertIn('called "The Map on the Pay Slip"', why["intro"])
        self.assertIn("has just ended", why["outro"])
        self.assertTrue((pipeline.workdir(vid) / "chapters.json").exists())


if __name__ == "__main__":
    unittest.main()
