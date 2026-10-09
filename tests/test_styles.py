import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ["ELEVEN_VOICE_PLAYER"] = "ENV_PLAYER"
os.environ["ELEVEN_VOICE_CHARACTER"] = "ENV_CHARACTER"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, detect, pipeline, styles, voices, writer  # noqa: E402
from app.config import PROMPTS  # noqa: E402
from app.server import app  # noqa: E402


class PackTests(unittest.TestCase):
    def test_ten_complete_packs(self):
        self.assertEqual(len(styles.PACKS), 10)
        for k, p in styles.PACKS.items():
            for field in ("name", "games", "hints", "text", "voices"):
                self.assertTrue(p[field], f"{k} missing {field}")
            self.assertEqual(set(p["voices"]), {"character", "companion"}, k)
            self.assertTrue(p["text"].startswith("GENRE:"), k)

    def test_six_personalities_and_safe_fallbacks(self):
        self.assertEqual(len(styles.PERSONALITIES), 6)
        self.assertEqual(styles.pack_id("nonsense"), styles.DEFAULT_PACK)
        self.assertEqual(styles.pack_id(None), styles.DEFAULT_PACK)
        self.assertEqual(styles.personality_id("nonsense"), styles.DEFAULT_PERSONALITY)

    def test_default_voice_ids_are_distinct_per_persona_within_a_pack(self):
        for k, p in styles.PACKS.items():
            self.assertNotEqual(p["voices"]["character"], p["voices"]["companion"], k)

    def test_pack_and_personality_reach_the_prompt_without_dropping_guardrails(self):
        opts = {"game": "GTA V", "persona": "mixed", "pack": "crime", "personality": "sarcastic"}
        system = writer.build_system("STYLE", opts)
        self.assertIn("GENRE: Crime city", system)
        self.assertIn("PERSONALITY: Sarcastic", system)
        self.assertIn("SILENCE DURING SPEECH", system)
        self.assertLess(system.index("HARD RULES"), system.index("GENRE: Crime city"))
        self.assertNotIn("{GENRE_PACK}", system)
        self.assertNotIn("{PERSONALITY}", system)
        self.assertIn("every hard rule above still applies", system)

    def test_every_pack_renders_the_scene_prompt(self):
        tpl = (PROMPTS / "scene.md").read_text()
        for k, p in styles.PACKS.items():
            out = tpl.format(game="G", hints=p["hints"], times=[0, 3])
            self.assertIn(p["hints"], out)


class VoiceTests(unittest.TestCase):
    def setUp(self):
        db.run("DELETE FROM settings WHERE key LIKE 'voice:%'")

    def test_precedence_user_choice_then_pack_default_then_env(self):
        self.assertEqual(voices.resolve("horror", "player"), "ENV_PLAYER")
        self.assertEqual(voices.resolve("horror", "character"), styles.PACKS["horror"]["voices"]["character"])
        voices.assign("horror", "character", "MINE")
        self.assertEqual(voices.resolve("horror", "character"), "MINE")
        self.assertEqual(voices.resolve("cozy", "character"), styles.PACKS["cozy"]["voices"]["character"])

    def test_unknown_pack_uses_default_pack(self):
        self.assertEqual(voices.resolve("zzz", "companion"), styles.PACKS[styles.DEFAULT_PACK]["voices"]["companion"])


class DetectTests(unittest.TestCase):
    def test_detect_parses_and_sanitizes(self):
        with mock.patch("app.detect.sample_frames", return_value=[]), \
                mock.patch("app.detect.gemini_json", return_value={"game": "Forza Horizon 5", "pack": "racing", "confidence": 0.9, "reason": "cars"}):
            r = detect.detect("x.mp4", "/tmp")
        self.assertEqual((r["game"], r["pack"]), ("Forza Horizon 5", "racing"))
        with mock.patch("app.detect.sample_frames", return_value=[]), \
                mock.patch("app.detect.gemini_json", return_value=[{"pack": "not-a-pack"}]):
            r = detect.detect("x.mp4", "/tmp")
        self.assertEqual((r["game"], r["pack"]), ("unknown", styles.DEFAULT_PACK))


class ServerStyleTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def vid(self, status):
        return db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                      (f"st{time.time_ns()}", "clip.mp4", status, time.time())).lastrowid

    def test_state_lists_catalog(self):
        s = self.c.get("/api/state").json()
        self.assertEqual(len(s["packs"]), 10)
        self.assertEqual(len(s["personalities"]), 6)
        self.assertTrue(s["ask_style"])

    def test_start_requires_choose_style_and_saves_choice(self):
        ready = self.vid("ready")
        self.assertEqual(self.c.post(f"/api/videos/{ready}/start", json={"pack": "crime"}).status_code, 400)
        waiting = self.vid("choose_style")
        with mock.patch("app.server.run_bg") as bg:
            r = self.c.post(f"/api/videos/{waiting}/start", json={"pack": "crime", "personality": "hype", "game": " GTA V "})
        self.assertEqual(r.status_code, 200)
        row = db.one("SELECT pack, personality, game, status FROM videos WHERE id=?", (waiting,))
        self.assertEqual((row["pack"], row["personality"], row["game"], row["status"]), ("crime", "hype", "GTA V", "queued"))
        bg.assert_called_once()

    def test_invalid_choices_fall_back_safely(self):
        v = self.vid("choose_style")
        pipeline.start(v, "bogus", "bogus", "")
        row = db.one("SELECT pack, personality, game FROM videos WHERE id=?", (v,))
        self.assertEqual((row["pack"], row["personality"], row["game"]), (styles.DEFAULT_PACK, "balanced", None))

    def test_voice_assign_endpoint_validates_persona(self):
        self.assertEqual(self.c.post("/api/voices/assign", json={"pack": "cozy", "persona": "villain", "voice_id": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/voices/assign", json={"pack": "cozy", "persona": "player", "voice_id": "V1"}).status_code, 200)
        self.assertEqual(self.c.get("/api/voices/assigned", params={"pack": "cozy"}).json()["player"], "V1")

    def test_process_waits_for_style_when_asking_and_proceeds_when_not(self):
        v = self.vid("queued")
        work = pipeline.workdir(v)
        work.mkdir(parents=True, exist_ok=True)
        (work / "source.mp4").write_bytes(b"x")
        sug = {"game": "Skyrim", "pack": "fantasy", "confidence": 0.8, "reason": "dragons"}
        db.put("ask_style", "1")
        with mock.patch("app.pipeline.detect.detect", return_value=sug):
            pipeline.process(v)
        row = db.one("SELECT status, game, pack FROM videos WHERE id=?", (v,))
        self.assertEqual((row["status"], row["game"], row["pack"]), ("choose_style", "Skyrim", None))
        db.put("ask_style", "0")
        with mock.patch("app.pipeline.transcript.get_transcript", side_effect=RuntimeError("stop here")):
            pipeline.process(v)
        row = db.one("SELECT pack, status FROM videos WHERE id=?", (v,))
        self.assertEqual(row["pack"], "fantasy")
        self.assertEqual(row["status"], "failed")
        db.put("ask_style", "1")


if __name__ == "__main__":
    unittest.main()
