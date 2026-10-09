"""Burn captions into the finished video.

This Mac's ffmpeg has no text drawing, so each caption is drawn as a transparent picture and
laid over the video on two tracks: in-game dialogue at the bottom, the inner voice near the top.
"""
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import CAPTION_FPS
from .guard import read_seconds

FONTS = {
    "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "italic": ["/System/Library/Fonts/Supplemental/Arial Italic.ttf", "/Library/Fonts/Arial Italic.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"],
}
ACCENT = {"player": (232, 184, 74), "character": (224, 122, 95), "companion": (111, 177, 224)}


def _font(kind, size):
    for p in FONTS[kind]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines[:3]


def render(item, W, H, path):
    """One caption as a full-frame transparent PNG. kind: dialogue | voiced | silent."""
    kind = item["kind"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    size = max(20, int(H * (0.040 if kind == "dialogue" else 0.044)))
    font = _font("italic" if kind == "silent" else "bold", size)
    text = item["text"]
    if kind == "silent":
        text = "“" + text.strip("“”\"") + "”"
    lines = _wrap(d, text, font, W * 0.74)
    line_h = int(size * 1.25)
    box_w = int(max(d.textlength(l, font=font) for l in lines)) + size
    box_h = line_h * len(lines) + int(size * 0.6)
    x0 = (W - box_w) // 2
    y0 = int(H - H * 0.075 - box_h) if kind == "dialogue" else int(H * 0.12)
    d.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=int(size * 0.35),
                        fill=(0, 0, 0, 175 if kind != "silent" else 140))
    if kind != "dialogue":
        accent = ACCENT.get(item.get("persona"), (255, 255, 255))
        d.rounded_rectangle([x0, y0, x0 + max(4, int(size * 0.14)), y0 + box_h], radius=2, fill=accent + (255,))
    color = (255, 255, 255, 255) if kind != "silent" else (205, 222, 245, 255)
    for i, l in enumerate(lines):
        tw = d.textlength(l, font=font)
        d.text(((W - tw) / 2, y0 + int(size * 0.3) + i * line_h), l, font=font, fill=color)
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


def _track(items, W, H, total, workdir, name):
    """A list for ffmpeg's concat reader: blank, caption, blank, caption... covering the whole video."""
    tdir = Path(workdir) / f"cap_{name}"
    tdir.mkdir(exist_ok=True)
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
        render(it, W, H, png)
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


def burn(src, out, dialogue, commentary, workdir, progress=None):
    """Write out = src with captions laid over it. Audio is copied untouched."""
    if not dialogue and not commentary:
        shutil.copyfile(src, out)
        return
    W, H = _size(src)
    total = _duration(src)
    inputs, graph, last, n = ["-i", str(src)], [], "[0:v]", 0
    for name, items in (("dialogue", dialogue), ("commentary", commentary)):
        if not items:
            continue
        n += 1
        inputs += ["-f", "concat", "-safe", "0", "-i", str(_track(items, W, H, total, workdir, name))]
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
