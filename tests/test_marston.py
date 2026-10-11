import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, guard, knowledge, pipeline, writer  # noqa: E402
from app.server import app  # noqa: E402


class MarstonPackTests(unittest.TestCase):
    def test_each_red_dead_title_gets_its_own_pack(self):
        self.assertEqual(knowledge.name_for_game("Red Dead Redemption"), "Red Dead Redemption")
        self.assertEqual(knowledge.name_for_game("red dead redemption 1 remastered"), "Red Dead Redemption")
        self.assertEqual(knowledge.name_for_game("Red Dead Redemption: Undead Nightmare"), "Red Dead Redemption")
        self.assertEqual(knowledge.name_for_game("Red Dead Redemption 2"), "Red Dead Redemption 2")
        self.assertEqual(knowledge.name_for_game("RDR2 epilogue"), "Red Dead Redemption 2")
        self.assertEqual(knowledge.name_for_game("Red Dead Online"), "Red Dead Redemption 2")
        self.assertEqual(knowledge.name_for_game("Red Dead"), "Red Dead Redemption 2")          # unchanged for a bare title

    def test_the_first_game_speaks_as_john_marston(self):
        text = knowledge.for_game("Red Dead Redemption")
        self.assertIn("John Marston", text)
        self.assertNotIn("Arthur", text)
        self.assertIn("Avoid anything that happens later", text)
        self.assertIn("never name a real character", text)

    def test_the_second_game_knows_about_the_epilogue_and_stays_cautious(self):
        text = knowledge.for_game("Red Dead Redemption 2")
        self.assertIn("THE EPILOGUE AND JOHN MARSTON", text)
        self.assertIn("never reveal anything later than the scene shows", text)

    def test_the_prompt_for_the_first_game_names_john_not_arthur(self):
        db.put("character", "Arthur Morgan")
        db.put("voice_notes", "Weary outlaw, loyal, brave, rescuing, winning")
        o = pipeline.video_opts({"game": "Red Dead Redemption", "pack": "western"})
        self.assertEqual(o["character"], "John Marston")
        self.assertIn("Gravelly", o["voice_notes"])
        system = writer.build_system("S", {**o, "persona": "character", "personality": "balanced"})
        self.assertIn("John Marston", system)
        self.assertNotIn("Arthur Morgan", system)


class CharacterPerVideoTests(unittest.TestCase):
    def setUp(self):
        db.put("character", "Arthur Morgan")
        db.put("voice_notes", "Weary outlaw, loyal, brave, rescuing, winning")
        self.c = TestClient(app)

    def vid(self, status="choose_style"):
        return db.run("INSERT INTO videos(drive_id,name,status,created,pack,game) VALUES(?,?,?,?,?,?)",
                      (f"jm{time.time_ns()}", "x.mp4", status, 1.0, "western", "Red Dead Redemption 2")).lastrowid

    def test_a_character_chosen_for_one_video_beats_the_saved_one(self):
        v = {"game": "Red Dead Redemption 2", "pack": "western", "character": "John Marston"}
        self.assertEqual(pipeline.video_opts(v)["character"], "John Marston")
        self.assertEqual(pipeline.video_opts({**v, "character": ""})["character"], "Arthur Morgan")
        self.assertEqual(pipeline.video_opts({**v, "character": None})["character"], "Arthur Morgan")

    def test_start_saves_the_character_for_that_video_only(self):
        a, b = self.vid(), self.vid()
        with mock.patch("app.server.run_bg"):
            self.assertEqual(self.c.post(f"/api/videos/{a}/start", json={"pack": "rdr2_camp", "personality": "balanced",
                                                                           "game": "Red Dead Redemption 2", "character": "  John Marston "}).status_code, 200)
        self.assertEqual(db.one("SELECT character FROM videos WHERE id=?", (a,))["character"], "John Marston")
        self.assertIsNone(db.one("SELECT character FROM videos WHERE id=?", (b,))["character"])
        self.assertEqual(db.get("character"), "Arthur Morgan")

    def test_regenerate_can_set_and_clear_it_and_leaves_it_alone_otherwise(self):
        v = self.vid("ready")
        pipeline.start(v, "western", "balanced", "Red Dead Redemption 2", "John Marston")
        pipeline.start(v, "western", "balanced", "Red Dead Redemption 2")                    # no character given: unchanged
        self.assertEqual(db.one("SELECT character FROM videos WHERE id=?", (v,))["character"], "John Marston")
        pipeline.start(v, "western", "balanced", "Red Dead Redemption 2", "")
        self.assertIsNone(db.one("SELECT character FROM videos WHERE id=?", (v,))["character"])
        pipeline.start(v, "western", "balanced", "Red Dead Redemption 2", "x" * 90)
        self.assertEqual(len(db.one("SELECT character FROM videos WHERE id=?", (v,))["character"]), 60)

    def test_the_state_carries_the_character(self):
        v = self.vid("ready")
        pipeline.start(v, "western", "balanced", "Red Dead Redemption 2", "John Marston")
        row = next(x for x in self.c.get("/api/state").json()["videos"] if x["id"] == v)
        self.assertEqual(row["character"], "John Marston")

    def test_marston_and_morgan_are_kept_out_of_daydreams(self):
        for name in ("Marston", "Morgan"):
            self.assertEqual(guard.content_reason({"daydream": True, "beat_kinds": "daydream"}, f"Someday {name} will rest."),
                             "daydream names a real character")


if __name__ == "__main__":
    unittest.main()
