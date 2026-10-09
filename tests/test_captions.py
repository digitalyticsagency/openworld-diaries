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

from app import beats, captions, db, guard, pipeline, voice, writer  # noqa: E402
from app.server import app  # noqa: E402

SCENES = [{"t": 10, "interactions": [{"kind": "kill"}], "moral_events": []},
          {"t": 40, "interactions": [], "moral_events": []}]


def silent(start, text="Not again. Never again.", **kw):
    return {"start": start, "line": text, "silent": True, "persona": "player", "emotion": "calm",
            "trigger_t": None, "max_duration": 6, **kw}


class SilentGuardTests(unittest.TestCase):
    WIN = [(8.0, 20.0)]  # someone is talking from 8 to 20 s

    def test_a_silent_thought_may_sit_inside_speech_but_a_voiced_line_may_not(self):
        kept, dropped = guard.enforce([silent(12)], self.WIN, SCENES)
        self.assertEqual(len(kept), 1)
        voiced = {"start": 12, "line": "Voiced line.", "persona": "player", "emotion": "calm", "trigger_t": None, "max_duration": 6}
        self.assertEqual(guard.enforce([voiced], self.WIN, SCENES)[1][0]["reason"], "overlaps speech")

    def test_silent_thoughts_are_short_and_one_at_a_time(self):
        long_one = silent(12, " ".join(["word"] * 20))
        self.assertEqual(guard.enforce([long_one], self.WIN, SCENES)[1][0]["reason"], "too long")
        kept, dropped = guard.enforce([silent(12), silent(13)], self.WIN, SCENES)
        self.assertEqual((len(kept), dropped[0]["reason"]), (1, "too close to previous thought"))
        self.assertEqual(len(guard.enforce([silent(12), silent(17)], self.WIN, SCENES)[0]), 2)

    def test_silent_and_voiced_lines_do_not_block_each_other(self):
        voiced = {"start": 22, "line": "A voiced line.", "persona": "player", "emotion": "calm", "trigger_t": None, "max_duration": 6}
        kept, _ = guard.enforce([silent(21.5), voiced], self.WIN, SCENES)
        self.assertEqual(len(kept), 2)

    def test_silent_thoughts_still_need_grounded_emotion(self):
        bad = silent(12, emotion="pain", trigger_t=40)
        good = silent(12, emotion="pain", trigger_t=10)
        self.assertEqual(guard.enforce([bad], self.WIN, SCENES)[0], [])
        self.assertEqual(len(guard.enforce([good], self.WIN, SCENES)[0]), 1)


def beat(t, sal=6.0):
    return {"t": t, "salience": sal, "kinds": ["kill"], "why": "x"}


class SilentPlanTests(unittest.TestCase):
    def test_beats_inside_speech_get_silent_thoughts_sized_to_reading_time(self):
        opps = beats.silent_opportunities([beat(15), beat(50)], [(10.0, 25.0)], 80)
        by_beat = [o for o in opps if o["beat_t"] is not None]
        self.assertEqual([o["beat_t"] for o in by_beat], [15])  # the beat at 50 is not inside speech
        o = by_beat[0]
        self.assertEqual(o["kind"], "silent")
        self.assertLessEqual(o["max_words"], 12)
        self.assertLessEqual(guard.read_seconds(" ".join(["w"] * o["max_words"])), o["max_duration"] + 1e-6)

    def test_atmosphere_thoughts_fill_long_speech_and_grow_with_eagerness(self):
        low = beats.silent_opportunities([], [(0.0, 120.0)], 130, 4)
        high = beats.silent_opportunities([], [(0.0, 120.0)], 130, 10)
        self.assertGreater(len(high), len(low))
        self.assertEqual(beats.silent_opportunities([], [(0.0, 4.0)], 10, 10), [])

    def test_silent_selection_keeps_thoughts_apart_and_fitted(self):
        opps = beats.silent_opportunities([beat(t, 9) for t in (20, 22, 24, 40)], [(10.0, 60.0)], 70, 10)
        chosen = beats.select_silent(opps, 10)
        ts = [c["t"] for c in chosen]
        self.assertTrue(all(b - a >= 4.0 for a, b in zip(ts, ts[1:])))
        for a, b in zip(chosen, chosen[1:]):
            self.assertLessEqual(guard.read_seconds(" ".join(["w"] * a["max_words"])), (b["t"] - a["t"]) + 1e-6)

    def test_manual_target_is_reached_when_room_allows_and_never_exceeded(self):
        voiced = [{"id": f"v{i}", "t": 10 + 12 * i, "beat_t": None, "salience": 4, "kinds": ["ambient"], "why": "", "max_words": 12,
                   "max_duration": 6, "kind": "ambient"} for i in range(30)]
        sil = beats.silent_opportunities([], [(0.0, 400.0)], 400, 10)
        for lpm in (1, 3, 6):
            got = beats.plan(voiced, sil, 8, 400, lpm)
            want = round(lpm * 400 / 60)
            self.assertLessEqual(len(got), want)
            self.assertGreaterEqual(len(got), min(want, want - 1))
        a = len(beats.plan(voiced, sil, 8, 400, 2))
        b = len(beats.plan(voiced, sil, 8, 400, 6))
        self.assertGreater(b, a)

    def test_automatic_plan_mixes_both_kinds_sorted_by_time(self):
        voiced = [{"id": "v", "t": 30, "beat_t": 28, "salience": 8, "kinds": ["kill"], "why": "", "max_words": 12, "max_duration": 6, "kind": "full"}]
        sil = beats.silent_opportunities([beat(15, 8)], [(10.0, 25.0)], 60, 8)
        got = beats.plan(voiced, sil, 8, 60, None)
        self.assertEqual([o["kind"] for o in got], ["silent", "full"])


class WriterSilentTests(unittest.TestCase):
    def test_silent_opportunities_become_silent_lines_and_voiced_ones_do_not(self):
        opps = [{"id": "s1", "t": 12, "beat_t": None, "salience": 4, "kinds": ["ambient"], "why": "w", "max_words": 8, "max_duration": 5, "kind": "silent"},
                {"id": "v1", "t": 30, "beat_t": None, "salience": 4, "kinds": ["ambient"], "why": "w", "max_words": 8, "max_duration": 5, "kind": "full"}]
        reply = json.dumps([{"opportunity": "s1", "persona": "player", "emotion": "calm", "line": "Here we go again."},
                            {"opportunity": "v1", "persona": "player", "emotion": "calm", "line": "Quiet out here."}])
        with mock.patch("app.writer.claude", return_value=reply):
            kept, _ = writer.write_lines([], [], [(8.0, 20.0)], "SYS", 60, opps)
        self.assertEqual({k["opp_id"]: k["silent"] for k in kept}, {"s1": True, "v1": False})

    def test_prompt_explains_silent_thoughts(self):
        system = writer.build_system("S", {"game": "g", "persona": "mixed", "pack": "western", "personality": "balanced"})
        self.assertIn('kind "silent"', system)
        self.assertIn("makes no sound", system)


class VoiceSkipsSilentTests(unittest.TestCase):
    def test_silent_lines_are_never_sent_to_text_to_speech(self):
        lines = [silent(12), {"start": 30, "line": "Spoken.", "persona": "player", "emotion": "calm", "max_duration": 6}]
        calls = []
        with mock.patch("app.voice.tts", side_effect=lambda text, v, e, out: (calls.append(text), Path(out).write_bytes(b"x"))), \
                mock.patch("app.voice.duration", return_value=2.0), \
                mock.patch("app.voice.voices.resolve", return_value="VOICE"):
            clips = voice.make_clips(lines, [], "SYS", tempfile.mkdtemp())
        self.assertEqual(calls, ["Spoken."])
        self.assertEqual([(c[0], c[2]) for c in clips], [(30, 2.0)])


class CaptionItemTests(unittest.TestCase):
    def test_dialogue_items_skip_sound_effects_and_stretch_tiny_cues(self):
        items = captions.dialogue_items([{"start": 1, "end": 1.2, "text": "Hey!"}, {"start": 5, "end": 6, "text": "[grunts]"},
                                         {"start": 8, "end": 11, "text": "  Come   on  "}, {"start": 12, "end": 12, "text": "zero"}])
        self.assertEqual([(i["text"], round(i["end"] - i["start"], 1)) for i in items], [("Hey!", 1.2), ("Come on", 3.0)])

    def test_commentary_items_time_voiced_by_audio_and_silent_by_reading(self):
        lines = [{"start": 10.0, "line": "A spoken line.", "persona": "player"}, silent(20.0, "A quiet private thought."),
                 {"start": 30.0, "line": "Never voiced.", "persona": "player"}, {"start": 40.0, "line": "Dropped.", "persona": "player", "dropped": True}]
        items = captions.commentary_items(lines, {10.0: 2.5})
        self.assertEqual([(i["kind"], i["start"]) for i in items], [("voiced", 10.0), ("silent", 20.0)])
        self.assertAlmostEqual(items[0]["end"], 12.5)
        self.assertAlmostEqual(items[1]["end"] - items[1]["start"], guard.read_seconds("A quiet private thought."))


class BurnTests(unittest.TestCase):
    def make_clip(self, path):
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x305030:s=640x360:r=24:d=6",
                        "-f", "lavfi", "-i", "sine=f=220:d=6", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)

    def frame(self, video, t):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                              capture_output=True, check=True).stdout

    def test_captions_appear_only_while_their_caption_is_up_and_audio_is_kept(self):
        d = Path(tempfile.mkdtemp())
        src, out = d / "src.mp4", d / "out.mp4"
        self.make_clip(src)
        progress = []
        captions.burn(src, out, [{"start": 1.0, "end": 3.0, "text": "Stay low, Arthur.", "kind": "dialogue"}],
                      [{"start": 3.5, "end": 5.5, "text": "Easy now.", "kind": "voiced", "persona": "character"}], d,
                      progress=lambda a, b: progress.append(a))
        self.assertTrue(out.exists())
        before, dialogue, between, commentary = (self.frame(out, t) for t in (0.3, 2.0, 3.2, 4.5))
        plain = self.frame(src, 2.0)
        self.assertEqual(self.frame(src, 0.3), before)                  # no caption yet: identical to the source
        self.assertNotEqual(dialogue, plain)                            # dialogue subtitle is drawn
        self.assertNotEqual(commentary, self.frame(src, 4.5))           # commentary caption is drawn
        self.assertEqual(self.frame(src, 3.2), between)                 # nothing between the two
        streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                                 capture_output=True, text=True).stdout.split()
        self.assertEqual(sorted(streams), ["audio", "video"])
        self.assertTrue(progress)

    def test_no_captions_just_copies(self):
        d = Path(tempfile.mkdtemp())
        src, out = d / "src.mp4", d / "out.mp4"
        self.make_clip(src)
        captions.burn(src, out, [], [], d)
        self.assertEqual(out.read_bytes(), src.read_bytes())


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)

    def test_caption_switches_default_on_and_persist(self):
        db.run("DELETE FROM settings WHERE key IN ('cap_commentary','cap_dialogue','target_lpm')")
        s = self.c.get("/api/state").json()
        self.assertEqual((s["cap_commentary"], s["cap_dialogue"], s["target_lpm"]), (True, True, 0))
        self.c.post("/api/settings", json={"cap_commentary": False, "cap_dialogue": False})
        s = self.c.get("/api/state").json()
        self.assertEqual((s["cap_commentary"], s["cap_dialogue"]), (False, False))
        self.assertFalse(pipeline.cap_commentary())
        self.c.post("/api/settings", json={"cap_commentary": True, "cap_dialogue": True})

    def test_manual_amount_is_clamped_and_zero_means_automatic(self):
        self.c.post("/api/settings", json={"target_lpm": 5.5})
        self.assertEqual(pipeline.target_lpm(), 5.5)
        self.c.post("/api/settings", json={"target_lpm": 99})
        self.assertEqual(pipeline.target_lpm(), 12.0)
        self.c.post("/api/settings", json={"target_lpm": 0})
        self.assertIsNone(pipeline.target_lpm())
        self.assertEqual(self.c.get("/api/state").json()["target_lpm"], 0)

    def test_lines_endpoint_marks_silent_thoughts(self):
        import time
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"c{time.time_ns()}", "x.mp4", "ready", time.time())).lastrowid
        db.run("INSERT INTO lines(video_id,start,persona,line,silent) VALUES(?,?,?,?,1)", (vid, 5, "player", "quiet"))
        db.run("INSERT INTO lines(video_id,start,persona,line) VALUES(?,?,?,?)", (vid, 9, "player", "loud"))
        got = self.c.get(f"/api/videos/{vid}/lines").json()
        self.assertEqual([(l["line"], l["silent"]) for l in got], [("quiet", 1), ("loud", 0)])


class ManualPlanTests(unittest.TestCase):
    def world(self):
        segs = [{"start": s, "end": s + 4, "speaker": "game"} for s in range(6, 400, 18)]
        notes = [{"t": t, "interactions": [{"kind": "kill", "intensity": 2}] if t % 60 == 0 and t else [], "notable_details": [], "location": "x"}
                 for t in range(0, 420, 3)]
        win = guard.speech_windows(segs)
        from app import gaps
        return notes, win, gaps.free_gaps(win, 420), 420

    def test_manual_target_reaches_more_than_the_slider_alone_and_reports_the_ceiling(self):
        notes, win, free, length = self.world()
        b = beats.find_beats(notes)
        auto, _, ceiling = pipeline.plan_commentary(b, free, win, length, 6.0, None, True)
        manual, _, ceiling2 = pipeline.plan_commentary(b, free, win, length, 6.0, 8.0, True)
        self.assertGreater(len(manual), len(auto))
        self.assertLessEqual(len(manual), round(8 * length / 60))
        self.assertEqual(ceiling, ceiling2)
        self.assertGreaterEqual(ceiling, len(manual) / (length / 60) - 0.2)

    def test_no_silent_thoughts_when_commentary_captions_are_off(self):
        notes, win, free, length = self.world()
        chosen, _, _ = pipeline.plan_commentary(beats.find_beats(notes), free, win, length, 9.0, 6.0, False)
        self.assertTrue(chosen)
        self.assertFalse([o for o in chosen if o["kind"] == "silent"])

    def test_caption_progress_is_shown_in_whole_seconds(self):
        import time
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"p{time.time_ns()}", "x", "captioning", time.time())).lastrowid
        pipeline.progress_cb(vid, "Captions")(10.1239, 453.05)
        self.assertRegex(db.one("SELECT progress FROM videos WHERE id=?", (vid,))["progress"], r"^Captions 10/453 ")


if __name__ == "__main__":
    unittest.main()
