import os
import sys
import tempfile
import time
import unittest

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import channel, db, travel, voices  # noqa: E402
from app.server import app  # noqa: E402


class CharacterVoiceTests(unittest.TestCase):
    def setUp(self):
        for n in voices.KNOWN_CHARACTERS:
            voices.assign_character(n, "")
        db.put("one_voice", "1")
        self.c = TestClient(app)

    def test_a_character_with_his_own_voice_speaks_in_it_and_others_keep_the_styles(self):
        base = voices.resolve("western", "character", "Arthur Morgan")                          # nothing chosen: the style's voice
        self.assertEqual(base, voices.configured("western", "character"))
        voices.assign_character("John Marston", "JOHNVOICE")
        self.assertEqual(voices.resolve("western", "character", "John Marston"), "JOHNVOICE")
        self.assertEqual(voices.resolve("western", "character", "  john   MARSTON "), "JOHNVOICE")
        self.assertEqual(voices.resolve("western", "character", "Arthur Morgan"), base)
        self.assertEqual(voices.resolve("western", "character"), base)

    def test_with_one_voice_off_only_the_character_persona_changes(self):
        voices.assign_character("John Marston", "JOHNVOICE")
        db.put("one_voice", "0")
        self.assertEqual(voices.resolve("western", "character", "John Marston"), "JOHNVOICE")
        self.assertEqual(voices.resolve("western", "companion", "John Marston"), voices.configured("western", "companion"))
        db.put("one_voice", "1")
        self.assertEqual(voices.resolve("western", "companion", "John Marston"), "JOHNVOICE")      # one voice: every line is the character's

    def test_clearing_goes_back_to_his_default_never_to_a_womans_style_voice(self):
        voices.assign_character("John Marston", "JOHNVOICE")
        voices.assign_character("John Marston", "")
        self.assertEqual(voices.character_voice("John Marston"), voices.DEFAULT_CHARACTER_VOICES["john marston"])
        self.assertIsNone(voices.chosen_voice("John Marston"))
        voices.assign_character("Arthur Morgan", "")
        self.assertIsNone(voices.character_voice("Arthur Morgan"))
        with self.assertRaises(ValueError):
            voices.assign_character("  ", "x")

    def test_endpoints_list_save_and_refuse_unknown_names(self):
        self.assertEqual([c["name"] for c in self.c.get("/api/voices/characters").json()], ["Arthur Morgan", "John Marston"])
        self.assertEqual(self.c.post("/api/voices/character", json={"name": "John Marston", "voice_id": "V1"}).status_code, 200)
        self.assertEqual(next(c for c in self.c.get("/api/voices/characters").json() if c["name"] == "John Marston")["voice"], "V1")
        listing = {c["name"]: c for c in (voices.characters())}
        self.assertFalse(listing["John Marston"]["default"])
        voices.assign_character("John Marston", "")
        self.assertTrue(next(c for c in self.c.get("/api/voices/characters").json() if c["name"] == "John Marston")["default"])
        self.assertEqual(self.c.post("/api/voices/character", json={"name": "Someone Else", "voice_id": "V1"}).status_code, 400)

    def test_a_video_as_john_shows_a_hint_until_he_has_a_voice(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,created,pack,game,character) VALUES(?,?,?,?,?,?,?)",
                     (f"cv{time.time_ns()}", "x.mp4", "ready", 1.0, "western", "Red Dead Redemption", "John Marston")).lastrowid
        row = lambda: next(v for v in self.c.get("/api/state").json()["videos"] if v["id"] == vid)
        self.assertTrue(row()["voice_hint"])
        self.assertEqual(row()["voices"]["character"], voices.DEFAULT_CHARACTER_VOICES["john marston"])
        self.assertEqual(row()["character_used"], "John Marston")
        voices.assign_character("John Marston", "V1")
        self.assertFalse(row()["voice_hint"])
        self.assertEqual(row()["voices"]["character"], "V1")


class CharacterDaydreamTests(unittest.TestCase):
    def test_each_character_has_his_own_ten_themes_and_others_get_the_general_ones(self):
        arthur, john = channel.themes_for("Arthur Morgan"), channel.themes_for("John Marston")
        self.assertEqual((len(arthur), len(john)), (10, 10))
        self.assertTrue(set(arthur).isdisjoint(john))
        self.assertEqual(channel.themes_for("morgan"), arthur)
        self.assertEqual(channel.themes_for("Franklin"), channel.THEMES)
        self.assertEqual(channel.themes_for(None), channel.THEMES)

    def test_the_themes_are_generic_and_never_name_a_real_character(self):
        from app import guard
        for t in channel.themes_for("Arthur Morgan") + channel.themes_for("John Marston"):
            self.assertIsNone(guard.CANON.search(t), t)

    def test_the_travel_thoughts_use_the_characters_themes(self):
        notes = [{"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel"} for t in range(0, 300, 3)]
        spots = [{"id": f"d{i}", "t": 60.0 + 40 * i, "kind": "daydream", "kinds": ["daydream"], "why": "x", "max_words": 12,
                  "max_duration": 5.0, "salience": 4.5, "beat_t": None} for i in range(4)]
        john = channel.themes_for("John Marston")
        travel.ORDER, saved = ["wistful"], travel.ORDER
        try:
            travel.assign(spots, notes, john)
        finally:
            travel.ORDER = saved
        self.assertTrue(all(any(t in o["why"] for t in john) for o in spots))
        self.assertFalse(any(any(t in o["why"] for t in channel.themes_for("Arthur Morgan")) for o in spots))


if __name__ == "__main__":
    unittest.main()


class JohnIsNeverAWomanTests(unittest.TestCase):
    """A cozy style's own character voice is a woman's. John Marston must never be spoken in it."""

    def test_in_every_style_john_speaks_in_his_own_man_voice(self):
        from app import styles
        voices.assign_character("John Marston", "")
        for pack in styles.PACKS:
            self.assertEqual(voices.resolve(pack, "character", "John Marston"),
                             voices.chosen_voice("John Marston") or voices.DEFAULT_CHARACTER_VOICES["john marston"], pack)
        db.put("one_voice", "1")
        self.assertEqual(voices.resolve("cozy", "character", "John Marston"), voices.DEFAULT_CHARACTER_VOICES["john marston"])
        self.assertNotEqual(voices.resolve("cozy", "character", "John Marston"), styles.PACKS["cozy"]["voices"]["character"])


if __name__ == "__main__":
    unittest.main()
