import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import sources  # noqa: E402


class LocalSourceTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def touch(self, name, age=60):
        p = self.d / name
        p.write_text("x")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_local_detection_and_normalize(self):
        self.assertTrue(sources.is_local("/a/b"))
        self.assertTrue(sources.is_local("~/x"))
        self.assertFalse(sources.is_local("1AbC_drive_id"))
        self.assertEqual(sources.normalize("~/x"), str(Path("~/x").expanduser()))

    def test_lists_settled_videos_and_sidecars(self):
        self.touch("run1.mp4")
        self.touch("run1.srt")
        self.touch("notes.txt")
        self.touch(".hidden.mp4")
        self.touch("copying.mov", age=1)
        vids, subs = sources.list_videos(str(self.d))
        self.assertEqual([v["name"] for v in vids], ["run1.mp4"])
        self.assertIn("run1", subs)

    def test_start_button_does_not_wait_for_a_fresh_upload_to_settle(self):
        self.touch("fresh.mp4", age=1)
        self.assertEqual(sources.list_videos(str(self.d))[0], [])
        vids, _ = sources.list_videos(str(self.d), settle=0)
        self.assertEqual([v["name"] for v in vids], ["fresh.mp4"])

    def test_fetch_and_read(self):
        v = self.touch("a.mp4")
        s = self.touch("a.srt")
        dest = self.d / "copy.mp4"
        sources.fetch(str(v), dest)
        self.assertTrue(dest.exists())
        self.assertEqual(sources.read_text(str(s)), "x")

    def test_missing_folder_raises(self):
        with self.assertRaises(RuntimeError):
            sources.list_videos("/no/such/folder/here")


if __name__ == "__main__":
    unittest.main()
