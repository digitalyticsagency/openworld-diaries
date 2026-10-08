import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import db, evolve, guard, writer  # noqa: E402

OPTS = {"game": "RDR2", "persona": "mixed", "character": "", "story_point": "", "voice_notes": ""}
SCENES = [
    {"t": 10, "is_quiet_moment": True, "interactions": []},
    {"t": 40, "is_quiet_moment": False, "interactions": [{"kind": "damage_taken"}]},
    {"t": 100, "is_quiet_moment": True, "interactions": []},
]
SEGS = [{"start": 28, "end": 34, "speaker": "player", "text": "ugh not again"}]


def fake_claude_lines(system, user, max_tokens=8000):
    return json.dumps([
        {"start": 10, "max_duration": 8, "persona": "player", "tone": "nerdy", "emotion": "awe",
         "trigger_t": None, "line": "Look at that light on the water.", "delivery": "soft"},
        {"start": 30, "max_duration": 8, "persona": "player", "tone": "funny", "emotion": "calm",
         "trigger_t": None, "line": "Talking over the player.", "delivery": "x"},
        {"start": 41, "max_duration": 8, "persona": "character", "tone": "visceral", "emotion": "pain",
         "trigger_t": 40, "line": "Ngh. That one found bone.", "delivery": "pained"},
        {"start": 70, "max_duration": 8, "persona": "player", "tone": "visceral", "emotion": "joy",
         "trigger_t": 100, "line": "Invented joy with no event.", "delivery": "x"},
    ])


class WriterTests(unittest.TestCase):
    def test_guardrails_applied_to_model_output(self):
        system = writer.build_system("STYLE", OPTS)
        self.assertIn("SILENCE DURING SPEECH", system)
        self.assertIn("STYLE", system)
        self.assertNotIn("{GUARDRAILS}", system)
        windows = guard.speech_windows(SEGS)
        with mock.patch("app.writer.claude", fake_claude_lines):
            kept, dropped = writer.write_lines(SCENES, SEGS, windows, system, 120)
        texts = [k["line"] for k in kept]
        self.assertIn("Look at that light on the water.", texts)
        self.assertIn("Ngh. That one found bone.", texts)
        self.assertNotIn("Talking over the player.", texts)
        self.assertNotIn("Invented joy with no event.", texts)
        reasons = {d["line"]: d["reason"] for d in dropped}
        self.assertEqual(reasons["Talking over the player."], "overlaps speech")


class EvolveTests(unittest.TestCase):
    def setUp(self):
        for t in ("videos", "lines", "metrics"):
            db.run(f"DELETE FROM {t}")
        db.run("DELETE FROM prompt_versions WHERE status!='champion'")
        db.run("UPDATE prompt_versions SET n=0, score_sum=0")
        db.run("DELETE FROM settings WHERE key='last_mutate_total'")
        self.champ = db.one("SELECT * FROM prompt_versions WHERE status='champion'")

    def video(self, variant, line_scores, thumb=None):
        vid = db.run("INSERT INTO videos(drive_id,name,status,variant_id,created) VALUES(?,?,?,?,?)",
                     (f"d{time.time_ns()}", "v", "published", variant, time.time())).lastrowid
        for s in line_scores:
            db.run("INSERT INTO lines(video_id,start,persona,emotion,line,self_score,thumb) VALUES(?,?,?,?,?,?,?)",
                   (vid, 1, "player", "calm", "x", s, thumb))
        evolve.rescore_video(vid)
        return vid

    def test_thumbs_outweigh_self_score(self):
        v = self.video(self.champ["id"], [9, 9], thumb=-1)
        self.assertLess(db.one("SELECT combined_score c FROM videos WHERE id=?", (v,))["c"], 3)

    def test_mutate_after_enough_videos_then_promote(self):
        for _ in range(5):
            self.video(self.champ["id"], [5])
        with mock.patch("app.evolve.claude", return_value="STYLE GUIDANCE (version 2)\n- be better"):
            self.assertEqual(evolve.maybe_mutate(), "mutated")
            self.assertIsNone(evolve.maybe_mutate())  # one challenger at a time
        chal = db.one("SELECT * FROM prompt_versions WHERE status='challenger'")
        self.assertEqual(chal["parent_id"], self.champ["id"])
        for _ in range(3):
            self.video(chal["id"], [9])
        self.assertEqual(evolve.maybe_promote(), "promoted")
        self.assertEqual(db.one("SELECT id FROM prompt_versions WHERE status='champion'")["id"], chal["id"])

    def test_weak_challenger_not_promoted(self):
        chal_id = db.run("INSERT INTO prompt_versions(text,status,parent_id,created) VALUES('x','challenger',?,?)",
                         (self.champ["id"], time.time())).lastrowid
        for _ in range(3):
            self.video(self.champ["id"], [7])
            self.video(chal_id, [7])
        self.assertIsNone(evolve.maybe_promote())

    def test_refine_rewrites_weak_lines_and_keeps_guardrails(self):
        lines = [{"start": 10, "max_duration": 8, "persona": "player", "emotion": "calm",
                  "trigger_t": None, "line": "Meh."},
                 {"start": 60, "max_duration": 8, "persona": "player", "emotion": "calm",
                  "trigger_t": None, "line": "Fine line here."}]
        calls = []

        def fake(system, user, max_tokens=8000):
            calls.append(1)
            if len(calls) == 1:
                return json.dumps([{"i": 0, "overall": 3, "fix": "more sensory"},
                                   {"i": 1, "overall": 8, "fix": ""}])
            return json.dumps([{"i": 0, "line": "Cold air, wet leather, quiet road."}])
        with mock.patch("app.evolve.claude", fake):
            out = evolve.refine(lines, SCENES, [], "SYSTEM")
        self.assertEqual(out[0]["line"], "Cold air, wet leather, quiet road.")
        self.assertEqual(out[1]["self_score"], 8.0)


if __name__ == "__main__":
    unittest.main()
