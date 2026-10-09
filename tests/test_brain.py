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

from app import beats, db, detect, evolve, gaps, guard, styles, writer  # noqa: E402
from app.server import app  # noqa: E402


class GapTests(unittest.TestCase):
    def test_player_voice_gets_a_wider_buffer_than_game_dialogue(self):
        game = guard.speech_windows([{"start": 10, "end": 12, "speaker": "game"}])
        player = guard.speech_windows([{"start": 10, "end": 12, "speaker": "player"}])
        self.assertEqual(game, [(9.4, 12.6)])
        self.assertEqual(player, [(8.5, 13.5)])
        self.assertEqual(guard.speech_windows([{"start": 10, "end": 12, "speaker": "game"}], buffer=2), [(8.0, 14.0)])

    def test_gaps_are_sized_in_words_and_tiny_ones_are_skipped(self):
        g = gaps.free_gaps([(4.0, 9.0), (10.0, 40.0)], 60)
        # 0-4 is a micro gap, 9-10 is too short, 40-60 is a full gap
        self.assertEqual([(x["start"], x["end"], x["kind"]) for x in g], [(0.0, 4.0, "micro"), (40.0, 60, "full")])
        self.assertEqual(g[1]["max_words"], 25)
        self.assertLess(g[0]["max_words"], 12)

    def test_locate_inside_gap_after_gap_and_nowhere(self):
        g = [{"start": 20.0, "end": 40.0, "max_words": 25, "kind": "full"},
             {"start": 60.0, "end": 66.0, "max_words": 8, "kind": "micro"}]
        start, gap = gaps.locate(g, 25.0)
        self.assertAlmostEqual(start, 25.4)
        self.assertEqual(gap["start"], 20.0)
        start, gap = gaps.locate(g, 55.0)  # inside speech: reaction lands in the next gap
        self.assertAlmostEqual(start, 60.35)
        self.assertIsNone(gaps.locate(g, 42.0, reaction=5))  # the next gap is too far away


def scene(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "location": "town", **kw}


class BeatTests(unittest.TestCase):
    def test_scores_rank_events_sensibly(self):
        death = beats.scene_score(scene(0, interactions=[{"kind": "death", "intensity": 3}]))[0]
        pickup = beats.scene_score(scene(0, interactions=[{"kind": "pickup", "intensity": 1}]))[0]
        nothing = beats.scene_score(scene(0))[0]
        self.assertGreater(death, pickup)
        self.assertGreater(pickup, nothing)
        self.assertLessEqual(death, 10)
        moral = beats.scene_score(scene(0, moral_events=[{"kind": "spare", "who": "x"}]))
        self.assertIn("moral", moral[1])
        self.assertGreater(moral[0], 3)

    def test_learned_kind_weight_changes_the_score(self):
        s = scene(0, interactions=[{"kind": "loot", "intensity": 1}])
        base = beats.scene_score(s)[0]
        self.assertGreater(beats.scene_score(s, kw={"loot": 1.6})[0], base)
        self.assertLess(beats.scene_score(s, kw={"loot": 0.6})[0], base)

    def test_new_place_needs_a_real_change_not_rewording(self):
        a = scene(0, location="Saint Denis main street")
        self.assertNotIn("new_place", beats.scene_score(scene(3, location="main street, Saint Denis"), a)[1])
        self.assertIn("new_place", beats.scene_score(scene(3, location="swamp bayou cabin"), a)[1])

    def test_only_local_peaks_become_beats(self):
        scenes = [scene(t) for t in range(0, 60, 3)]
        scenes[5]["interactions"] = [{"kind": "kill", "intensity": 3}]
        scenes[6]["interactions"] = [{"kind": "damage_taken", "intensity": 1}]
        found = beats.find_beats(scenes)
        self.assertEqual([b["t"] for b in found], [15])

    def test_opportunities_and_blocked_beats(self):
        g = [{"start": 0.0, "end": 30.0, "max_words": 25, "kind": "full"}]
        b = [{"t": 10, "salience": 6, "kinds": ["kill"], "why": "x"}, {"t": 55, "salience": 7, "kinds": ["kill"], "why": "y"}]
        opps, blocked = beats.opportunities(b, g, 60)
        self.assertEqual([o["beat_t"] for o in opps if o["kind"] != "ambient"], [10])
        self.assertEqual([x["t"] for x in blocked], [55])
        self.assertTrue(all(o["t"] + o["max_duration"] <= 30 for o in opps))

    def test_ambient_spots_only_in_long_gaps(self):
        short = [{"start": 0.0, "end": 12.0, "max_words": 25, "kind": "full"}]
        long_ = [{"start": 0.0, "end": 80.0, "max_words": 25, "kind": "full"}]
        self.assertEqual(beats.opportunities([], short, 12)[0], [])
        amb = beats.opportunities([], long_, 80)[0]
        self.assertGreaterEqual(len(amb), 2)
        self.assertTrue(all(o["kind"] == "ambient" and o["salience"] > 3 for o in amb))

    def test_eagerness_controls_how_much_is_spoken(self):
        opps = [{"id": f"b{i}", "t": 10 + 20 * i, "beat_t": 10 + 20 * i, "salience": 3 + i * 0.7, "kinds": ["x"],
                 "why": "", "max_words": 20, "max_duration": 8, "kind": "full"} for i in range(10)]
        counts = [len(beats.select(opps, e, 220)[0]) for e in (1, 4, 7, 10)]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_selection_respects_spacing_and_floor(self):
        opps = [{"id": f"b{i}", "t": 10 + i * 2.0, "beat_t": None, "salience": 9, "kinds": ["x"],
                 "why": "", "max_words": 10, "max_duration": 5, "kind": "full"} for i in range(8)]
        chosen, _ = beats.select(opps, 10, 40)
        ts = [c["t"] for c in chosen]
        self.assertTrue(all(b - a >= 2.5 for a, b in zip(ts, ts[1:])))

    def test_strong_moments_may_follow_each_other_closer(self):
        weak = [{"id": "a", "t": 10, "salience": 5.0}, {"id": "b", "t": 15, "salience": 5.0}]
        strong = [{"id": "a", "t": 10, "salience": 9.5}, {"id": "b", "t": 15, "salience": 9.5}]
        for o in weak + strong:
            o.update(beat_t=None, kinds=["x"], why="", max_words=10, max_duration=5, kind="full")
        self.assertEqual(len(beats.select(weak, 8, 30)[0]), 1)
        self.assertEqual(len(beats.select(strong, 8, 30)[0]), 2)

    def test_droughts_are_broken_by_the_best_available_spot(self):
        opps = [{"id": "x", "t": 5, "salience": 9}, {"id": "mid", "t": 100, "salience": 3.6}, {"id": "y", "t": 195, "salience": 9}]
        for o in opps:
            o.update(beat_t=None, kinds=["x"], why="", max_words=10, max_duration=5, kind="full")
        ids = [c["id"] for c in beats.select(opps, 6, 200)[0]]
        self.assertEqual(ids, ["x", "mid", "y"])


class WriterPlanTests(unittest.TestCase):
    def test_fill_missed_retries_only_strong_unanswered_moments(self):
        opps = [{"id": "s1", "t": 20, "beat_t": 20, "salience": 8, "kinds": ["kill"], "why": "w", "max_words": 10, "max_duration": 5, "kind": "full"},
                {"id": "s2", "t": 50, "beat_t": 50, "salience": 4, "kinds": ["pickup"], "why": "w", "max_words": 10, "max_duration": 5, "kind": "full"}]
        scenes = [scene(20, interactions=[{"kind": "kill"}]), scene(50, interactions=[{"kind": "pickup"}])]
        asked = {}

        def fake(system, user, max_tokens=8000):
            asked.update(json.loads(user))
            return json.dumps([{"opportunity": "s1", "persona": "player", "emotion": "calm", "line": "Quiet now."}])
        with mock.patch("app.writer.claude", fake):
            kept, n = writer.fill_missed(scenes, [], [], "SYS", [], opps)
        self.assertEqual([o["id"] for o in asked["opportunities"]], ["s1"])
        self.assertEqual((n, kept[0]["line"], kept[0]["start"]), (1, "Quiet now.", 20))
        with mock.patch("app.writer.claude", side_effect=AssertionError("nothing missed")):
            self.assertEqual(writer.fill_missed(scenes, [], [], "SYS", kept, opps)[1], 0)

    def test_word_limit_comes_from_the_opportunity(self):
        opps = [{"id": "m", "t": 20, "beat_t": None, "salience": 5, "kinds": ["ambient"], "why": "w", "max_words": 4, "max_duration": 3, "kind": "micro"}]
        long_line = json.dumps([{"opportunity": "m", "persona": "player", "emotion": "calm", "line": "this line is far too long for it"}])
        with mock.patch("app.writer.claude", return_value=long_line):
            kept, dropped = writer.write_lines([scene(20)], [], [], "SYS", 60, opps)
        self.assertEqual((kept, dropped[0]["reason"]), ([], "too long"))


class LearningTests(unittest.TestCase):
    def setUp(self):
        for t in ("videos", "lines"):
            db.run(f"DELETE FROM {t}")
        db.run("DELETE FROM settings WHERE key LIKE 'arm_shift:%'")

    def video(self, pack="western", rating=None, arm="control", score=None):
        return db.run("INSERT INTO videos(drive_id,name,status,pack,density_rating,arm,combined_score,created) VALUES(?,?,?,?,?,?,?,?)",
                      (f"v{time.time_ns()}", "x", "ready", pack, rating, arm, score, time.time())).lastrowid

    def test_density_bias_follows_ratings_per_pack_and_is_damped(self):
        self.assertEqual(evolve.density_bias("western"), 0)
        self.video(rating=-1)
        self.video(rating=-1)
        self.assertEqual(evolve.density_bias("western"), 1.2)
        self.assertEqual(evolve.density_bias("crime"), 0)
        self.video(rating=0)
        self.video(rating=0)
        self.assertLess(evolve.density_bias("western"), 1.2)
        for _ in range(10):
            self.video(rating=-1)
        self.assertLessEqual(evolve.density_bias("western"), 3)  # never runs away

    def test_chatty_ratings_lower_eagerness(self):
        self.video(rating=1)
        self.assertEqual(evolve.density_bias("western"), -0.6)

    def test_kind_weights_follow_thumbs(self):
        v = self.video()
        for thumb, kinds in [(1, "kill"), (1, "kill,moral"), (-1, "loot"), (-1, "loot")]:
            db.run("INSERT INTO lines(video_id,start,persona,line,thumb,beat_kinds) VALUES(?,?,?,?,?,?)", (v, 1, "player", "x", thumb, kinds))
        w = evolve.kind_weights()
        self.assertGreater(w["kill"], 1)
        self.assertLess(w["loot"], 1)
        self.assertTrue(0.6 <= min(w.values()) and max(w.values()) <= 1.6)

    def test_ab_arm_is_adopted_only_with_enough_clear_wins(self):
        for _ in range(3):
            self.video(arm="control", score=5)
        self.video(arm="more", score=8)
        self.video(arm="more", score=8)
        self.assertIsNone(evolve.maybe_shift("western"))
        self.video(arm="more", score=8)
        self.assertEqual(evolve.maybe_shift("western"), "more")
        self.assertEqual(evolve.density_bias("western"), 0.7)
        self.assertIsNone(evolve.maybe_shift("western"))  # applied arms are not counted twice

    def test_choose_arm_never_tests_when_user_set_the_amount(self):
        self.assertEqual(evolve.choose_arm(True), ("control", 0.0))
        with mock.patch("app.evolve.random.random", return_value=0.0), mock.patch("app.evolve.random.choice", return_value=("more", 0.8)):
            self.assertEqual(evolve.choose_arm(False), ("more", 0.8))


class StyleFixTests(unittest.TestCase):
    def test_known_games_map_to_their_genre(self):
        cases = {"Red Dead Redemption 2": "western", "Grand Theft Auto V": "crime", "Resident Evil 4": "horror",
                 "The Witcher 3": "fantasy", "Call of Duty: Warzone": "shooter", "Forza Horizon 5": "racing",
                 "EA SPORTS FC 25": "sports", "Minecraft": "sandbox", "Starfield": "scifi", "Stardew Valley": "cozy"}
        for game, pack in cases.items():
            self.assertEqual(styles.pack_for_game(game), pack, game)
        self.assertIsNone(styles.pack_for_game("Some Unknown Indie"))
        self.assertIsNone(styles.pack_for_game("unknown"))

    def test_game_name_beats_the_looks_based_guess(self):
        with mock.patch("app.detect.sample_frames", return_value=[]), \
                mock.patch("app.detect.gemini_json", return_value={"game": "Red Dead Redemption 2", "pack": "shooter", "confidence": 1, "reason": "guns"}):
            r = detect.detect("x.mp4", "/tmp")
        self.assertEqual(r["pack"], "western")
        self.assertIn("Recognized", r["reason"])
        with mock.patch("app.detect.sample_frames", return_value=[]), \
                mock.patch("app.detect.gemini_json", return_value={"game": "unknown", "pack": "horror", "reason": "dark"}):
            self.assertEqual(detect.detect("x.mp4", "/tmp")["pack"], "horror")


class DensityEndpointTests(unittest.TestCase):
    def test_rating_saved_validated_and_exposed(self):
        c = TestClient(app)
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,created) VALUES(?,?,?,?,?)",
                     (f"d{time.time_ns()}", "x.mp4", "ready", "crime", time.time())).lastrowid
        self.assertEqual(c.post(f"/api/videos/{vid}/density", json={"value": 5}).status_code, 400)
        self.assertEqual(c.post(f"/api/videos/{vid}/density", json={"value": -1}).status_code, 200)
        state = c.get("/api/state").json()
        row = next(v for v in state["videos"] if v["id"] == vid)
        self.assertEqual(row["density_rating"], -1)
        self.assertEqual(state["brain"]["bias"].get("crime"), 0.6)
        self.assertIn("kind_weights", state["brain"])


class SpacingAgreementTests(unittest.TestCase):
    """The planner and the guard must agree, or planned lines get dropped as 'too close'."""

    def test_every_planned_line_fits_before_the_next_one(self):
        opps = [{"id": f"b{i}", "t": 10 + i * 5.0, "beat_t": 10 + i * 5.0, "salience": 9, "kinds": ["x"],
                 "why": "", "max_words": 25, "max_duration": 12, "kind": "full"} for i in range(10)]
        chosen, _ = beats.select(opps, 10, 80)
        self.assertGreater(len(chosen), 3)
        for a, b in zip(chosen, chosen[1:]):
            end = a["t"] + guard.est_duration(" ".join(["w"] * a["max_words"]))
            self.assertLessEqual(end + 2.5, b["t"] + 1e-6)
            self.assertGreaterEqual(a["max_words"], 3)

    def test_a_full_length_line_in_each_planned_slot_survives_the_guard(self):
        opps = [{"id": f"b{i}", "t": 10 + i * 6.0, "beat_t": None, "salience": 8, "kinds": ["x"], "why": "",
                 "max_words": 25, "max_duration": 14, "kind": "full"} for i in range(8)]
        chosen, _ = beats.select(opps, 10, 70)
        lines = [{"start": o["t"], "max_duration": o["max_duration"], "max_words": o["max_words"], "persona": "player",
                  "emotion": "calm", "trigger_t": None, "line": " ".join(["word"] * o["max_words"])} for o in chosen]
        kept, dropped = guard.enforce(lines, [], [], min_gap=2.5)
        self.assertEqual(len(kept), len(chosen), [d["reason"] for d in dropped])


if __name__ == "__main__":
    unittest.main()
