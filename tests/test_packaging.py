import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import db, packaging, pipeline, thumbnail  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "location": "unknown", **kw}


def beat(t, sal, kinds=("kill",), why="a wolf is shot"):
    return {"t": float(t), "salience": sal, "kinds": list(kinds), "why": why}


def sheet(**kw):
    notes = [frame(t, animals=[{"species": "Wolf", "behavior": "approaching"}] if t == 99 else [],
                   location="Valentine" if t < 60 else "unknown") for t in range(0, 300, 3)]
    chapters = [{"t": 0, "title": "Riding"}, {"t": 90, "title": "Hunting"}, {"t": 200, "title": "Camp"}]
    lines = [{"start": 100, "line": "Easy now.", "silent": False, "dropped": False, "salience": 8},
             {"start": 150, "line": "Quiet thought.", "silent": True, "dropped": False, "salience": 9}]
    return packaging.facts("Red Dead Redemption 2", "Western outlaw", 300, notes,
                           [beat(100, 8.0), beat(200, 6.0, why="a strongbox opens"), beat(250, 9.0, kinds=("npc_threat",))], chapters, lines)


GOOD = {"titles": ["Riding Into Valentine With An Inner Voice", "A Wolf On The Trail"], "description": "A quiet ride, a wolf, and a camp.",
        "tags": ["Red Dead Redemption 2", "RDR2", "free roam"], "pinned_comment": "Would you have shot the wolf?",
        "thumb_texts": ["WOLF ON THE TRAIL", "ONE SHOT", "QUIET RIDE"], "thumb_moment": 0}


class FactSheetTests(unittest.TestCase):
    def test_the_sheet_holds_only_real_things(self):
        fs = sheet()
        self.assertEqual(fs["animals_seen"], ["wolf"])
        self.assertEqual(fs["places_seen"], ["Valentine"])
        self.assertEqual(fs["chapters"][1], "1:30 Hunting")
        self.assertEqual([m["at"] for m in fs["moments"]], ["1:40", "3:20"])         # the spoken threat is not a moment
        self.assertEqual(fs["sample_lines"], ["Easy now."])                          # silent thoughts are not quoted
        self.assertEqual(fs["length"], "5:00")


class HonestyChecksTests(unittest.TestCase):
    def test_a_good_answer_passes_and_gets_chapters_and_a_disclosure(self):
        pkg = packaging.validate(GOOD, sheet())
        self.assertEqual(pkg["titles"], GOOD["titles"])
        self.assertIn("Chapters:\n0:00 Riding\n1:30 Hunting\n3:20 Camp", pkg["description"])
        self.assertTrue(pkg["description"].endswith(packaging.DISCLOSURE))
        self.assertEqual(pkg["pinned_comment"], "Would you have shot the wolf?")
        self.assertEqual(pkg["thumb_moment"], 0)
        self.assertIn("red dead redemption 2", pkg["tags"])
        self.assertIn("inner voice", pkg["tags"])

    def test_clickbait_shouting_and_overlong_titles_are_dropped(self):
        bad = {**GOOD, "titles": ["You Won't Believe This Wolf", "WOLF ATTACK ON THE TRAIL TODAY", "x" * 101, "Shocking Ride", "A calm ride"]}
        self.assertEqual(packaging.validate(bad, sheet())["titles"], ["A calm ride"])

    def test_with_nothing_usable_there_is_still_a_true_fallback(self):
        pkg = packaging.validate({"titles": ["You won't believe it"], "description": "mind-blowing", "pinned_comment": "Amazing!",
                                  "thumb_texts": ["THIS IS WAY TOO LONG FOR A THUMBNAIL"], "thumb_moment": 99}, sheet())
        self.assertTrue(pkg["titles"] and "Red Dead Redemption 2" in pkg["titles"][0])
        self.assertIn("AI inner-voice commentary", pkg["description"])
        self.assertTrue(pkg["pinned_comment"].endswith("?") and "1:40" in pkg["pinned_comment"])
        self.assertEqual(len(pkg["thumb_texts"]), 3)
        self.assertTrue(all(len(t.split()) <= 4 for t in pkg["thumb_texts"]))
        self.assertEqual(pkg["thumb_moment"], 0)                                      # the strongest real moment

    def test_tags_are_clean_unique_and_within_the_limit(self):
        tags = packaging._clean_tags(["#Wolf", "wolf", "x" * 40, "  Free   Roam "] + [f"tag{i}" * 3 for i in range(200)], "Red Dead")
        self.assertEqual(tags[:4], ["red dead", "gameplay", "inner voice", "wolf"])
        self.assertIn("free roam", tags)
        self.assertLessEqual(sum(len(t) + 1 for t in tags), packaging.MAX_TAGS_CHARS)
        self.assertTrue(all(len(t) <= 30 for t in tags))

    def test_thumbnail_text_is_short_capitals_without_odd_symbols(self):
        self.assertEqual(packaging._clean_thumb("wolf on the trail!"), "WOLF ON THE TRAIL!")
        self.assertIsNone(packaging._clean_thumb("one two three four five"))
        self.assertIsNone(packaging._clean_thumb("shocking"))
        self.assertEqual(packaging._clean_thumb("<b>hey</b>"), "BHEYB")

    def test_the_model_is_asked_with_the_fact_sheet_only(self):
        with mock.patch("app.packaging.claude", return_value=json.dumps(GOOD)) as c:
            pkg = packaging.generate(sheet())
        system, user = c.call_args.args[0], json.loads(c.call_args.args[1])
        self.assertIn("HONEST ONLY", system)
        self.assertEqual(user["game"], "Red Dead Redemption 2")
        self.assertEqual(pkg["titles"][0], GOOD["titles"][0])


class ThumbnailTests(unittest.TestCase):
    def test_all_three_looks_draw_and_differ(self):
        d = Path(tempfile.mkdtemp())
        Image.new("RGB", (1920, 1080), (90, 120, 160)).save(d / "f.jpg")
        outs = [thumbnail.render(d / "f.jpg", "WOLF ON THE TRAIL", v, d / f"t{v}.jpg") for v in thumbnail.VARIANTS]
        raw = []
        for o in outs:
            with Image.open(o) as im:
                self.assertEqual(im.size, (1280, 720))
                raw.append(im.tobytes())
        self.assertEqual(len(set(raw)), 3)

    def test_a_frame_is_cut_from_a_real_video(self):
        d = Path(tempfile.mkdtemp())
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=10:duration=3", "-y", str(d / "v.mp4")], check=True)
        out = thumbnail.grab(d / "v.mp4", 1.0, d / "g.jpg")
        with Image.open(out) as im:
            self.assertEqual(im.size, (1280, 720))


class EndpointTests(unittest.TestCase):
    def test_make_and_read_packaging_and_thumbnails(self):
        c = TestClient(app)
        vid = db.run("INSERT INTO videos(drive_id,name,status,created,pack) VALUES(?,?,?,?,?)",
                     ("pk1", "clip.mp4", "ready", 1.0, "western")).lastrowid
        self.assertEqual(c.get(f"/api/videos/{vid}/packaging").status_code, 404)
        self.assertEqual(c.post(f"/api/videos/{vid}/packaging").status_code, 400)      # no source video here
        w = pipeline.workdir(vid)
        w.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=10:duration=3", "-y", str(w / "source.mp4")], check=True)
        (w / "scenes.json").write_text(json.dumps([frame(t) for t in (0, 1, 2)]))
        (w / "transcript.json").write_text("[]")
        with mock.patch("app.packaging.claude", return_value=json.dumps(GOOD)):
            r = c.post(f"/api/videos/{vid}/packaging")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(c.get(f"/api/videos/{vid}/packaging").json()["titles"][0], GOOD["titles"][0])
        self.assertEqual(c.get(f"/api/videos/{vid}/thumb/A").headers["content-type"], "image/jpeg")
        self.assertEqual(c.get(f"/api/videos/{vid}/thumb/Z").status_code, 404)


if __name__ == "__main__":
    unittest.main()
