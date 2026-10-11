"""Thumbnails: a frame from the chosen moment with a short bold text, in three looks. Drawn with Pillow."""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from .captions import _font

W, H = 1280, 720
VARIANTS = ("A", "B", "C")


def grab(video, t, out):
    """One frame at t, cropped to 16:9 and scaled to thumbnail size."""
    subprocess.run(["ffmpeg", "-y", "-ss", str(max(0.0, t)), "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}", "-q:v", "2", str(out)],
                   check=True, capture_output=True)
    return out


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        trial = (cur + " " + w).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return (lines + [cur])[:3]


def _fit(draw, text, max_w, max_h, start):
    size = start
    while size > 40:
        f = _font("impact", "bold", size)
        lines = _wrap(draw, text, f, max_w)
        if len(lines) * size * 1.05 <= max_h and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, size
        size -= 6
    f = _font("impact", "bold", 40)
    return f, _wrap(draw, text, f, max_w), 40


def _text(draw, lines, font, size, x, y, fill, stroke_fill, align="left", width=0):
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        lx = x + (width - lw) / 2 if align == "center" else x
        draw.text((lx, y + i * size * 1.05), line, font=font, fill=fill, stroke_width=max(4, size // 14), stroke_fill=stroke_fill)


def render(frame_path, text, variant, out):
    """variant A: white text bottom-left; B: yellow text on a dark band at the top; C: centred text on a vignette."""
    im = Image.open(frame_path).convert("RGB").resize((W, H))
    im = ImageEnhance.Contrast(ImageEnhance.Color(im).enhance(1.25)).enhance(1.12)
    d = ImageDraw.Draw(im, "RGBA")
    if variant == "B":
        d.rectangle((0, 0, W, 250), fill=(0, 0, 0, 170))
        f, lines, size = _fit(d, text, W - 120, 210, 190)
        _text(d, lines, f, size, 60, 20 + (210 - len(lines) * size * 1.05) / 2, (255, 214, 10), (0, 0, 0))
    elif variant == "C":
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).ellipse((-W * 0.2, -H * 0.2, W * 1.2, H * 1.2), fill=255)
        dark = Image.new("RGB", (W, H), (0, 0, 0))
        im = Image.composite(im, dark, mask.filter(ImageFilter.GaussianBlur(160)))
        d = ImageDraw.Draw(im, "RGBA")
        f, lines, size = _fit(d, text, W - 200, 400, 200)
        y = (H - len(lines) * size * 1.05) / 2
        d.rectangle((100, y - 30, 118, y + len(lines) * size * 1.05 + 10), fill=(200, 30, 30, 255))
        _text(d, lines, f, size, 100, y, (255, 255, 255), (0, 0, 0), "center", W - 200)
    else:
        d.rectangle((0, H - 300, W, H), fill=(0, 0, 0, 120))
        f, lines, size = _fit(d, text, W - 120, 260, 190)
        _text(d, lines, f, size, 60, H - 40 - len(lines) * size * 1.05, (255, 255, 255), (0, 0, 0))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=90)
    return out
