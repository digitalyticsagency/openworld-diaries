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

from app import db, guard, memory, pipeline, writer  # noqa: E402
from app.config import DATA  # noqa: E402
from app.server import app  # noqa: E402

SCENES = [
    {"t": 10, "is_quiet_moment": False, "interactions": [{"kind": "damage_taken"}], "moral_events": []},
    {"t": 40, "is_quiet_moment": True, "interactions": [], "moral_events": [{"kind": "spare", "who": "a thief"}]},
    {"t": 80, "is_quiet_moment": True, "interactions": [], "moral_events": []},
]


def L(start, **kw):
    return {"start": start, "max_duration": 10, "persona": "player", "emotion": "calm",
            "trigger_t": None, "line": "A short quiet thought.", **kw}


class GuardFeatureTests(unittest.TestCase):
    def test_amount_profile_is_monotonic(self):
        gaps = [guard.amount_profile(a)[0] for a in range(1, 11)]
        lpm = [guard.amount_profile(a)[1] for a in range(1, 11)]
        self.assertEqual(gaps, sorted(gaps, reverse=True))
        self.assertEqual(lpm, sorted(lpm))
        self.assertGreaterEqual(min(gaps), 5)
        self.assertEqual(guard.amount_profile(99), guard.amount_profile(10))
        self.assertEqual(guard.amount_profile(-3), guard.amount_profile(1))

    def test_min_gap_parameter(self):
        pair = [L(30), L(40)]
        self.assertEqual(len(guard.enforce(pair, [], SCENES, min_gap=5)[0]), 2)
        self.assertEqual(len(guard.enforce(pair, [], SCENES, min_gap=22)[0]), 1)

    def test_callback_must_point_at_real_earlier_event(self):
        ok = L(60, callback_t=10)
        future = L(5, callback_t=10)
        invented = L(60, callback_t=80)
        self.assertEqual(len(guard.enforce([ok], [], SCENES)[0]), 1)
        self.assertEqual(guard.enforce([future], [], SCENES)[1][0]["reason"], "refers to an event that did not happen")
        self.assertEqual(guard.enforce([invented], [], SCENES)[1][0]["reason"], "refers to an event that did not happen")

    def test_guilt_needs_a_moral_event(self):
        grounded = L(50, emotion="guilt", trigger_t=40)
        ungrounded = L(50, emotion="guilt", trigger_t=80)
        self.assertEqual(len(guard.enforce([grounded], [], SCENES)[0]), 1)
        self.assertEqual(guard.enforce([ungrounded], [], SCENES)[0], [])


class WriterFeatureTests(unittest.TestCase):
    def test_amount_profile_and_memory_reach_the_model(self):
        seen = {}

        def fake(system, user, max_tokens=8000):
            seen.update(json.loads(user))
            return "[]"
        opts = {"game": "g", "persona": "mixed", "character": "", "story_point": "", "voice_notes": ""}
        with mock.patch("app.writer.claude", fake):
            writer.write_lines(SCENES, [], [], "SYS", 100, amount=9, profile=["rides fast"])
        self.assertEqual(seen["commentary"]["min_gap_seconds"], guard.amount_profile(9)[0])
        self.assertEqual(seen["player_profile"], ["rides fast"])
        self.assertIn("earlier_events", seen)
        self.assertIn("CONSEQUENCES", writer.build_system("S", opts))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        db.run("DELETE FROM memory")
        db.put("learn", "1")
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,'m','ready',?)",
                     (f"m{time.time_ns()}", time.time())).lastrowid
        w = DATA / "work" / str(vid)
        w.mkdir(parents=True, exist_ok=True)
        (w / "scenes.json").write_text(json.dumps(SCENES))
        (w / "transcript.json").write_text("[]")
        self.vid = vid

    def test_update_merges_and_counts(self):
        reply = json.dumps([{"kind": "habit", "text": "Spares thieves."}, {"kind": "bogus", "text": "Loves swamps."}])
        with mock.patch("app.memory.claude", return_value=reply):
            self.assertEqual(memory.update(self.vid), 2)
            memory.update(self.vid)
        rows = {r["text"]: r for r in db.rows("SELECT * FROM memory")}
        self.assertEqual(rows["Spares thieves."]["count"], 2)
        self.assertEqual(rows["Loves swamps."]["kind"], "habit")
        self.assertEqual(memory.profile()[0] in rows, True)

    def test_switch_off_means_no_learning_and_no_profile(self):
        db.put("learn", "0")
        with mock.patch("app.memory.claude", side_effect=AssertionError("must not call")):
            self.assertEqual(memory.update(self.vid), 0)
        self.assertEqual(memory.profile(), [])


class ServerFeatureTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        db.put("youtube_publish", "0")

    def make_video(self, status="ready", final=True):
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                     (f"s{time.time_ns()}", "clip.mp4", status, time.time())).lastrowid
        w = DATA / "work" / str(vid)
        w.mkdir(parents=True, exist_ok=True)
        if final:
            (w / "final.mp4").write_bytes(b"VIDEO")
        return vid

    def test_youtube_switch_defaults_off_and_persists(self):
        db.run("DELETE FROM settings WHERE key='youtube_publish'")
        self.assertFalse(self.c.get("/api/state").json()["youtube_publish"])
        self.c.post("/api/settings", json={"youtube_publish": True})
        self.assertTrue(self.c.get("/api/state").json()["youtube_publish"])
        self.c.post("/api/settings", json={"youtube_publish": False})
        self.assertFalse(pipeline.youtube_on())

    def test_amount_is_clamped(self):
        self.c.post("/api/settings", json={"amount": 99})
        self.assertEqual(self.c.get("/api/state").json()["amount"], 10)
        self.c.post("/api/settings", json={"amount": 0})
        self.assertEqual(self.c.get("/api/state").json()["amount"], 1)

    def test_download_and_missing_download(self):
        vid = self.make_video()
        r = self.c.get(f"/api/videos/{vid}/download")
        self.assertEqual((r.status_code, r.content), (200, b"VIDEO"))
        self.assertIn("clip_inner.mp4", r.headers["content-disposition"])
        self.assertEqual(self.c.get(f"/api/videos/{self.make_video(final=False)}/download").status_code, 404)

    def test_busy_video_rejects_actions(self):
        vid = self.make_video(status="voicing")
        self.assertEqual(self.c.post(f"/api/videos/{vid}/regenerate", json={}).status_code, 409)
        self.assertEqual(self.c.delete(f"/api/videos/{vid}").status_code, 409)

    def test_publish_needs_channel_and_finished_video(self):
        db.run("DELETE FROM settings WHERE key='active_channel'")
        self.assertEqual(self.c.post(f"/api/videos/{self.make_video()}/publish").status_code, 400)
        db.put("active_channel", "UC123")
        self.assertEqual(self.c.post(f"/api/videos/{self.make_video(status='published')}/publish").status_code, 400)
        db.run("DELETE FROM settings WHERE key='active_channel'")

    def test_delete_removes_files_and_stops_reimport(self):
        vid = self.make_video()
        drive_id = db.one("SELECT drive_id FROM videos WHERE id=?", (vid,))["drive_id"]
        self.assertEqual(self.c.delete(f"/api/videos/{vid}").status_code, 200)
        self.assertFalse((DATA / "work" / str(vid)).exists())
        self.assertIsNone(db.one("SELECT id FROM videos WHERE id=?", (vid,)))
        self.assertEqual(db.get(f"ignored:{drive_id}"), "1")

    def test_edit_and_delete_line(self):
        vid = self.make_video()
        lid = db.run("INSERT INTO lines(video_id,start,persona,line,dropped) VALUES(?,?,?,?,1)",
                     (vid, 5, "player", "old")).lastrowid
        self.assertEqual(self.c.patch(f"/api/lines/{lid}", json={"line": "  new text "}).status_code, 200)
        row = db.one("SELECT line, dropped FROM lines WHERE id=?", (lid,))
        self.assertEqual((row["line"], row["dropped"]), ("new text", 0))
        self.assertEqual(self.c.patch(f"/api/lines/{lid}", json={"line": "   "}).status_code, 400)
        self.assertEqual(self.c.delete(f"/api/lines/{lid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/videos/{vid}/lines").json(), [])

    def test_forget_memory(self):
        mid = db.run("INSERT INTO memory(kind,text,count,updated) VALUES('habit','x',1,0)").lastrowid
        self.assertEqual(self.c.delete(f"/api/memory/{mid}").status_code, 200)
        self.assertIsNone(db.one("SELECT id FROM memory WHERE id=?", (mid,)))


if __name__ == "__main__":
    unittest.main()
