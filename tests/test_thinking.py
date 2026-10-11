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

from app import cutscenes, db, guard, pipeline, thinking, travel  # noqa: E402
from app.config import PROMPTS  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "time_weather": "day, clear", **kw}


class CatalogTests(unittest.TestCase):
    def test_eight_themes_each_with_several_generic_lines(self):
        self.assertEqual(list(thinking.THEMES), ["family", "friends", "future", "past", "fear", "gratitude", "meaning", "work"])
        for k, t in thinking.THEMES.items():
            self.assertGreaterEqual(len(t["lines"]), 6, k)
            for line in t["lines"]:
                self.assertIsNone(guard.CANON.search(line), line)           # never a real character's name
        self.assertEqual(len(thinking.catalog()), 8)

    def test_ids_are_cleaned_to_real_themes_in_order_without_repeats(self):
        self.assertEqual(thinking.clean_ids(["work", "family", "family", "nonsense", 5, None]), ["family", "work"])
        self.assertEqual(thinking.clean_ids(None), [])
        self.assertEqual(thinking.clean_ids("family"), [])

    def test_the_mix_of_everything_shortcut_means_every_theme(self):
        self.assertEqual(thinking.clean_ids(["all"]), list(thinking.THEMES))
        self.assertEqual(thinking.clean_ids(["family", "all", "nonsense"]), list(thinking.THEMES))
        self.assertEqual(len(thinking.prompts(["all"])), 8 * 6)
        self.assertEqual([lab for lab, _ in thinking.prompts(["all"])[:8]], [t["name"] for t in thinking.THEMES.values()])

    def test_the_note_is_one_short_plain_line(self):
        self.assertEqual(thinking.clean_note("  missing   his\nbrother <b>{x}</b> \\ "), "missing his brother bx/b")
        self.assertEqual(len(thinking.clean_note("x" * 500)), thinking.MAX_NOTE)
        self.assertEqual(thinking.clean_note(None), "")

    def test_prompts_rotate_through_the_chosen_themes_line_by_line(self):
        p = thinking.prompts(["family", "future"])
        self.assertEqual([lab for lab, _ in p[:4]], ["Family and home", "Future and dreams", "Family and home", "Future and dreams"])
        self.assertEqual(p[0][1], thinking.THEMES["family"]["lines"][0])
        self.assertEqual(p[2][1], thinking.THEMES["family"]["lines"][1])
        self.assertEqual(thinking.prompts([]), [])


class TravelThoughtTests(unittest.TestCase):
    def spots(self, n=12):
        return [{"id": f"d{i}", "t": 60.0 + 40 * i, "kind": "daydream", "kinds": ["daydream"], "why": "x", "max_words": 12,
                 "max_duration": 5.0, "salience": 4.5, "beat_t": None} for i in range(n)]

    def notes(self):
        return [frame(t, landmark="a ridge", mount_or_vehicle="brown horse", player_state="riding") for t in range(0, 700, 3)]

    def test_with_themes_chosen_every_other_thought_is_a_reflection_on_them(self):
        spots = self.spots()
        travel.assign(spots, self.notes(), reflect=thinking.prompts(["family", "friends"]))
        kinds = [o["travel"] for o in spots]
        self.assertGreaterEqual(kinds.count("wistful"), len(kinds) // 3)
        self.assertTrue(all(a != b for a, b in zip(kinds, kinds[1:])))
        refl = [o for o in spots if o["travel"] == "wistful"]
        self.assertTrue(all(("family and home" in o["why"] or "friends and loneliness" in o["why"]) for o in refl))
        self.assertTrue(any(k in kinds for k in ("notice", "goal", "mood", "horse", "memory")))      # the rest stays grounded

    def test_without_chosen_themes_the_old_rotation_and_themes_are_unchanged(self):
        spots = self.spots(6)
        travel.assign(spots, self.notes())
        self.assertTrue(all("Theme for this one" in o["why"] for o in spots if o["travel"] == "wistful"))
        self.assertTrue(all("thinking about" not in o["why"] for o in spots))

    def test_the_owners_note_colours_the_thought_as_a_feeling_never_a_fact(self):
        spots = self.spots(6)
        travel.assign(spots, self.notes(), reflect=thinking.prompts(["past"]), note="missing his brother")
        why = next(o["why"] for o in spots if o["travel"] == "wistful")
        self.assertIn('"missing his brother"', why)
        self.assertIn("never as a fact", why)
        self.assertIn("never name anyone", why)

    def test_reflections_cycle_through_a_themes_lines(self):
        spots = self.spots(12)
        travel.ORDER, saved = ["wistful"], travel.ORDER
        travel.THEMED_ORDER, saved2 = ["wistful"], travel.THEMED_ORDER
        try:
            travel.assign(spots, [frame(t) for t in range(0, 700, 3)], reflect=thinking.prompts(["gratitude"]))
        finally:
            travel.ORDER, travel.THEMED_ORDER = saved, saved2
        lines = [o["why"] for o in spots if o["travel"] == "wistful"]
        self.assertEqual(len(lines), 12)
        for i, why in enumerate(lines[:6]):
            self.assertIn(thinking.THEMES["gratitude"]["lines"][i], why)

    def test_the_reflection_wording_is_guarded_against_names_and_facts(self):
        why = next(o["why"] for o in (lambda s: (travel.assign(s, self.notes(), reflect=thinking.prompts(["family"])), s)[1])(self.spots(4)) if o["travel"] == "wistful")
        self.assertIn("never name a real character and never state a story fact", why)
        self.assertIn("TRAVEL THOUGHTS", (PROMPTS / "guardrails.md").read_text())
        self.assertIn("family, friends, the future, the past, fear, small joys, meaning or work", (PROMPTS / "guardrails.md").read_text())


class StoreTests(unittest.TestCase):
    def vid(self, status="choose_style"):
        return db.run("INSERT INTO videos(drive_id,name,status,created,pack,game) VALUES(?,?,?,?,?,?)",
                      (f"th{time.time_ns()}", "x.mp4", status, 1.0, "western", "Red Dead Redemption 2")).lastrowid

    def row(self, v):
        return db.one("SELECT themes, theme_note FROM videos WHERE id=?", (v,))

    def test_start_stores_cleaned_themes_and_a_note_and_regenerate_can_change_or_clear_them(self):
        v = self.vid()
        pipeline.start(v, "western", "balanced", "RDR2", themes=["future", "nonsense", "family"], theme_note="  a quiet life ")
        self.assertEqual(json.loads(self.row(v)["themes"]), ["family", "future"])
        self.assertEqual(self.row(v)["theme_note"], "a quiet life")
        pipeline.start(v, "western", "balanced", "RDR2")                                      # nothing given: unchanged
        self.assertEqual(json.loads(self.row(v)["themes"]), ["family", "future"])
        pipeline.start(v, "western", "balanced", "RDR2", themes=[], theme_note="")
        self.assertEqual(json.loads(self.row(v)["themes"]), [])
        self.assertIsNone(self.row(v)["theme_note"])

    def test_all_is_stored_as_every_theme(self):
        v = self.vid()
        pipeline.start(v, "western", "balanced", "RDR2", themes=["all"])
        self.assertEqual(json.loads(self.row(v)["themes"]), list(thinking.THEMES))

    def test_the_choice_belongs_to_one_video(self):
        a, b = self.vid(), self.vid()
        pipeline.start(a, "western", "balanced", "RDR2", themes=["work"])
        self.assertIsNone(self.row(b)["themes"])

    def test_the_start_endpoint_and_the_state_carry_the_choice(self):
        c = TestClient(app)
        v = self.vid()
        with mock.patch("app.server.run_bg"):
            r = c.post(f"/api/videos/{v}/start", json={"pack": "free_wanderer", "personality": "poetic", "game": "Red Dead Redemption 2",
                                                       "themes": ["family", "friends"], "theme_note": "missing home"})
        self.assertEqual(r.status_code, 200, r.text)
        s = c.get("/api/state").json()
        self.assertEqual(len(s["think_themes"]), 8)
        row = next(x for x in s["videos"] if x["id"] == v)
        self.assertEqual(json.loads(row["themes"]), ["family", "friends"])
        self.assertEqual(row["theme_note"], "missing home")

    def test_regenerate_passes_the_choice_on(self):
        c = TestClient(app)
        v = self.vid("ready")
        w = pipeline.workdir(v)
        w.mkdir(parents=True)
        (w / "scenes.json").write_text("[]")
        with mock.patch("app.server.run_bg") as run:
            self.assertEqual(c.post(f"/api/videos/{v}/regenerate", json={"themes": ["past"], "theme_note": "an old regret"}).status_code, 200)
        args = run.call_args.args
        self.assertIs(args[0], pipeline.regenerate)
        self.assertEqual(args[-2:], (["past"], "an old regret"))


class PipelineTests(unittest.TestCase):
    def test_the_chosen_themes_reach_the_planner_for_that_video_only(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"tp{time.time_ns()}", "x.mp4", "queued", "western", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps([frame(t) for t in range(0, 300, 3)]))
        (work / "transcript.json").write_text("[]")
        seen = []

        def spy(chosen, notes, themes=None, reflect=None, note=""):
            seen.append((reflect, note))
            return {}
        with mock.patch("app.pipeline.scenes.video_length", return_value=300.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.travel.assign", side_effect=spy), \
                mock.patch("app.pipeline.writer.write_lines", return_value=([], [])), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
            pipeline.regenerate(vid, themes=["family", "friends"], theme_note="missing home")
            pipeline.regenerate(vid, themes=[], theme_note="")
        self.assertEqual([lab for lab, _ in seen[0][0][:2]], ["Family and home", "Friends and loneliness"])
        self.assertEqual(seen[0][1], "missing home")
        self.assertEqual(seen[1], (None, ""))                                                  # cleared: back to the normal thoughts


class PageTests(unittest.TestCase):
    def test_the_page_offers_the_choice_at_upload_and_after(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        for needle in ("What is he thinking about on quiet travel?", "thinkingBox(v.id, c.themes || [], c.theme_note || '', 'chth', 'ch')", "data-regth", "Thinking about…", "theme_note", "Or in your own words", "Mix of everything", "data-allth", "data-noneth"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()


class FavouriteThemesTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        db.put("default_themes", "[]")

    def tearDown(self):
        db.put("default_themes", "[]")

    def vid(self, themes=None):
        return db.run("INSERT INTO videos(drive_id,name,status,created,pack,game,themes) VALUES(?,?,?,?,?,?,?)",
                      (f"fv{time.time_ns()}", "x.mp4", "queued", 1.0, "western", "Red Dead Redemption 2", themes)).lastrowid

    def test_the_favourites_are_saved_cleaned_and_shown(self):
        self.assertEqual(self.c.get("/api/state").json()["default_themes"], [])
        self.assertEqual(self.c.post("/api/settings", json={"default_themes": ["work", "family", "nonsense", "family"]}).status_code, 200)
        self.assertEqual(self.c.get("/api/state").json()["default_themes"], ["family", "work"])
        self.c.post("/api/settings", json={"default_themes": ["all"]})
        self.assertEqual(len(self.c.get("/api/state").json()["default_themes"]), 8)
        self.c.post("/api/settings", json={"default_themes": []})
        self.assertEqual(self.c.get("/api/state").json()["default_themes"], [])

    def test_a_video_with_no_choice_uses_the_favourites_and_its_own_choice_wins_even_when_empty(self):
        db.put("default_themes", json.dumps(["future", "past"]))
        self.assertEqual(thinking.for_video({"themes": None}), ["future", "past"])
        self.assertEqual(thinking.for_video({"themes": json.dumps(["work"])}), ["work"])
        self.assertEqual(thinking.for_video({"themes": "[]"}), [])                          # cleared on purpose: no theme
        self.assertEqual(thinking.for_video({"themes": "junk{"}), ["future", "past"])
        self.assertEqual(thinking.for_video(None), ["future", "past"])
        db.put("default_themes", "not json")
        self.assertEqual(thinking.defaults(), [])

    def test_the_planner_gets_the_favourites_for_a_video_that_has_no_choice(self):
        db.put("default_themes", json.dumps(["gratitude"]))
        seen = []

        def spy(chosen, notes, themes=None, reflect=None, note=""):
            seen.append(reflect)
            return {}
        for themes in (None, "[]"):
            vid = self.vid(themes)
            work = pipeline.workdir(vid)
            work.mkdir(parents=True)
            (work / "source.mp4").write_bytes(b"x")
            (work / "scenes.json").write_text(json.dumps([frame(t) for t in range(0, 300, 3)]))
            (work / "transcript.json").write_text("[]")
            with mock.patch("app.pipeline.scenes.video_length", return_value=300.0), \
                    mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                    mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                    mock.patch("app.pipeline.travel.assign", side_effect=spy), \
                    mock.patch("app.pipeline.writer.write_lines", return_value=([], [])), \
                    mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                    mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                    mock.patch("app.pipeline.voice.make_clips", side_effect=RuntimeError("stop here")):
                pipeline.regenerate(vid)
        self.assertEqual([lab for lab, _ in seen[0][:1]], ["Gratitude and small joys"])      # no choice of its own: the favourites
        self.assertIsNone(seen[1])                                                           # chose none: no reflection

    def test_the_page_pre_ticks_the_favourites_and_can_save_them(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        for needle in ("My favourite thinking themes", "id=\"favthemes\"", "themes: DEFAULT_THEMES.slice()", "Save as my favourites", "data-savefav", "data-favall", "renderFavThemes"):
            self.assertIn(needle, html)
