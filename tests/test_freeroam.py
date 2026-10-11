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
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import ambience, beats, db, packaging, pipeline, styles, travel, viral, voice, writer  # noqa: E402
from app.config import PROMPTS  # noqa: E402
from app.server import app  # noqa: E402


def frame(t, **kw):
    return {"t": t, "interactions": [], "moral_events": [], "notable_details": [], "activity": "travel", "time_weather": "day, clear",
            "location": "unknown", **kw}


def level(path, start, seconds):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-ss", str(start), "-t", str(seconds), "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = [ln for ln in err.splitlines() if "mean_volume" in ln]
    return float(m[0].split("mean_volume:")[1].split()[0]) if m else -99.0


def video_with_music_and_bursts(path, seconds=20):
    """A steady quiet 'music' tone plus short loud bursts (hooves, shots) at 2.0-2.3 s of every 5 s."""
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-f", "lavfi", "-i", f"sine=frequency=1000:duration={seconds}",
                    "-filter_complex", "[1:a]volume=0.05[m];[2:a]volume='if(between(mod(t,5),2,2.3),0.8,0)':eval=frame[b];[m][b]amix=inputs=2:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)


def plain_video(path, seconds=30):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=10:duration={seconds}", "-f", "lavfi",
                    "-i", f"sine=frequency=300:duration={seconds}", "-c:a", "aac", "-pix_fmt", "yuv420p", "-y", str(path)], check=True)


def tone(path, seconds, freq=440, vol=0.5):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-af", f"volume={vol}", "-y", str(path)], check=True)


class MixTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def test_only_in_keeps_the_game_audio_in_the_windows_and_removes_it_elsewhere(self):
        plain_video(self.d / "v.mp4")
        voice.mix(self.d / "v.mp4", [], self.d / "o.mp4", only_in=[(10.0, 15.0)])
        self.assertGreater(level(self.d / "o.mp4", 11, 3), -30)             # speech or a cutscene: the game audio plays
        self.assertLess(level(self.d / "o.mp4", 2, 5), -80)                 # elsewhere: gone
        self.assertLess(level(self.d / "o.mp4", 20, 8), -80)
        self.assertAlmostEqual(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(self.d / "o.mp4")],
                                                    capture_output=True, text=True).stdout), 30.0, delta=0.4)

    def test_no_windows_at_all_means_the_game_audio_is_silent_throughout(self):
        plain_video(self.d / "v.mp4", 12)
        voice.mix(self.d / "v.mp4", [], self.d / "o.mp4", only_in=[])
        self.assertLess(level(self.d / "o.mp4", 1, 8), -80)

    def test_a_bed_replaces_the_music_where_the_game_audio_is_removed(self):
        plain_video(self.d / "v.mp4")
        tone(self.d / "bed.mp3", 5, freq=1000, vol=0.05)
        voice.mix(self.d / "v.mp4", [], self.d / "o.mp4", only_in=[(10.0, 15.0)], ambience=[(0.0, 30.0, self.d / "bed.mp3")])
        self.assertGreater(level(self.d / "o.mp4", 2, 5), -60)              # the bed is audible where the game audio is gone
        self.assertLess(level(self.d / "o.mp4", 2, 5), -20)                 # and quiet

    def test_the_music_lowerer_pushes_the_steady_bed_down_and_leaves_the_loud_sounds(self):
        video_with_music_and_bursts(self.d / "v.mp4")
        voice.mix(self.d / "v.mp4", [], self.d / "low.mp4", lower_music=True)
        voice.mix(self.d / "v.mp4", [], self.d / "same.mp4")
        before = level(self.d / "same.mp4", 2.0, 0.3) - level(self.d / "same.mp4", 3.2, 1.6)        # burst over steady, dB
        after = level(self.d / "low.mp4", 2.0, 0.3) - level(self.d / "low.mp4", 3.2, 1.6)
        self.assertGreater(after - before, 8.0)                                                      # the steady bed fell relative to the bursts
        self.assertGreater(level(self.d / "low.mp4", 2.0, 0.3), -30)                                 # the burst itself is still loud

    def test_nothing_changes_without_the_new_options(self):
        plain_video(self.d / "v.mp4", 12)
        voice.mix(self.d / "v.mp4", [], self.d / "a.mp4")
        voice.mix(self.d / "v.mp4", [], self.d / "b.mp4", only_in=None, lower_music=False)
        self.assertAlmostEqual(level(self.d / "a.mp4", 0, 10), level(self.d / "b.mp4", 0, 10), delta=0.3)


class AudioPlanTests(unittest.TestCase):
    def setUp(self):
        for k in ("music_mode", "ambience_on", "game_audio"):
            db.run("DELETE FROM settings WHERE key=?", (k,))

    def vid(self, game="Red Dead Redemption 2"):
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,game,created) VALUES(?,?,?,?,?,?)",
                     (f"fr{time.time_ns()}", "x.mp4", "ready", "western", game, time.time())).lastrowid
        w = pipeline.workdir(vid)
        w.mkdir(parents=True)
        (w / "source.mp4").write_bytes(b"x")
        (w / "scenes.json").write_text(json.dumps([frame(t * 3) for t in range(60)]))
        (w / "transcript.json").write_text("[]")
        return vid

    def note(self, vid):
        return json.loads((pipeline.workdir(vid) / "ambience.json").read_text())

    def test_the_mode_defaults_to_keep_and_ignores_junk(self):
        self.assertEqual(pipeline.music_mode(), "keep")
        db.put("music_mode", "nonsense")
        self.assertEqual(pipeline.music_mode(), "keep")
        for m in ("lower", "ambience", "own", "keep"):
            db.put("music_mode", m)
            self.assertEqual(pipeline.music_mode(), m)

    def test_keep_changes_nothing_and_lower_asks_for_the_lowerer(self):
        vid = self.vid()
        self.assertEqual(pipeline._audio_plan(vid, [], []), ([], None, False))
        db.put("music_mode", "lower")
        self.assertEqual(pipeline._audio_plan(vid, [], []), ([], None, True))

    def test_ambience_mode_for_red_dead_places_beds_through_the_whole_video_and_keeps_speech(self):
        db.put("music_mode", "ambience")
        vid = self.vid()
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), \
                mock.patch("app.pipeline.ambience.prepare", side_effect=lambda plan, fam, d: [(s["start"], s["end"], Path("bed.mp3")) for s in plan]) as prep:
            beds, only_in, lower = pipeline._audio_plan(vid, [{"start": 100, "end": 110, "speaker": "game", "text": "Hey."}], [])
        self.assertTrue(beds and not lower)
        self.assertIsNotNone(only_in)
        self.assertTrue(any(a <= 105 <= b for a, b in only_in))                                  # the game's own speech is kept
        self.assertEqual(prep.call_args.args[1], "rdr")
        for a, b, _ in beds:
            self.assertFalse(a < 109 and b > 101, (a, b))                                         # no bed over the speech
        self.assertEqual(self.note(vid)["mode"], "ambience")

    def test_ambience_mode_in_a_game_without_ambience_lowers_the_music_instead(self):
        db.put("music_mode", "ambience")
        vid = self.vid("Skyrim")
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), mock.patch("app.pipeline.ambience.prepare") as prep:
            self.assertEqual(pipeline._audio_plan(vid, [], []), ([], None, True))
        prep.assert_not_called()
        self.assertIn("no ambience for this game", self.note(vid)["fallback"])

    def test_own_music_uses_the_track_through_the_video_and_falls_back_without_one(self):
        db.put("music_mode", "own")
        vid = self.vid()
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), mock.patch("app.pipeline.own_music_path", return_value=None):
            self.assertEqual(pipeline._audio_plan(vid, [], []), ([], None, True))
        self.assertIn("no music file uploaded", self.note(vid)["fallback"])
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), mock.patch("app.pipeline.own_music_path", return_value=Path("own.mp3")):
            beds, only_in, lower = pipeline._audio_plan(vid, [], [])
        self.assertEqual(beds, [(0.0, 180.0, Path("own.mp3"))])
        self.assertEqual((only_in, lower), ([], False))

    def test_a_failure_making_beds_never_leaves_the_video_silent(self):
        db.put("music_mode", "ambience")
        vid = self.vid()
        with mock.patch("app.pipeline.scenes.video_length", return_value=180.0), mock.patch("app.pipeline.ambience.prepare", side_effect=RuntimeError("no credits")):
            self.assertEqual(pipeline._audio_plan(vid, [], []), ([], None, True))
        n = self.note(vid)
        self.assertIn("no credits", n["error"])
        self.assertIn("lowered", n["fallback"])

    def test_the_mix_receives_the_plan(self):
        db.put("music_mode", "lower")
        vid = self.vid()
        with mock.patch("app.pipeline.voice.mix") as mix, mock.patch("app.pipeline.captions.burn"), mock.patch("app.pipeline.voice.verify_av"):
            pipeline._finish(vid, [], [], [], pipeline.workdir(vid) / "source.mp4", [])
        self.assertTrue(mix.call_args.kwargs["lower_music"])
        self.assertIsNone(mix.call_args.kwargs["only_in"])


class MusicUploadTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.c.delete("/api/music")
        self.d = Path(tempfile.mkdtemp())

    def test_a_track_is_saved_levelled_replaced_and_removed(self):
        tone(self.d / "a.mp3", 8, vol=0.9)
        r = self.c.post("/api/music?name=calm.mp3", content=(self.d / "a.mp3").read_bytes())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.get("/api/state").json()["own_music"], "own.mp3")
        self.assertAlmostEqual(voice.mean_db(pipeline.own_music_path()), ambience.MUSIC_TARGET_DB, delta=2.5)
        tone(self.d / "b.wav", 8, vol=0.2)
        self.assertEqual(self.c.post("/api/music?name=other.wav", content=(self.d / "b.wav").read_bytes()).status_code, 200)
        self.assertEqual(self.c.get("/api/state").json()["own_music"], "own.wav")
        self.assertEqual(len(list((pipeline.DATA / "music").glob("own.*"))), 1)
        self.assertEqual(self.c.delete("/api/music").status_code, 200)
        self.assertIsNone(self.c.get("/api/state").json()["own_music"])

    def test_bad_files_are_refused_and_leave_nothing_behind(self):
        self.assertEqual(self.c.post("/api/music?name=x.exe", content=b"x" * 5000).status_code, 400)
        self.assertEqual(self.c.post("/api/music?name=x.mp3", content=b"x" * 5000).status_code, 400)       # not audio
        self.assertEqual(self.c.post("/api/music?name=x.mp3", content=b"x").status_code, 400)
        tone(self.d / "short.mp3", 2)
        self.assertEqual(self.c.post("/api/music?name=short.mp3", content=(self.d / "short.mp3").read_bytes()).status_code, 400)
        self.assertIsNone(self.c.get("/api/state").json()["own_music"])
        self.assertFalse(list((pipeline.DATA / "music").glob(".upload*")))

    def test_the_setting_is_validated(self):
        self.assertEqual(self.c.post("/api/settings", json={"music_mode": "loud"}).status_code, 400)
        self.assertEqual(self.c.post("/api/settings", json={"music_mode": "lower", "calm_mode": True}).status_code, 200)
        s = self.c.get("/api/state").json()
        self.assertEqual((s["music_mode"], s["calm_mode"]), ("lower", True))
        self.c.post("/api/settings", json={"music_mode": "keep", "calm_mode": False})


class CalmModeTests(unittest.TestCase):
    def test_calm_mode_caps_eagerness_and_spreads_the_road_thoughts(self):
        db.put("calm_mode", "0")
        db.put("travel_lpm", "")
        normal = pipeline.travel_gap()
        db.put("calm_mode", "1")
        self.assertGreaterEqual(pipeline.travel_gap(), pipeline.CALM_TRAVEL_GAP)
        self.assertGreater(pipeline.travel_gap(), normal)
        db.put("travel_lpm", "6")
        self.assertGreaterEqual(pipeline.travel_gap(), pipeline.CALM_TRAVEL_GAP)         # even a busy slider cannot break calm
        db.put("calm_mode", "0")
        db.put("travel_lpm", "")

    def test_a_calm_eagerness_only_lets_the_strongest_moments_through(self):
        self.assertGreater(beats.threshold(pipeline.CALM_EAGER), beats.threshold(8.0) + 2.5)
        opps = [{"id": f"b{i}", "t": 20.0 + 30 * i, "kind": "full", "kinds": ["pickup"], "why": "x", "salience": 4.0 + i * 0.6,
                 "max_words": 10, "max_duration": 4.0, "beat_t": None} for i in range(10)]
        calm = beats.select(opps, pipeline.CALM_EAGER, 400)[0]
        busy = beats.select(opps, 8.0, 400)[0]
        self.assertLess(len(calm), len(busy))


class FreeRoamStyleTests(unittest.TestCase):
    NAMES = ["free_wanderer", "free_wildlife", "free_scenic", "free_night", "free_lawless", "free_horse", "free_cruise", "free_any"]

    def test_eight_free_roam_styles_that_are_complete(self):
        for k in self.NAMES:
            p = styles.PACKS[k]
            self.assertTrue(p["name"].startswith("Free roam"), k)
            self.assertTrue(p["text"].startswith("GENRE:") and p["hints"] and p["games"], k)
            self.assertEqual(set(p["voices"]), {"character", "companion"}, k)
            self.assertNotEqual(p["voices"]["character"], p["voices"]["companion"], k)
        self.assertEqual(len({styles.PACKS[k]["name"] for k in self.NAMES}), 8)

    def test_they_belong_to_their_games_and_the_general_one_fits_all(self):
        self.assertTrue(styles.same_family("free_wanderer", "western"))
        self.assertTrue(styles.same_family("free_horse", "western"))
        self.assertTrue(styles.same_family("free_cruise", "crime"))
        self.assertFalse(styles.same_family("free_cruise", "western"))
        for game_pack in ("western", "crime", "fantasy", "scifi"):
            self.assertTrue(styles.same_family("free_any", game_pack), game_pack)
        self.assertFalse(styles.same_family(None, "free_any"))

    def test_the_slow_styles_ask_for_fewer_longer_thoughts_and_reach_the_writer_and_scan(self):
        self.assertIn("fewer, longer", styles.PACKS["free_wanderer"]["text"])
        tpl = (PROMPTS / "scene.md").read_text()
        for k in self.NAMES:
            self.assertIn(styles.PACKS[k]["hints"], tpl.format(game="G", hints=styles.PACKS[k]["hints"], times=[0]))
            system = writer.build_system("S", {"game": "Red Dead Redemption 2", "persona": "character", "pack": k, "personality": "balanced"})
            self.assertIn(styles.PACKS[k]["text"], system)
        self.assertEqual(len(styles.PACKS), 38)


class WanderingGoalTests(unittest.TestCase):
    def spot(self, t, i):
        return {"id": f"d{i}", "t": t, "kind": "daydream", "kinds": ["daydream"], "why": "x", "max_words": 12, "max_duration": 5.0, "salience": 4.5, "beat_t": None}

    def test_a_goal_is_picked_from_something_really_in_view(self):
        notes = [frame(t, landmark="a stone bridge over the river") for t in range(0, 300, 3)]
        o = self.spot(60, 0)
        travel.ORDER, saved = ["goal", "wistful"], travel.ORDER
        try:
            travel.assign([o], notes)
        finally:
            travel.ORDER = saved
        self.assertEqual(o["travel"], "goal")
        self.assertIn("a stone bridge over the river", o["why"])
        self.assertIn("promise nothing that needs a fact not listed", o["why"])
        self.assertIn("travel_goal", o["kinds"])

    def test_with_nothing_in_view_there_is_no_goal(self):
        notes = [frame(t) for t in range(0, 300, 3)]
        spots = [self.spot(60 + 40 * i, i) for i in range(5)]
        travel.assign(spots, notes)
        self.assertNotIn("goal", {o["travel"] for o in spots})

    def test_goal_is_part_of_the_rotation_and_never_repeats_in_a_row(self):
        self.assertIn("goal", travel.ORDER)
        notes = [frame(t, landmark="a ridge", mount_or_vehicle="brown horse", player_state="riding") for t in range(0, 600, 3)]
        spots = [self.spot(100 + 40 * i, i) for i in range(10)]
        travel.assign(spots, notes)
        kinds = [o["travel"] for o in spots]
        self.assertIn("goal", kinds)
        self.assertTrue(all(a != b for a, b in zip(kinds, kinds[1:])))


class JourneyTests(unittest.TestCase):
    def run_of(self, count, **kw):
        return [frame(t * 3, player_state="riding", mount_or_vehicle="brown horse", **kw) for t in range(count)]

    def scan(self):
        return (self.run_of(40, time_weather="night, clear", location="Grizzlies West") +
                [frame(120 + t * 3, activity="hunting") for t in range(40)] +
                [{**n, "t": 240 + n["t"]} for n in self.run_of(40, time_weather="day, snow")])

    def test_a_journey_chapter_is_named_by_when_weather_and_where(self):
        titles = [c["title"] for c in viral.chapters(self.scan(), 360)]
        self.assertEqual(titles, ["Night ride through Grizzlies West", "Hunting", "Snowy ride"])

    def test_plain_names_stay_when_the_scan_says_nothing(self):
        plain = self.run_of(40) + [frame(120 + t * 3, activity="hunting") for t in range(40)] + [{**n, "t": 240 + n["t"]} for n in self.run_of(40)]
        self.assertEqual([c["title"] for c in viral.chapters(plain, 360)], ["Riding", "Hunting", "Riding"])

    def test_a_place_that_is_only_briefly_seen_or_too_long_is_not_used(self):
        notes = self.run_of(30, time_weather="dusk")
        notes[3]["location"] = "Somewhere"
        self.assertEqual(viral.journey_title("Riding", notes), "Dusk ride")
        long = self.run_of(30, location="The Very Long Name Of A Place Nobody Could Fit In A Chapter")
        self.assertEqual(viral.journey_title("Riding", long), "Riding")
        self.assertEqual(viral.journey_title("Hunting", self.run_of(5, location="X")), "Hunting")

    def test_driving_and_walking_get_their_own_verbs(self):
        drive = [frame(t * 3, player_state="driving", time_weather="night") for t in range(20)]
        walk = [frame(t * 3, time_weather="rain") for t in range(20)]
        self.assertEqual(viral.journey_title("Driving", drive), "Night drive")
        self.assertEqual(viral.journey_title("Exploring on foot", walk), "Rainy walk")

    def test_the_fact_sheet_marks_a_free_roam_video_and_lists_the_sky(self):
        notes = [frame(t * 3, time_weather="dawn, fog") for t in range(40)]
        fs = packaging.facts("Red Dead Redemption 2", "Western outlaw", 120, notes, [], [], [])
        self.assertTrue(fs["free_roam"])
        self.assertEqual(set(fs["time_and_weather_seen"]), {"dawn", "fog"})
        busy = [frame(t * 3, activity="combat") for t in range(40)]
        self.assertFalse(packaging.facts("G", "S", 120, busy, [], [], [])["free_roam"])
        self.assertIn("free_roam is true", (PROMPTS / "packaging.md").read_text())


class PageTests(unittest.TestCase):
    def test_the_page_has_the_new_controls(self):
        from app.config import ROOT
        html = (ROOT / "app" / "static" / "index.html").read_text()
        for needle in ('id="music_mode"', 'id="ownmusic_file"', 'id="calm_on"', "Replace it with scene ambience", "Replace it with my own music", "Calm free-roam pace"):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()
