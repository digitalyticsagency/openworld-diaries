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
from PIL import Image  # noqa: E402

from app import captions, db, pipeline  # noqa: E402
from app.server import app  # noqa: E402

W, H = 1280, 720
TEXT = "Oh, is this about Sofia? Enough, enough with Sofia! It's been two years."


def drawn(kind="dialogue", style="cinematic", pos="bottom", size="medium", cpos=None, text=TEXT, shift=False, persona="character"):
    """Render one caption and return (image, bounding box of everything drawn)."""
    cfg = {"dialogue": {"style": style, "pos": pos}, "commentary": {"style": style, "pos": cpos or pos}, "size": size}
    p = Path(tempfile.mkdtemp()) / "c.png"
    captions.render({"kind": kind, "text": text, "persona": persona}, W, H, p, cfg, shift)
    img = Image.open(p)
    return img, img.split()[3].point(lambda a: 255 if a > 8 else 0).getbbox()


class ConfigTests(unittest.TestCase):
    def test_seven_styles_five_positions_three_sizes(self):
        self.assertEqual(len(captions.STYLES), 7)
        self.assertEqual(list(captions.POSITIONS), ["top", "upper", "middle", "lower", "bottom"])
        self.assertEqual(list(captions.SIZES), ["small", "medium", "large"])
        for k, st in captions.STYLES.items():
            self.assertTrue(st["name"] and st["note"], k)

    def test_clean_cfg_repairs_anything(self):
        self.assertEqual(captions.clean_cfg(None), captions.DEFAULT_CFG)
        bad = captions.clean_cfg({"dialogue": {"style": "nope", "pos": "sideways"}, "commentary": {"style": "pop", "pos": "middle"}, "size": "huge"})
        self.assertEqual(bad["dialogue"], captions.DEFAULT_CFG["dialogue"])
        self.assertEqual(bad["commentary"], {"style": "pop", "pos": "middle"})
        self.assertEqual(bad["size"], "medium")

    def test_the_reference_look_is_the_default_for_both_tracks(self):
        self.assertEqual(captions.DEFAULT_CFG["dialogue"]["style"], "cinematic")
        self.assertEqual(captions.DEFAULT_CFG["commentary"]["style"], "cinematic")
        self.assertEqual(captions.STYLES["cinematic"]["font"], "serif")
        self.assertIsNone(captions.STYLES["cinematic"]["box"])


class RenderTests(unittest.TestCase):
    def test_positions_land_in_order_down_the_screen(self):
        tops = {pos: drawn(pos=pos)[1][1] for pos in captions.POSITIONS}
        self.assertEqual(sorted(tops, key=tops.get), ["top", "upper", "middle", "lower", "bottom"])
        self.assertLess(tops["top"], H * 0.12)
        self.assertGreater(tops["bottom"], H * 0.80)
        mid = drawn(pos="middle", text="Short.")[1]
        self.assertAlmostEqual((mid[1] + mid[3]) / 2, H / 2, delta=H * 0.06)

    def test_nothing_is_ever_drawn_outside_the_frame_in_any_style_or_position(self):
        for style in captions.STYLES:
            for pos in captions.POSITIONS:
                for size in captions.SIZES:
                    bbox = drawn(style=style, pos=pos, size=size)[1]
                    self.assertIsNotNone(bbox, (style, pos, size))
                    self.assertTrue(0 <= bbox[0] and bbox[2] <= W and 0 <= bbox[1] and bbox[3] <= H, (style, pos, size, bbox))
                    self.assertLessEqual(bbox[2] - bbox[0], W * 0.9, (style, pos, size))

    def test_size_scales_the_text(self):
        small, medium, large = (drawn(size=s, text="Hello there friend")[1] for s in ("small", "medium", "large"))
        h = lambda b: b[3] - b[1]
        self.assertLess(h(small), h(medium))
        self.assertLess(h(medium), h(large))

    def test_boxless_styles_add_only_an_outline_but_box_styles_add_real_padding(self):
        """A box adds about one text-height of padding round the words; an outline and shadow add far less."""
        text = "Hi there"
        scratch = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(Image.new("RGBA", (4, 4)))
        def padding(style):
            st = captions.STYLES[style]
            size = max(18, int(H * st["size"]))
            shown = text.upper() if st.get("upper") else text
            width = scratch.textlength(shown, font=captions._font(st["font"], st["weight"], size))
            bbox = drawn(style=style, text=text)[1]
            return (bbox[2] - bbox[0] - width) / size
        for style in ("cinematic", "clean", "pop", "storybook"):
            self.assertLess(padding(style), 0.6, style)
        for style in ("classic", "typewriter", "minimal"):
            self.assertGreater(padding(style), 0.8, style)

    def test_styles_look_different_from_each_other(self):
        pics = {s: drawn(style=s, text="Same words here")[0].tobytes() for s in captions.STYLES}
        self.assertEqual(len(set(pics.values())), len(pics))

    def test_pop_uses_capitals_and_silent_thoughts_are_quoted_italics(self):
        self.assertNotEqual(drawn(style="pop", text="quiet words")[0].tobytes(), drawn(style="clean", text="quiet words")[0].tobytes())
        thought, spoken = drawn(kind="silent", text="Not again."), drawn(kind="voiced", text="Not again.")
        self.assertNotEqual(thought[0].tobytes(), spoken[0].tobytes())

    def test_dialogue_and_commentary_get_their_own_style(self):
        cfg = {"dialogue": {"style": "pop", "pos": "bottom"}, "commentary": {"style": "typewriter", "pos": "top"}, "size": "medium"}
        d, c = Path(tempfile.mkdtemp()) / "d.png", Path(tempfile.mkdtemp()) / "c.png"
        captions.render({"kind": "dialogue", "text": "Same words"}, W, H, d, cfg)
        captions.render({"kind": "voiced", "text": "Same words", "persona": "player"}, W, H, c, cfg)
        self.assertNotEqual(Image.open(d).split()[3].getbbox()[1], Image.open(c).split()[3].getbbox()[1])

    def test_sharing_a_position_moves_the_commentary_so_they_do_not_overlap(self):
        for pos in ("top", "bottom", "upper", "lower"):
            dlg = drawn("dialogue", pos=pos, shift=True)[1]
            com = drawn("voiced", pos=pos, shift=True)[1]
            self.assertTrue(com[3] <= dlg[1] or com[1] >= dlg[3], (pos, dlg, com))

    def test_the_shift_only_happens_when_the_tracks_really_share_a_position(self):
        same = {"dialogue": {"style": "cinematic", "pos": "bottom"}, "commentary": {"style": "cinematic", "pos": "bottom"}}
        apart = {"dialogue": {"style": "cinematic", "pos": "bottom"}, "commentary": {"style": "cinematic", "pos": "top"}}
        self.assertTrue(captions.shares_position(same))
        self.assertFalse(captions.shares_position(apart))
        self.assertFalse(captions.shares_position(None))                        # the defaults are top and bottom
        self.assertEqual(drawn("voiced", pos="top")[1], drawn("voiced", pos="top", shift=False)[1])


class PreviewTests(unittest.TestCase):
    def test_preview_draws_both_tracks_over_the_frame(self):
        base = Image.new("RGB", (1920, 1080), (30, 90, 40))
        img = captions.preview(base, {"dialogue": {"style": "cinematic", "pos": "bottom"}, "commentary": {"style": "pop", "pos": "top"}, "size": "medium"}, "voiced", 960)
        self.assertEqual(img.size, (960, 540))
        px = lambda y: img.crop((0, y, 960, y + 60)).getcolors(maxcolors=100000)
        self.assertGreater(len(px(20)), 3)          # top band has caption pixels, not one flat colour
        self.assertGreater(len(px(440)), 3)         # bottom band too
        self.assertEqual(len(img.crop((0, 200, 960, 260)).getcolors(maxcolors=100000)), 1)  # the middle is untouched

    def test_silent_thought_preview_differs_from_spoken(self):
        base = Image.new("RGB", (1280, 720), (30, 30, 60))
        cfg = captions.DEFAULT_CFG
        self.assertNotEqual(captions.preview(base, cfg, "voiced").tobytes(), captions.preview(base, cfg, "silent").tobytes())


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        for k in ("cap_style_dialogue", "cap_style_commentary", "cap_pos_dialogue", "cap_pos_commentary", "cap_size"):
            db.run("DELETE FROM settings WHERE key=?", (k,))

    def test_defaults_and_catalog_in_state(self):
        s = self.c.get("/api/state").json()
        self.assertEqual(s["cap_cfg"], captions.DEFAULT_CFG)
        self.assertEqual((len(s["cap_styles"]), len(s["cap_positions"]), len(s["cap_sizes"])), (7, 5, 3))

    def test_choices_are_saved_and_validated(self):
        self.assertEqual(self.c.post("/api/settings", json={"cap_style_dialogue": "pop", "cap_pos_dialogue": "upper", "cap_size": "large",
                                                           "cap_style_commentary": "storybook", "cap_pos_commentary": "lower"}).status_code, 200)
        self.assertEqual(pipeline.caption_cfg(), {"dialogue": {"style": "pop", "pos": "upper"}, "commentary": {"style": "storybook", "pos": "lower"}, "size": "large"})
        for body in ({"cap_style_dialogue": "neon"}, {"cap_pos_commentary": "sideways"}, {"cap_size": "huge"}):
            self.assertEqual(self.c.post("/api/settings", json=body).status_code, 400)
        self.assertEqual(pipeline.caption_cfg()["size"], "large")   # a refused value changes nothing

    def test_preview_endpoint_returns_an_image_and_honours_overrides(self):
        r = self.c.get("/api/captions/preview")
        self.assertEqual((r.status_code, r.headers["content-type"], r.headers["cache-control"]), (200, "image/jpeg", "no-store"))
        a = self.c.get("/api/captions/preview", params={"ds": "pop", "dp": "middle"}).content
        b = self.c.get("/api/captions/preview", params={"ds": "classic", "dp": "middle"}).content
        self.assertNotEqual(a, b)
        self.assertEqual(self.c.get("/api/captions/preview", params={"ds": "nonsense", "dp": "sideways"}).status_code, 200)  # repaired, not an error

    def test_recaption_needs_a_finished_video_and_matching_clips(self):
        vid = db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)", (f"r{time.time_ns()}", "x.mp4", "ready", time.time())).lastrowid
        self.assertEqual(self.c.post(f"/api/videos/{vid}/recaption").status_code, 400)
        work = pipeline.workdir(vid)
        (work / "clips").mkdir(parents=True)
        (work / "scenes.json").write_text("[]")
        (work / "transcript.json").write_text("[]")
        (work / "final.mp4").write_bytes(b"x")
        db.run("INSERT INTO lines(video_id,start,persona,line,dropped,silent) VALUES(?,?,?,?,0,0)", (vid, 5, "player", "Spoken."))
        pipeline.recaption(vid)   # there is a line but no clip file: it must fail clearly, not draw nonsense
        row = db.one("SELECT status, error FROM videos WHERE id=?", (vid,))
        self.assertEqual(row["status"], "failed")
        self.assertIn("Rebuild video", row["error"])


class BurnStyleTests(unittest.TestCase):
    def frame(self, video, t):
        return subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                              capture_output=True, check=True).stdout

    def test_the_chosen_look_and_position_reach_the_real_video(self):
        d = Path(tempfile.mkdtemp())
        src = d / "src.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x305030:s=640x360:r=24:d=4", "-f", "lavfi", "-i", "sine=f=220:d=4",
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
        item = [{"start": 0.5, "end": 3.5, "text": "Hello there, friend of mine", "kind": "dialogue"}]
        out_bottom, out_top = d / "b.mp4", d / "t.mp4"
        captions.burn(src, out_bottom, item, [], d, cfg={"dialogue": {"style": "cinematic", "pos": "bottom"}, "commentary": captions.DEFAULT_CFG["commentary"], "size": "medium"})
        captions.burn(src, out_top, item, [], d, cfg={"dialogue": {"style": "pop", "pos": "top"}, "commentary": captions.DEFAULT_CFG["commentary"], "size": "large"})
        plain = self.frame(src, 2.0)
        b, t = self.frame(out_bottom, 2.0), self.frame(out_top, 2.0)
        self.assertNotEqual(b, plain)
        self.assertNotEqual(t, plain)
        self.assertNotEqual(b, t)
        w, h = 640, 360
        rows = lambda frame, a, z: frame[a * w:z * w]
        self.assertEqual(rows(b, 0, 60), rows(plain, 0, 60))      # bottom caption: the top of the picture is untouched
        self.assertNotEqual(rows(t, 0, 60), rows(plain, 0, 60))   # top caption: it is drawn there
        self.assertEqual(rows(t, 300, 360), rows(plain, 300, 360))


if __name__ == "__main__":
    unittest.main()
