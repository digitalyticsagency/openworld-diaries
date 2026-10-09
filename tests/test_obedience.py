"""The settings must be obeyed: the slider drives how much is planned and spoken, keys are managed safely."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import beats, envfile, gaps, guard, writer  # noqa: E402
from app.server import app  # noqa: E402


def make_world():
    segs = [{"start": s, "end": s + 3, "speaker": "game"} for s in range(8, 440, 29)]
    notes = []
    for t in range(0, 450, 3):
        n = {"t": t, "interactions": [], "notable_details": [], "location": "town"}
        if t % 45 == 0 and t:
            n["interactions"] = [{"kind": "kill" if t % 90 == 0 else "damage_taken", "intensity": 2}]
        notes.append(n)
    win = guard.speech_windows(segs)
    return notes, win, gaps.free_gaps(win, 450)


class SliderObedienceTests(unittest.TestCase):
    def plan(self, eager):
        notes, win, free = make_world()
        opps, _ = beats.opportunities(beats.find_beats(notes), free, 450, eager)
        return beats.select(opps, eager, 450)[0]

    def test_more_eager_means_strictly_more_planned_lines_across_the_range(self):
        counts = [len(self.plan(e)) for e in (2, 5, 7, 9, 10)]
        self.assertEqual(counts, sorted(counts), counts)
        self.assertGreater(counts[-1], counts[0] + 4)

    def test_high_eagerness_supplies_more_spots_than_the_old_fixed_supply(self):
        notes, win, free = make_world()
        b = beats.find_beats(notes)
        low = beats.opportunities(b, free, 450, 6)[0]
        high = beats.opportunities(b, free, 450, 10)[0]
        self.assertGreater(len(high), len(low))

    def test_every_planned_line_still_fits_its_room(self):
        chosen = self.plan(10)
        for a, b in zip(chosen, chosen[1:]):
            self.assertLessEqual(a["t"] + guard.est_duration(" ".join(["w"] * a["max_words"])) + 2.5, b["t"] + 1e-6)


class WriterObedienceTests(unittest.TestCase):
    def opp(self, i, t, words=7):
        return {"id": f"b{i}", "t": t, "beat_t": None, "salience": 5, "kinds": ["ambient"], "why": "w",
                "max_words": words, "max_duration": 6, "kind": "micro"}

    def test_a_line_that_is_only_too_long_is_shortened_not_lost(self):
        opps = [self.opp(0, 10)]
        answers = iter([
            json.dumps([{"opportunity": "b0", "persona": "player", "emotion": "calm", "line": "this is a far too wordy line for the room"}]),
            json.dumps([{"opportunity": "b0", "line": "Too wordy. Cut."}])])
        with mock.patch("app.writer.claude", lambda *a, **k: next(answers)):
            kept, dropped = writer.write_lines([], [], [], "SYS", 60, opps)
        self.assertEqual([k["line"] for k in kept], ["Too wordy. Cut."])
        self.assertEqual([d for d in dropped if d["reason"] == "too long"], [])

    def test_unanswered_planned_spots_get_a_second_chance_by_default(self):
        opps = [self.opp(0, 10), self.opp(1, 30)]
        asked = {}

        def fake(system, user, max_tokens=8000):
            asked.update(json.loads(user))
            return json.dumps([{"opportunity": o["id"], "persona": "player", "emotion": "calm", "line": f"Line {o['id']}."}
                               for o in asked["opportunities"]])
        first = [{"opportunity": "b0", "persona": "player", "emotion": "calm", "line": "Only the first."}]
        kept, _ = guard.enforce([{**first[0], "start": 10, "max_duration": 6, "max_words": 7, "opp_id": "b0", "salience": 5}], [], [])
        with mock.patch("app.writer.claude", fake):
            merged, n = writer.fill_missed([], [], [], "SYS", kept, opps)
        self.assertEqual([o["id"] for o in asked["opportunities"]], ["b1"])
        self.assertEqual((n, len(merged)), (1, 2))
        self.assertIn("Do not skip", asked["note"])

    def test_prompt_demands_a_line_for_every_opportunity(self):
        system = writer.build_system("S", {"game": "g", "persona": "mixed", "pack": "western", "personality": "balanced"})
        self.assertIn("exactly one line for every opportunity", system)


class KeysTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / ".env"
        self.path.write_text("# my notes\nGEMINI_API_KEY=old-value-1234\nOTHER=keep\n")
        self._p = mock.patch.object(envfile, "ENV_PATH", self.path)
        self._p.start()
        self.addCleanup(self._p.stop)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY")])
        os.environ["GEMINI_API_KEY"] = "old-value-1234"
        self.c = TestClient(app)

    def test_saving_replaces_in_place_and_keeps_everything_else(self):
        envfile.set_value("GEMINI_API_KEY", "AQ.new-secret-9999")
        text = self.path.read_text()
        self.assertIn("GEMINI_API_KEY=AQ.new-secret-9999", text)
        self.assertEqual(text.count("GEMINI_API_KEY"), 1)
        self.assertIn("# my notes", text)
        self.assertIn("OTHER=keep", text)
        self.assertEqual(os.environ["GEMINI_API_KEY"], "AQ.new-secret-9999")
        self.assertEqual(oct(self.path.stat().st_mode)[-3:], "600")

    def test_new_key_is_appended_and_can_be_cleared(self):
        envfile.set_value("ANTHROPIC_API_KEY", "sk-test-abcd1234")
        self.assertIn("ANTHROPIC_API_KEY=sk-test-abcd1234", self.path.read_text())
        envfile.clear("ANTHROPIC_API_KEY")
        self.assertNotIn("ANTHROPIC_API_KEY", self.path.read_text())
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ)

    def test_bad_values_and_unknown_names_are_refused(self):
        for bad in ("", "has space", "line\nbreak", "semi;colon", '"quoted"; rm'):
            with self.assertRaises(ValueError):
                envfile.set_value("GEMINI_API_KEY", bad if bad != '"quoted"; rm' else "a b;c")
        with self.assertRaises(KeyError):
            envfile.set_value("PATH", "x")

    def test_status_never_reveals_a_secret(self):
        os.environ["GEMINI_API_KEY"] = "AQ.super-secret-value-WXYZ"
        st = {i["name"]: i for i in envfile.status()}
        self.assertTrue(st["GEMINI_API_KEY"]["set"])
        self.assertEqual(st["GEMINI_API_KEY"]["hint"], "…WXYZ")
        self.assertNotIn("super-secret", json.dumps(envfile.status()))
        self.assertIn("value", st["CLAUDE_MODEL"])  # model names are not secret

    def test_endpoints_validate_and_refuse_foreign_origins(self):
        body = {"value": "AQ.abc-123456789"}
        self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY", json=body).status_code, 200)
        self.assertEqual(self.c.post("/api/keys/NOT_A_KEY", json=body).status_code, 404)
        self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY", json={"value": "bad value"}).status_code, 400)
        self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY", json=body, headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.c.delete("/api/keys/GEMINI_API_KEY", headers={"origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY", json=body, headers={"origin": "http://localhost:8000"}).status_code, 200)
        listing = self.c.get("/api/keys").json()
        self.assertNotIn("AQ.abc", json.dumps(listing))

    def test_test_endpoint_reports_without_leaking(self):
        with mock.patch("app.envfile.test", return_value=(True, "Works.")):
            self.assertEqual(self.c.post("/api/keys/GEMINI_API_KEY/test").json(), {"ok": True, "message": "Works."})
        self.assertEqual(envfile.test("ANTHROPIC_API_KEY"), (False, "Not set yet."))


class ABTestSwitchTests(unittest.TestCase):
    def test_ab_switch_defaults_off_and_persists(self):
        c = TestClient(app)
        self.assertFalse(c.get("/api/state").json()["ab_test"])
        c.post("/api/settings", json={"ab_test": True})
        self.assertTrue(c.get("/api/state").json()["ab_test"])
        c.post("/api/settings", json={"ab_test": False})
        self.assertFalse(c.get("/api/state").json()["ab_test"])


class NoCacheTests(unittest.TestCase):
    def test_page_is_never_cached(self):
        r = TestClient(app).get("/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")


class GeminiTestFunctionTests(unittest.TestCase):
    def test_gemini_check_reads_the_model_list_with_the_client_still_open(self):
        class Client:
            closed = False

            def __init__(self, api_key):
                self.models = self

            def list(self):
                for n in ("models/gemini-3.8-flash", "models/gemini-3.7-flash"):
                    yield type("M", (), {"name": n})()
        os.environ["GEMINI_API_KEY"] = "AQ.test-key-1234"
        try:
            with mock.patch("google.genai.Client", Client):
                self.assertEqual(envfile._gemini_models("AQ.test-key-1234"), ["gemini-3.8-flash", "gemini-3.7-flash"])
                ok, msg = envfile.test("GEMINI_API_KEY")
            self.assertTrue(ok)
            self.assertIn("available", msg)
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


if __name__ == "__main__":
    unittest.main()
