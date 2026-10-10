import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import beats, channel, db, gaps, guard, knowledge, pipeline, sentiment, writer  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "location": "road", "activity": "travel", **kw}


# ---------------------------------------------------------------- conscience: innocents versus outlaws
class RobustnessTests(unittest.TestCase):
    SEG = [{"start": 1, "end": 2, "text": "Well done.", "speaker": "game"}]

    def test_a_failed_reading_is_retried_and_never_saved_as_nothing(self):
        d = tempfile.mkdtemp()
        with mock.patch("app.sentiment.claude", side_effect=RuntimeError("busy")) as c:
            self.assertEqual(sentiment.ensure(d, self.SEG), [])
        self.assertEqual(c.call_count, 2)                              # tried twice
        self.assertFalse((Path(d) / "speech_tone.json").exists())      # so the next run asks again
        ok = json.dumps([{"i": 0, "to_player": True, "tone": "praise"}])
        with mock.patch("app.sentiment.claude", return_value=ok):
            self.assertEqual(len(sentiment.ensure(d, self.SEG)), 1)
        self.assertTrue((Path(d) / "speech_tone.json").exists())

    def test_one_bad_answer_then_a_good_one_still_succeeds(self):
        good = json.dumps([{"i": 0, "to_player": True, "tone": "thanks"}])
        with mock.patch("app.sentiment.claude", side_effect=["not json", good]):
            self.assertEqual(sentiment.classify(self.SEG), ([{"i": 0, "to_player": True, "tone": "thanks"}], True))

    def test_the_welcome_and_the_outro_are_never_squeezed_by_a_neighbour(self):
        def opp(id_, t, **kw):
            return {"id": id_, "t": t, "beat_t": None, "salience": 5, "kinds": ["x"], "why": "", "max_words": 22, "max_duration": 9.0, "kind": "full", **kw}
        outro = opp("outro", 100.0, salience=98, must_include=["like", "subscribe"])
        near = opp("b1", 105.0, salience=9)          # would leave the outro only ~1 word
        far = opp("b2", 120.0, salience=9)
        fitted = beats._fit_to_neighbours([outro, near, far])
        ids = [o["id"] for o in fitted]
        self.assertEqual(ids, ["outro", "b2"])
        self.assertGreaterEqual(fitted[0]["max_words"], beats.MUST_FIT_WORDS)

    def test_a_neighbour_that_leaves_enough_room_is_kept(self):
        def opp(id_, t, **kw):
            return {"id": id_, "t": t, "beat_t": None, "salience": 5, "kinds": ["x"], "why": "", "max_words": 22, "max_duration": 9.0, "kind": "full", **kw}
        fitted = beats._fit_to_neighbours([opp("outro", 100.0, must_include=["like", "subscribe"]), opp("b1", 112.0)])
        self.assertEqual([o["id"] for o in fitted], ["outro", "b1"])

    def test_a_tiny_gap_still_gets_a_line_that_really_says_it(self):
        cfg = {"channel": "", "intro_text": "Welcome to the channel, folks. Let's see where the trail takes us.",
               "outro_text": "Thanks for riding along. Like and subscribe, see you next time."}
        for oid, words in (("intro", ["welcome", "channel"]), ("outro", ["like", "subscribe"])):
            for room in (3, 4, 6, 12):
                text = channel.template_for({"id": oid, "max_words": room, "must_include": words}, cfg).lower()
                self.assertTrue(all(w in text for w in words), (oid, room, text))
                self.assertLessEqual(len(text.split()), max(room, 4))   # the shortest honest form is 4 words

    def test_fix_lines_repairs_a_truncated_outro(self):
        cfg = {"channel": "", "intro_text": "x", "outro_text": "Thanks for riding along. Like and subscribe, see you next time."}
        opp = {"id": "outro", "t": 50.0, "max_duration": 4.0, "max_words": 5, "salience": 98, "must_include": ["like", "subscribe"]}
        out = channel.fix_lines([{"start": 50.0, "opp_id": "outro", "line": "Thanks for riding along. Like."}], [opp], cfg)
        self.assertIn("subscribe", out[0]["line"].lower())


class CasualtyTests(unittest.TestCase):
    def test_an_innocent_casualty_means_remorse(self):
        kind, w, why = beats.casualty_beat({"who": "civilian", "evidence": "honor icon dropped"})
        self.assertEqual(kind, "casualty_innocent")
        self.assertGreaterEqual(w, 9)
        self.assertIn("remorse", why)

    def test_an_armed_outlaw_means_grim_justice_never_remorse(self):
        for who in ("bandit", "gang_member", "lawman"):
            kind, w, why = beats.casualty_beat({"who": who, "evidence": "drawn weapon"})
            self.assertEqual(kind, "justice_outlaw")
            self.assertIn("no remorse", why)

    def test_unclear_stays_neutral(self):
        kind, _, why = beats.casualty_beat({"who": "unknown", "evidence": "dark, far away"})
        self.assertEqual(kind, "casualty_unclear")
        self.assertIn("neither guilt nor justice", why)

    def test_cues_only_mode_refuses_to_judge_from_looks(self):
        guess = {"who": "civilian", "evidence": "plain clothes, unarmed"}
        self.assertEqual(beats.casualty_beat(guess, "both")[0], "casualty_innocent")
        self.assertEqual(beats.casualty_beat(guess, "cues")[0], "casualty_unclear")
        cued = {"who": "civilian", "evidence": "the honor icon flashed"}
        self.assertEqual(beats.casualty_beat(cued, "cues")[0], "casualty_innocent")

    def test_horses_animals_and_nobody(self):
        self.assertEqual(beats.casualty_beat({"who": "horse", "evidence": "x"})[:2], ("casualty_animal", 8.0))
        animal = beats.casualty_beat({"who": "animal", "evidence": "x"})
        self.assertLess(animal[1], 5)
        self.assertIn("not cruelty", animal[2])
        self.assertIsNone(beats.casualty_beat({"who": "none"}))
        self.assertIsNone(beats.casualty_beat(None))


# ---------------------------------------------------------------- what counts as an action worth reacting to
class ActionScoreTests(unittest.TestCase):
    def score(self, **kw):
        return beats.scene_score(frame(0, **kw))

    def test_plain_travel_scores_nothing(self):
        self.assertEqual(self.score()[0], 0)

    def test_real_actions_score_and_say_what_they_are(self):
        for act in ("shop", "horse_care", "animal_interaction", "hunting"):
            s, kinds, why = self.score(activity=act)
            self.assertGreater(s, 2, act)
            self.assertIn(act, kinds)
        s, kinds, why = self.score(activity="shop", shop={"action": "buying", "item": "a tonic"})
        self.assertIn("buying a tonic", " ".join(why))
        self.assertEqual(kinds.count("shop"), 1)           # shop is only counted once

    def test_predators_are_danger_and_big_animals_earn_respect(self):
        sit = lambda beh: self.score(animals=[{"species": "wolf", "predator": True, "behavior": beh}])[0]
        self.assertGreater(sit("attacking"), sit("approaching"))
        self.assertGreater(sit("approaching"), sit("grazing"))
        self.assertGreaterEqual(sit("attacking"), 8)
        self.assertIn("predator", self.score(animals=[{"species": "grizzly bear", "behavior": "approaching"}])[1])   # named, not flagged
        s, kinds, _ = self.score(animals=[{"species": "bison", "size": "large", "predator": False, "behavior": "grazing"}])
        self.assertIn("big_animal", kinds)
        self.assertEqual(self.score(animals=[{"species": "rabbit", "size": "small", "behavior": "fleeing"}])[0], 0)

    def test_an_innocent_death_outweighs_an_outlaw_death(self):
        innocent = self.score(casualty={"who": "civilian", "evidence": "honor icon"})
        outlaw = self.score(casualty={"who": "bandit", "evidence": "gun drawn"})
        self.assertGreater(innocent[0], outlaw[0])
        self.assertIn("casualty_innocent", innocent[1])
        self.assertIn("justice_outlaw", outlaw[1])

    def test_old_scans_without_the_new_fields_still_work(self):
        old = {"t": 5, "interactions": [{"kind": "kill", "intensity": 2}], "notable_details": [], "location": "x"}
        self.assertGreater(beats.scene_score(old)[0], 4)

    def test_learned_taste_applies_to_the_new_kinds(self):
        base = self.score(activity="horse_care")[0]
        self.assertGreater(beats.scene_score(frame(0, activity="horse_care"), kw={"horse_care": 1.6})[0], base)


# ---------------------------------------------------------------- what people say to Arthur
class SpeechBeatTests(unittest.TestCase):
    SEGS = [{"start": 10, "end": 13, "text": "You did well out there.", "speaker": "game"},
            {"start": 20, "end": 23, "text": "Get out of my town, Morgan!", "speaker": "game"},
            {"start": 30, "end": 32, "text": "Nice weather.", "speaker": "game"},
            {"start": 40, "end": 42, "text": "Please, help my son!", "speaker": "game"},
            {"start": 41, "end": 43, "text": "He is hurt.", "speaker": "game"}]

    def tones(self):
        return [{"i": 0, "to_player": True, "tone": "praise"}, {"i": 1, "to_player": True, "tone": "threat"},
                {"i": 2, "to_player": False, "tone": "neutral"}, {"i": 3, "to_player": True, "tone": "plea"},
                {"i": 4, "to_player": True, "tone": "plea"}, {"i": 99, "to_player": True, "tone": "threat"}]

    def test_only_words_aimed_at_him_with_a_real_tone_become_beats(self):
        out = beats.speech_beats(self.SEGS, self.tones())
        self.assertEqual([(b["t"], b["kinds"][0]) for b in out], [(13, "npc_praise"), (23, "npc_threat"), (42, "npc_plea")])
        self.assertIn("You did well out there.", out[0]["why"])

    def test_a_threat_outweighs_praise_and_close_lines_are_one_moment(self):
        out = beats.speech_beats(self.SEGS, self.tones())
        self.assertGreater(out[1]["salience"], out[0]["salience"])
        self.assertEqual(len([b for b in out if b["kinds"] == ["npc_plea"]]), 1)

    def test_bad_data_is_ignored(self):
        self.assertEqual(beats.speech_beats(self.SEGS, None), [])
        self.assertEqual(beats.speech_beats(self.SEGS, [{"i": "x"}, {"i": 0}, {"i": 0, "to_player": True, "tone": "dance"}]), [])

    def test_classifier_skips_the_player_and_sound_effects_and_survives_failure(self):
        segs = [{"start": 1, "end": 2, "text": "hi", "speaker": "player"}, {"start": 3, "end": 4, "text": "[grunts]", "speaker": "game"},
                {"start": 5, "end": 6, "text": "Well done.", "speaker": "game"}]
        sent = {}

        def fake(system, user, max_tokens=0):
            sent["items"] = json.loads(user)
            return json.dumps([{"i": 2, "to_player": True, "tone": "praise"}])
        with mock.patch("app.sentiment.claude", fake):
            self.assertEqual(sentiment.classify(segs), ([{"i": 2, "to_player": True, "tone": "praise"}], True))
        self.assertEqual([i["i"] for i in sent["items"]], [2])
        with mock.patch("app.sentiment.claude", side_effect=RuntimeError("down")):
            self.assertEqual(sentiment.classify(segs), ([], False))

    def test_the_answer_is_cached_next_to_the_other_work(self):
        d = tempfile.mkdtemp()
        with mock.patch("app.sentiment.claude", return_value=json.dumps([{"i": 0, "to_player": True, "tone": "praise"}])) as c:
            a = sentiment.ensure(d, [{"start": 1, "end": 2, "text": "Well done.", "speaker": "game"}])
            b = sentiment.ensure(d, [{"start": 1, "end": 2, "text": "Well done.", "speaker": "game"}])
        self.assertEqual((a, c.call_count), (b, 1))


# ---------------------------------------------------------------- quiet travel and daydreams
class DaydreamTests(unittest.TestCase):
    def test_only_plain_travel_is_quiet(self):
        scenes = [frame(t) for t in range(0, 30, 3)]
        scenes[5] = frame(15, interactions=[{"kind": "pickup"}])
        scenes[8] = frame(24, activity="shop")
        quiet = beats.travel_quiet_fn(scenes)
        self.assertTrue(quiet(3))
        self.assertFalse(quiet(15))
        self.assertFalse(quiet(12))      # a neighbouring frame has the interaction
        self.assertFalse(quiet(24))
        self.assertFalse(quiet(200))     # nothing is known there

    def test_a_nearby_animal_or_a_death_breaks_the_quiet(self):
        scenes = [frame(t) for t in range(0, 30, 3)]
        scenes[4] = frame(12, animals=[{"species": "wolf"}])
        scenes[7] = frame(21, casualty={"who": "bandit", "evidence": "x"})
        quiet = beats.travel_quiet_fn(scenes)
        self.assertFalse(quiet(12))
        self.assertFalse(quiet(21))
        self.assertTrue(quiet(0))

    def test_scans_from_before_activity_existed_fall_back_on_the_player_state(self):
        old = [{"t": t, "player_state": "riding", "interactions": []} for t in range(0, 30, 3)]
        old[5] = {"t": 15, "player_state": "aiming", "interactions": []}
        quiet = beats.travel_quiet_fn(old)
        self.assertTrue(quiet(3))
        self.assertFalse(quiet(15))

    def test_long_quiet_travel_gets_daydreams_and_nothing_else_gets_filler(self):
        free = [{"start": 0.0, "end": 200.0, "max_words": 25, "kind": "full"}]
        travel = lambda t: t < 100                       # riding for 100 s, then something else for 100 s
        opps, _ = beats.opportunities([], free, 200, 8.0, travel)
        self.assertTrue(opps)
        self.assertTrue(all(o["kind"] == "daydream" and o["kinds"] == ["daydream"] for o in opps))
        self.assertTrue(all(o["t"] < 100 for o in opps))
        self.assertTrue(all("never name a real character" in o["why"] for o in opps))
        none, _ = beats.opportunities([], free, 200, 8.0, lambda t: False)
        self.assertEqual(none, [])

    def test_without_a_quiet_function_the_old_behaviour_is_kept(self):
        free = [{"start": 0.0, "end": 200.0, "max_words": 25, "kind": "full"}]
        opps, _ = beats.opportunities([], free, 200, 8.0)
        self.assertTrue(opps and all(o["kind"] == "ambient" for o in opps))

    def test_daydreams_are_never_closer_than_the_spacing_and_stay_short(self):
        free = [{"start": 0.0, "end": 600.0, "max_words": 25, "kind": "full"}]
        opps, _ = beats.opportunities([], free, 600, 12.0, lambda t: True)
        chosen = beats.select(opps, 12.0, 600)[0]
        ts = [c["t"] for c in chosen]
        self.assertGreater(len(ts), 5)
        self.assertTrue(all(b - a >= 20 - 1e-6 for a, b in zip(ts, ts[1:])))
        self.assertTrue(all(c["max_words"] <= 16 for c in chosen))

    def test_themes_rotate_so_no_two_in_a_row_match(self):
        chosen = [{"id": f"d{i}", "t": i * 30.0, "kind": "daydream", "why": "Quiet travel."} for i in range(4)] + \
                 [{"id": "x", "t": 5.0, "kind": "full", "why": "A fight."}]
        channel.assign_themes(chosen)
        themes = [o["why"].split("Theme for this one: ")[1] for o in sorted(chosen, key=lambda o: o["t"]) if o["kind"] == "daydream"]
        self.assertEqual(len(set(themes)), 4)
        self.assertNotIn("Theme", chosen[4]["why"])


# ---------------------------------------------------------------- the channel lines
FREE = [{"start": 2.0, "end": 14.0, "max_words": 25, "kind": "full"}, {"start": 60.0, "end": 90.0, "max_words": 25, "kind": "full"},
        {"start": 300.0, "end": 330.0, "max_words": 25, "kind": "full"}, {"start": 400.0, "end": 410.0, "max_words": 25, "kind": "full"}]


class ChannelTests(unittest.TestCase):
    def cfg(self, **kw):
        base = {"intro_on": True, "outro_on": True, "catch_on": True, "channel": "", "intro_text": "Welcome to the channel, folks.",
                "outro_text": "Like and subscribe, and see you next time.", "phrases": ["Let's get into it", "Stay tuned", "Here we go"]}
        return base | kw

    def test_defaults_when_nothing_is_saved(self):
        for k in ("intro_on", "outro_on", "catch_on", "channel_name", "intro_text", "outro_text", "catchphrases"):
            db.run("DELETE FROM settings WHERE key=?", (k,))
        c = channel.settings(db.get)
        self.assertTrue(c["intro_on"] and c["outro_on"] and c["catch_on"])
        self.assertIn("channel", c["intro_text"].lower())
        self.assertIn("subscribe", c["outro_text"].lower())
        self.assertGreaterEqual(len(c["phrases"]), 3)

    def test_the_welcome_goes_in_the_first_gap_and_must_say_welcome(self):
        o = channel.intro_opportunity(FREE, 420, self.cfg())
        self.assertEqual((o["id"], o["kind"]), ("intro", "full"))
        self.assertLess(o["t"], 14)
        self.assertGreaterEqual(o["salience"], 90)
        self.assertEqual(o["must_include"], ["welcome", "channel"])
        self.assertIn("only greeting", o["why"])
        self.assertIn("Open World", channel.intro_opportunity(FREE, 420, self.cfg(channel="Open World Diaries"))["why"])

    def test_the_outro_goes_in_the_last_gap_after_the_halfway_point_and_apart_from_the_intro(self):
        intro = channel.intro_opportunity(FREE, 420, self.cfg())
        o = channel.outro_opportunity(FREE, 420, self.cfg(), intro)
        self.assertEqual(o["id"], "outro")
        self.assertGreater(o["t"], 210)
        self.assertEqual(o["must_include"], ["like", "subscribe"])
        self.assertIsNone(channel.outro_opportunity(FREE[:2], 420, self.cfg(), intro))   # no room late in the video

    def test_no_room_means_no_line(self):
        self.assertIsNone(channel.intro_opportunity([{"start": 2.0, "end": 4.0, "max_words": 3, "kind": "micro"}], 420, self.cfg()))
        self.assertIsNone(channel.intro_opportunity([], 420, self.cfg()))

    def test_drifted_lines_use_the_saved_wording_and_missing_ones_are_added(self):
        cfg = self.cfg()
        opps = [channel.intro_opportunity(FREE, 420, cfg), channel.outro_opportunity(FREE, 420, cfg)]
        lines = [{"start": opps[0]["t"], "opp_id": "intro", "line": "Hey, glad you are here."}]
        out = channel.fix_lines(lines, opps, cfg)
        by = {l["opp_id"]: l for l in out}
        self.assertIn("welcome", by["intro"]["line"].lower())          # drifted: replaced
        self.assertIn("subscribe", by["outro"]["line"].lower())        # missing: added
        self.assertEqual([l["start"] for l in out], sorted(l["start"] for l in out))
        good = [{"start": 3.0, "opp_id": "intro", "line": "Welcome to the channel, partners."}]
        self.assertEqual(channel.fix_lines(good, opps[:1], cfg)[0]["line"], "Welcome to the channel, partners.")

    def test_the_saved_wording_is_trimmed_to_fit_the_gap(self):
        tight = {"id": "intro", "max_words": 4, "must_include": ["welcome"]}
        self.assertLessEqual(len(channel.template_for(tight, self.cfg()).split()), 4)

    def test_catchphrases_are_few_spaced_strong_and_never_repeated(self):
        chosen = [{"id": f"b{i}", "t": 20.0 * i, "kind": "full", "salience": 6.0 + (i % 3), "max_words": 12, "why": "A fight."} for i in range(40)]
        chosen.append({"id": "intro", "t": 1.0, "kind": "full", "salience": 99.0, "max_words": 20, "why": "INTRO."})
        n = channel.assign_catchphrases(chosen, ["A", "B", "C", "D", "E"], 800, seed=3)
        tagged = sorted((o for o in chosen if o.get("catch")), key=lambda o: o["t"])
        self.assertEqual(n, len(tagged))
        self.assertLessEqual(n, 800 // 240)
        self.assertEqual(len({o["catch"] for o in tagged}), n)
        self.assertTrue(all(b["t"] - a["t"] >= 75 for a, b in zip(tagged, tagged[1:])))
        self.assertTrue(all(o["salience"] >= 5 and o["id"] != "intro" for o in tagged))
        self.assertEqual(channel.assign_catchphrases(chosen, [], 800), 0)


# ---------------------------------------------------------------- the guard's content rules
def L(text, **kw):
    return {"start": 20.0, "max_duration": 8, "max_words": 25, "persona": "character", "emotion": "calm", "trigger_t": None, "line": text, **kw}


class ContentRuleTests(unittest.TestCase):
    def reason(self, line):
        kept, dropped = guard.enforce([line], [], [{"t": 20, "interactions": [{"kind": "kill"}]}])
        return dropped[0]["reason"] if dropped else None

    def test_greetings_and_subscribe_asks_are_only_for_the_intro_and_outro(self):
        for text in ("Welcome to the channel, friends.", "Don't forget to subscribe.", "Hey everyone, ready?", "Like and subscribe now."):
            self.assertEqual(self.reason(L(text)), "greeting or subscribe line outside the intro and outro", text)
            self.assertIsNone(self.reason(L(text, opp_id="intro" if "elcome" in text or "everyone" in text else "outro")), text)

    def test_talking_to_a_horse_is_not_a_greeting(self):
        self.assertIsNone(self.reason(L("Hey there, girl. Easy now.")))
        self.assertIsNone(self.reason(L("Welcome sight, that town.")))

    def test_daydreams_never_name_a_real_character(self):
        self.assertEqual(self.reason(L("Someday I'll sit with Mary on a porch.", daydream=True)), "daydream names a real character")
        self.assertEqual(self.reason(L("Maybe Dutch was right about one thing.", daydream=True)), "daydream names a real character")
        self.assertIsNone(self.reason(L("Someday a porch, a lamp, someone waiting.", daydream=True)))
        self.assertIsNone(self.reason(L("Dutch is waiting at camp.")))          # outside a daydream, names are fine

    def test_remorse_for_innocents_and_justice_for_outlaws_cannot_be_swapped(self):
        trig = {"trigger_t": 20}
        self.assertEqual(self.reason(L("He had it coming.", beat_kinds="justice_outlaw", emotion="regret", **trig)), "remorse over an armed outlaw")
        self.assertEqual(self.reason(L("Poor fool.", beat_kinds="casualty_innocent", emotion="pride", **trig)), "celebrating an innocent's death")
        self.assertEqual(self.reason(L("Ha.", beat_kinds="casualty_innocent", emotion="amusement", **trig)), "celebrating an innocent's death")
        self.assertEqual(self.reason(L("Wrong man.", beat_kinds="casualty_unclear", emotion="guilt", **trig)), "claims guilt or justice when it is unclear")
        self.assertIsNone(self.reason(L("Didn't have to be this way.", beat_kinds="casualty_innocent", emotion="regret", **trig)))
        self.assertIsNone(self.reason(L("Steady. Done.", beat_kinds="justice_outlaw", emotion="calm", **trig)))
        self.assertIsNone(self.reason(L("Quiet now.", beat_kinds="casualty_unclear", emotion="calm", **trig)))

    def test_silent_thoughts_obey_the_same_rules(self):
        kept, dropped = guard.enforce([L("Welcome to the channel!", silent=True)], [(10.0, 30.0)], [])
        self.assertEqual(dropped[0]["reason"], "greeting or subscribe line outside the intro and outro")


class RepeatTests(unittest.TestCase):
    def test_the_same_thought_said_again_is_dropped_but_a_new_one_is_kept(self):
        prev = [{"line": "Easy now, easy, we're nearly there."}]
        kept, dropped = guard.dedupe([{"start": 1, "line": "Easy now, easy, we are nearly there."},
                                      {"start": 2, "line": "Easy now, easy girl, steady."},      # same first three words
                                      {"start": 3, "line": "That rifle kicks like a mule."}], prev)
        self.assertEqual([k["line"] for k in kept], ["That rifle kicks like a mule."])
        self.assertEqual({d["reason"] for d in dropped}, {"repeats an earlier line"})

    def test_lines_within_one_batch_are_compared_too_and_intro_is_exempt(self):
        kept, dropped = guard.dedupe([{"start": 1, "line": "Look at that view out here."}, {"start": 2, "line": "Look at that view out there."}], [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(guard.dedupe([{"start": 1, "opp_id": "intro", "line": "Welcome to the channel, folks."}], [{"line": "Welcome to the channel, folks."}])[0]), 1)


# ---------------------------------------------------------------- continuity across the chunks of one video
class ContinuityTests(unittest.TestCase):
    def opp(self, i, t):
        return {"id": f"b{i}", "t": float(t), "beat_t": float(t), "salience": 7, "kinds": ["hunting"], "why": "hunting", "max_words": 12, "max_duration": 6, "kind": "full"}

    def test_every_chunk_is_told_it_is_one_continuous_video_and_what_was_already_said(self):
        asked = []

        def fake(system, user, max_tokens=8000):
            u = json.loads(user)
            asked.append(u)
            return json.dumps([{"opportunity": o["id"], "persona": "character", "emotion": "calm", "line": f"Line number {o['id']} about nothing."} for o in u["opportunities"]])
        opps = [self.opp(1, 100), self.opp(2, 450)]
        scenes = [{"t": t, "interactions": [{"kind": "kill"}]} for t in range(90, 460, 3)]
        with mock.patch("app.writer.claude", fake):
            writer.write_lines(scenes, [], [], "SYS", 600, opps)
        self.assertEqual([a["chunk"]["index"] for a in asked], [0, 1])
        self.assertTrue(asked[0]["chunk"]["first"])
        self.assertFalse(asked[1]["chunk"]["first"])
        self.assertIn("Never greet", asked[1]["chunk"]["note"])
        self.assertEqual(asked[0]["already_said"]["openers"], [])
        self.assertEqual(asked[1]["already_said"]["openers"], ["Line number b1 about"])
        self.assertIn("Line number b1 about nothing.", asked[1]["earlier_lines"])

    def test_a_line_repeating_an_earlier_chunk_is_dropped(self):
        def fake(system, user, max_tokens=8000):
            u = json.loads(user)
            return json.dumps([{"opportunity": o["id"], "persona": "character", "emotion": "calm", "line": "Easy now, easy, nearly there."} for o in u["opportunities"]])
        scenes = [{"t": t, "interactions": [{"kind": "kill"}]} for t in range(90, 460, 3)]
        with mock.patch("app.writer.claude", fake):
            kept, dropped = writer.write_lines(scenes, [], [], "SYS", 600, [self.opp(1, 100), self.opp(2, 450)])
        self.assertEqual(len(kept), 1)
        self.assertEqual([d["reason"] for d in dropped], ["repeats an earlier line"])

    def test_game_knowledge_is_in_the_prompt_for_red_dead_only_and_can_be_switched_off(self):
        base = {"persona": "character", "pack": "western", "personality": "balanced", "knowledge": True}
        rdr = writer.build_system("S", base | {"game": "Red Dead Redemption 2"})
        self.assertIn("GAME KNOWLEDGE: Red Dead Redemption 2", rdr)
        self.assertIn("remorse", rdr)
        self.assertNotIn("GAME KNOWLEDGE", writer.build_system("S", base | {"game": "Forza Horizon 5"}))
        self.assertNotIn("GAME KNOWLEDGE", writer.build_system("S", base | {"game": "Red Dead Redemption 2", "knowledge": False}))
        self.assertNotIn("{KNOWLEDGE}", rdr)

    def test_the_new_rules_are_in_the_prompt(self):
        system = writer.build_system("S", {"game": "g", "persona": "character", "pack": "western", "personality": "balanced"})
        for needle in ("GROUNDING", "NO GREETINGS", "TRAVEL THOUGHTS", "CONSCIENCE", "SPEECH TO ARTHUR", "CATCHPHRASES", "continuous piece"):
            self.assertIn(needle, system)

    def test_knowledge_lookup(self):
        self.assertEqual(knowledge.name_for_game("RED DEAD REDEMPTION 2 walkthrough"), "Red Dead Redemption 2")
        self.assertIsNone(knowledge.name_for_game("GTA V"))
        self.assertEqual(knowledge.for_game(None), "")


# ---------------------------------------------------------------- a whole video, start to finish, with a pretend writer
class SimulationTests(unittest.TestCase):
    """A made-up 8-minute ride with a cutscene, a shop, a wolf, two deaths and an angry townsman, planned and written
    end to end (only the language model is pretended)."""

    def world(self):
        scenes = [frame(t) for t in range(0, 480, 3)]
        def at(t, **kw): scenes[t // 3] = frame(t, **kw)
        at(120, activity="shop", shop={"action": "buying", "item": "ammunition"})
        at(180, animals=[{"species": "wolf", "predator": True, "size": "medium", "behavior": "approaching"}])
        at(240, activity="combat", casualty={"who": "civilian", "evidence": "honor icon dropped"})
        at(300, activity="combat", casualty={"who": "bandit", "evidence": "drawn weapon"})
        segs = [{"start": 200, "end": 204, "text": "Get out of my town, Morgan!", "speaker": "game"},
                {"start": 340, "end": 341, "text": "Thanks, mister.", "speaker": "game"}]
        tones = [{"i": 0, "to_player": True, "tone": "threat"}, {"i": 1, "to_player": True, "tone": "thanks"}]
        return scenes, segs, tones

    def run_plan(self, quiet_on=True):
        scenes, segs, tones = self.world()
        length = 480
        cuts = [[400.0, 440.0]]
        blocks = [[398.8, 441.2]]
        speech = guard.speech_windows(segs)
        import app.cutscenes as cs
        windows = cs.merge([list(w) for w in speech] + blocks)
        all_beats = sorted(beats.find_beats(scenes) + beats.speech_beats(segs, tones), key=lambda b: b["t"])
        all_beats = [b for b in all_beats if not cs.overlaps(blocks, b["t"], b["t"] + 0.01)]
        free = gaps.free_gaps(windows, length)
        cfg = {"intro_on": True, "outro_on": True, "catch_on": True, "channel": "", "intro_text": channel.DEFAULTS["intro_text"],
               "outro_text": channel.DEFAULTS["outro_text"], "phrases": ["Let's get into it", "Stay tuned"]}
        intro = channel.intro_opportunity(free, length, cfg)
        outro = channel.outro_opportunity(free, length, cfg, intro)
        quiet = beats.travel_quiet_fn(scenes) if quiet_on else (lambda t: False)
        chosen, _, _ = pipeline.plan_commentary(all_beats, free, cs.subtract_ranges(speech, blocks), length, 8.0, None, True,
                                                extra=[o for o in (intro, outro) if o], quiet_fn=quiet)
        channel.assign_themes(chosen)
        channel.assign_catchphrases(chosen, cfg["phrases"], length, 1)
        return scenes, segs, windows, blocks, chosen, cfg

    WORDS = ("river dust rifle saddle lantern copper thunder creek hollow pine ember canyon whistle gravel meadow shadow "
             "barrel harvest cinder granite willow anvil prairie timber ridge clover tallow bramble flint ledger kettle "
             "orchard saddlebag horizon furrow gully lariat mesa thicket wagon yarrow borrow hazel quarry spindle").split()

    def unique_line(self, key, prefix=""):
        import random
        r = random.Random(key)
        return (prefix + " ".join(r.sample(self.WORDS, 6))).strip().capitalize() + "."

    def pretend_writer(self, bad=False):
        def fake(system, user, max_tokens=8000):
            u = json.loads(user)
            out = []
            for o in u["opportunities"]:
                text = {"intro": "Welcome to the channel, partners. Let's ride.", "outro": "That's the trail. Like and subscribe, partners."}.get(o["id"])
                if not text:
                    text = self.unique_line(o["id"])           # every line different, as real writing would be
                    if bad and o["id"] == u["opportunities"][-1]["id"]:
                        text = "Welcome to the channel again, everyone!"
                out.append({"opportunity": o["id"], "persona": "character", "emotion": "calm", "line": text})
            return json.dumps(out)
        return fake

    def test_the_plan_and_the_words(self):
        scenes, segs, windows, blocks, chosen, cfg = self.run_plan()
        ids = [o["id"] for o in chosen]
        self.assertEqual((ids.count("intro"), ids.count("outro")), (1, 1))
        kinds = {k for o in chosen for k in o["kinds"]}
        self.assertTrue({"predator", "casualty_innocent", "justice_outlaw", "npc_threat"} <= kinds, kinds)
        self.assertIn("daydream", kinds)
        quiet = beats.travel_quiet_fn(scenes)
        for o in chosen:
            if o["kind"] == "daydream":
                self.assertTrue(quiet(o["t"]), o)
            self.assertFalse(cutscene_hit := any(a <= o["t"] <= b for a, b in blocks), o)
        with mock.patch("app.writer.claude", self.pretend_writer()):
            kept, dropped = writer.write_lines(scenes, segs, windows, "SYS", 480, chosen, blocks=blocks)
        kept = channel.fix_lines(kept, chosen, cfg)
        texts = [k["line"] for k in kept]
        self.assertEqual(sum("welcome to the channel" in t.lower() for t in texts), 1)
        self.assertEqual(sum("subscribe" in t.lower() for t in texts), 1)
        self.assertTrue(all(not guard.CANON.search(k["line"]) for k in kept if k.get("daydream")))
        self.assertGreater(len([k for k in kept if k.get("daydream")]), 2)
        for k in kept:
            self.assertFalse(any(a < k["start"] + 1 and b > k["start"] for a, b in blocks), k)
        starts = sorted(k["start"] for k in kept)
        self.assertEqual(starts[0], min(o["t"] for o in chosen if o["id"] == "intro"))

    def test_a_stray_greeting_in_the_middle_is_dropped_by_the_guard(self):
        scenes, segs, windows, blocks, chosen, cfg = self.run_plan()
        with mock.patch("app.writer.claude", self.pretend_writer(bad=True)):
            kept, dropped = writer.write_lines(scenes, segs, windows, "SYS", 480, chosen, blocks=blocks)
        self.assertIn("greeting or subscribe line outside the intro and outro", [d["reason"] for d in dropped])
        self.assertEqual(sum("welcome to the channel" in k["line"].lower() for k in kept), 1)

    def test_with_daydreams_off_the_quiet_stretches_stay_silent(self):
        scenes, segs, windows, blocks, chosen, cfg = self.run_plan(quiet_on=False)
        self.assertFalse([o for o in chosen if o["kind"] == "daydream"])
        self.assertFalse([o for o in chosen if o["kind"] == "ambient"])      # no filler anywhere
        self.assertTrue({o["id"] for o in chosen} >= {"intro", "outro"})

    def test_a_real_moment_is_never_dropped_for_being_in_a_quiet_stretch(self):
        scenes, segs, windows, blocks, chosen, cfg = self.run_plan()
        times = [o["t"] for o in chosen]
        for moment in (120, 180, 240, 300):
            self.assertTrue(any(abs(t - moment) < 14 for t in times), moment)


# ---------------------------------------------------------------- settings and the page
class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_defaults_in_state(self):
        for k in ("daydream", "knowledge_on", "moral_mode", "intro_on", "outro_on", "catch_on"):
            db.run("DELETE FROM settings WHERE key=?", (k,))
        s = self.c.get("/api/state").json()
        self.assertTrue(s["daydream"] and s["knowledge_on"])
        self.assertEqual(s["moral_mode"], "both")
        self.assertTrue(s["channel"]["intro_on"] and s["channel"]["outro_on"] and s["channel"]["catch_on"])
        self.assertIn("welcome", s["channel"]["intro_text"].lower())

    def test_choices_are_saved_cleaned_and_validated(self):
        r = self.c.post("/api/settings", json={"daydream": False, "moral_mode": "cues", "channel_name": "  Open   World  Diaries ",
                                               "catchphrases": " Let's go \n\n  Stay tuned  \n" + "x" * 200, "intro_on": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(pipeline.daydream_on())
        self.assertEqual(pipeline.moral_mode(), "cues")
        c = channel.settings(db.get)
        self.assertEqual(c["channel"], "Open World Diaries")
        self.assertEqual(c["phrases"][:2], ["Let's go", "Stay tuned"])
        self.assertLessEqual(max(len(p) for p in c["phrases"]), 80)
        self.assertFalse(c["intro_on"])
        self.assertEqual(self.c.post("/api/settings", json={"moral_mode": "always"}).status_code, 400)
        self.assertEqual(pipeline.moral_mode(), "cues")
        self.c.post("/api/settings", json={"daydream": True, "moral_mode": "both", "channel_name": "", "catchphrases": "", "intro_on": True})

    def test_the_video_card_knows_its_knowledge_pack_and_whether_the_scan_is_old(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,game,created) VALUES(?,?,?,?,?)",
                     (f"k{time.time_ns()}", "x.mp4", "ready", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "scenes.json").write_text(json.dumps([{"t": 0, "player_state": "riding"}]))
        row = next(v for v in self.c.get("/api/state").json()["videos"] if v["id"] == vid)
        self.assertEqual((row["knowledge_name"], row["scan_has_actions"]), ("Red Dead Redemption 2", False))
        (work / "scenes.json").write_text(json.dumps([{"t": 0, "activity": "travel"}]))
        row = next(v for v in self.c.get("/api/state").json()["videos"] if v["id"] == vid)
        self.assertTrue(row["scan_has_actions"])

    def test_scanning_again_clears_the_old_scan_and_starts_work(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"s{time.time_ns()}", "x.mp4", "ready", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "scenes.json").write_text("[]")
        (work / "scenes.partial.json").write_text("[]")
        with mock.patch("app.server.run_bg") as bg:
            self.assertEqual(self.c.post(f"/api/videos/{vid}/rescan").status_code, 200)
        self.assertFalse((work / "scenes.json").exists() or (work / "scenes.partial.json").exists())
        self.assertEqual(db.one("SELECT status FROM videos WHERE id=?", (vid,))["status"], "watching")
        bg.assert_called_once()
        busy = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"b{time.time_ns()}", "y.mp4", "writing", time.time())).lastrowid
        self.assertEqual(self.c.post(f"/api/videos/{busy}/rescan").status_code, 409)

    def test_video_options_carry_the_knowledge_switch(self):
        db.put("knowledge_on", "0")
        self.assertFalse(pipeline.video_opts({"pack": "western"})["knowledge"])
        db.put("knowledge_on", "1")
        self.assertTrue(pipeline.video_opts({"pack": "western"})["knowledge"])

    def test_the_scan_prompt_asks_for_the_new_details(self):
        text = (Path(__file__).resolve().parent.parent / "prompts" / "scene.md").read_text()
        for field in ('"activity"', '"animals"', '"npcs"', '"shop"', '"casualty"'):
            self.assertIn(field, text)
        self.assertIn("never guess that a person was innocent or a bandit without evidence", text)
        self.assertTrue(text.format(game="g", hints="h", times=[0]))


class TranscriptLengthTests(unittest.TestCase):
    def test_times_past_the_end_of_the_video_are_dropped_and_overruns_trimmed(self):
        from app import transcript
        segs = [{"start": 10, "end": 12, "text": "ok"}, {"start": 495, "end": 510, "text": "trimmed"}, {"start": 526, "end": 530, "text": "invented"},
                {"start": 499.2, "end": 501, "text": "right at the end"}]
        out = transcript.within(segs, 499.4)
        self.assertEqual([s["text"] for s in out], ["ok", "trimmed"])
        self.assertEqual(out[1]["end"], 499.4)
        self.assertEqual(transcript.within([], 100), [])


if __name__ == "__main__":
    unittest.main()
