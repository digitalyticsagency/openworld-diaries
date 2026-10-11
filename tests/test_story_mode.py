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

from app import channel, cutscenes, db, gaps, guard, pipeline, storymode, thinking, writer  # noqa: E402
from app.config import PROMPTS  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "player_state": "riding",
            "time_weather": "day, clear", **kw}


def ride(seconds=120, step=3, **kw):
    return [frame(t, **kw) for t in range(0, seconds, step)]


class TravellingTests(unittest.TestCase):
    def test_riding_walking_and_running_count(self):
        for state in ("riding", "walking", "sprinting", "running", None):
            self.assertTrue(storymode.travelling(frame(0, player_state=state)), state)

    def test_a_horse_or_a_passing_animal_nearby_does_not_break_it(self):
        self.assertTrue(storymode.travelling(frame(0, animals=[{"species": "horse", "size": "large", "behavior": "calm"}])))
        self.assertTrue(storymode.travelling(frame(0, interactions=[{"kind": "overtake"}])))
        self.assertTrue(storymode.travelling(frame(0, interactions=[{"kind": "discovery"}])))

    def test_fights_shops_talk_chases_and_menus_do_not_count(self):
        for bad in ({"activity": "combat"}, {"activity": "shop"}, {"activity": "npc_talk"}, {"activity": "hunting"}, {"activity": "camp"},
                    {"player_state": "aiming"}, {"player_state": "in dialogue"}, {"player_state": "in menu"}, {"is_cutscene": True},
                    {"interactions": [{"kind": "kill"}]}, {"interactions": [{"kind": "damage_taken"}]}, {"interactions": [{"kind": "npc_contact"}]},
                    {"moral_events": [{"kind": "help"}]}, {"casualty": {"who": "bandit"}}, {"npcs": [{"behavior": "hostile"}]},
                    {"wanted_level": 2}, {"animals": [{"species": "wolf", "behavior": "attacking", "predator": True}]}):
            self.assertFalse(storymode.travelling(frame(0, **bad)), bad)


class SpotTests(unittest.TestCase):
    def test_lines_come_at_the_pace_asked_for(self):
        free = gaps.free_gaps([], 180)
        slow = storymode.spots(ride(180), free, 180, 3)
        fast = storymode.spots(ride(180), free, 180, 8)
        self.assertGreater(len(fast), len(slow) * 2)
        ts = [t for t, _ in storymode.spots(ride(180), free, 180, 6)]
        gaps_ = [b - a for a, b in zip(ts, ts[1:])]
        self.assertTrue(all(7.4 <= g <= 12.6 for g in gaps_), gaps_[:8])                         # 6 a minute is about a line every 10 seconds
        self.assertAlmostEqual(sum(gaps_) / len(gaps_), 10.0, delta=1.2)
        self.assertGreater(len({round(g, 1) for g in gaps_}), 4)                                 # but not a metronome

    def test_the_pace_is_clamped_and_junk_falls_back(self):
        self.assertEqual(storymode.clamp_lpm(100), storymode.MAX_LPM)
        self.assertEqual(storymode.clamp_lpm(0.1), storymode.MIN_LPM)
        self.assertEqual(storymode.clamp_lpm("junk"), storymode.DEFAULT_LPM)
        self.assertEqual(storymode.clamp_lpm(None), storymode.DEFAULT_LPM)

    def test_nothing_is_planned_outside_travel_or_inside_speech(self):
        notes = ride(180)
        for i in range(20, 30):
            notes[i] = frame(i * 3, activity="combat")                                        # a fight at 60-87 s
        free = gaps.free_gaps([(120.0, 140.0)], 180)                                          # someone speaks at 120-140 s
        for t, cap in storymode.spots(notes, free, 180, 6):
            self.assertFalse(58 < t < 88, t)
            self.assertFalse(t < 140 and t + cap > 120, (t, cap))

    def test_it_waits_a_moment_after_something_else_ends_and_stops_before_the_next(self):
        notes = ride(120)
        for i in range(10, 14):
            notes[i] = frame(i * 3, activity="combat")                                        # a fight at 30-39 s
        ts = storymode.stretches(notes, 120)
        self.assertEqual(len(ts), 2)
        self.assertLessEqual(ts[0][1], 28.5 - storymode.LEAVE + 1e-6)
        self.assertGreaterEqual(ts[1][0], 40.5 + storymode.SETTLE - 1e-6)

    def test_a_video_with_no_travel_has_no_story(self):
        notes = [frame(t * 3, activity="combat") for t in range(60)]
        self.assertEqual(storymode.plan(notes, gaps.free_gaps([], 180), 180, 6), [])

    def test_every_line_has_room_for_real_words_and_never_runs_into_the_next(self):
        spots = storymode.spots(ride(180), gaps.free_gaps([], 180), 180, 8)
        for (t, cap), (t2, _) in zip(spots, spots[1:]):
            self.assertGreaterEqual(gaps.words_for(cap), storymode.MIN_WORDS)
            self.assertLessEqual(t + cap, t2)


class PlanTests(unittest.TestCase):
    def plan(self, **kw):
        args = dict(notes=ride(180, landmark="a stone bridge"), free=gaps.free_gaps([], 180), length=180, lpm=6, theme_ids=["family", "past"], note="")
        args.update(kw)
        return storymode.plan(args["notes"], args["free"], args["length"], args["lpm"], args["theme_ids"], args["note"])

    def test_threads_run_in_blocks_on_the_chosen_themes(self):
        p = self.plan()
        self.assertEqual([o["thread"] for o in p[:8]], ["Family and home"] * 4 + ["Past and regrets"] * 4)
        self.assertIn("line 1 of 4", p[0]["why"])
        self.assertIn("Start a new thread", p[0]["why"])
        self.assertIn("line 3 of 4", p[2]["why"])
        self.assertIn("Continue the same thread", p[2]["why"])
        self.assertIn("picking up from your previous line", p[2]["why"])

    def test_with_no_themes_chosen_the_story_uses_all_of_them(self):
        p = self.plan(theme_ids=[], length=400, notes=ride(400), free=gaps.free_gaps([], 400))
        self.assertEqual(len({o["thread"] for o in p}), 8)

    def test_the_scenery_is_only_a_trigger_now_and_then_and_never_to_be_described(self):
        p = self.plan()
        with_trigger = [o for o in p if "A passing sight may start the thought" in o["why"]]
        self.assertTrue(with_trigger)
        self.assertLess(len(with_trigger), len(p) / 2)                                          # only some thoughts, and the rest are pure thought
        self.assertTrue(all("do not describe it" in o["why"] and "a stone bridge" in o["why"] for o in with_trigger))
        bare = self.plan(notes=ride(180))                                                      # no landmark: no trigger at all
        self.assertFalse([o for o in bare if "passing sight" in o["why"]])

    def test_the_owners_note_is_a_feeling_never_a_fact_and_only_opens_a_thread(self):
        p = self.plan(note="missing his brother")
        self.assertIn('"missing his brother"', p[0]["why"])
        self.assertIn("never as a fact", p[0]["why"])
        self.assertNotIn("missing his brother", p[1]["why"])

    def test_they_are_quiet_travel_thoughts_so_the_travel_rule_and_the_name_check_apply(self):
        o = self.plan()[0]
        self.assertEqual((o["kind"], o["travel"]), ("daydream", "story"))
        self.assertEqual(o["kinds"], ["daydream", "travel_story"])
        self.assertIn("Never name a real character and never state a story fact", o["why"])
        self.assertIn("Use emotion calm, awe, longing, amusement or none", o["why"])
        ln = {"start": 5.0, "line": "Maybe Dutch was right.", "persona": "character", "emotion": "calm", "max_words": 20, "max_duration": 5.0,
              "opp_id": o["id"], "beat_kinds": "daydream,travel_story", "daydream": True, "silent": False}
        self.assertEqual(guard.content_reason(ln, ln["line"]), "daydream names a real character")


class WriterTests(unittest.TestCase):
    def test_story_mode_adds_the_story_rules_after_the_locked_ones(self):
        opts = {"game": "Red Dead Redemption 2", "persona": "character", "pack": "free_wanderer", "personality": "balanced"}
        plain = writer.build_system("S", opts)
        story = writer.build_system("S", {**opts, "story_mode": True})
        self.assertNotIn("STORY MODE", plain)
        self.assertIn("STORY MODE", story)
        self.assertLess(story.index("HARD RULES"), story.index("STORY MODE"))
        self.assertIn("Never describe the scenery", story)
        self.assertIn("picks up from the previous line", story)
        self.assertIn("inner story thread", (PROMPTS / "guardrails.md").read_text())


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        db.put("story_mode", "0")
        db.put("story_lpm", "")

    def tearDown(self):
        db.put("story_mode", "0")
        db.put("story_lpm", "")

    def vid(self, **kw):
        cols = {"drive_id": f"sm{time.time_ns()}", "name": "x.mp4", "status": "queued", "pack": "free_wanderer",
                "game": "Red Dead Redemption 2", "created": time.time(), **kw}
        return db.run(f"INSERT INTO videos({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values())).lastrowid

    def test_the_default_is_off_and_six_a_minute_and_can_be_changed(self):
        s = self.c.get("/api/state").json()
        self.assertEqual((s["story_mode"], s["story_lpm"]), (False, 6.0))
        self.c.post("/api/settings", json={"story_mode": True, "story_lpm": 4})
        s = self.c.get("/api/state").json()
        self.assertEqual((s["story_mode"], s["story_lpm"]), (True, 4.0))
        self.c.post("/api/settings", json={"story_lpm": 99})
        self.assertEqual(self.c.get("/api/state").json()["story_lpm"], 10.0)

    def test_a_video_follows_the_default_until_it_has_its_own_choice(self):
        v = self.vid()
        self.assertEqual(pipeline.story_for(db.one("SELECT * FROM videos WHERE id=?", (v,))), (False, 6.0))
        db.put("story_mode", "1")
        db.put("story_lpm", "8")
        self.assertEqual(pipeline.story_for(db.one("SELECT * FROM videos WHERE id=?", (v,))), (True, 8.0))
        pipeline.start(v, "free_wanderer", "balanced", "RDR2", story_mode=False, story_lpm=3)
        self.assertEqual(pipeline.story_for(db.one("SELECT * FROM videos WHERE id=?", (v,))), (False, 3.0))
        other = self.vid()
        self.assertEqual(pipeline.story_for(db.one("SELECT * FROM videos WHERE id=?", (other,))), (True, 8.0))

    def test_start_and_regenerate_carry_the_choice(self):
        v = self.vid(status="choose_style")
        with mock.patch("app.server.run_bg"):
            self.assertEqual(self.c.post(f"/api/videos/{v}/start", json={"pack": "free_wanderer", "personality": "poetic", "game": "RDR2",
                                                                         "story_mode": True, "story_lpm": 5}).status_code, 200)
        row = next(x for x in self.c.get("/api/state").json()["videos"] if x["id"] == v)
        self.assertEqual((row["story_mode"], row["story_lpm"]), (1, 5.0))
        r = self.vid(status="ready")
        w = pipeline.workdir(r)
        w.mkdir(parents=True)
        (w / "scenes.json").write_text("[]")
        with mock.patch("app.server.run_bg") as run:
            self.assertEqual(self.c.post(f"/api/videos/{r}/regenerate", json={"story_mode": True, "story_lpm": 7}).status_code, 200)
        self.assertEqual(run.call_args.args[-2:], (True, 7.0))

    def test_the_page_has_the_controls(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        for needle in ("Inner story only", "id=\"story_on\"", "id=\"story_lpm\"", "data-${attrs[0]}", "'chstory', 'chlpm'", "'regstory', 'reglpm'", "inner story:"):
            self.assertIn(needle, html)


class PipelineTests(unittest.TestCase):
    def run_video(self, story, scan, speech=None):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created,story_mode,story_lpm,themes) VALUES(?,?,?,?,?,?,?,?,?)",
                     (f"sp{time.time_ns()}", "x.mp4", "queued", "free_wanderer", "Red Dead Redemption 2", time.time(), 1 if story else 0, 6.0,
                      json.dumps(["family"]))).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps(scan))
        (work / "transcript.json").write_text(json.dumps(speech or []))
        seen = {}

        def spy(scenes, segments, windows, system, length, opps, **kw):
            seen["opps"], seen["system"] = opps, system
            return [], []
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.writer.write_lines", side_effect=spy), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid)
        return seen, json.loads(db.one("SELECT stats FROM videos WHERE id=?", (vid,))["stats"])

    def scan_with_events(self):
        notes = ride(180, landmark="a stone bridge")
        notes[10] = frame(30, interactions=[{"kind": "kill", "object": "wolf", "outcome": "positive", "intensity": 3, "evidence": "x"}])
        notes[30] = frame(90, activity="shop")
        notes[40] = frame(120, npcs=[{"role": "bandit", "behavior": "hostile"}], player_state="aiming")
        return notes

    def test_story_mode_plans_only_thoughts_the_welcome_and_the_outro(self):
        seen, stats = self.run_video(True, self.scan_with_events())
        kinds = {o["id"].rstrip("0123456789") for o in seen["opps"]}
        self.assertEqual(kinds, {"s", "intro", "outro"})
        self.assertIn("STORY MODE", seen["system"])
        self.assertTrue(stats["story"]["on"])
        self.assertEqual(stats["story"]["lpm"], 6.0)
        self.assertFalse(stats["hook"])
        self.assertEqual(stats["cliffhangers"], 0)
        for o in seen["opps"]:
            if o["id"].startswith("s"):
                self.assertTrue(o["why"].startswith("INNER STORY"))
                self.assertFalse(28 < o["t"] < 34 or 88 < o["t"] < 94 or 118 < o["t"] < 124, o["t"])      # the kill, the shop and the fight stay silent

    def test_without_story_mode_nothing_changes(self):
        seen, stats = self.run_video(False, self.scan_with_events())
        self.assertNotIn("STORY MODE", seen["system"])
        self.assertIsNone(stats["story"])
        self.assertFalse([o for o in seen["opps"] if o["id"].startswith("s")])

    def test_it_never_talks_over_speech(self):
        seen, _ = self.run_video(True, ride(180), speech=[{"start": 60, "end": 80, "speaker": "game", "text": "Hold up, stranger."}])
        for o in seen["opps"]:
            if o["id"].startswith("s"):
                self.assertFalse(o["t"] < 80.5 and o["t"] + o["max_duration"] > 59.4, (o["t"], o["max_duration"]))


if __name__ == "__main__":
    unittest.main()


class NaturalInnerVoiceTests(unittest.TestCase):
    def test_the_writer_is_taught_how_a_mind_really_sounds_in_every_mode(self):
        opts = {"game": "Red Dead Redemption 2", "persona": "character", "pack": "free_wanderer", "personality": "balanced"}
        for story in (False, True):
            system = writer.build_system("S", {**opts, "story_mode": story})
            self.assertIn("HOW A MIND REALLY SOUNDS", system)
            self.assertIn("Think, do not report", system)
            self.assertIn("Be fragmentary", system)
            self.assertIn("Talk to yourself", system)
            self.assertIn("never say \"we can see\"", system)
            self.assertLess(system.index("HOW A MIND REALLY SOUNDS"), system.index("HARD RULES"))     # taught first, bound by the locked rules after

    def test_story_mode_adds_shapes_and_lengths(self):
        system = writer.build_system("S", {"game": "G", "persona": "character", "pack": "western", "personality": "balanced", "story_mode": True})
        self.assertIn("Sound like a mind, not a narrator", system)
        self.assertIn("Never put three lines of the same length or the same shape in a row", system)

    def test_each_thought_has_its_own_shape_and_length_and_they_vary(self):
        p = storymode.plan(ride(400), gaps.free_gaps([], 400), 400, 6)
        shapes = [o["why"].split("Shape: ")[1].split(".")[0] for o in p]
        lengths = [o["why"].split("Length: ")[1].split(",")[0] for o in p]
        self.assertGreaterEqual(len(set(shapes)), 5)
        self.assertEqual(set(lengths), {"medium", "short", "a word or two", "longer"})
        for a, b in zip(shapes, shapes[1:]):
            self.assertNotEqual(a, b)                                                           # never the same shape twice in a row
        for a, b in zip(lengths, lengths[1:]):
            self.assertNotEqual(a, b)
        for o in p:
            self.assertLessEqual(o["max_words"], 22)
            if "a word or two" in o["why"]:
                self.assertLessEqual(o["max_words"], 4)

    def test_the_plan_is_the_same_every_time_for_the_same_video(self):
        a = storymode.plan(ride(180), gaps.free_gaps([], 180), 180, 6)
        b = storymode.plan(ride(180), gaps.free_gaps([], 180), 180, 6)
        self.assertEqual([(o["t"], o["why"]) for o in a], [(o["t"], o["why"]) for o in b])

    def test_a_chattier_slider_means_more_thoughts_and_a_quieter_one_fewer(self):
        counts = [len(storymode.plan(ride(300), gaps.free_gaps([], 300), 300, lpm)) for lpm in (2, 4, 6, 8, 10)]
        self.assertEqual(counts, sorted(counts))
        self.assertGreater(counts[-1], counts[0] * 3)

    def test_a_viewer_directed_line_is_dropped_because_nobody_is_listening(self):
        scenes = ride(120)

        def reason(text, opp="b1", kinds="new_place"):
            ln = {"start": 20.0, "line": text, "persona": "character", "emotion": "calm", "max_words": 20, "max_duration": 6.0,
                  "opp_id": opp, "beat_kinds": kinds, "silent": False}
            keep, drop = guard.enforce([ln], [], scenes, min_gap=2.5)
            return None if keep else drop[0]["reason"]
        for bad in ("We can see the river from here.", "As we ride along, the hills open up.", "Here we are at the crossing.",
                    "And here comes the wagon.", "Behold, a bridge.", "You can see why he stayed."):
            self.assertEqual(reason(bad), "narrates the scene instead of thinking", bad)
        for ok in ("Here we go.", "Wonder if the river's cold.", "Bridges. Always feels like a choice.", "Hm. Not today."):
            self.assertIsNone(reason(ok), ok)
        self.assertIsNone(reason("Welcome to the channel, as we ride out.", opp="intro"))

    def test_look_at_that_is_dropped_in_the_inner_story_only(self):
        scenes = ride(120)

        def reason(text, kinds):
            ln = {"start": 20.0, "line": text, "persona": "character", "emotion": "calm", "max_words": 20, "max_duration": 6.0,
                  "opp_id": "s1", "beat_kinds": kinds, "silent": False}
            keep, drop = guard.enforce([ln], [], scenes, min_gap=2.5)
            return None if keep else drop[0]["reason"]
        self.assertEqual(reason("Look at that draft horse, leaning into the harness.", "daydream,travel_story"), "narrates the scene instead of thinking")
        self.assertEqual(reason("What a beautiful valley.", "daydream,travel_story"), "narrates the scene instead of thinking")
        self.assertIsNone(reason("Look at that draft horse, leaning into the harness.", "big_animal"))       # a normal reaction may point
        self.assertIsNone(reason("Wonder what it carries. Hm.", "daydream,travel_story"))


class SliderTests(unittest.TestCase):
    def test_the_page_has_a_slider_for_how_much_he_talks_everywhere_the_story_is_set(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn('<input type="range" id="story_lpm"', html)
        self.assertIn("How much he talks in the inner story", html)
        self.assertIn('type="range" id="sl_${attrs[1]}_${id}"', html)                            # the per-video sliders
        self.assertIn("const talkLabel", html)
        self.assertIn("about one every", html)
        self.assertNotIn('<input type="number" id="story_lpm"', html)
        self.assertNotIn('type="number" data-${attrs[1]}', html)

    def test_the_labels_make_sense(self):
        import subprocess
        js = ("const talkLabel = v => { v = +v; const sec = Math.round(60 / v); return `${v} lines a minute, about one every ${sec} seconds · ${v <= 3 ? 'quiet' : v <= 6 ? 'natural' : v <= 8 ? 'chatty' : 'very chatty'}`; };"
              "console.log([1.5, 3, 6, 8, 10].map(talkLabel).join('|'))")
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True).stdout.strip().split("|")
        self.assertEqual(out[0], "1.5 lines a minute, about one every 40 seconds · quiet")
        self.assertEqual(out[2], "6 lines a minute, about one every 10 seconds · natural")
        self.assertEqual(out[4], "10 lines a minute, about one every 6 seconds · very chatty")
