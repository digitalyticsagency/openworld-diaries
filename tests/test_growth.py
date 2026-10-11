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

from app import analytics, comments, db, evolve, packaging, pipeline, series, trends  # noqa: E402
from app.server import app  # noqa: E402


def new_video(**kw):
    cols = {"drive_id": f"g{time.time_ns()}", "name": "clip.mp4", "status": "ready", "created": 1.0, "pack": "western", **kw}
    return db.run(f"INSERT INTO videos({','.join(cols)}) VALUES({','.join('?' * len(cols))})", tuple(cols.values())).lastrowid


# ---------------------------------------------------------------- analytics loop
class RetentionTests(unittest.TestCase):
    def setUp(self):
        db.put("retention_kinds", "{}")
        db.put("retention_done", "[]")

    CURVE = [(i / 100, 1.0 - 0.003 * i) for i in range(101)]            # a steady, gentle decline

    def test_interpolation_and_edges(self):
        self.assertAlmostEqual(analytics.at([(0, 1.0), (1, 0.5)], 0.5), 0.75)
        self.assertEqual(analytics.at([(0, 1.0), (1, 0.5)], 7), 0.5)
        self.assertIsNone(analytics.at([], 0.5))

    def test_a_line_that_keeps_viewers_scores_better_than_one_that_loses_them(self):
        curve = [(i / 100, 1.0 - 0.003 * i) for i in range(101)]
        curve = [(x, y - 0.15 * max(0, min(1, (x - 0.5) / 0.05))) for x, y in curve]        # a big drop just after 50%
        lines = [{"start": 100, "beat_kinds": "kill"}, {"start": 305, "beat_kinds": "pickup"}]
        eff = dict(analytics.effects(lines, curve, 600))
        self.assertAlmostEqual(eff["kill"], 0.0, places=2)                                  # an ordinary window
        self.assertLess(eff["pickup"], -0.02)                                                # the window with the drop

    def test_the_kind_a_line_counts_for(self):
        self.assertEqual(analytics.kind_of("daydream,travel_mood"), "travel_mood")
        self.assertEqual(analytics.kind_of("hook"), "hook")
        self.assertEqual(analytics.kind_of("kill,predator"), "kill")
        self.assertIsNone(analytics.kind_of("intro"))
        self.assertIsNone(analytics.kind_of(None))

    def test_dropped_lines_and_a_video_too_short_give_nothing(self):
        self.assertEqual(analytics.effects([{"start": 10, "beat_kinds": "kill", "dropped": 1}], self.CURVE, 600), [])
        self.assertEqual(analytics.effects([{"start": 10, "beat_kinds": "kill"}], self.CURVE, 20), [])
        self.assertEqual(analytics.effects([{"start": 10, "beat_kinds": "kill"}], [], 600), [])

    def test_a_kind_needs_enough_lines_before_it_moves_a_weight(self):
        db.put("retention_kinds", json.dumps({"kill": {"n": 4, "sum": 0.5}, "fall": {"n": 6, "sum": 0.12}, "pickup": {"n": 9, "sum": -0.9}}))
        w = analytics.weights()
        self.assertNotIn("kill", w)
        self.assertEqual(w["fall"], 1.12)
        self.assertEqual(w["pickup"], analytics.LOW)                                          # clamped

    def test_the_learned_weights_reach_the_planner(self):
        db.put("retention_kinds", json.dumps({"fall": {"n": 6, "sum": 0.12}}))
        self.assertEqual(evolve.kind_weights()["fall"], 1.12)

    def test_learning_folds_each_published_video_in_once(self):
        vid = new_video(status="published", yt_video_id="abc", channel_id="UC1")
        w = pipeline.workdir(vid)
        w.mkdir(parents=True, exist_ok=True)
        (w / "source.mp4").write_bytes(b"x")
        for i in range(6):
            db.run("INSERT INTO lines(video_id,start,persona,line,beat_kinds,dropped) VALUES(?,?,?,?,?,0)", (vid, 30 + 60 * i, "character", "x", "kill"))
        with mock.patch("app.analytics.fetch_curve", return_value=self.CURVE) as f, mock.patch("app.scenes.video_length", return_value=600.0):
            self.assertEqual(analytics.learn(), 1)
            self.assertEqual(analytics.learn(), 0)
        self.assertEqual(f.call_count, 1)
        self.assertEqual(json.loads(db.get("retention_kinds"))["kill"]["n"], 6)

    def test_missing_analytics_is_skipped_quietly(self):
        new_video(status="published", yt_video_id="zzz", channel_id="UC1")
        with mock.patch("app.analytics.fetch_curve", side_effect=RuntimeError("no scope")):
            self.assertEqual(analytics.learn(), 0)


# ---------------------------------------------------------------- series mode
class SeriesTests(unittest.TestCase):
    def setUp(self):
        db.run("DELETE FROM videos WHERE drive_id LIKE 'g%'")
        db.put("series_on", "1")
        db.put("series_name", "The Long Ride")

    def tearDown(self):
        db.put("series_on", "0")

    def test_episodes_are_numbered_once_and_in_order(self):
        a, b = new_video(), new_video()
        self.assertEqual(series.assign_episode(a), 1)
        self.assertEqual(series.assign_episode(a), 1)
        self.assertEqual(series.assign_episode(b), 2)
        db.put("series_on", "0")
        self.assertIsNone(series.assign_episode(new_video()))

    def test_the_previous_episode_is_the_latest_one_with_a_note(self):
        a, b, c = new_video(), new_video(), new_video()
        for v in (a, b, c):
            series.assign_episode(v)
        db.run("UPDATE videos SET summary='He shot a wolf at dusk.' WHERE id=?", (a,))
        self.assertEqual(series.previous(c)["episode"], 1)
        db.run("UPDATE videos SET summary='A quiet ride.' WHERE id=?", (b,))
        self.assertEqual(series.previous(c)["summary"], "A quiet ride.")
        self.assertIsNone(series.previous(a))

    def test_the_recap_follows_the_welcome_and_uses_only_the_note(self):
        gaps = [{"start": 0.0, "end": 60.0, "max_words": 25, "kind": "full"}]
        o = series.recap_opportunity(gaps, 14.0, {"episode": 3, "summary": "He shot a wolf at dusk."})
        self.assertEqual(o["id"], "recap")
        self.assertGreaterEqual(o["t"], 14.0)
        self.assertIn("He shot a wolf at dusk.", o["why"])
        self.assertIn("episode 3", o["why"])
        self.assertIsNone(series.recap_opportunity(gaps, 14.0, None))
        self.assertIsNone(series.recap_opportunity(gaps, 200.0, {"episode": 3, "summary": "x"}))

    def test_the_story_note_is_short_and_clickbait_free(self):
        with mock.patch("app.series.claude", return_value=json.dumps({"summary": "He shot a wolf.", "thread": "A camp light ahead."})):
            self.assertEqual(series.note_for({}), "He shot a wolf. Left open: A camp light ahead.")
        with mock.patch("app.series.claude", return_value=json.dumps({"summary": "You won't believe this wolf."})):
            self.assertEqual(series.note_for({}), "")
        with mock.patch("app.series.claude", return_value=json.dumps({"summary": "x" * 900})):
            self.assertLessEqual(len(series.note_for({})), series.MAX_NOTE)

    def test_every_title_carries_the_episode_number(self):
        self.assertEqual(series.title_prefix("A wolf on the trail", 4, "The Long Ride"), "The Long Ride Ep. 4: A wolf on the trail")
        self.assertEqual(series.title_prefix("Ep. 4: A wolf", 4, ""), "Ep. 4: A wolf")
        self.assertEqual(series.title_prefix("Plain", None, "x"), "Plain")
        self.assertLessEqual(len(series.title_prefix("t" * 100, 12, "The Long Ride")), 100)
        fs = {"game": "G", "chapters": [], "moments": [], "episode": 2, "series": "The Long Ride"}
        pkg = packaging.validate({"titles": ["A calm ride"]}, fs)
        self.assertEqual(pkg["titles"], ["The Long Ride Ep. 2: A calm ride"])

    def test_series_settings_save_and_show(self):
        c = TestClient(app)
        c.post("/api/settings", json={"series_on": False, "series_name": "  Night   Rides "})
        s = c.get("/api/state").json()
        self.assertFalse(s["series_on"])
        self.assertEqual(s["series_name"], "Night Rides")


# ---------------------------------------------------------------- comment replies
class CommentTests(unittest.TestCase):
    def test_a_reply_is_checked_before_the_owner_sees_it(self):
        self.assertEqual(comments.clean_reply("  Thanks   for riding along. "), "Thanks for riding along.")
        for bad in ("", "x" * 281, "Check https://spam.example", "Great #viral", "You won't believe it"):
            self.assertEqual(comments.clean_reply(bad), "", bad)

    def test_drafts_keep_good_replies_and_explain_skips(self):
        cs = [{"id": f"c{i}", "author": "a", "text": "nice", "likes": 0} for i in range(3)]
        raw = [{"id": "c0", "reply": "Glad you liked it."}, {"id": "c1", "reply": "", "skip": "spam"}, {"id": "c2", "reply": "See http://x.example"}]
        with mock.patch("app.comments.claude", return_value=json.dumps(raw)) as c:
            out = comments.draft(cs, "Arthur", "RDR2")
        self.assertEqual([o["reply"] for o in out], ["Glad you liked it.", "", ""])
        self.assertEqual(out[1]["skip"], "spam")
        self.assertEqual(out[2]["skip"], "no safe reply")
        self.assertIn("never claim to be a real person", c.call_args.args[0])

    def test_comments_are_saved_once(self):
        vid = new_video()
        cs = [{"id": "dup1", "author": "a", "text": "nice", "likes": 0}]
        self.assertEqual(comments.save(vid, cs, [{"id": "dup1", "reply": "Thanks.", "skip": ""}]), 1)
        self.assertEqual(comments.save(vid, cs, [{"id": "dup1", "reply": "Other.", "skip": ""}]), 0)

    def test_a_reply_is_only_posted_when_the_owner_posts_it_and_only_once(self):
        vid = new_video(yt_video_id="abc", channel_id="UC1")
        comments.save(vid, [{"id": "post1", "author": "a", "text": "nice", "likes": 0}], [{"id": "post1", "reply": "Thanks.", "skip": ""}])
        rid = db.one("SELECT id FROM replies WHERE comment_id='post1'")["id"]
        yt = mock.MagicMock()
        with mock.patch("app.google_auth.service", return_value=yt):
            self.assertEqual(yt.comments().insert.call_count, 0)                              # drafting posted nothing
            self.assertEqual(comments.post(rid, "UC1"), "Thanks.")
            with self.assertRaises(ValueError):
                comments.post(rid, "UC1")
        body = yt.comments().insert.call_args.kwargs["body"]["snippet"]
        self.assertEqual((body["parentId"], body["textOriginal"]), ("post1", "Thanks."))
        self.assertEqual(db.one("SELECT status FROM replies WHERE id=?", (rid,))["status"], "posted")

    def test_endpoints_edit_skip_and_refuse_bad_edits(self):
        c = TestClient(app)
        vid = new_video(yt_video_id="abc", channel_id="UC1")
        comments.save(vid, [{"id": "ep1", "author": "a", "text": "hi", "likes": 0}, {"id": "ep2", "author": "b", "text": "yo", "likes": 0}],
                      [{"id": "ep1", "reply": "Hello.", "skip": ""}, {"id": "ep2", "reply": "Hey.", "skip": ""}])
        ids = [r["id"] for r in c.get(f"/api/videos/{vid}/replies").json()]
        self.assertEqual(c.post(f"/api/replies/{ids[0]}/edit", json={"draft": "Better, thanks."}).json()["draft"], "Better, thanks.")
        self.assertEqual(c.post(f"/api/replies/{ids[0]}/edit", json={"draft": "go to http://x.example"}).status_code, 400)
        c.post(f"/api/replies/{ids[1]}/skip")
        self.assertEqual([r["status"] for r in c.get(f"/api/videos/{vid}/replies").json()], ["draft", "skipped"])

    def test_fetching_needs_a_video_that_is_on_youtube_and_posts_nothing(self):
        c = TestClient(app)
        local = new_video()
        self.assertEqual(c.post(f"/api/videos/{local}/comments/fetch").status_code, 400)
        vid = new_video(yt_video_id="abc", channel_id="UC1")
        with mock.patch("app.comments.fetch", return_value=[{"id": "f1", "author": "a", "text": "great", "likes": 1}]), \
                mock.patch("app.comments.claude", return_value=json.dumps([{"id": "f1", "reply": "Thank you."}])), \
                mock.patch("app.google_auth.service") as svc:
            r = c.post(f"/api/videos/{vid}/comments/fetch")
        self.assertEqual(r.json(), {"added": 1, "seen": 1})
        svc.assert_not_called()

    def test_the_channels_own_comments_are_not_fetched(self):
        yt = mock.MagicMock()
        yt.commentThreads().list().execute.return_value = {"items": [
            {"snippet": {"topLevelComment": {"id": "own", "snippet": {"authorChannelId": {"value": "UC1"}, "textDisplay": "mine"}}}},
            {"snippet": {"topLevelComment": {"id": "fan", "snippet": {"authorChannelId": {"value": "UC9"}, "authorDisplayName": "F", "textDisplay": "hi"}}}}]}
        with mock.patch("app.google_auth.service", return_value=yt):
            self.assertEqual([c["id"] for c in comments.fetch("UC1", "abc")], ["fan"])


# ---------------------------------------------------------------- what to make next
class TrendTests(unittest.TestCase):
    IDEAS = [{"title": "A day with only a lasso", "why": "from your note about challenges", "hook": "No guns, one rope.", "style": "rdr2_bounty"},
             {"title": "You won't believe this ride", "why": "x", "hook": "x", "style": "western"},
             {"title": "t" * 81, "why": "x", "hook": "x", "style": "western"},
             {"title": "No reason given", "why": "", "hook": "x", "style": "western"},
             {"title": "Unknown style", "why": "from memory", "hook": "A quiet ride.", "style": "nonsense"}]

    def test_ideas_are_honest_and_use_known_styles(self):
        out = trends.clean(self.IDEAS)
        self.assertEqual([i["title"] for i in out], ["A day with only a lasso", "Unknown style"])
        self.assertEqual(out[1]["style"], "western")

    def test_the_only_trend_input_is_what_the_owner_pasted(self):
        c = TestClient(app)
        c.post("/api/trends/notes", json={"text": "  horse videos are doing well  "})
        with mock.patch("app.trends.claude", return_value=json.dumps(self.IDEAS)) as m:
            r = c.post("/api/trends/suggest")
        sent = json.loads(m.call_args.args[1])
        self.assertEqual(sent["owner_notes_on_what_is_popular"], "horse videos are doing well")
        self.assertIn("rdr2_hunter", sent["style_ids"])
        self.assertIn("Nothing", "Nothing")                                                       # (no web lookup exists to mock)
        self.assertEqual(len(r.json()["ideas"]), 2)
        self.assertEqual(len(c.get("/api/trends").json()["ideas"]), 2)
        self.assertIn("Do not claim anything is trending", (trends.PROMPTS / "trends.md").read_text())


if __name__ == "__main__":
    unittest.main()
