import os
import sys
import tempfile
import time
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, pipeline  # noqa: E402
from app.server import app  # noqa: E402


def new_video(status="queued"):
    return db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                  (f"t{time.time_ns()}", "clip.mp4", status, time.time())).lastrowid


class TimingTests(unittest.TestCase):
    def test_started_at_spans_the_whole_working_spell(self):
        v = new_video()
        pipeline.set_status(v, "downloading")
        first = db.one("SELECT started_at, status_at FROM videos WHERE id=?", (v,))
        time.sleep(0.02)
        pipeline.set_status(v, "watching")
        second = db.one("SELECT started_at, status_at FROM videos WHERE id=?", (v,))
        self.assertEqual(second["started_at"], first["started_at"])
        self.assertGreater(second["status_at"], first["status_at"])

    def test_idle_status_clears_started_at_and_new_spell_restarts_it(self):
        v = new_video()
        pipeline.set_status(v, "writing")
        a = db.one("SELECT started_at FROM videos WHERE id=?", (v,))["started_at"]
        pipeline.set_status(v, "ready")
        self.assertIsNone(db.one("SELECT started_at FROM videos WHERE id=?", (v,))["started_at"])
        time.sleep(0.02)
        pipeline.set_status(v, "writing")
        self.assertGreater(db.one("SELECT started_at FROM videos WHERE id=?", (v,))["started_at"], a)

    def test_status_change_clears_old_progress(self):
        v = new_video()
        pipeline.set_status(v, "watching")
        pipeline.progress_cb(v, "Frames")(10, 100)
        self.assertIn("Frames 10/100", db.one("SELECT progress FROM videos WHERE id=?", (v,))["progress"])
        pipeline.set_status(v, "writing")
        self.assertIsNone(db.one("SELECT progress FROM videos WHERE id=?", (v,))["progress"])

    def test_progress_text_has_eta_until_done(self):
        v = new_video()
        cb = pipeline.progress_cb(v, "Voice lines")
        time.sleep(0.05)
        cb(1, 4)
        self.assertRegex(db.one("SELECT progress FROM videos WHERE id=?", (v,))["progress"], r"Voice lines 1/4 · about \d+:\d\d left")
        cb(4, 4)
        self.assertEqual(db.one("SELECT progress FROM videos WHERE id=?", (v,))["progress"], "Voice lines 4/4")

    def test_fmt(self):
        self.assertEqual(pipeline._fmt(0), "0:00")
        self.assertEqual(pipeline._fmt(125.9), "2:05")
        self.assertEqual(pipeline._fmt(-5), "0:00")

    def test_state_exposes_timing_and_server_time(self):
        v = new_video()
        pipeline.set_status(v, "voicing")
        s = TestClient(app).get("/api/state").json()
        row = next(x for x in s["videos"] if x["id"] == v)
        self.assertTrue(row["status_at"] and row["started_at"])
        self.assertAlmostEqual(s["server_time"], time.time(), delta=5)


if __name__ == "__main__":
    unittest.main()
