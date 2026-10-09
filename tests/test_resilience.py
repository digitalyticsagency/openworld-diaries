import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import db, llm, pipeline, scenes  # noqa: E402


class ParseJsonTests(unittest.TestCase):
    def test_invalid_escapes_are_repaired_not_fatal(self):
        self.assertEqual(llm.parse_json('{"a": "it\\\'s \\_ fine"}')["a"], "it\\'s \\_ fine")
        self.assertEqual(llm.parse_json('```json\n[{"a": "\\.x"}]\n```')[0]["a"], "\\.x")

    def test_valid_escapes_are_untouched(self):
        self.assertEqual(llm.parse_json('{"a": "x\\ny \\"q\\" \\\\ \\u00e9"}')["a"], 'x\ny "q" \\ é')
        self.assertEqual(llm.parse_json('{"a": "C:\\\\x"}')["a"], "C:\\x")

    def test_real_garbage_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            llm.parse_json("not json at all")


class ResumableScanTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())
        self.frames = []
        for i in range(25):
            f = self.work / f"f{i}.jpg"
            f.write_bytes(b"x")
            self.frames.append((i * 3, f))

    def fake(self, fail_on=None):
        calls = []

        def f(parts):
            calls.append(len([p for p in parts if isinstance(p, tuple)]))
            if fail_on is not None and len(calls) == fail_on:
                raise ValueError("model gave unreadable output")
            return [{"is_quiet_moment": True} for _ in range(calls[-1])]
        return f, calls

    def test_failed_scan_keeps_finished_batches_and_resumes(self):
        with mock.patch("app.scenes.extract_frames", return_value=self.frames):
            f1, calls1 = self.fake(fail_on=2)
            with mock.patch("app.scenes.gemini_json", f1):
                with self.assertRaises(ValueError):
                    scenes.analyze("v.mp4", self.work, "g")
            self.assertEqual(len(json.loads((self.work / "scenes.partial.json").read_text())), 10)
            f2, calls2 = self.fake()
            with mock.patch("app.scenes.gemini_json", f2):
                notes = scenes.analyze("v.mp4", self.work, "g")
        self.assertEqual(calls2, [10, 5])  # the first batch is not paid for again
        self.assertEqual([n["t"] for n in notes], [i * 3 for i in range(25)])
        self.assertFalse((self.work / "scenes.partial.json").exists())
        self.assertTrue((self.work / "scenes.json").exists())


class ReuseScanTests(unittest.TestCase):
    def video(self, status="ready"):
        return db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                      (f"r{time.time_ns()}", "clip.mp4", status, time.time())).lastrowid

    def test_same_file_reuses_the_earlier_scan(self):
        a, b = self.video(), self.video("queued")
        for vid in (a, b):
            pipeline.workdir(vid).mkdir(parents=True, exist_ok=True)
            (pipeline.workdir(vid) / "source.mp4").write_bytes(b"same bytes" * 1000)
        (pipeline.workdir(a) / "scenes.json").write_text('[{"t": 0}]')
        (pipeline.workdir(a) / "transcript.json").write_text("[]")
        self.assertEqual(pipeline._reuse_scan(a, pipeline.workdir(a) / "source.mp4", pipeline.workdir(a)), None)
        self.assertEqual(pipeline._reuse_scan(b, pipeline.workdir(b) / "source.mp4", pipeline.workdir(b)), a)
        self.assertEqual((pipeline.workdir(b) / "scenes.json").read_text(), '[{"t": 0}]')

    def test_different_files_do_not_share(self):
        a, b = self.video(), self.video("queued")
        for vid, data in ((a, b"one" * 1000), (b, b"two" * 1000)):
            pipeline.workdir(vid).mkdir(parents=True, exist_ok=True)
            (pipeline.workdir(vid) / "source.mp4").write_bytes(data)
        (pipeline.workdir(a) / "scenes.json").write_text("[]")
        (pipeline.workdir(a) / "transcript.json").write_text("[]")
        pipeline._reuse_scan(a, pipeline.workdir(a) / "source.mp4", pipeline.workdir(a))
        self.assertIsNone(pipeline._reuse_scan(b, pipeline.workdir(b) / "source.mp4", pipeline.workdir(b)))
        self.assertFalse((pipeline.workdir(b) / "scenes.json").exists())


class BackfillTests(unittest.TestCase):
    def test_older_videos_without_a_fingerprint_are_still_matched(self):
        def make(status):
            v = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                       (f"o{time.time_ns()}", "c.mp4", status, time.time())).lastrowid
            pipeline.workdir(v).mkdir(parents=True, exist_ok=True)
            (pipeline.workdir(v) / "source.mp4").write_bytes(b"legacy" * 5000)
            return v
        old, new = make("ready"), make("queued")
        (pipeline.workdir(old) / "scenes.json").write_text("[]")
        (pipeline.workdir(old) / "transcript.json").write_text("[]")
        self.assertIsNone(db.one("SELECT fingerprint FROM videos WHERE id=?", (old,))["fingerprint"])
        self.assertEqual(pipeline._reuse_scan(new, pipeline.workdir(new) / "source.mp4", pipeline.workdir(new)), old)
        self.assertIsNotNone(db.one("SELECT fingerprint FROM videos WHERE id=?", (old,))["fingerprint"])


if __name__ == "__main__":
    unittest.main()
