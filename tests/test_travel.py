import json
import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import beats, db, guard, pipeline, report, scenes, travel  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel",
            "time_weather": "day, clear", **kw}


def spot(t, i=0):
    return {"id": f"d{i}", "t": t, "kind": "daydream", "kinds": ["daydream"], "why": "Quiet travel.", "max_words": 12,
            "max_duration": 5.0, "salience": 4.5, "beat_t": None}


class AdaptiveScanTests(unittest.TestCase):
    def test_busy_moments_are_scanned_closely_and_quiet_stretches_sparsely(self):
        motions = [0.0] + [1.0] * 30 + [9.0] * 10 + [1.0] * 30      # frames 2 seconds apart
        kept = scenes.pick_times(motions, fine=2.0, calm=6.0, share=0.2)
        times = [i * 2.0 for i in kept]
        quiet_gaps = [b - a for a, b in zip(times, times[1:]) if a < 60]
        busy_gaps = [b - a for a, b in zip(times, times[1:]) if 62 <= a < 80]
        self.assertTrue(all(g >= 6 - 1e-6 for g in quiet_gaps))
        self.assertTrue(all(g <= 2 + 1e-6 for g in busy_gaps) and busy_gaps)
        self.assertLess(len(kept), len(motions) * 0.6)

    def test_no_frames_is_no_scan(self):
        self.assertEqual(scenes.pick_times([]), [])

    def test_a_scan_started_on_the_old_grid_still_resumes_on_it(self):
        d = tempfile.mkdtemp()
        fdir = os.path.join(d, "frames")
        os.makedirs(fdir)
        for i in range(3):
            with open(os.path.join(fdir, f"f_{i:05d}.jpg"), "wb") as f:
                f.write(b"x")
        with open(os.path.join(fdir, ".done"), "w") as f:
            f.write("ok")
        got = scenes.extract_frames("unused.mp4", d)
        self.assertEqual([t for t, _ in got], [0, 3, 6])


class TravelThoughtTests(unittest.TestCase):
    def test_kinds_rotate_and_never_repeat_in_a_row(self):
        notes = [frame(t, landmark="a ruined cabin", mount_or_vehicle="brown horse", pace="trot", player_state="riding")
                 for t in range(0, 400, 3)]
        notes[10] = frame(30, interactions=[{"kind": "kill", "object": "a wolf", "outcome": "positive", "intensity": 3}],
                          player_state="riding", mount_or_vehicle="brown horse")
        chosen = [spot(120 + 35 * i, i) for i in range(8)]
        counts = travel.assign(chosen, notes)
        kinds = [o["travel"] for o in chosen]
        self.assertTrue(all(a != b for a, b in zip(kinds, kinds[1:])))
        self.assertEqual(sum(counts.values()), 8)
        self.assertTrue({"notice", "mood", "horse", "memory", "wistful"} <= set(kinds))

    def test_a_kind_without_real_facts_is_skipped(self):
        notes = [frame(t) for t in range(0, 200, 3)]            # no landmark, no horse, no event
        chosen = [spot(60 + 35 * i, i) for i in range(5)]
        travel.assign(chosen, notes)
        self.assertTrue({o["travel"] for o in chosen} <= {"mood", "wistful"})

    def test_notice_only_mentions_what_the_scan_listed(self):
        notes = [frame(t, landmark="smoke over the ridge") for t in range(0, 60, 3)]
        o = spot(30)
        travel.assign([o], notes)
        self.assertEqual(o["travel"], "notice")
        self.assertIn("smoke over the ridge", o["why"])
        self.assertIn("Mention only these", o["why"])

    def test_memory_points_at_a_real_earlier_event_and_it_passes_the_guard(self):
        notes = [frame(t) for t in range(0, 300, 3)]
        notes[5] = frame(15, interactions=[{"kind": "reward", "object": "a strongbox", "outcome": "positive", "intensity": 2}])
        o = spot(200)
        travel.ORDER, saved = ["memory", "wistful"], travel.ORDER
        try:
            travel.assign([o], notes)
        finally:
            travel.ORDER = saved
        self.assertEqual(o["travel"], "memory")
        self.assertEqual(o["memory_t"], 15)
        self.assertTrue(guard.callback_ok({"callback_t": o["memory_t"], "start": 200}, notes))

    def test_an_event_too_recent_is_not_remembered(self):
        notes = [frame(t) for t in range(0, 100, 3)]
        notes[10] = frame(30, interactions=[{"kind": "kill", "object": "x", "outcome": "positive", "intensity": 3}])
        self.assertIsNone(travel.memory_event(notes, 50, set()))
        self.assertIsNotNone(travel.memory_event(notes, 90, set()))

    def test_mood_follows_the_hour_the_weather_the_trip_and_what_just_happened(self):
        night = [frame(t, time_weather="night, fog") for t in range(0, 30, 3)]
        self.assertIn("uneasy", travel.mood_for(night, 15)[0])
        long_ride = [frame(t) for t in range(0, 600, 3)]
        mood, why = travel.mood_for(long_ride, 590)
        self.assertEqual(mood, "weary")
        self.assertTrue(any("minutes" in w for w in why))
        hurt = [frame(t) for t in range(0, 100, 3)]
        hurt[20] = frame(60, interactions=[{"kind": "damage_taken", "outcome": "negative", "intensity": 2}])
        self.assertEqual(travel.mood_for(hurt, 90)[0], "sore and careful")

    def test_the_new_pace_and_horse_fields_are_in_the_scan_prompt(self):
        text = (pipeline.PROMPTS if hasattr(pipeline, "PROMPTS") else __import__("app.config", fromlist=["x"]).PROMPTS)
        body = (text / "scene.md").read_text()
        for needle in ('"pace"', '"horse"', '"landmark"'):
            self.assertIn(needle, body)
        body.format(game="g", hints="h", times=[0])                # still a valid template


class TravelPaceTests(unittest.TestCase):
    GAP = [{"start": 0.0, "end": 600.0, "max_words": 25, "kind": "full"}]

    def test_the_travel_pace_sets_how_close_thoughts_are(self):
        quiet = lambda t: True
        slow = beats.opportunities([], self.GAP, 600, 8.0, quiet, travel_gap=60.0)[0]
        fast = beats.opportunities([], self.GAP, 600, 8.0, quiet, travel_gap=15.0)[0]
        self.assertGreater(len(fast), len(slow))
        chosen = beats.select(slow, 8.0, 600)[0]
        self.assertTrue(all(b["t"] - a["t"] >= 59.9 for a, b in zip(chosen, chosen[1:])))

    def test_the_slider_is_a_thoughts_per_minute_with_a_floor(self):
        db.put("travel_lpm", "")
        self.assertEqual(pipeline.travel_gap(), 32.0)
        db.put("travel_lpm", "2")
        self.assertEqual(pipeline.travel_gap(), 30.0)
        db.put("travel_lpm", "6")
        self.assertEqual(pipeline.travel_gap(), 10.0)
        db.put("travel_lpm", "")


class BrainReportTests(unittest.TestCase):
    def test_report_explains_said_skipped_and_quiet(self):
        notes = [frame(t) for t in range(0, 300, 2)]
        all_beats = [{"t": 20.0, "salience": 8, "why": "a wolf attacks", "kinds": ["kill"]},
                     {"t": 100.0, "salience": 7, "why": "a strongbox opens", "kinds": ["reward"]},
                     {"t": 150.0, "salience": 6, "why": "a fall", "kinds": ["fall"]}]
        lines = [{"start": 22.0, "line": "Easy now.", "silent": False}]
        blocked = [all_beats[1]]
        r = report.build(notes, lines, [], all_beats, blocked, 300.0, [(95.0, 110.0)], [(60.0, 290.0)], {"mood": 2})
        res = {m["t"]: m["result"] for m in r["moments"]}
        self.assertEqual(res[20.0], "said: Easy now.")
        self.assertIn("speaking", res[100.0])
        self.assertIn("not strong enough", res[150.0])
        self.assertEqual(r["said"]["travel"], {"mood": 2})
        self.assertTrue(any(q["why"] == "a cutscene, so nothing is said" for q in r["quiet"]))

    def test_the_report_endpoint_answers_and_says_so_when_there_is_none(self):
        c = TestClient(app)
        self.assertEqual(c.get("/api/videos/987/report").status_code, 404)
        w = pipeline.workdir(987)
        w.mkdir(parents=True, exist_ok=True)
        (w / "report.json").write_text(json.dumps({"seen": {}, "said": {}, "moments": [], "quiet": [], "removed": 0}))
        self.assertEqual(c.get("/api/videos/987/report").status_code, 200)

    def test_state_carries_the_travel_pace(self):
        c = TestClient(app)
        self.assertEqual(c.post("/api/settings", json={"travel_lpm": 3}).status_code, 200)
        self.assertEqual(c.get("/api/state").json()["travel_lpm"], 3.0)
        c.post("/api/settings", json={"travel_lpm": 0})
        self.assertEqual(c.get("/api/state").json()["travel_lpm"], 0)


if __name__ == "__main__":
    unittest.main()
