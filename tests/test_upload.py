import os
import sys
import tempfile
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import sources  # noqa: E402
from app.server import app  # noqa: E402


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.inbox = tempfile.mkdtemp()
        sources.inbox_dir = lambda: __import__("pathlib").Path(self.inbox)
        self.c = TestClient(app)

    def up(self, name, data=b"abc"):
        return self.c.post("/api/upload", params={"name": name}, content=data)

    def test_saves_video_and_never_overwrites(self):
        r = self.up("run.mp4", b"x" * 1000)
        self.assertEqual(r.json(), {"saved": "run.mp4", "bytes": 1000})
        self.assertEqual(self.up("run.mp4").json()["saved"], "run-1.mp4")
        self.assertEqual(sorted(os.listdir(self.inbox)), ["run-1.mp4", "run.mp4"])

    def test_accepts_captions(self):
        self.assertEqual(self.up("run.srt").status_code, 200)

    def test_rejects_other_types_and_hidden(self):
        self.assertEqual(self.up("evil.sh").status_code, 400)
        self.assertEqual(self.up(".hidden.mp4").status_code, 400)

    def test_path_traversal_is_flattened(self):
        r = self.up("../../etc/passwd.mp4")
        self.assertEqual(r.json()["saved"], "passwd.mp4")
        self.assertEqual(os.listdir(self.inbox), ["passwd.mp4"])

    def test_no_partial_files_left(self):
        self.up("a.mp4")
        self.assertFalse([f for f in os.listdir(self.inbox) if f.endswith(".part")])


if __name__ == "__main__":
    unittest.main()
