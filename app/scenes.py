import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .config import FRAME_BATCH, FRAME_EVERY, PROMPTS, SCAN_BUSY_SHARE, SCAN_CALM, SCAN_FINE
from .llm import gemini_json


def video_length(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def pick_times(motions, fine=SCAN_FINE, calm=SCAN_CALM, share=SCAN_BUSY_SHARE):
    """Which frames to scan. motions[i] is how much the picture changed into frame i (frames are `fine` seconds apart).
    The busiest share of the video is scanned at every frame, quiet stretches at most every `calm` seconds."""
    if not motions:
        return []
    ranked = sorted(motions)
    thr = ranked[min(len(ranked) - 1, int(len(ranked) * (1 - share)))]
    keep, last = [0], 0.0
    for i in range(1, len(motions)):
        t = i * fine
        gap = t - last
        if gap >= calm - 1e-6 or (motions[i] > thr and gap >= fine - 1e-6):
            keep.append(i)
            last = t
    return keep


def _motion(files):
    out, prev = [], None
    for f in files:
        with Image.open(f) as im:
            cur = im.convert("L").resize((32, 18))
        out.append(ImageStat.Stat(ImageChops.difference(cur, prev)).mean[0] if prev is not None else 0.0)
        prev = cur
    return out


def extract_frames(video, work):
    """[(second, file)] to scan. New scans sample finely, then keep dense frames only where the picture is busy.
    Scans started before this existed keep their old fixed 3 second grid, so a half-finished one can resume."""
    fdir = Path(work) / "frames"
    done, keep = fdir / ".done", fdir / ".keep.json"
    if done.exists() and not keep.exists():
        return [(i * FRAME_EVERY, f) for i, f in enumerate(sorted(fdir.glob("f_*.jpg")))]
    if not keep.exists():  # a half-finished extraction is redone from scratch
        shutil.rmtree(fdir, ignore_errors=True)
        fdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf",
                        f"fps=1/{SCAN_FINE},scale=768:-1", "-q:v", "4",
                        str(fdir / "f_%05d.jpg")], check=True, capture_output=True)
        files = sorted(fdir.glob("f_*.jpg"))
        kept = pick_times(_motion(files))
        chosen = [(round(i * SCAN_FINE, 2), files[i].name) for i in kept]
        keepset = set(kept)
        for i, f in enumerate(files):
            if i not in keepset:
                f.unlink()
        keep.write_text(json.dumps(chosen))
        done.write_text("ok")
    return [(t, fdir / name) for t, name in json.loads(keep.read_text())]


def analyze(video, work, game, hints="", progress=None):
    tpl = (PROMPTS / "scene.md").read_text()
    frames = extract_frames(video, work)
    partial = Path(work) / "scenes.partial.json"
    notes = json.loads(partial.read_text()) if partial.exists() else []
    notes = notes[: (len(notes) // FRAME_BATCH) * FRAME_BATCH]  # only whole batches count as done
    for i in range(len(notes), len(frames), FRAME_BATCH):
        chunk = frames[i:i + FRAME_BATCH]
        parts = [(f.read_bytes(), "image/jpeg") for _, f in chunk]
        parts.append(tpl.format(game=game, hints=hints or "anything the player touches, gains, loses or decides", times=[t for t, _ in chunk]))
        batch = gemini_json(parts)
        for (t, _), n in zip(chunk, batch):  # trust our timestamps, not the model's
            n["t"] = t
        notes += batch
        partial.write_text(json.dumps(notes))  # a failure later must not lose the finished batches
        print(f"scenes {min(i + FRAME_BATCH, len(frames))}/{len(frames)}", file=sys.stderr)
        if progress:
            progress(min(i + FRAME_BATCH, len(frames)), len(frames))
    (Path(work) / "scenes.json").write_text(json.dumps(notes, indent=2))
    partial.unlink(missing_ok=True)
    return notes
