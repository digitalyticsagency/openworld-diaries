import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import clips, db, pipeline  # noqa: E402
from app.server import app  # noqa: E402


def beat(t, sal, kinds=("kill",), why="a wolf is shot"):
    return {"t": float(t), "salience": sal, "kinds": list(kinds), "why": why}


def probe(path, field="format=duration"):
    return subprocess.run(["ffprobe", "-v", "error", "-show_entries", field, "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True).stdout.strip()


def make_video(path, seconds=60, size="640x360", audio=True):
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=10:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-pix_fmt", "yuv420p", "-y", str(path)], check=True)


class PlanningTests(unittest.TestCase):
    BEATS = [beat(40, 9.0), beat(120, 8.0), beat(200, 7.0), beat(280, 6.5), beat(330, 6.0), beat(30, 9.9, kinds=("npc_threat",)),
             beat(150, 9.5, kinds=("kill",), why="in a cutscene")]

    def test_moments_are_real_events_outside_cutscenes_and_spread_apart(self):
        got = clips.moments(self.BEATS + [beat(45, 8.9)], 400, [(140, 160)], 8)
        ts = [b["t"] for b in got]
        self.assertNotIn(30.0, ts)                          # something someone said
        self.assertNotIn(150.0, ts)                         # inside a cutscene
        self.assertNotIn(45.0, ts)                          # too close to a stronger one
        self.assertTrue(all(b - a >= clips.APART for a, b in zip(ts, ts[1:])))

    def test_a_window_never_runs_into_a_cutscene_or_cuts_a_line_in_half(self):
        b = beat(100, 8)
        w = clips.window(b, 5, 25, 400, [(110, 130)], [], 59)
        self.assertEqual(w, [95.0, 109.7])                                            # ends before the cutscene
        self.assertIsNone(clips.window(beat(120, 8), 5, 25, 400, [(110, 130)], [], 59))   # the moment is in the cutscene
        w = clips.window(b, 3, 5, 400, [], [(103.0, 112.0)], 59)
        self.assertEqual(w[1], 112.4)                                                 # a line that starts inside is finished
        w = clips.window(b, 3, 5, 400, [], [(90.0, 98.0)], 59)
        self.assertGreaterEqual(w[0], 98.2)                                           # begins after the one already running

    def test_the_reel_is_about_a_minute_in_time_order_and_never_overlaps(self):
        wins = clips.plan_reel(self.BEATS, 400, [(140, 160)], [])
        self.assertGreaterEqual(len(wins), clips.REEL_MIN)
        total = sum(v["win"][1] - v["win"][0] for v in wins)
        self.assertLessEqual(total, clips.REEL_BUDGET)
        self.assertEqual([v["win"][0] for v in wins], sorted(v["win"][0] for v in wins))
        self.assertTrue(all(a["win"][1] <= b["win"][0] for a, b in zip(wins, wins[1:])))

    def test_too_few_moments_means_no_reel(self):
        self.assertEqual(clips.plan_reel([beat(40, 9.0), beat(200, 8.0)], 400, [], []), [])

    def test_shorts_are_under_a_minute_long_enough_and_apart(self):
        sh = clips.plan_shorts(self.BEATS, 400, [(140, 160)], [])
        self.assertTrue(sh)
        for s in sh:
            d = s["win"][1] - s["win"][0]
            self.assertTrue(clips.SHORT_MIN <= d <= clips.SHORT_MAX, d)
        self.assertTrue(all(a["win"][1] <= b["win"][0] for a, b in zip(sh, sh[1:])))
        self.assertLessEqual(len(sh), clips.SHORTS)


class CuttingTests(unittest.TestCase):
    def test_the_reel_joins_the_windows_with_sound(self):
        d = Path(tempfile.mkdtemp())
        make_video(d / "final.mp4")
        clips.cut_reel(d / "final.mp4", [[5, 10], [30, 36], [50, 55]], d / "reel.mp4")
        self.assertAlmostEqual(float(probe(d / "reel.mp4")), 16.0, delta=0.6)
        self.assertEqual(probe(d / "reel.mp4", "stream=codec_type").split(), ["video", "audio"])

    def test_the_vertical_frame_is_1080_by_1920_and_keeps_the_sound(self):
        d = Path(tempfile.mkdtemp())
        make_video(d / "source.mp4", audio=False)
        make_video(d / "final.mp4")
        clips.vertical(d / "source.mp4", d / "final.mp4", [10, 20], d / "v.mp4")
        self.assertEqual(probe(d / "v.mp4", "stream=width,height").splitlines()[0], "1080,1920")
        self.assertIn("audio", probe(d / "v.mp4", "stream=codec_type"))
        self.assertAlmostEqual(float(probe(d / "v.mp4")), 10.0, delta=0.5)


class MakeClipsTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.vid = db.run("INSERT INTO videos(drive_id,name,status,created,pack) VALUES(?,?,?,?,?)",
                          (f"cl{time.time_ns()}", "clip.mp4", "ready", 1.0, "western")).lastrowid
        self.w = pipeline.workdir(self.vid)
        self.w.mkdir(parents=True, exist_ok=True)

    def frames(self):
        out = []
        for t in range(0, 130, 3):
            n = {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel"}
            if t in (24, 54, 84, 114):
                n.update(activity="hunting", interactions=[{"kind": "kill", "object": "wolf", "outcome": "positive", "intensity": 3, "evidence": "x"}])
            out.append(n)
        return out

    def test_refused_without_a_finished_video(self):
        self.assertEqual(self.c.post(f"/api/videos/{self.vid}/clips").status_code, 400)
        self.assertEqual(self.c.get(f"/api/videos/{self.vid}/clips").status_code, 404)
        self.assertEqual(self.c.get(f"/api/videos/{self.vid}/clips/reel").status_code, 404)
        self.assertEqual(self.c.get(f"/api/videos/{self.vid}/clips/../x").status_code, 404)

    def test_clips_are_cut_captioned_listed_and_the_status_goes_back(self):
        make_video(self.w / "source.mp4", 130, audio=False)
        make_video(self.w / "final.mp4", 130)
        (self.w / "scenes.json").write_text(json.dumps(self.frames()))
        (self.w / "transcript.json").write_text(json.dumps([{"start": 26, "end": 28, "speaker": "game", "text": "Watch yourself."}]))
        db.run("INSERT INTO lines(video_id,start,persona,line,silent,max_duration) VALUES(?,?,?,?,?,?)", (self.vid, 30, "character", "Steady now.", 1, 4))
        pipeline.make_clips(self.vid)
        row = db.one("SELECT status,error FROM videos WHERE id=?", (self.vid,))
        self.assertEqual((row["status"], row["error"]), ("ready", None), row["error"])
        meta = self.c.get(f"/api/videos/{self.vid}/clips").json()
        self.assertTrue(meta["reel"] and meta["shorts"])
        for s in meta["shorts"]:
            f = self.w / f"short_{s['n']}.mp4"
            self.assertEqual(probe(f, "stream=width,height").splitlines()[0], "1080,1920")
            self.assertLessEqual(float(probe(f)), 60.0)
        self.assertEqual(self.c.get(f"/api/videos/{self.vid}/clips/reel").headers["content-type"], "video/mp4")
        self.assertFalse(list(self.w.glob("short_*_raw.mp4")))                      # no leftovers

    def test_a_failure_keeps_the_video_ready_and_says_why(self):
        make_video(self.w / "source.mp4", 130, audio=False)
        (self.w / "final.mp4").write_bytes(b"not a video")
        (self.w / "scenes.json").write_text(json.dumps(self.frames()))
        (self.w / "transcript.json").write_text("[]")
        pipeline.make_clips(self.vid)
        row = db.one("SELECT status,error FROM videos WHERE id=?", (self.vid,))
        self.assertEqual(row["status"], "ready")
        self.assertIn("Clips failed", row["error"])

    def test_a_restart_during_clipping_leaves_the_video_ready(self):
        db.run("UPDATE videos SET status='clipping' WHERE id=?", (self.vid,))
        with TestClient(app):                                                       # starting the app runs the lifespan
            pass
        self.assertEqual(db.one("SELECT status FROM videos WHERE id=?", (self.vid,))["status"], "ready")
        self.assertIn("clipping", pipeline.WORKING)


if __name__ == "__main__":
    unittest.main()
