import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ["OWD_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import beats, captions, cutscenes, db, gaps, guard, pipeline  # noqa: E402
from app.server import app  # noqa: E402

W, H = cutscenes.W, cutscenes.H


def frame(top=0, bottom=0, middle=120):
    img = Image.new("L", (W, H), middle)
    bar = max(2, int(H * 0.12))
    img.paste(top, (0, 0, W, bar))
    img.paste(bottom, (0, H - bar, W, H))
    return img


class BarTests(unittest.TestCase):
    def test_letterboxed_picture_is_a_cutscene_frame(self):
        self.assertTrue(cutscenes.has_bars(frame()))

    def test_ordinary_frames_and_partial_dark_edges_are_not(self):
        self.assertFalse(cutscenes.has_bars(Image.effect_noise((W, H), 60)))
        self.assertFalse(cutscenes.has_bars(frame(top=0, bottom=120)))      # only one bar
        self.assertFalse(cutscenes.has_bars(Image.new("L", (W, H), 0)))     # a fade to black, not a picture
        self.assertFalse(cutscenes.has_bars(frame(top=90, bottom=90)))      # grey, not black

    def test_real_video_bars_are_found_only_while_they_are_drawn(self):
        d = Path(tempfile.mkdtemp())
        src = d / "v.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=10:d=12",
                        "-vf", "drawbox=x=0:y=0:w=640:h=45:c=black:t=fill:enable='lt(t,6)',drawbox=x=0:y=315:w=640:h=45:c=black:t=fill:enable='lt(t,6)'",
                        "-pix_fmt", "yuv420p", str(src)], check=True)
        flags = cutscenes.letterbox_flags(src)
        self.assertEqual([t for t, v in flags.items() if v], [0, 3])
        self.assertEqual([t for t, v in flags.items() if not v], [6, 9])


def notes(spec):
    """spec: {t: dict of fields}"""
    return [{"t": t, **v} for t, v in sorted(spec.items())]


class IntervalTests(unittest.TestCase):
    def test_any_signal_counts_and_reasons_are_recorded(self):
        n = notes({0: {"player_state": "walking"}, 3: {"player_state": "cutscene"}, 6: {"is_cutscene": True},
                   9: {"player_state": "in menu"}, 12: {"player_state": "in dialogue"}})
        flags = {t: (f, s) for t, f, s in cutscenes.frame_flags(n, {0: False, 3: False, 6: False, 9: False, 12: True})}
        self.assertEqual(flags[0][0], False)
        self.assertIn("state cutscene", flags[3][1])
        self.assertIn("scan says cutscene", flags[6][1])
        self.assertEqual(flags[12][1], ["black bars"])  # in dialogue alone is not a cutscene signal

    def test_runs_merge_across_small_gaps_and_blips_need_bars(self):
        def f(ts, bars=()):
            return [(t, True, ["black bars"] if t in bars else ["state cutscene"]) for t in ts]
        flags = f([30, 33, 36]) + [(39, False, [])] + f([42, 45]) + [(48, False, [])] * 4 + f([100])
        self.assertEqual(cutscenes.intervals(flags), [[30, 48]])             # joined; the lone frame at 100 is dropped
        self.assertEqual(cutscenes.intervals(f([100], bars=(100,))), [[100, 103]])  # a lone frame with bars counts

    def test_padding_widens_ranges(self):
        flags = [(t, True, ["black bars"]) for t in (30, 33, 36)]
        self.assertEqual(cutscenes.intervals(flags, pad=1.2), [[28.8, 40.2]])


class EditTests(unittest.TestCase):
    def test_effective_is_automatic_minus_removed_plus_added(self):
        c = {"auto": [[10, 60], [100, 130]], "add": [[200, 230]], "remove": [[20, 40]]}
        self.assertEqual(cutscenes.effective(c), [[10, 20], [40, 60], [100, 130], [200, 230]])

    def test_add_and_remove_undo_each_other(self):
        c = cutscenes.empty()
        c["auto"] = [[10, 60]]
        cutscenes.remove_range(c, 20, 40)
        cutscenes.add_range(c, 25, 35)
        self.assertEqual(cutscenes.effective(c), [[10, 20], [25, 35], [40, 60]])
        cutscenes.remove_range(c, 0, 300)
        self.assertEqual(cutscenes.effective(c), [])

    def test_bad_stored_text_never_breaks_anything(self):
        self.assertEqual(cutscenes.load("not json"), cutscenes.empty())
        self.assertEqual(cutscenes.load(None), cutscenes.empty())

    def test_helpers(self):
        self.assertTrue(cutscenes.overlaps([[10, 20]], 15, 16))
        self.assertFalse(cutscenes.overlaps([[10, 20]], 20, 25))
        self.assertEqual(cutscenes.padded([[10, 20], [21, 30]], 1.2), [[8.8, 31.2]])
        self.assertEqual(cutscenes.subtract_ranges([(0, 50)], [[10, 20]]), [[0, 10], [20, 50]])


def line(start, text="A short quiet thought.", **kw):
    return {"start": start, "max_duration": 6, "persona": "player", "emotion": "calm", "trigger_t": None, "line": text, **kw}


class BlockTests(unittest.TestCase):
    BLOCKS = [[100.0, 160.0]]

    def test_nothing_voiced_or_silent_may_touch_a_cutscene(self):
        kept, dropped = guard.enforce([line(120), line(120, silent=True), line(98, "Quite a long line that will run into the cutscene")], [], [], blocks=self.BLOCKS)
        self.assertEqual(kept, [])
        self.assertEqual({d["reason"] for d in dropped}, {"during a cutscene"})

    def test_lines_outside_are_unaffected(self):
        kept, _ = guard.enforce([line(30), line(40, silent=True), line(170)], [], [], blocks=self.BLOCKS)
        self.assertEqual(len(kept), 3)

    def test_drop_in_blocks_marks_lines_with_a_reason(self):
        lines = [line(120), line(30), line(130, silent=True), line(140, dropped=True)]
        self.assertEqual(pipeline.drop_in_blocks(lines, self.BLOCKS), 2)
        self.assertEqual([(l.get("dropped"), l.get("note")) for l in lines],
                         [(True, "during a cutscene"), (None, None), (True, "during a cutscene"), (True, None)])


class AfterCutsceneTests(unittest.TestCase):
    def free(self):
        return gaps.free_gaps([(0.0, 100.0), (130.0, 200.0)], 300)

    def test_a_long_cutscene_earns_one_short_reaction_in_the_next_gap_using_what_was_said(self):
        segs = [{"start": 20, "end": 24, "text": "We ride at dawn."}, {"start": 90, "end": 95, "text": "Don't forget me, Arthur."},
                {"start": 150, "end": 152, "text": "after the cutscene"}]
        out = beats.after_cutscene([[10.0, 105.0]], gaps.free_gaps([(0.0, 105.0), (150.0, 160.0)], 300), segs)
        self.assertEqual(len(out), 1)
        o = out[0]
        self.assertGreaterEqual(o["t"], 105.0)
        self.assertLessEqual(o["max_words"], 9)
        self.assertEqual((o["kind"], o["kinds"]), ("micro", ["cutscene_end"]))
        self.assertIn("Don't forget me, Arthur.", o["why"])
        self.assertNotIn("after the cutscene", o["why"])

    def test_short_cutscenes_and_cutscenes_with_no_room_after_get_none(self):
        free = gaps.free_gaps([(0.0, 50.0)], 300)
        self.assertEqual(beats.after_cutscene([[10.0, 14.0]], free, []), [])
        self.assertEqual(beats.after_cutscene([[10.0, 299.0]], gaps.free_gaps([(0.0, 299.0)], 300), []), [])

    def test_the_reaction_is_chosen_even_at_sparse_eagerness(self):
        free = gaps.free_gaps([(0.0, 50.0)], 300)
        after = beats.after_cutscene([[10.0, 49.0]], free, [{"start": 20, "end": 22, "text": "Hello."}])
        chosen, _, _ = pipeline.plan_commentary([], free, [], 300, 3.0, None, True, extra=after)
        self.assertIn("c0", [o["id"] for o in chosen])   # kept even though sparse mode drops weaker moments


class CaptionBlockTests(unittest.TestCase):
    def test_dialogue_subtitles_are_not_drawn_over_a_cutscene(self):
        segs = [{"start": 5, "end": 8, "text": "Outside."}, {"start": 105, "end": 108, "text": "Inside the cutscene."},
                {"start": 98, "end": 102, "text": "Crossing in."}]
        items = captions.dialogue_items(segs, [[100.0, 160.0]])
        self.assertEqual([i["text"] for i in items], ["Outside."])
        self.assertEqual(len(captions.dialogue_items(segs)), 3)


class CutsceneEndpointTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.vid = db.run("INSERT INTO videos(drive_id,name,status,cutscenes,created) VALUES(?,?,?,?,?)",
                          (f"k{time.time_ns()}", "x.mp4", "ready", '{"auto": [[10, 60]], "add": [], "remove": []}', time.time())).lastrowid

    def test_switch_defaults_on_and_persists(self):
        db.run("DELETE FROM settings WHERE key='cutscene_quiet'")
        self.assertTrue(self.c.get("/api/state").json()["cutscene_quiet"])
        self.c.post("/api/settings", json={"cutscene_quiet": False})
        self.assertFalse(pipeline.cutscene_quiet())
        self.assertEqual(pipeline.active_cuts(pipeline.cut_state(self.vid)), [])
        self.c.post("/api/settings", json={"cutscene_quiet": True})
        self.assertEqual(pipeline.active_cuts(pipeline.cut_state(self.vid)), [[10.0, 60.0]])

    def test_add_remove_reset_and_validation(self):
        got = self.c.get(f"/api/videos/{self.vid}/cutscenes").json()
        self.assertEqual((got["effective"], got["seconds"], got["detected"]), ([[10.0, 60.0]], 50.0, True))
        self.assertEqual(self.c.post(f"/api/videos/{self.vid}/cutscenes/remove", json={"start": 20, "end": 40}).json()["effective"], [[10, 20], [40, 60]])
        self.assertEqual(self.c.post(f"/api/videos/{self.vid}/cutscenes/add", json={"start": 200, "end": 230}).json()["effective"][-1], [200, 230])
        self.assertEqual(self.c.post(f"/api/videos/{self.vid}/cutscenes/add", json={"start": 50, "end": 40}).status_code, 400)
        self.assertEqual(self.c.post(f"/api/videos/{self.vid}/cutscenes/reset").json()["effective"], [[10, 60]])

    def test_state_summarizes_cutscenes_per_video(self):
        row = next(v for v in self.c.get("/api/state").json()["videos"] if v["id"] == self.vid)
        self.assertEqual((row["cut_count"], row["cut_seconds"]), (1, 50.0))

    def test_ensure_cuts_detects_once_and_keeps_edits(self):
        v = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"e{time.time_ns()}", "y.mp4", "ready", time.time())).lastrowid
        from unittest import mock
        with mock.patch("app.pipeline.cutscenes.detect", return_value={"ranges": [[5.0, 50.0]], "bars_checked": 3}) as d:
            c = pipeline.ensure_cuts(v, [], "x.mp4")
            self.assertEqual(c["auto"], [[5.0, 50.0]])
            c["add"] = [[100.0, 120.0]]
            pipeline.save_cuts(v, c)
            pipeline.ensure_cuts(v, [], "x.mp4")
            self.assertEqual(d.call_count, 1)
        self.assertEqual(pipeline.cut_state(v)["add"], [[100.0, 120.0]])


if __name__ == "__main__":
    unittest.main()
