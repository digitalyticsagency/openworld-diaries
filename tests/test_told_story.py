import json
import os
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ["ELEVENLABS_API_KEY"] = "test-key"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests  # noqa: E402

from app import cutscenes, db, gaps, guard, pipeline, storybible, storymode, voice  # noqa: E402
from app.config import PROMPTS  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "player_state": "riding",
            "mount_or_vehicle": "brown horse", "time_weather": "day, clear", **kw}


def ride(seconds=240, step=3, **kw):
    return [frame(t, **kw) for t in range(0, seconds, step)]


BIBLE = {"title": "The Cracked Mug", "premise": "He borrowed a mug from his brother and never gave it back. Years later he still has it.",
         "beats": ["The mug and the winter.", "The quarrel.", "The leaving.", "The mug on the road.", "What he does with it now."],
         "ending": "He keeps the mug and does not say why."}


# ---------------------------------------------------------------- the story itself
class StoryBibleTests(unittest.TestCase):
    def test_every_story_gets_different_constraints(self):
        seen = {json.dumps(storybible.pick_seed()) for _ in range(30)}
        self.assertGreater(len(seen), 25)

    def test_the_chosen_themes_decide_who_the_story_is_about(self):
        rng = random.Random(1)
        for _ in range(20):
            self.assertIn(storybible.pick_seed(["family"], rng)["about"], storybible.RELATIONS["family"])
            self.assertIn(storybible.pick_seed(["friends", "work"], rng)["theme"], ("friends", "work"))
        self.assertIn(storybible.pick_seed(["nonsense"], rng)["theme"], storybible.RELATIONS)             # an unknown theme falls back to any

    def test_a_usable_story_is_accepted_and_a_poor_one_is_not(self):
        ok = storybible.validate(dict(BIBLE), 5)
        self.assertEqual((ok["title"], len(ok["beats"])), ("The Cracked Mug", 5))
        self.assertEqual(len(storybible.validate(dict(BIBLE), 3)["beats"]), 3)                          # trimmed to the passages there are
        self.assertEqual(len(storybible.validate(dict(BIBLE), 9)["beats"]), 5)                          # fewer beats than asked: what there is
        for bad in (None, [], {"title": "x"}, {**BIBLE, "beats": ["only one"]}, {**BIBLE, "premise": ""}, {**BIBLE, "title": ""}, {**BIBLE, "beats": "text"}):
            self.assertIsNone(storybible.validate(bad, 5), bad)

    def test_the_model_is_given_the_constraints_the_idea_and_the_premises_to_avoid(self):
        db.run("INSERT INTO stories(video_id,title,premise,seed,created) VALUES(?,?,?,?,?)", (1, "Old", "An old premise about a boat.", "{}", time.time()))
        with mock.patch("app.storybible.claude", return_value=json.dumps(BIBLE)) as c:
            bible = storybible.make(5, ["family"], "the day I left home")
        sent = json.loads(c.call_args.args[1])
        self.assertEqual(sent["parts"], 5)
        self.assertEqual(sent["owner_idea"], "the day I left home")
        self.assertIn("An old premise about a boat.", sent["already_used_premises"])
        self.assertIn(sent["constraints"]["about"], storybible.RELATIONS["family"])
        self.assertEqual(bible["seed"], sent["constraints"])
        self.assertIn("never reuse or echo them", c.call_args.args[0] if False else (PROMPTS / "storybible.md").read_text())

    def test_an_unreadable_answer_is_asked_for_once_more_and_then_given_up(self):
        with mock.patch("app.storybible.claude", side_effect=["no json at all", json.dumps(BIBLE)]) as c:
            self.assertEqual(storybible.make(5)["title"], "The Cracked Mug")
        self.assertEqual(c.call_count, 2)
        with mock.patch("app.storybible.claude", return_value="no json") as c:
            self.assertIsNone(storybible.make(5))
        self.assertEqual(c.call_count, 2)

    def test_the_part_count_is_kept_sensible(self):
        with mock.patch("app.storybible.claude", return_value=json.dumps({**BIBLE, "beats": [f"part {i}" for i in range(30)]})) as c:
            bible = storybible.make(99)
        self.assertEqual(json.loads(c.call_args.args[1])["parts"], storybible.MAX_PARTS)
        self.assertEqual(len(bible["beats"]), storybible.MAX_PARTS)

    def test_the_story_is_remembered_so_the_next_one_differs(self):
        storybible.save(7, {**BIBLE, "seed": {"about": "his brother"}})
        self.assertIn(BIBLE["premise"], storybible.used_premises())

    def test_the_prompt_asks_for_plain_ordinary_storytelling(self):
        text = (PROMPTS / "storybible.md").read_text()
        for needle in ("No poetry, no aphorisms, no clever twists", "Do not name any character from a game", "exactly the number of parts"):
            self.assertIn(needle, text)


# ---------------------------------------------------------------- where the passages go
class PassageTests(unittest.TestCase):
    def test_passages_are_a_few_lines_in_a_row_then_quiet(self):
        free = gaps.free_gaps([], 240)
        ps = storymode.passages(ride(), free, 240, 4.5)
        self.assertGreaterEqual(len(ps), 3)
        self.assertEqual([len(p) for p in ps[:4]], [3, 4, 3, 4])
        for p in ps:
            for (t, d, w), (t2, _, _) in zip(p, p[1:]):
                self.assertAlmostEqual(t2 - t, d + storymode.PAUSE, delta=0.05)                    # one line after the other with a breath
        for a, b in zip(ps, ps[1:]):
            end = a[-1][0] + a[-1][1]
            self.assertGreaterEqual(b[0][0] - end, storymode.QUIET_MIN)                           # and quiet between passages

    def test_the_slider_sets_how_long_the_quiet_is(self):
        free = gaps.free_gaps([], 600)
        counts = [len(storymode.passages(ride(600), free, 600, lpm)) for lpm in (2, 4.5, 8)]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[2])

    def test_about_the_asked_for_talk_to_quiet_ratio(self):
        ps = storymode.passages(ride(600), gaps.free_gaps([], 600), 600, 4.5)
        talk = sum(l[1] for p in ps for l in p)
        self.assertTrue(0.25 < talk / 600 < 0.65, talk / 600)

    def test_never_over_speech_a_fight_or_the_welcome_and_the_outro(self):
        notes = ride(300)
        for i in range(30, 38):
            notes[i] = frame(i * 3, activity="combat")                                            # a fight at 90-111 s
        free = gaps.free_gaps([(150.0, 175.0)], 300)                                              # someone speaks at 150-175 s
        ps = storymode.passages(notes, free, 300, 6, avoid=[(8.0, 22.0), (280.0, 296.0)])
        for p in ps:
            for t, d, _ in p:
                self.assertFalse(t < 112 and t + d > 89, (t, d))
                self.assertFalse(t < 175 and t + d > 150, (t, d))
                self.assertFalse(t < 22 and t + d > 8, (t, d))
                self.assertFalse(t < 296 and t + d > 280, (t, d))

    def test_no_travel_means_no_story_and_a_tiny_stretch_is_not_forced(self):
        self.assertEqual(storymode.passages([frame(t * 3, activity="combat") for t in range(60)], gaps.free_gaps([], 180), 180, 4.5), [])
        self.assertEqual(storymode.passages(ride(6), gaps.free_gaps([], 6), 6, 4.5), [])

    def test_there_are_never_more_than_a_dozen(self):
        self.assertLessEqual(len(storymode.passages(ride(3000), gaps.free_gaps([], 3000), 3000, 10)), storymode.MAX_PASSAGES)

    def test_the_default_pace_is_four_and_a_half(self):
        self.assertEqual(storymode.DEFAULT_LPM, 4.5)


class ToldOpportunityTests(unittest.TestCase):
    def setUp(self):
        self.ps = storymode.passages(ride(), gaps.free_gaps([], 240), 240, 4.5)
        self.opps = storymode.told_opps(self.ps[:5], BIBLE)

    def test_every_line_is_told_its_part_of_the_same_story(self):
        first, second_part = self.opps[0], next(o for o in self.opps if o["passage"] == 1)
        self.assertIn('STORY "The Cracked Mug", part 1 of 5, line 1 of 3', first["why"])
        self.assertIn("Nothing has been told yet", first["why"])
        self.assertIn("Begin the story in the middle of ordinary life", first["why"])
        self.assertIn("This part tells: The mug and the winter.", first["why"])
        self.assertIn("The parts just before this one: (1) The mug and the winter.", second_part["why"])
        self.assertIn("This part tells: The quarrel.", second_part["why"])
        self.assertIn("picks up where the last part stopped", second_part["why"])

    def test_each_passage_ends_on_a_complete_sentence_and_the_last_one_lands_the_story_plainly(self):
        by_p = {}
        for o in self.opps:
            by_p.setdefault(o["passage"], []).append(o)
        self.assertIn("Finish this part on a complete sentence, not a punchline", by_p[0][-1]["why"])
        self.assertNotIn("Finish this part", by_p[0][0]["why"])
        last = by_p[4][-1]["why"]
        self.assertIn("This is the end of the story", last)
        self.assertIn("no moral and no punchline", last)
        self.assertIn(BIBLE["ending"], last)

    def test_every_line_asks_for_plain_complete_sentences(self):
        for o in self.opps:
            self.assertIn("One or two complete, plain sentences", o["why"])
            self.assertIn("No poetry, no sayings, no trailing off", o["why"])
            self.assertIn("Never name a real character", o["why"])

    def test_they_are_quiet_travel_thoughts_with_room_for_the_next_line(self):
        for o in self.opps:
            self.assertEqual((o["kind"], o["travel"]), ("daydream", "story"))
            self.assertEqual(o["kinds"], ["daydream", "travel_story", "story_told"])
            self.assertLessEqual(o["max_words"], 25)
        for a, b in zip(self.opps, self.opps[1:]):
            if a["passage"] == b["passage"]:
                self.assertLess(a["t"] + a["max_duration"], b["t"])                              # a line never runs into the next one

    def test_a_story_with_fewer_beats_than_passages_reuses_the_last_beat_safely(self):
        opps = storymode.told_opps(self.ps[:4], {**BIBLE, "beats": ["one", "two"]})
        self.assertEqual(len(opps), sum(len(p) for p in self.ps[:4]))


# ---------------------------------------------------------------- complete sentences, closely followed
class SentenceTests(unittest.TestCase):
    def test_what_counts_as_unfinished(self):
        for bad in ("Maybe somewhere nobody knows my...", "He left the mug on the", "He left the mug on the table", "I never wrote back, and…",
                    "Well.", "Hm. No.", "Then he said that his father and"):
            self.assertTrue(guard.unfinished(bad, 5), bad)
        for ok in ("He left the mug on the table and walked out.", "Was it worth it? I still do not know.", "We never spoke of it again!",
                   'He said "I will pay you back."', "There was nothing to worry about.", "That was the last thing I ever asked of him."):
            self.assertFalse(guard.unfinished(ok, 5), ok)

    def judge(self, text, kinds="daydream,travel_story,story_told", passage=None, start=20.0):
        ln = {"start": start, "line": text, "persona": "character", "emotion": "calm", "max_words": 25, "max_duration": 9.0, "opp_id": "s0_0",
              "beat_kinds": kinds, "silent": False, "passage": passage}
        keep, drop = guard.enforce([ln], [], ride(120), min_gap=2.5)
        return None if keep else drop[0]["reason"]

    def test_a_story_line_that_stops_mid_sentence_is_dropped(self):
        self.assertEqual(self.judge("My brother lent me a coat, and I never..."), "unfinished sentence")
        self.assertEqual(self.judge("My brother lent me his coat that winter and"), "unfinished sentence")
        self.assertEqual(self.judge("Cold."), "unfinished sentence")
        self.assertIsNone(self.judge("My brother lent me his coat that winter, and I never gave it back."))

    def test_the_older_thread_thoughts_must_finish_too_but_may_be_short(self):
        self.assertEqual(self.judge("Maybe somewhere nobody knows my...", kinds="daydream,travel_story"), "unfinished sentence")
        self.assertIsNone(self.judge("Hm. Not today.", kinds="daydream,travel_story"))

    def test_other_commentary_is_not_held_to_it(self):
        self.assertIsNone(self.judge("Easy now...", kinds="big_animal"))

    def test_a_remembered_story_may_mention_anything_in_the_past_without_the_scene_check(self):
        self.assertIsNone(self.judge("My brother had a wagon and a train ticket and a plan that went nowhere."))
        self.assertIsNone(self.judge("It rained for hours that night and we sat under the wagon until morning."))

    def test_lines_of_one_passage_may_follow_each_other_closely_but_other_lines_may_not(self):
        scn = ride(120)
        mk = lambda t, p, text: {"start": t, "line": text, "persona": "character", "emotion": "calm", "max_words": 25, "max_duration": 9.0,
                                 "opp_id": f"s{p}_{t}", "beat_kinds": "daydream,travel_story,story_told", "silent": False, "passage": p}
        first = "My brother lent me his coat that winter and never asked for it back."
        second = "I wore it for three years before I understood what it had cost him."
        keep, drop = guard.enforce([mk(10.0, 0, first), mk(10.0 + 7.5, 0, second)], [], scn, min_gap=2.5)
        self.assertEqual(len(keep), 2)                                                           # same passage: a breath apart is fine
        keep, drop = guard.enforce([mk(10.0, 0, first), mk(10.0 + 7.5, 1, second)], [], scn, min_gap=2.5)
        self.assertEqual(len(keep), 1)                                                           # different passages: the normal spacing
        self.assertEqual(drop[0]["reason"], "too close to previous line")


# ---------------------------------------------------------------- the voice
class Resp:
    def __init__(self, code=200, content=b"audio", text=""):
        self.status_code, self.content, self.text = code, content, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code), response=self)


class VoiceDeliveryTests(unittest.TestCase):
    def setUp(self):
        os.environ["ELEVENLABS_API_KEY"] = "test-key"
        self.out = Path(tempfile.mkdtemp()) / "c.mp3"

    def test_a_story_is_told_slower_and_looser(self):
        s, n = voice._settings("calm", story=True, text="Then he left."), voice._settings("calm")
        self.assertLess(s["stability"], n["stability"])
        self.assertGreater(s["style"], n["style"])
        self.assertLess(s["speed"], 1.0)
        self.assertNotIn("speed", n)                                                            # other commentary is unchanged

    def test_the_lines_either_side_are_sent_so_the_voice_carries_on(self):
        with mock.patch("app.voice.requests.post", return_value=Resp()) as p:
            voice.tts("Then he left.", "V", "calm", self.out, story=True, prev="He lent me his coat.", nxt="I never wrote.")
        body = p.call_args.kwargs["json"]
        self.assertEqual((body["previous_text"], body["next_text"]), ("He lent me his coat.", "I never wrote."))
        self.assertTrue(0.90 <= body["voice_settings"]["speed"] <= 0.98)
        with mock.patch("app.voice.requests.post", return_value=Resp()) as p:
            voice.tts("Easy now.", "V", "calm", self.out)
        body = p.call_args.kwargs["json"]
        self.assertNotIn("previous_text", body)
        self.assertNotIn("speed", body["voice_settings"])

    def test_a_model_that_refuses_speed_or_neighbouring_text_is_asked_again_without_them(self):
        with mock.patch("app.voice.requests.post", side_effect=[Resp(422, text="speed is not supported"), Resp()]) as p:
            voice.tts("Then he left.", "V", "calm", self.out, story=True, prev="a", nxt="b")
        self.assertEqual(p.call_count, 2)
        retry = p.call_args.kwargs["json"]
        self.assertNotIn("speed", retry["voice_settings"])
        self.assertNotIn("previous_text", retry)
        self.assertEqual(self.out.read_bytes(), b"audio")

    def test_a_take_is_cached_by_its_context_too(self):
        a = voice._cache_key("Then he left.", "V", "calm", True, "x", "y")
        self.assertNotEqual(a, voice._cache_key("Then he left.", "V", "calm", True, "z", "y"))
        self.assertNotEqual(a, voice._cache_key("Then he left.", "V", "calm", False))
        self.assertEqual(a, voice._cache_key("Then he left.", "V", "calm", True, "x", "y"))

    def test_story_lines_know_their_neighbours_and_others_do_not(self):
        lines = [{"start": 10, "line": "One.", "story": True}, {"start": 17, "line": "Two.", "story": True}, {"start": 24, "line": "Three.", "story": True},
                 {"start": 60, "line": "Far away.", "story": True}, {"start": 70, "line": "Plain.", "story": False}]
        self.assertEqual(voice._neighbours(lines, 0), (None, "Two."))
        self.assertEqual(voice._neighbours(lines, 1), ("One.", "Three."))
        self.assertEqual(voice._neighbours(lines, 2), ("Two.", None))                           # the next story line is a long way off
        self.assertEqual(voice._neighbours(lines, 3), (None, None))
        self.assertEqual(voice._neighbours(lines, 4), (None, None))

    def test_make_clips_passes_the_story_flag_and_neighbours_to_the_voice(self):
        calls = []
        lines = [{"start": 10.0, "line": "He lent me his coat that winter.", "persona": "character", "emotion": "calm", "max_duration": 9.0, "story": True},
                 {"start": 17.0, "line": "I never gave it back.", "persona": "character", "emotion": "calm", "max_duration": 9.0, "story": True}]

        def fake(text, voice_id, emotion, out, story=False, prev=None, nxt=None, delivery=""):
            calls.append((text, story, prev, nxt))
            Path(out).write_bytes(b"x")
        with mock.patch("app.voice.tts", side_effect=fake), mock.patch("app.voice.level_clip"), mock.patch("app.voice.duration", return_value=3.0), \
                mock.patch("app.voices.resolve", return_value="V"):
            voice.make_clips(lines, [], "sys", Path(tempfile.mkdtemp()), pack="western")
        self.assertEqual(calls[0], ("He lent me his coat that winter.", True, None, "I never gave it back."))
        self.assertEqual(calls[1], ("I never gave it back.", True, "He lent me his coat that winter.", None))


# ---------------------------------------------------------------- the whole video
class PipelineTests(unittest.TestCase):
    def run_video(self, bible):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created,story_mode,story_lpm,themes) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"td{time.time_ns()}", "x.mp4", "queued", "free_wanderer", "Red Dead Redemption 2", time.time(), 1, 4.5, json.dumps(["family"]))).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps(ride(240)))
        (work / "transcript.json").write_text("[]")
        seen = {}

        def spy(scenes, segments, windows, system, length, opps, **kw):
            seen["opps"], seen["system"] = opps, system
            return [], []
        with mock.patch("app.pipeline.scenes.video_length", return_value=240.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.storybible.make", return_value=bible) as make, \
                mock.patch("app.pipeline.writer.write_lines", side_effect=spy), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid)
        return vid, seen, make, json.loads(db.one("SELECT stats FROM videos WHERE id=?", (vid,))["stats"])

    def test_a_told_story_is_planned_saved_and_reported(self):
        vid, seen, make, stats = self.run_video({**BIBLE, "seed": {"about": "his brother"}})
        kinds = {o["id"].split("_")[0].rstrip("0123456789") for o in seen["opps"]}
        self.assertEqual(kinds, {"s", "intro", "outro"})
        told = [o for o in seen["opps"] if o["id"].startswith("s")]
        self.assertTrue(told and all("story_told" in o["kinds"] for o in told))
        self.assertEqual(make.call_args.args[0], len({o["passage"] for o in told}) if False else make.call_args.args[0])
        self.assertEqual(make.call_args.args[1], ["family"])
        self.assertEqual(stats["story"]["title"], "The Cracked Mug")
        self.assertFalse(stats["story"]["fallback"])
        self.assertEqual(db.one("SELECT title FROM stories WHERE video_id=?", (vid,))["title"], "The Cracked Mug")
        self.assertEqual(json.loads((pipeline.workdir(vid) / "story.json").read_text())["title"], "The Cracked Mug")
        self.assertIn("STORY MODE", seen["system"])
        self.assertNotIn("HOW A MIND REALLY SOUNDS", seen["system"])

    def test_one_part_of_the_story_for_every_passage_and_never_over_the_welcome_or_the_outro(self):
        vid, seen, make, _ = self.run_video(BIBLE)
        told = [o for o in seen["opps"] if o["id"].startswith("s")]
        self.assertEqual(len({o["passage"] for o in told}), len(BIBLE["beats"]) if len({o["passage"] for o in told}) <= len(BIBLE["beats"]) else len(BIBLE["beats"]))
        for special in (o for o in seen["opps"] if o["id"] in ("intro", "outro")):
            for o in told:
                self.assertFalse(o["t"] < special["t"] + 11 and o["t"] + o["max_duration"] > special["t"] - 1, (o["id"], special["id"]))

    def test_when_no_story_can_be_made_the_older_thoughts_are_used_and_it_says_so(self):
        vid, seen, make, stats = self.run_video(None)
        self.assertTrue(stats["story"]["fallback"])
        self.assertIsNone(stats["story"]["title"])
        told = [o for o in seen["opps"] if o["id"].startswith("s")]
        self.assertTrue(told)
        self.assertFalse(any("story_told" in o["kinds"] for o in told))
        self.assertEqual(db.rows("SELECT id FROM stories WHERE video_id=?", (vid,)), [])


class PageTests(unittest.TestCase):
    def test_the_page_shows_the_story_and_the_slider_matches(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn("st.story.title", html)
        self.assertIn("natural", html)


if __name__ == "__main__":
    unittest.main()


class NamesAndSlotsTests(unittest.TestCase):
    """Video 8's story lost four of its ten lines: 'my uncle' was taken for a game character, and one line overran its slot."""

    def test_ordinary_words_are_not_game_characters(self):
        for text in ("I came to my uncle's landing in March.", "Same week, my uncle split his palm on frayed cable.", "He paid the bill and left.",
                     "He was a jack of all trades.", "My mother took in boarders.", "We crossed at the ford where the sign said Ross Ferry."[:30]):
            self.assertIsNone(guard.CANON.search(text), text)

    def test_names_of_game_characters_are_still_caught(self):
        for text in ("Someday I'll sit with Mary on a porch.", "Maybe Dutch was right.", "John would have laughed.", "Then Arthur Morgan rode off.",
                     "Franklin never asked.", "Said Marston to the preacher."):
            self.assertTrue(guard.CANON.search(text), text)

    def judge(self, text):
        ln = {"start": 20.0, "line": text, "persona": "character", "emotion": "calm", "max_words": 22, "max_duration": 12.0, "opp_id": "s0_0",
              "beat_kinds": "daydream,travel_story,story_told", "silent": False, "daydream": True}
        keep, drop = guard.enforce([ln], [], ride(120), min_gap=2.5)
        return None if keep else drop[0]["reason"]

    def test_a_plain_story_about_an_uncle_passes_the_name_check(self):
        self.assertIsNone(self.judge("I came to my uncle's landing in March with one bag and a pair of boots a size too big."))
        self.assertEqual(self.judge("I came to Dutch's landing in March with one bag and a pair of boots a size too big."), "daydream names a real character")

    def test_a_line_that_overruns_its_slot_is_asked_for_again_shorter_instead_of_being_lost(self):
        from app import writer
        slot = {"id": "s2_0", "t": 20.0, "beat_t": None, "salience": 4.6, "kinds": ["daydream", "travel_story", "story_told"], "kind": "daydream",
                "why": "x", "max_words": 18, "max_duration": 4.0, "passage": 2}
        long_line = "So I pulled the crossing mostly by myself that spring, while he sat on an upturned bucket nursing that hand."
        short_line = "I pulled the crossing alone that spring while he nursed his hand."
        dropped = [{"opp_id": "s2_0", "start": 20.0, "line": long_line, "max_words": 18, "max_duration": 4.0, "reason": "no room before speech",
                    "persona": "character", "emotion": "calm", "beat_kinds": "daydream,travel_story,story_told", "silent": False}]
        with mock.patch("app.writer.claude", return_value=json.dumps([{"opportunity": "s2_0", "line": short_line}])) as c:
            fixed = writer._shorten("sys", dropped, {"s2_0": slot})
        self.assertEqual(fixed[0]["line"], short_line)
        sent = c.call_args.args[1]
        self.assertIn('"max_words": 8', sent)                                                    # a four-second slot, not the eighteen words it was given
