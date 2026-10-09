"""Burn captions into the finished video.

This Mac's ffmpeg has no text drawing, so each caption is drawn as a transparent picture and laid over
the video on two tracks: the in-game dialogue and the inner voice. Each track has its own style and
position. Styles are presets; the first one, Cinematic, matches the game's own subtitles.
"""
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .config import CAPTION_FPS
from .guard import read_seconds

S = "/System/Library/Fonts/Supplemental/"
FONTS = {
    ("serif", "regular"): [S + "Georgia.ttf", S + "Times New Roman.ttf"],
    ("serif", "bold"): [S + "Georgia Bold.ttf", S + "Times New Roman Bold.ttf"],
    ("serif", "italic"): [S + "Georgia Italic.ttf", S + "Times New Roman Italic.ttf"],
    ("sans", "regular"): [S + "Arial.ttf", S + "Verdana.ttf"],
    ("sans", "bold"): [S + "Arial Bold.ttf", S + "Verdana Bold.ttf"],
    ("sans", "italic"): [S + "Arial Italic.ttf", S + "Verdana Italic.ttf"],
    ("impact", "regular"): [S + "Impact.ttf", S + "Arial Black.ttf"],
    ("impact", "bold"): [S + "Impact.ttf", S + "Arial Black.ttf"],
    ("impact", "italic"): [S + "Impact.ttf", S + "Arial Black.ttf"],
    ("mono", "regular"): [S + "Courier New.ttf"],
    ("mono", "bold"): [S + "Courier New Bold.ttf"],
    ("mono", "italic"): [S + "Courier New Italic.ttf"],
}
FALLBACKS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial.ttf"]

# size is a fraction of the video height; stroke a fraction of the text size
STYLES = {
    "cinematic": {"name": "Cinematic", "note": "White serif with a soft outline and shadow, no box. Like the game's own subtitles.",
                  "font": "serif", "weight": "regular", "color": (255, 255, 255), "alt": (246, 232, 196),
                  "stroke": 0.07, "shadow": True, "box": None, "size": 0.046},
    "classic": {"name": "Classic box", "note": "Bold text on a dark rounded box with a coloured bar.",
                "font": "sans", "weight": "bold", "color": (255, 255, 255), "alt": (255, 255, 255),
                "stroke": 0.0, "shadow": False, "box": "round", "bar": True, "size": 0.042},
    "clean": {"name": "Clean outline", "note": "Bold sans-serif, white, with a thick black outline.",
              "font": "sans", "weight": "bold", "color": (255, 255, 255), "alt": (255, 236, 170),
              "stroke": 0.10, "shadow": False, "box": None, "size": 0.046},
    "pop": {"name": "Pop", "note": "Big yellow capitals with a heavy outline.",
            "font": "impact", "weight": "bold", "color": (255, 226, 60), "alt": (255, 255, 255),
            "stroke": 0.12, "shadow": True, "box": None, "upper": True, "size": 0.056},
    "minimal": {"name": "Minimal", "note": "Small light text on a faint pill. Stays out of the way.",
                "font": "sans", "weight": "regular", "color": (255, 255, 255), "alt": (214, 224, 240),
                "stroke": 0.0, "shadow": True, "box": "pill", "box_alpha": 105, "size": 0.036},
    "storybook": {"name": "Storybook", "note": "Cream italic serif with a soft shadow.",
                  "font": "serif", "weight": "italic", "color": (250, 240, 215), "alt": (250, 224, 168),
                  "stroke": 0.06, "shadow": True, "box": None, "size": 0.048},
    "typewriter": {"name": "Typewriter", "note": "Monospaced text on a solid dark bar.",
                   "font": "mono", "weight": "bold", "color": (240, 240, 230), "alt": (192, 232, 172),
                   "stroke": 0.0, "shadow": False, "box": "square", "box_alpha": 215, "size": 0.040},
}
POSITIONS = {"top": "Top", "upper": "Upper third", "middle": "Middle", "lower": "Lower third", "bottom": "Bottom"}
SIZES = {"small": 0.8, "medium": 1.0, "large": 1.25}
ACCENT = {"player": (232, 184, 74), "character": (224, 122, 95), "companion": (111, 177, 224)}
DEFAULT_CFG = {"dialogue": {"style": "cinematic", "pos": "bottom"}, "commentary": {"style": "cinematic", "pos": "top"}, "size": "medium"}


def catalog():
    return {"cap_styles": [{"id": k, "name": v["name"], "note": v["note"]} for k, v in STYLES.items()],
            "cap_positions": [{"id": k, "name": v} for k, v in POSITIONS.items()],
            "cap_sizes": [{"id": k, "name": k.capitalize()} for k in SIZES]}


def clean_cfg(cfg):
    """A complete, valid configuration, whatever was stored."""
    cfg = cfg or {}
    out = {"size": cfg.get("size") if cfg.get("size") in SIZES else "medium"}
    for track in ("dialogue", "commentary"):
        c = cfg.get(track) or {}
        out[track] = {"style": c.get("style") if c.get("style") in STYLES else DEFAULT_CFG[track]["style"],
                      "pos": c.get("pos") if c.get("pos") in POSITIONS else DEFAULT_CFG[track]["pos"]}
    return out


def shares_position(cfg):
    """True when the dialogue and the commentary are set to the same place on screen."""
    cfg = clean_cfg(cfg)
    return cfg["dialogue"]["pos"] == cfg["commentary"]["pos"]


def _font(family, weight, size):
    for p in FONTS.get((family, weight), []) + FALLBACKS:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w, stroke):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) + 2 * stroke <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:3]


def _top_for(pos, H, block_h, shift):
    anchors = {"top": int(H * 0.065), "upper": int(H * 0.17), "middle": (H - block_h) // 2,
               "lower": int(H * 0.80) - block_h, "bottom": int(H * 0.935) - block_h}
    y = anchors[pos]
    if shift:  # two tracks share a position: the inner voice moves toward the middle of the screen
        y += shift if pos in ("top", "upper") else -shift
    return max(0, min(H - block_h, y))


def render(item, W, H, path, cfg=None, shift_commentary=False):
    """One caption as a full-frame transparent PNG. kind: dialogue | voiced | silent."""
    cfg = clean_cfg(cfg)
    kind = item["kind"]
    track = "dialogue" if kind == "dialogue" else "commentary"
    st = STYLES[cfg[track]["style"]]
    size = max(18, int(H * st["size"] * SIZES[cfg["size"]]))
    weight = st["weight"]
    if kind == "silent" and weight != "italic":
        weight = "italic"               # an unspoken thought is always set in italics, quoted
    font = _font(st["font"], weight, size)
    text = item["text"].strip()
    if kind == "silent":
        text = "“" + text.strip("“”\"") + "”"
    if st.get("upper"):
        text = text.upper()
    stroke = int(round(size * st["stroke"]))
    scratch = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    lines = _wrap(scratch, text, font, W * 0.76, stroke)
    line_h = int(size * 1.22)
    text_w = int(max(scratch.textlength(l, font=font) for l in lines)) + 2 * stroke
    pad = int(size * 0.5) if st["box"] else int(size * 0.3)
    bw, bh = text_w + 2 * pad, line_h * len(lines) + int(size * 0.35) + (pad if st["box"] else 0)
    margin = int(size * 0.8)                          # room for the shadow
    canvas = Image.new("RGBA", (bw + 2 * margin, bh + 2 * margin), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    if st["box"]:
        alpha = st.get("box_alpha", 175 if kind != "silent" else 140)
        box = [margin, margin, margin + bw, margin + bh]
        if st["box"] == "square":
            d.rectangle(box, fill=(0, 0, 0, alpha))
        else:
            d.rounded_rectangle(box, radius=int(size * (0.9 if st["box"] == "pill" else 0.35)), fill=(0, 0, 0, alpha))
        if st.get("bar") and kind != "dialogue":
            d.rounded_rectangle([margin, margin, margin + max(4, int(size * 0.14)), margin + bh], radius=2,
                                fill=ACCENT.get(item.get("persona"), (255, 255, 255)) + (255,))
    color = st["alt"] if kind != "dialogue" else st["color"]
    if kind != "dialogue" and st.get("bar"):
        color = st["color"]
    text_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(text_layer)
    for i, l in enumerate(lines):
        tw = td.textlength(l, font=font)
        x = margin + (bw - tw) / 2
        y = margin + (pad if st["box"] else int(size * 0.1)) + i * line_h
        td.text((x, y), l, font=font, fill=color + (255,), stroke_width=stroke, stroke_fill=(0, 0, 0, 255))
    if st["shadow"]:
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow.putalpha(text_layer.split()[3].point(lambda a: int(a * 0.75)))
        shadow = ImageChops.offset(shadow, max(1, size // 22), max(2, size // 16)).filter(
            ImageFilter.GaussianBlur(max(1, size // 24)))
        canvas.alpha_composite(shadow)
    canvas.alpha_composite(text_layer)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shift = int(size * 3.4) if shift_commentary and track == "commentary" else 0
    y = _top_for(cfg[track]["pos"], H, bh, shift) - margin
    img.alpha_composite(canvas, (max(0, (W - canvas.width) // 2), max(0, y)))
    img.save(path)


def dialogue_items(segments, blocks=None):
    """What is said in the game, as on-screen text. Sound effects like [grunts] are left out."""
    out = []
    for s in segments:
        text = re.sub(r"\s+", " ", (s.get("text") or "")).strip()
        if not text or text.startswith("[") or s["end"] <= s["start"]:
            continue
        item = {"start": s["start"], "end": max(s["end"], s["start"] + 1.2), "text": text, "kind": "dialogue"}
        if blocks and any(item["start"] < y and item["end"] > x for x, y in blocks):
            continue  # a cutscene plays untouched, with the game's own subtitles
        out.append(item)
    return out


def commentary_items(lines, clip_seconds):
    """Voiced lines last as long as the audio; silent thoughts as long as they take to read."""
    out = []
    for ln in lines:
        if ln.get("dropped"):
            continue
        if ln.get("silent"):
            dur = min(read_seconds(ln["line"]), ln.get("max_duration") or 99)
            kind = "silent"
        else:
            dur = clip_seconds.get(round(ln["start"], 2))
            if not dur:
                continue  # it was never voiced, so there is nothing to caption
            kind = "voiced"
        out.append({"start": ln["start"], "end": ln["start"] + dur, "text": ln["line"], "kind": kind,
                    "persona": ln.get("persona")})
    return out


def _track(items, W, H, total, workdir, name, cfg, shift):
    """A list for ffmpeg's concat reader: blank, caption, blank, caption... covering the whole video."""
    tdir = Path(workdir) / f"cap_{name}"
    shutil.rmtree(tdir, ignore_errors=True)
    tdir.mkdir()
    blank = tdir / "blank.png"
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    items = sorted(items, key=lambda i: i["start"])
    for a, b in zip(items, items[1:]):  # one caption at a time per track
        a["end"] = min(a["end"], b["start"] - 0.02)
    entries, t = [], 0.0
    for n, it in enumerate(items):
        if it["end"] - it["start"] < 0.3:
            continue
        png = tdir / f"c{n:03d}.png"
        render(it, W, H, png, cfg, shift)
        if it["start"] > t:
            entries.append((blank, it["start"] - t))
        entries.append((png, it["end"] - it["start"]))
        t = it["end"]
    entries.append((blank, max(1.0, total - t)))
    lst = tdir / "list.txt"
    with open(lst, "w") as f:
        for p, d in entries:
            f.write(f"file '{p}'\nduration {d:.3f}\n")
        f.write(f"file '{entries[-1][0]}'\n")  # the concat reader needs the last file repeated
    return lst


def _size(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def _duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def burn(src, out, dialogue, commentary, workdir, progress=None, cfg=None):
    """Write out = src with captions laid over it. Audio is copied untouched."""
    if not dialogue and not commentary:
        shutil.copyfile(src, out)
        return
    cfg = clean_cfg(cfg)
    W, H = _size(src)
    total = _duration(src)
    shift = shares_position(cfg)
    inputs, graph, last, n = ["-i", str(src)], [], "[0:v]", 0
    for name, items in (("dialogue", dialogue), ("commentary", commentary)):
        if not items:
            continue
        n += 1
        inputs += ["-f", "concat", "-safe", "0", "-i", str(_track(items, W, H, total, workdir, name, cfg, shift))]
        graph.append(f"[{n}:v]fps={CAPTION_FPS},format=rgba[t{n}]")
        graph.append(f"{last}[t{n}]overlay=0:0:eof_action=pass:format=auto[o{n}]")
        last = f"[o{n}]"
    graph.append(f"{last}format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart",
           "-progress", "pipe:1", "-nostats", str(out)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith("out_time_us=") and progress:
            try:
                progress(min(total, int(line.split("=")[1]) / 1e6), total)
            except ValueError:
                pass
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError("Adding captions failed: " + err[-300:])


PREVIEW_TEXT = {
    "dialogue": "Oh, is this about Sofia? Enough, enough with Sofia! It's been two years.",
    "voiced": "Four rounds left and I'm peeking like a nervous groundhog.",
    "silent": "Not again. Never again.",
}


def preview(base, cfg, second="voiced", width=1280):
    """The dialogue subtitle plus one inner-voice caption (a spoken line or a silent thought) drawn over a still
    frame with the same code as the real thing."""
    cfg = clean_cfg(cfg)
    base = base.convert("RGBA")
    base = base.resize((width, int(width * base.height / base.width)))
    W, H = base.size
    shift = shares_position(cfg)
    second = "silent" if second == "silent" else "voiced"
    tmp = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip())
    out = base
    for kind in ("dialogue", second):
        p = tmp / f"{kind}.png"
        render({"kind": kind, "text": PREVIEW_TEXT[kind], "persona": "character"}, W, H, p, cfg, shift)
        out = Image.alpha_composite(out, Image.open(p))
    shutil.rmtree(tmp, ignore_errors=True)
    return out.convert("RGB")
