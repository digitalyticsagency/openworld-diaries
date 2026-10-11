import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import beats, db, guard, knowledge, pipeline, styles, viral, writer  # noqa: E402
from app.config import PROMPTS  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", **kw}


class GtaStyleTests(unittest.TestCase):
    def test_ten_gta_styles_in_the_crime_family(self):
        gta = {k: p for k, p in styles.PACKS.items() if k.startswith("gta_")}
        self.assertEqual(len(gta), 10)
        for k, p in gta.items():
            self.assertEqual(p["family"], "crime", k)
            self.assertIn("GTA", p["text"], k)
            self.assertTrue(p["hints"] and p["games"], k)
            self.assertNotEqual(p["voices"]["character"], p["voices"]["companion"], k)
        self.assertEqual(len({p["name"] for p in gta.values()}), 10)
        self.assertEqual(len(styles.PACKS), 30)

    def test_a_gta_style_belongs_with_the_crime_family_and_not_with_western(self):
        self.assertTrue(styles.same_family("gta_chase", "crime"))
        self.assertFalse(styles.same_family("gta_chase", "western"))
        self.assertEqual(styles.pack_for_game("Grand Theft Auto V"), "crime")
        self.assertEqual(styles.pack_for_game("GTA VI"), "crime")

    def test_every_gta_style_reaches_the_scan_prompt_and_the_writer(self):
        tpl = (PROMPTS / "scene.md").read_text()
        for k, p in styles.PACKS.items():
            if k.startswith("gta_"):
                self.assertIn(p["hints"], tpl.format(game="GTA V", hints=p["hints"], times=[0]))
                system = writer.build_system("S", {"game": "GTA V", "persona": "character", "pack": k, "personality": "balanced"})
                self.assertIn(p["text"], system)

    def test_the_gta6_style_claims_nothing_beyond_the_screen(self):
        text = styles.PACKS["gta_sunshine"]["text"]
        self.assertIn("never claim to know the story", text)


class GtaKnowledgeTests(unittest.TestCase):
    def test_the_right_pack_for_each_title(self):
        self.assertEqual(knowledge.name_for_game("Grand Theft Auto V"), "Grand Theft Auto V")
        self.assertEqual(knowledge.name_for_game("GTA 5 free roam"), "Grand Theft Auto V")
        self.assertEqual(knowledge.name_for_game("Grand Theft Auto VI"), "Grand Theft Auto VI")
        self.assertEqual(knowledge.name_for_game("GTA 6 gameplay"), "Grand Theft Auto VI")
        self.assertEqual(knowledge.name_for_game("Red Dead Redemption 2"), "Red Dead Redemption 2")
        self.assertIsNone(knowledge.name_for_game("Skyrim"))

    def test_the_gta6_pack_says_only_what_the_screen_shows(self):
        text = knowledge.for_game("GTA VI")
        self.assertIn("do not state anything about the story", text)
        self.assertNotIn("Arthur", text)

    def test_gta5_does_not_pretend_to_know_who_is_playing(self):
        text = knowledge.for_game("GTA V")
        self.assertIn("name whoever the scene notes", text)
        self.assertIn("Never glorify real-world harm", text)

    def test_the_red_dead_character_and_notes_do_not_follow_another_game(self):
        db.put("character", "Arthur Morgan")
        db.put("voice_notes", "Weary outlaw, loyal, brave, rescuing, winning")
        rd = pipeline.video_opts({"game": "Red Dead Redemption 2", "pack": "western"})
        self.assertEqual((rd["character"], rd["voice_notes"]), ("Arthur Morgan", "Weary outlaw, loyal, brave, rescuing, winning"))
        g5 = pipeline.video_opts({"game": "Grand Theft Auto V", "pack": "gta_heist"})
        self.assertEqual(g5["character"], "the player's current character")
        self.assertIn("Streetwise", g5["voice_notes"])
        self.assertNotIn("outlaw", g5["voice_notes"].lower())
        skyrim = pipeline.video_opts({"game": "Skyrim", "pack": "fantasy"})
        self.assertEqual(skyrim["character"], "Arthur Morgan")                 # no pack: the owner's own choice is left alone

    def test_a_character_the_owner_chose_is_never_replaced(self):
        db.put("character", "Franklin")
        db.put("voice_notes", "Calm and sharp")
        o = pipeline.video_opts({"game": "Grand Theft Auto V", "pack": "gta_street"})
        self.assertEqual((o["character"], o["voice_notes"]), ("Franklin", "Calm and sharp"))
        db.put("character", "Arthur Morgan")
        db.put("voice_notes", "Weary outlaw, loyal, brave, rescuing, winning")


class WantedLevelTests(unittest.TestCase):
    def test_stars_make_a_moment_and_a_rise_makes_it_stronger(self):
        calm = beats.scene_score(frame(10))[0]
        two = beats.scene_score(frame(10, wanted_level=2))
        four = beats.scene_score(frame(10, wanted_level=4))
        self.assertGreater(two[0], calm)
        self.assertGreater(four[0], two[0])
        self.assertIn("wanted", two[1])
        rose = beats.scene_score(frame(13, wanted_level=3), frame(10, wanted_level=1))
        steady = beats.scene_score(frame(13, wanted_level=3), frame(10, wanted_level=3))
        self.assertGreater(rose[0], steady[0])
        self.assertIn("just rose", " ".join(rose[2]))

    def test_one_star_or_unreadable_values_are_not_a_beat(self):
        self.assertEqual(beats.scene_score(frame(10, wanted_level=1))[0], 0.0)
        for junk in ("unknown", None, "", "five", 99):
            self.assertLessEqual(beats.scene_score(frame(10, wanted_level=junk))[0], beats.scene_score(frame(10, wanted_level=5))[0])

    def test_a_chase_is_never_quiet_travel_and_it_grounds_fear(self):
        scenes = [frame(t, player_state="driving") for t in range(0, 30, 3)]
        scenes[4] = frame(12, player_state="driving", wanted_level=3)
        quiet = beats.travel_quiet_fn(scenes)
        self.assertTrue(quiet(0))
        self.assertFalse(quiet(12))
        self.assertTrue(guard.grounded({"emotion": "fear", "trigger_t": 12}, scenes))
        self.assertFalse(guard.grounded({"emotion": "fear", "trigger_t": 0}, scenes))

    def test_the_scan_prompt_asks_for_the_wanted_level(self):
        tpl = (PROMPTS / "scene.md").read_text()
        self.assertIn('"wanted_level"', tpl)
        tpl.format(game="g", hints="h", times=[0])


class NoRedDeadLeftoversTests(unittest.TestCase):
    def test_nothing_the_planner_writes_names_arthur(self):
        text = beats.casualty_beat({"who": "civilian", "evidence": "x"})[2]
        self.assertNotIn("Arthur", text)
        self.assertNotIn("Arthur", beats.speech_beats([{"start": 5, "end": 6, "text": "Nice work.", "speaker": "game"}],
                                                      [{"i": 0, "to_player": True, "tone": "praise"}])[0]["why"])

    def test_canon_names_from_gta_are_blocked_in_daydreams_too(self):
        self.assertEqual(guard.content_reason({"daydream": True, "beat_kinds": "daydream"}, "Someday Franklin will retire."), "daydream names a real character")

    def test_driving_chapters_are_named_driving(self):
        scan = [frame(t, player_state="driving", mount_or_vehicle="red coupe") for t in range(0, 100, 3)] + \
               [frame(t, activity="combat") for t in range(100, 200, 3)] + [frame(t, activity="shop") for t in range(200, 300, 3)]
        self.assertEqual([c["title"] for c in viral.chapters(scan, 300)], ["Driving", "Gunfight", "Shopping"])


if __name__ == "__main__":
    unittest.main()
