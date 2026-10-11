import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import gaps, guard, storymode  # noqa: E402
from app.config import PROMPTS  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "player_state": "riding",
            "mount_or_vehicle": "brown horse", "time_weather": "day, clear", "location": "Heartlands", **kw}


def scenes(**kw):
    return [frame(t, **kw) for t in range(0, 120, 3)]


def judge(text, scn=None, opp="s1", kinds="daydream,travel_story", start=30.0):
    ln = {"start": start, "line": text, "persona": "character", "emotion": "calm", "max_words": 22, "max_duration": 8.0, "opp_id": opp,
          "beat_kinds": kinds, "silent": False}
    keep, drop = guard.enforce([ln], [], scn if scn is not None else scenes(), min_gap=2.5)
    return None if keep else drop[0]["reason"]


class WhatIsHereRightNowTests(unittest.TestCase):
    def test_a_vehicle_that_is_not_in_the_scene_is_dropped(self):
        self.assertEqual(judge("Some days the old me's one car ahead."), "mentions a car the scene does not show")
        self.assertEqual(judge("Engine's humming, nothing to worry about."), "mentions a car the scene does not show")
        self.assertEqual(judge("The boat rocks under me."), "mentions a boat the scene does not show")
        self.assertEqual(judge("Wagon wheels creaking behind me."), "mentions a wagon the scene does not show")
        self.assertEqual(judge("That train whistle again."), "mentions a train the scene does not show")
        self.assertEqual(judge("Helicopter overhead, probably nothing."), "mentions an aircraft the scene does not show")

    def test_the_same_word_is_fine_when_the_scene_shows_it(self):
        train = scenes(notable_details=["a steam train running beside the track"])
        self.assertIsNone(judge("That train whistle again.", train))
        self.assertIsNone(judge("Some days the old me's one car ahead.", train))                      # a car of the train
        wagon = scenes(nearby=["a wagon with two white horses"])
        self.assertIsNone(judge("Wagon wheels creaking ahead.", wagon))
        driving = scenes(player_state="driving", mount_or_vehicle="red coupe")
        self.assertIsNone(judge("Engine's humming, nothing to worry about.", [{**f, "notable_details": ["engine sound"]} for f in driving]))

    def test_riding_means_a_horse_may_be_mentioned_and_walking_means_it_may_not(self):
        self.assertIsNone(judge("She never flinches at bridges."))
        self.assertIsNone(judge("Reins feel looser every mile."))                                       # riding: a mount is there
        walking = scenes(player_state="walking", mount_or_vehicle="none")
        self.assertEqual(judge("Reins feel looser every mile.", walking), "mentions a horse the scene does not show")

    def test_the_sky_must_match(self):
        self.assertEqual(judge("The moon's out early tonight."), "mentions night the scene does not show")
        self.assertEqual(judge("Rain on the hat brim again."), "mentions rain the scene does not show")
        self.assertEqual(judge("Snow's coming, I can smell it."), "mentions snow the scene does not show")
        self.assertIsNone(judge("That sunset's a long way off."))                                       # dawn, dusk and day shade into each other: not judged
        night = scenes(time_weather="night, clear")
        self.assertIsNone(judge("The moon's out early tonight.", night))
        rain = scenes(time_weather="day, rain")
        self.assertIsNone(judge("Rain on the hat brim again.", rain))
        self.assertIsNone(judge("Cold in the saddle.", scenes(time_weather="day, snow")))

    def test_memories_hopes_and_the_past_are_free_to_be_about_anything(self):
        for text in ("Used to ride a train just like that.", "Once spent a whole night under the moon, just thinking.",
                     "I'd trade it all for a boat and a quiet bay.", "Someday I'll own a car, maybe.", "Years ago it rained for a week straight.",
                     "Wish I had a wagon and a field to go with it.", "Sleep past dawn, no iron under the pillow.", "Could use a boat right about now."):
            self.assertIsNone(judge(text), text)
        self.assertIsNone(judge("Used to ride a train just like that.", kinds="daydream,travel_memory"))

    def test_the_welcome_the_outro_and_unscanned_moments_are_not_judged(self):
        self.assertIsNone(judge("Welcome to the channel, folks. Moon's up.", opp="intro", kinds="intro"))
        self.assertIsNone(judge("Engine's humming along.", scn=[]))
        self.assertIsNone(judge("Engine's humming along.", scn=[frame(500)]))                               # no scan note near this moment

    def test_a_plain_feeling_with_no_physical_detail_always_passes(self):
        for text in ("Wonder if a man like me ever gets to stay put somewhere.", "Tried honest once. Almost.", "Hm. Not today."):
            self.assertIsNone(judge(text), text)

    def test_ordinary_reactions_follow_the_same_rule(self):
        self.assertEqual(judge("That truck's in a hurry.", kinds="big_animal"), "mentions a car the scene does not show")
        self.assertIsNone(judge("That horse is in a hurry.", kinds="big_animal"))

    def test_silent_thoughts_follow_it_too(self):
        ln = {"start": 30.0, "line": "That engine again.", "persona": "character", "emotion": "calm", "max_words": 12, "max_duration": 6.0,
              "opp_id": "t1", "beat_kinds": "silent", "silent": True}
        keep, drop = guard.enforce([ln], [], scenes(), min_gap=2.5)
        self.assertEqual(keep, [])
        self.assertEqual(drop[0]["reason"], "mentions a car the scene does not show")


class TheWriterIsToldTests(unittest.TestCase):
    def test_the_rule_is_in_the_locked_rules(self):
        text = (PROMPTS / "guardrails.md").read_text()
        self.assertIn("20. MATCH THE SCENE", text)
        self.assertIn("If he is on a horse, there is no car", text)
        self.assertIn("Memories and hopes may be about anything, but say so", text)

    def test_every_inner_story_thought_is_told_what_is_true_right_now(self):
        notes = [frame(t, time_weather="dawn, fog", location="Heartlands") for t in range(0, 120, 3)]
        p = storymode.plan(notes, gaps.free_gaps([], 120), 120, 6, ["family"])
        for o in p:
            self.assertIn("Right now the scan shows: riding brown horse; dawn, fog; Heartlands.", o["why"])
            self.assertIn("Do not say anything about the present that contradicts that.", o["why"])

    def test_a_thought_with_nothing_known_is_not_given_an_empty_claim(self):
        bare = [{"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel"} for t in range(0, 120, 3)]
        for o in storymode.plan(bare, gaps.free_gaps([], 120), 120, 6, ["family"]):
            self.assertNotIn("Right now the scan shows", o["why"])


if __name__ == "__main__":
    unittest.main()
