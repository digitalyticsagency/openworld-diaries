import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
os.environ["ELEVENLABS_API_KEY"] = "test-key"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import ambience, db, pipeline, voice  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel",
            "time_weather": "day, clear", "location": "unknown", **kw}


def tone(path, seconds, freq=440, vol=0.5):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-af", f"volume={vol}", "-y", str(path)], check=True)


def video(path, seconds=30, freq=300):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}", "-f", "lavfi",
                    "-i", f"sine=frequency={freq}:duration={seconds}", "-c:a", "aac", "-pix_fmt", "yuv420p", "-shortest", "-y", str(path)], check=True)


class FamilyTests(unittest.TestCase):
    def test_only_red_dead_and_gta_have_ambience(self):
        for g in ("Red Dead Redemption 2", "Red Dead Redemption", "RDR2", "Red Dead Online"):
            self.assertEqual(ambience.family_for(g), "rdr", g)
        for g in ("Grand Theft Auto V", "GTA 6", "GTA VI gameplay"):
            self.assertEqual(ambience.family_for(g), "gta", g)
        for g in ("Skyrim", "Minecraft", "", None, "Forza Horizon 5"):
            self.assertIsNone(ambience.family_for(g), g)


class MatchingTests(unittest.TestCase):
    def test_a_bed_fits_the_scene(self):
        r = lambda **kw: ambience.bed_for(frame(0, **kw), "rdr")
        self.assertEqual(r(activity="camp"), "camp_night")
        self.assertEqual(r(time_weather="day, snow"), "snow_wind")
        self.assertEqual(r(time_weather="night, rain"), "rain")
        self.assertEqual(r(time_weather="dawn, fog"), "fog_morning")
        self.assertEqual(r(location="Bayou Nwa swamp"), "swamp")
        self.assertEqual(r(location="the streets of Saint Denis"), "town")
        self.assertEqual(r(time_weather="night, clear"), "camp_night")
        self.assertEqual(r(), "plains_day")
        g = lambda **kw: ambience.bed_for(frame(0, **kw), "gta")
        self.assertEqual(g(), "city_day")
        self.assertEqual(g(time_weather="night"), "city_night")
        self.assertEqual(g(time_weather="rain"), "rain_city")
        self.assertEqual(g(location="Vespucci beach"), "beach")
        self.assertEqual(g(location="Route 68 highway", player_state="driving"), "highway")
        self.assertEqual(g(location="Mount Chiliad forest"), "country")
        self.assertTrue(set(ambience.BEDS["rdr"]) >= {"camp_night", "snow_wind", "rain", "plains_day", "town"})
        self.assertTrue(set(ambience.BEDS["gta"]) >= {"city_day", "city_night", "rain_city", "beach", "highway", "country"})

    def test_only_a_calm_moment_counts(self):
        self.assertTrue(ambience.calm(frame(0)))
        self.assertTrue(ambience.calm(frame(0, activity="camp")))
        for bad in ({"interactions": [{"kind": "kill"}]}, {"moral_events": [{"kind": "help"}]}, {"animals": [{"species": "wolf"}]},
                    {"casualty": {"who": "bandit"}}, {"npcs": [{"behavior": "hostile"}]}, {"wanted_level": 2}, {"wanted_level": 1},
                    {"activity": "combat"}, {"activity": "shop"}, {"player_state": "aiming"}, {"is_cutscene": True},
                    {"player_state": "in dialogue"}, {"activity": "hunting"}):
            self.assertFalse(ambience.calm(frame(0, **bad)), bad)


class PlanTests(unittest.TestCase):
    def notes(self, n=60, **kw):
        return [frame(t * 3, **kw) for t in range(n)]

    def test_a_quiet_stretch_gets_one_bed_with_no_gaps(self):
        plan = ambience.plan(self.notes(), 180, [], "rdr")
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["bed"], "plains_day")
        self.assertLessEqual(plan[0]["start"], 1.5)
        self.assertGreaterEqual(plan[0]["end"], 178)

    def test_nothing_is_planned_for_another_game_or_an_empty_scan(self):
        self.assertEqual(ambience.plan(self.notes(), 180, [], None), [])
        self.assertEqual(ambience.plan([], 180, [], "rdr"), [])

    def test_never_over_speech_or_a_cutscene_and_never_during_action(self):
        notes = self.notes()
        for i in range(20, 26):
            notes[i] = frame(i * 3, interactions=[{"kind": "kill"}])          # 60-78 s: a fight
        plan = ambience.plan(notes, 180, [[100.0, 120.0]], "rdr")             # speech or a cutscene at 100-120 s
        for seg in plan:
            self.assertFalse(seg["start"] < 76.5 and seg["end"] > 58.5, seg)
            self.assertFalse(seg["start"] < 120.6 and seg["end"] > 99.4, seg)
        self.assertGreater(len(plan), 1)

    def test_short_leftovers_are_not_worth_a_bed(self):
        plan = ambience.plan(self.notes(), 180, [[8.0, 100.0], [105.0, 175.0]], "rdr")
        self.assertTrue(all(s["end"] - s["start"] >= ambience.MIN_SEGMENT for s in plan))
        self.assertEqual(plan, [])

    def test_a_change_of_scene_changes_the_bed(self):
        notes = [frame(t * 3, time_weather="day, snow") for t in range(20)] + [frame(60 + t * 3, time_weather="night, rain") for t in range(20)]
        self.assertEqual([s["bed"] for s in ambience.plan(notes, 120, [], "rdr")], ["snow_wind", "rain"])


class BedFileTests(unittest.TestCase):
    def test_a_bed_is_made_once_with_the_right_request_and_then_kept(self):
        d = Path(tempfile.mkdtemp())
        seg = [{"start": 0, "end": 30, "bed": "snow_wind"}]

        def fake_post(url, headers, body):
            r = mock.Mock(status_code=200)
            tone(d / "src.mp3", 3, vol=0.9)
            r.content = (d / "src.mp3").read_bytes()
            return r
        with mock.patch("app.voice._post", side_effect=fake_post) as p:
            got = ambience.prepare(seg, "rdr", d / "cache")
            again = ambience.prepare(seg, "rdr", d / "cache")
        self.assertEqual(p.call_count, 1)
        self.assertEqual(got, again)
        url, headers, body = p.call_args.args
        self.assertTrue(url.endswith("/v1/sound-generation"))
        self.assertEqual(body["text"], ambience.BEDS["rdr"]["snow_wind"])
        self.assertLessEqual(body["duration_seconds"], 22)
        self.assertLess(voice.mean_db(got[0][2]), -30)                                         # kept far below the game audio

    def test_two_games_never_share_a_bed_file(self):
        d = Path(tempfile.mkdtemp())
        with mock.patch("app.ambience.generate", side_effect=lambda prompt, out: Path(out).write_bytes(b"x")):
            a = ambience.prepare([{"start": 0, "end": 30, "bed": "rain"}], "rdr", d)
            b = ambience.prepare([{"start": 0, "end": 30, "bed": "rain_city"}], "gta", d)
        self.assertNotEqual(a[0][2], b[0][2])


class MixTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        video(self.d / "v.mp4", 30)
        tone(self.d / "bed.mp3", 5, freq=1000, vol=0.05)

    def probe(self, path, entry="format=duration"):
        return subprocess.run(["ffprobe", "-v", "error", "-show_entries", entry, "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()

    def test_a_bed_is_laid_under_the_game_audio_and_the_length_never_changes(self):
        voice.mix(self.d / "v.mp4", [], self.d / "plain.mp4", game_level=1.0, ambience=[])
        voice.mix(self.d / "v.mp4", [], self.d / "amb.mp4", ambience=[(5.0, 25.0, self.d / "bed.mp3")])
        self.assertAlmostEqual(float(self.probe(self.d / "amb.mp4")), float(self.probe(self.d / "v.mp4")), delta=0.3)
        self.assertIn("audio", self.probe(self.d / "amb.mp4", "stream=codec_type"))
        self.assertGreater(abs(voice.mean_db(self.d / "amb.mp4") - voice.mean_db(self.d / "plain.mp4")), 0.01)

    def test_the_bed_loops_to_cover_a_stretch_longer_than_itself(self):
        voice.mix(self.d / "v.mp4", [], self.d / "amb.mp4", ambience=[(2.0, 28.0, self.d / "bed.mp3")], game_level=0.0)
        self.assertAlmostEqual(float(self.probe(self.d / "amb.mp4")), 30.0, delta=0.4)
        def level(start, seconds):
            err = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(seconds), "-i", str(self.d / "amb.mp4"), "-vn",
                                  "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
            m = [ln for ln in err.splitlines() if "mean_volume" in ln]
            return float(m[0].split("mean_volume:")[1].split()[0]) if m else None
        self.assertLess(level(0, 1.5), -80)                                                     # nothing before the bed starts
        self.assertGreater(level(20, 5), -60)                                                   # still audible at 20 s, after 5 s of file

    def test_the_game_level_scales_or_removes_the_game_audio(self):
        voice.mix(self.d / "v.mp4", [], self.d / "full.mp4")
        voice.mix(self.d / "v.mp4", [], self.d / "half.mp4", game_level=0.5)
        voice.mix(self.d / "v.mp4", [], self.d / "off.mp4", game_level=0.0)
        full, half, off = (voice.mean_db(self.d / f"{n}.mp4") for n in ("full", "half", "off"))
        self.assertAlmostEqual(full - half, 6.0, delta=1.0)
        self.assertTrue(off is None or off < -80)
        self.assertAlmostEqual(float(self.probe(self.d / "off.mp4")), 30.0, delta=0.4)

    def test_the_voice_still_ducks_the_game_and_the_bed_together(self):
        tone(self.d / "line.mp3", 3, freq=800, vol=0.8)
        voice.mix(self.d / "v.mp4", [(10.0, self.d / "line.mp3", 3.0)], self.d / "all.mp4", ambience=[(0.0, 30.0, self.d / "bed.mp3")], game_level=0.8)
        self.assertAlmostEqual(float(self.probe(self.d / "all.mp4")), 30.0, delta=0.4)
        self.assertEqual(self.probe(self.d / "all.mp4", "stream=codec_type").split(), ["video", "audio"])

    def test_the_old_behaviour_is_untouched_without_a_bed_or_a_level(self):
        tone(self.d / "line.mp3", 3, freq=800, vol=0.8)
        voice.mix(self.d / "v.mp4", [(10.0, self.d / "line.mp3", 3.0)], self.d / "x.mp4")
        self.assertAlmostEqual(float(self.probe(self.d / "x.mp4")), 30.0, delta=0.4)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        db.put("ambience_on", "0")
        db.put("game_audio", "100")

    def vid(self, game="Red Dead Redemption 2"):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"am{time.time_ns()}", "x.mp4", "ready", "western", game, time.time())).lastrowid
        w = pipeline.workdir(vid)
        w.mkdir(parents=True)
        (w / "source.mp4").write_bytes(b"x")
        (w / "scenes.json").write_text(json.dumps([frame(t * 3) for t in range(60)]))
        (w / "transcript.json").write_text("[]")
        return vid

    def test_off_by_default_nothing_is_planned_or_generated(self):
        vid = self.vid()
        with mock.patch("app.pipeline.ambience.prepare") as prep:
            self.assertEqual(pipeline._ambience(vid, [], []), [])
        prep.assert_not_called()
        self.assertFalse(json.loads((pipeline.workdir(vid) / "ambience.json").read_text())["on"])

    def test_on_for_red_dead_it_plans_and_prepares(self):
        db.put("ambience_on", "1")
        vid = self.vid()
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), \
                mock.patch("app.pipeline.ambience.prepare", return_value=[(0.0, 100.0, Path("bed.mp3"))]) as prep:
            out = pipeline._ambience(vid, [], [])
        self.assertEqual(out, [(0.0, 100.0, Path("bed.mp3"))])
        self.assertEqual(prep.call_args.args[1], "rdr")
        saved = json.loads((pipeline.workdir(vid) / "ambience.json").read_text())
        self.assertTrue(saved["on"] and saved["segments"])

    def test_on_for_any_other_game_adds_nothing(self):
        db.put("ambience_on", "1")
        vid = self.vid("Skyrim")
        with mock.patch("app.pipeline.ambience.prepare") as prep:
            self.assertEqual(pipeline._ambience(vid, [], []), [])
        prep.assert_not_called()

    def test_a_failed_bed_never_breaks_the_video(self):
        db.put("ambience_on", "1")
        vid = self.vid()
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), \
                mock.patch("app.pipeline.ambience.prepare", side_effect=RuntimeError("no credits")):
            self.assertEqual(pipeline._ambience(vid, [], []), [])
        self.assertIn("no credits", json.loads((pipeline.workdir(vid) / "ambience.json").read_text())["error"])

    def test_the_game_level_setting_is_clamped_and_reaches_the_mix(self):
        for raw, want in (("100", 1.0), ("0", 0.0), ("35", 0.35), ("250", 1.0), ("-5", 0.0), ("junk", 1.0)):
            db.put("game_audio", raw)
            self.assertEqual(pipeline.game_audio_level(), want, raw)
        db.put("game_audio", "40")
        vid = self.vid()
        with mock.patch("app.pipeline.voice.mix") as mix, mock.patch("app.pipeline.captions.burn"), mock.patch("app.pipeline.voice.verify_av"):
            pipeline._finish(vid, [], [], [], pipeline.workdir(vid) / "source.mp4", [])
        self.assertEqual(mix.call_args.kwargs["game_level"], 0.4)
        self.assertEqual(mix.call_args.kwargs["ambience"], [])
        db.put("game_audio", "100")


class SettingsTests(unittest.TestCase):
    def test_the_switch_defaults_off_and_the_level_saves(self):
        c = TestClient(app)
        db.put("ambience_on", "0")
        s = c.get("/api/state").json()
        self.assertFalse(s["ambience_on"])
        self.assertEqual(s["game_audio"], 100)
        c.post("/api/settings", json={"ambience_on": True, "game_audio": 60})
        s = c.get("/api/state").json()
        self.assertTrue(s["ambience_on"])
        self.assertEqual(s["game_audio"], 60)
        c.post("/api/settings", json={"game_audio": 900})
        self.assertEqual(c.get("/api/state").json()["game_audio"], 100)
        c.post("/api/settings", json={"ambience_on": False, "game_audio": 100})

    def test_the_report_carries_what_was_added(self):
        c = TestClient(app)
        w = pipeline.workdir(991)
        w.mkdir(parents=True, exist_ok=True)
        (w / "report.json").write_text(json.dumps({"seen": {}, "said": {}, "moments": [], "quiet": [], "removed": 0}))
        (w / "ambience.json").write_text(json.dumps({"on": True, "segments": [{"start": 0, "end": 20, "bed": "rain"}], "error": None}))
        self.assertEqual(c.get("/api/videos/991/report").json()["ambience"]["segments"][0]["bed"], "rain")


if __name__ == "__main__":
    unittest.main()
