import json
import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import beats, channel, gaps, guard, pipeline, viral  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", **kw}


def beat(t, sal, kinds=("kill",), why="a wolf is shot"):
    return {"t": float(t), "salience": sal, "kinds": list(kinds), "why": why}


def lines_for(opp_id, kinds, text):
    return {"opp_id": opp_id, "beat_kinds": kinds, "line": text, "start": 5, "persona": "character"}


class ColdOpenTests(unittest.TestCase):
    FREE = [{"start": 0.0, "end": 40.0, "max_words": 25, "kind": "full"}]

    def test_the_teaser_points_at_the_strongest_real_event_later_in_the_video(self):
        bs = [beat(10, 9.0), beat(120, 7.0), beat(300, 8.0, why="a bear attacks"), beat(400, 9.5, kinds=("npc_threat",))]
        top = viral.strongest_moment(bs, 600)
        self.assertEqual(top["t"], 300.0)             # not the early one, and not a line someone said
        o = viral.hook_opportunity(self.FREE, top)
        self.assertEqual(o["id"], "hook")
        self.assertEqual(o["teaser_t"], 300.0)
        self.assertIn("a bear attacks", o["why"])
        self.assertIn("5:00", o["why"])
        self.assertLessEqual(o["t"], viral.HOOK_WITHIN)
        self.assertLessEqual(o["max_duration"], viral.HOOK_MAX_SECONDS)

    def test_no_real_moment_or_no_room_means_no_teaser(self):
        self.assertIsNone(viral.strongest_moment([beat(5, 9.0)], 600))
        self.assertIsNone(viral.hook_opportunity(self.FREE, None))
        late = [{"start": 30.0, "end": 60.0, "max_words": 25, "kind": "full"}]
        self.assertIsNone(viral.hook_opportunity(late, beat(300, 8)))
        tiny = [{"start": 0.0, "end": 2.6, "max_words": 4, "kind": "micro"}]
        self.assertIsNone(viral.hook_opportunity(tiny, beat(300, 8)))

    def test_the_welcome_follows_the_teaser(self):
        hook = viral.hook_opportunity(self.FREE, beat(300, 8))
        cfg = channel.settings(lambda k, d=None: d)
        intro = channel.intro_opportunity(self.FREE, 600, cfg, after=hook["t"] + hook["max_duration"] + 0.8)
        self.assertGreaterEqual(intro["t"], hook["t"] + hook["max_duration"])
        self.assertEqual(channel.intro_opportunity(self.FREE, 600, cfg)["t"], 1.0)      # unchanged without a teaser

    def test_the_teaser_is_protected_from_a_close_neighbour(self):
        hook = viral.hook_opportunity(self.FREE, beat(300, 8))
        near = {"id": "b1", "t": hook["t"] + 4.6, "beat_t": None, "salience": 5.0, "kinds": ["pickup"], "why": "x",
                "max_words": 12, "max_duration": 5.0, "kind": "full"}
        fitted = beats._fit_to_neighbours([hook, near])
        self.assertEqual([o["id"] for o in fitted], ["hook"])


class CliffhangerTests(unittest.TestCase):
    FREE = [{"start": 0.0, "end": 400.0, "max_words": 25, "kind": "full"}]

    def test_a_real_buildup_gets_a_short_line_before_the_moment(self):
        scenes = [frame(t) for t in range(0, 130, 3)]
        scenes[38] = frame(114, animals=[{"species": "wolf", "behavior": "approaching", "predator": True}])
        out = viral.cliffhangers([beat(120, 8.0)], scenes, self.FREE, 400)
        self.assertEqual(len(out), 1)
        o = out[0]
        self.assertIn("a wolf is approaching", o["why"])
        self.assertLess(o["t"] + o["max_duration"], 120)                  # it ends before the moment
        self.assertLessEqual(o["max_words"], 10)
        self.assertLess(o["salience"], 8.0)                               # the moment's own reaction always outranks it

    def test_no_cue_in_the_frames_means_no_suspense(self):
        scenes = [frame(t) for t in range(0, 130, 3)]
        self.assertEqual(viral.cliffhangers([beat(120, 8.0)], scenes, self.FREE, 400), [])

    def test_a_cue_after_the_moment_does_not_count(self):
        scenes = [frame(t) for t in range(0, 130, 3)]
        scenes[41] = frame(123, npcs=[{"role": "bandit", "behavior": "hostile"}])
        self.assertIsNone(viral.buildup_cue(scenes, 120))

    def test_weak_early_and_speech_moments_get_none_and_they_are_spaced(self):
        scenes = [frame(t, npcs=[{"role": "bandit", "behavior": "hostile"}]) for t in range(0, 400, 3)]
        bs = [beat(120, 8), beat(150, 8.5), beat(10, 9), beat(200, 5.0), beat(260, 8, kinds=("npc_threat",)), beat(330, 7.5)]
        out = viral.cliffhangers(bs, scenes, self.FREE, 400)
        times = sorted(o["beat_t"] for o in out)
        self.assertNotIn(10.0, times)
        self.assertNotIn(200.0, times)
        self.assertNotIn(260.0, times)
        self.assertTrue(all(b - a >= viral.CLIFF_SPACING for a, b in zip(times, times[1:])))


class HonestOnlyTests(unittest.TestCase):
    def reason(self, kinds, text):
        return guard.content_reason({"opp_id": "hook", "beat_kinds": kinds, "emotion": "awe"}, text)

    def test_clickbait_is_dropped_from_teasers_and_suspense_only(self):
        self.assertEqual(self.reason("hook", "You won't believe what happens at the river."), "clickbait wording in a teaser")
        self.assertEqual(self.reason("cliffhanger", "Shocking things are coming."), "clickbait wording in a teaser")
        self.assertIsNone(self.reason("hook", "Something is waiting out there in the dark."))
        self.assertIsNone(guard.content_reason({"opp_id": "b1", "beat_kinds": "kill"}, "Shocking, that shot."))

    def test_teaser_lines_go_through_the_enforcer(self):
        notes = [frame(t) for t in range(0, 30, 3)]
        keep, drop = guard.enforce([{**lines_for("hook", "hook", "You won't believe this one."), "max_words": 14, "max_duration": 5.0,
                                     "emotion": "awe", "silent": False}], [], notes)
        self.assertEqual(keep, [])
        self.assertEqual(drop[0]["reason"], "clickbait wording in a teaser")


class ChapterTests(unittest.TestCase):
    def scan(self):
        out = []
        for t in range(0, 600, 3):
            act = "travel" if t < 150 else "hunting" if t < 300 else "shop" if t < 390 else "combat" if t < 480 else "camp"
            out.append(frame(t, activity=act, player_state="riding" if t < 150 else "walking"))
        return out

    def test_chapters_follow_the_activity_runs(self):
        chs = viral.chapters(self.scan(), 600)
        self.assertEqual([c["title"] for c in chs], ["Riding", "Hunting", "Shopping", "Gunfight", "Camp"])
        self.assertEqual(chs[0]["t"], 0.0)
        self.assertEqual(viral.chapters_text(chs).splitlines()[1], "2:30 Hunting")

    def test_short_blips_do_not_make_chapters_and_fewer_than_three_gives_none(self):
        scan = self.scan()
        scan[20] = frame(60, activity="shop")                       # a single frame
        self.assertNotIn("Shopping", [c["title"] for c in viral.chapters(scan, 600)][:2])
        flat = [frame(t) for t in range(0, 300, 3)]
        self.assertEqual(viral.chapters(flat, 300), [])
        self.assertEqual(viral.chapters([], 300), [])

    def test_the_first_chapter_is_at_zero_and_there_are_never_too_many(self):
        scan = [frame(t, activity=["hunting", "shop", "combat", "camp"][(t // 40) % 4]) for t in range(7, 1200, 3)]
        chs = viral.chapters(scan, 1200)
        self.assertEqual(chs[0]["t"], 0.0)
        self.assertLessEqual(len(chs), viral.CHAPTER_MAX)

    def test_the_report_endpoint_carries_the_chapters(self):
        c = TestClient(app)
        w = pipeline.workdir(988)
        w.mkdir(parents=True, exist_ok=True)
        (w / "report.json").write_text(json.dumps({"seen": {}, "said": {}, "moments": [], "quiet": [], "removed": 0}))
        (w / "chapters.json").write_text(json.dumps({"chapters": [{"t": 0, "title": "Riding"}], "text": "0:00 Riding"}))
        self.assertEqual(c.get("/api/videos/988/report").json()["chapters"]["text"], "0:00 Riding")


class SettingsTests(unittest.TestCase):
    def test_hook_and_cliffhanger_switches_default_on_and_save(self):
        c = TestClient(app)
        s = c.get("/api/state").json()["channel"]
        self.assertTrue(s["hook_on"] and s["cliff_on"])
        c.post("/api/settings", json={"hook_on": False, "cliff_on": False})
        s = c.get("/api/state").json()["channel"]
        self.assertFalse(s["hook_on"] or s["cliff_on"])
        c.post("/api/settings", json={"hook_on": True, "cliff_on": True})

    def test_the_guardrails_know_about_hooks_and_suspense(self):
        from app.config import PROMPTS
        self.assertIn("HOOKS AND SUSPENSE", (PROMPTS / "guardrails.md").read_text())


if __name__ == "__main__":
    unittest.main()
