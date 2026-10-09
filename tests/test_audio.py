"""The finished video must have sound for its whole length, and one steady voice."""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import captions, db, pipeline, styles, voice, voices  # noqa: E402
from app.server import app  # noqa: E402


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


def make_video(path, seconds=20):
    ff("-f", "lavfi", "-i", f"color=c=0x203040:s=320x180:r=24:d={seconds}", "-f", "lavfi",
       "-i", f"sine=f=300:d={seconds}", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path))


def make_clip(path, seconds=2.0, freq=700, volume=0.05):
    ff("-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", "-af", f"volume={volume}", str(path))


class MixLengthTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = self.d / "src.mp4"
        make_video(self.src, 20)

    def test_audio_runs_the_whole_video_even_when_the_last_line_is_early(self):
        """The bug: the soundtrack stopped when the last voice line ended."""
        clip = self.d / "c.mp3"
        make_clip(clip)
        out = self.d / "out.mp4"
        voice.mix(self.src, [(3.0, clip, 2.0)], out)      # one line at 3 s in a 20 s video
        self.assertGreaterEqual(voice.stream_seconds(out, "a"), 19.5)
        self.assertAlmostEqual(voice.stream_seconds(out, "v"), 20.0, delta=0.5)
        voice.verify_av(out)

    def test_many_lines_and_a_line_near_the_end_also_keep_full_length(self):
        clips = []
        for i, t in enumerate((1.0, 6.0, 17.5)):
            c = self.d / f"c{i}.mp3"
            make_clip(c, 1.5)
            clips.append((t, c, 1.5))
        out = self.d / "out.mp4"
        voice.mix(self.src, clips, out)
        self.assertGreaterEqual(voice.stream_seconds(out, "a"), 19.5)

    def test_game_audio_is_still_there_after_the_last_line(self):
        clip = self.d / "c.mp3"
        make_clip(clip)
        out = self.d / "out.mp4"
        voice.mix(self.src, [(2.0, clip, 2.0)], out)
        late = subprocess.run(["ffmpeg", "-hide_banner", "-ss", "12", "-t", "6", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                              capture_output=True, text=True).stderr
        mean = float(re.search(r"mean_volume: (-?[\d.]+) dB", late).group(1))
        self.assertGreater(mean, -40)   # the late part is not silent

    def test_captions_keep_the_full_audio_length_too(self):
        clip = self.d / "c.mp3"
        make_clip(clip)
        mixed, final = self.d / "mixed.mp4", self.d / "final.mp4"
        voice.mix(self.src, [(3.0, clip, 2.0)], mixed)
        captions.burn(mixed, final, [{"start": 1, "end": 3, "text": "Hello", "kind": "dialogue"}], [], self.d)
        voice.verify_av(final)
        self.assertGreaterEqual(voice.stream_seconds(final, "a"), 19.5)

    def test_verify_refuses_a_video_whose_sound_ends_early(self):
        short = self.d / "short.mp4"
        ff("-i", str(self.src), "-t", "20", "-af", "atrim=0:8", "-c:v", "copy", str(short))
        with self.assertRaises(RuntimeError):
            voice.verify_av(short)


class LevelTests(unittest.TestCase):
    def test_every_clip_is_brought_to_the_same_level(self):
        d = Path(tempfile.mkdtemp())
        levels = []
        for i, vol in enumerate((0.5, 1.0, 2.5)):   # means near -27, -21 and -13 dB: about the spread real lines had
            p = d / f"c{i}.mp3"
            make_clip(p, 2.0, volume=vol)
            voice.level_clip(p)
            levels.append(voice.mean_db(p))
        self.assertLess(max(levels) - min(levels), 1.5, levels)
        self.assertAlmostEqual(sum(levels) / 3, voice.TARGET_MEAN_DB, delta=2.5)

    def test_a_very_quiet_take_still_reaches_the_common_level(self):
        """The bug found in the real video: takes that arrive 30+ dB down stayed 6 dB below the rest."""
        p = Path(tempfile.mkdtemp()) / "c.mp3"
        make_clip(p, 2.0, volume=0.04)            # about -38 dB: needs more than a single 14 dB boost
        self.assertLess(voice.mean_db(p), -34)
        voice.level_clip(p)
        self.assertAlmostEqual(voice.mean_db(p), voice.TARGET_MEAN_DB, delta=2.0)

    def test_the_boost_is_capped_so_noise_is_never_blown_up(self):
        p = Path(tempfile.mkdtemp()) / "c.mp3"
        make_clip(p, 2.0, volume=0.01)           # nearly silent
        before = voice.mean_db(p)
        voice.level_clip(p)
        self.assertLessEqual(voice.mean_db(p) - before, 2 * voice.MAX_BOOST_DB + 0.6)  # at most two capped passes

    def test_leveling_keeps_the_length(self):
        p = Path(tempfile.mkdtemp()) / "c.mp3"
        make_clip(p, 2.0)
        before = voice.duration(p)
        voice.level_clip(p)
        self.assertAlmostEqual(voice.duration(p), before, delta=0.15)


class SteadyTtsTests(unittest.TestCase):
    def test_settings_are_steady_and_expressiveness_is_modest(self):
        calm, strong = voice._settings("calm"), voice._settings("pain")
        for s in (calm, strong):
            self.assertGreaterEqual(s["stability"], 0.5)
            self.assertGreaterEqual(s["similarity_boost"], 0.9)
            self.assertTrue(s["use_speaker_boost"])
        self.assertLessEqual(strong["style"], 0.3)
        self.assertGreater(calm["stability"], strong["stability"])

    def test_a_fixed_seed_is_sent_and_a_refusal_is_retried_without_it(self):
        bodies = []

        class R:
            def __init__(self, code, text=""):
                self.status_code, self.text, self.content = code, text, b"audio"

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError("bad")
        replies = iter([R(422, "unknown field seed"), R(200)])

        def post(url, headers, json, timeout):
            bodies.append(dict(json))
            return next(replies)
        os.environ["ELEVENLABS_API_KEY"] = "x"
        out = Path(tempfile.mkdtemp()) / "a.mp3"
        with mock.patch("app.voice.requests.post", post):
            voice.tts("Hi", "V", "calm", out)
        self.assertEqual(bodies[0]["seed"], voice.VOICE_SEED)
        self.assertNotIn("seed", bodies[1])
        self.assertEqual(out.read_bytes(), b"audio")


class OneVoiceTests(unittest.TestCase):
    def setUp(self):
        db.run("DELETE FROM settings WHERE key LIKE 'voice:%'")
        voices.assign("western", "character", "ARTHUR")
        voices.assign("western", "companion", "WYATT")
        voices.assign("western", "player", "PLAYER")
        self.addCleanup(lambda: db.put("one_voice", "1"))

    def test_every_persona_speaks_in_the_character_voice_when_on(self):
        db.put("one_voice", "1")
        self.assertEqual({voices.resolve("western", p) for p in ("player", "character", "companion")}, {"ARTHUR"})

    def test_personas_keep_their_own_voices_when_off(self):
        db.put("one_voice", "0")
        self.assertEqual([voices.resolve("western", p) for p in ("player", "character", "companion")], ["PLAYER", "ARTHUR", "WYATT"])

    def test_the_picker_still_shows_each_slot_own_setting(self):
        db.put("one_voice", "1")
        self.assertEqual(voices.assigned("western"), {"player": "PLAYER", "character": "ARTHUR", "companion": "WYATT"})

    def test_writing_becomes_first_person_character_when_the_persona_was_mixed(self):
        v = {"game": None, "pack": "western", "personality": "balanced"}
        db.put("persona", "mixed")
        db.put("one_voice", "1")
        self.assertEqual(pipeline.video_opts(v)["persona"], "character")
        db.put("one_voice", "0")
        self.assertEqual(pipeline.video_opts(v)["persona"], "mixed")
        db.put("one_voice", "1")
        db.put("persona", "companion")      # an explicit choice is respected
        self.assertEqual(pipeline.video_opts(v)["persona"], "companion")
        db.put("persona", "mixed")

    def test_setting_defaults_on_and_the_card_shows_the_voice_that_really_speaks(self):
        db.run("DELETE FROM settings WHERE key='one_voice'")
        c = TestClient(app)
        self.assertTrue(c.get("/api/state").json()["one_voice"])
        import time
        vid = db.run("INSERT INTO videos(drive_id,name,status,pack,created) VALUES(?,?,?,?,?)",
                     (f"o{time.time_ns()}", "x.mp4", "ready", "western", time.time())).lastrowid
        row = next(v for v in c.get("/api/state").json()["videos"] if v["id"] == vid)
        self.assertEqual(len(set(row["voices"].values())), 1)
        c.post("/api/settings", json={"one_voice": False})
        row = next(v for v in c.get("/api/state").json()["videos"] if v["id"] == vid)
        self.assertEqual(len(set(row["voices"].values())), 3)
        c.post("/api/settings", json={"one_voice": True})


class StaleClipTests(unittest.TestCase):
    def test_old_clips_are_removed_before_a_new_run(self):
        work = Path(tempfile.mkdtemp())
        (work / "clips").mkdir()
        for name in ("c_000.mp3", "c_007.mp3", "c_003.lvl.mp3"):
            (work / "clips" / name).write_bytes(b"old")
        lines = [{"start": 5.0, "line": "Spoken.", "persona": "player", "emotion": "calm", "max_duration": 6}]
        with mock.patch("app.voice.tts", side_effect=lambda t, v, e, out: Path(out).write_bytes(b"x")), \
                mock.patch("app.voice.level_clip"), mock.patch("app.voice.duration", return_value=2.0), \
                mock.patch("app.voice.voices.resolve", return_value="V"):
            voice.make_clips(lines, [], "SYS", work)
        self.assertEqual(sorted(p.name for p in (work / "clips").iterdir()), ["c_000.mp3"])
        self.assertEqual((work / "clips" / "c_000.mp3").read_bytes(), b"x")   # the new one, not the old one


if __name__ == "__main__":
    unittest.main()
