import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import requests  # noqa: E402

from app import db, pipeline, voice  # noqa: E402


class Resp:
    def __init__(self, code=200, content=b"audio", text=""):
        self.status_code, self.content, self.text = code, content, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


class RetryTests(unittest.TestCase):
    def setUp(self):
        os.environ["ELEVENLABS_API_KEY"] = "test-key"
        self.sleep = mock.patch("app.voice.time.sleep")
        self.sleep.start()
        self.out = Path(tempfile.mkdtemp()) / "c.mp3"

    def tearDown(self):
        self.sleep.stop()

    def test_a_connection_timeout_is_tried_again_and_then_works(self):
        seq = [requests.exceptions.ConnectTimeout("t"), requests.exceptions.ConnectionError("c"), Resp()]
        with mock.patch("app.voice.requests.post", side_effect=seq) as p:
            voice.tts("Easy now.", "V", "calm", self.out)
        self.assertEqual(p.call_count, 3)
        self.assertEqual(self.out.read_bytes(), b"audio")

    def test_busy_service_codes_are_tried_again(self):
        with mock.patch("app.voice.requests.post", side_effect=[Resp(429), Resp(503), Resp()]) as p:
            voice.tts("Easy now.", "V", "calm", self.out)
        self.assertEqual(p.call_count, 3)

    def test_a_wrong_key_or_bad_request_is_not_retried(self):
        with mock.patch("app.voice.requests.post", return_value=Resp(401)) as p, self.assertRaises(requests.exceptions.HTTPError):
            voice.tts("Easy now.", "V", "calm", self.out)
        self.assertEqual(p.call_count, 1)

    def test_it_gives_up_after_the_last_try_with_the_real_error(self):
        with mock.patch("app.voice.requests.post", side_effect=requests.exceptions.ConnectTimeout("t")) as p, \
                self.assertRaises(requests.exceptions.ConnectTimeout):
            voice.tts("Easy now.", "V", "calm", self.out)
        self.assertEqual(p.call_count, 1 + len(voice.RETRY_WAITS))

    def test_the_connect_wait_is_short_so_a_dead_network_fails_fast(self):
        self.assertLessEqual(voice.TIMEOUT[0], 20)


class TakeCacheTests(unittest.TestCase):
    def line(self, start, text):
        return {"start": start, "line": text, "persona": "character", "emotion": "calm", "max_duration": 9.0}

    def run_clips(self, work, lines, fail_on=None):
        calls = []

        def fake_tts(text, voice_id, emotion, out):
            calls.append(text)
            if text == fail_on:
                raise requests.exceptions.ConnectTimeout("network")
            Path(out).write_bytes(b"audio:" + text.encode())

        with mock.patch("app.voice.tts", side_effect=fake_tts), mock.patch("app.voice.level_clip"), \
                mock.patch("app.voice.duration", return_value=2.0), mock.patch("app.voices.resolve", return_value="V"):
            try:
                return voice.make_clips(lines, [], "sys", work, pack="western"), calls
            except requests.exceptions.ConnectTimeout:
                return None, calls

    def test_after_a_failure_the_retry_only_pays_for_the_lines_that_are_missing(self):
        work = Path(tempfile.mkdtemp())
        lines = [self.line(5, "One."), self.line(30, "Two."), self.line(60, "Three.")]
        clips, calls = self.run_clips(work, lines, fail_on="Three.")
        self.assertIsNone(clips)
        self.assertEqual(calls, ["One.", "Two.", "Three."])
        clips, calls = self.run_clips(work, [dict(l) for l in lines])
        self.assertEqual(calls, ["Three."])                                   # the first two takes are reused
        self.assertEqual(len(clips), 3)
        self.assertEqual([Path(p).read_bytes() for _, p, _ in clips], [b"audio:One.", b"audio:Two.", b"audio:Three."])

    def test_a_different_line_or_voice_is_never_mistaken_for_a_cached_one(self):
        a = voice._cache_key("Easy now.", "V1", "calm")
        self.assertNotEqual(a, voice._cache_key("Easy now!", "V1", "calm"))
        self.assertNotEqual(a, voice._cache_key("Easy now.", "V2", "calm"))
        self.assertNotEqual(a, voice._cache_key("Easy now.", "V1", "fear"))
        self.assertEqual(a, voice._cache_key("Easy now.", "V1", "calm"))

    def test_old_clip_files_are_still_cleared_each_run(self):
        work = Path(tempfile.mkdtemp())
        (work / "clips").mkdir()
        (work / "clips" / "c_099.mp3").write_bytes(b"stale")
        clips, _ = self.run_clips(work, [self.line(5, "One.")])
        self.assertFalse((work / "clips" / "c_099.mp3").exists())
        self.assertEqual(len(clips), 1)


class FailedVoicingKeepsTheLinesTests(unittest.TestCase):
    def test_lines_are_saved_before_the_voices_so_a_failed_call_loses_nothing(self):
        import json
        import time
        from app import cutscenes
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"fv{time.time_ns()}", "x.mp4", "queued", "western", "Red Dead Redemption 2", time.time())).lastrowid
        work = pipeline.workdir(vid)
        work.mkdir(parents=True)
        (work / "source.mp4").write_bytes(b"x")
        (work / "scenes.json").write_text(json.dumps([{"t": 0, "activity": "travel"}]))
        (work / "transcript.json").write_text("[]")
        line = {"start": 5.0, "line": "Easy now.", "persona": "character", "emotion": "calm", "max_duration": 4, "opp_id": "b1",
                "beat_kinds": "hunting", "salience": 6, "silent": False}
        with mock.patch("app.pipeline.scenes.video_length", return_value=120.0), \
                mock.patch("app.pipeline.ensure_cuts", return_value=cutscenes.empty()), \
                mock.patch("app.pipeline.sentiment.ensure", return_value=[]), \
                mock.patch("app.pipeline.writer.write_lines", return_value=([dict(line)], [])), \
                mock.patch("app.pipeline.writer.fill_missed", side_effect=lambda *a, **k: (a[4], 0)), \
                mock.patch("app.pipeline.evolve.refine", side_effect=lambda lines, *a, **k: lines), \
                mock.patch("app.pipeline.voice.make_clips", side_effect=requests.exceptions.ConnectTimeout("network")):
            pipeline.regenerate(vid)
        row = db.one("SELECT status, error FROM videos WHERE id=?", (vid,))
        self.assertEqual(row["status"], "failed")
        self.assertIn("ConnectTimeout", row["error"])
        self.assertIn("Easy now.", [r["line"] for r in db.rows("SELECT line FROM lines WHERE video_id=?", (vid,))])

    def test_the_page_offers_voice_and_build_again_on_such_a_video(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        self.assertIn("v.status==='failed' && v.stats && !v.has_video", html)
        self.assertIn("Voice and build again", html)


if __name__ == "__main__":
    unittest.main()
