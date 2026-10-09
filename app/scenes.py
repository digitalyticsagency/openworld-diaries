import json
import shutil
import subprocess
import sys
from pathlib import Path

from .config import FRAME_BATCH, FRAME_EVERY, PROMPTS
from .llm import gemini_json


def video_length(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(video)], check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def extract_frames(video, work):
    fdir = Path(work) / "frames"
    done = fdir / ".done"
    if not done.exists():  # a half-finished extraction is redone from scratch
        shutil.rmtree(fdir, ignore_errors=True)
        fdir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf",
                        f"fps=1/{FRAME_EVERY},scale=768:-1", "-q:v", "4",
                        str(fdir / "f_%05d.jpg")], check=True, capture_output=True)
        done.write_text("ok")
    return [(i * FRAME_EVERY, f) for i, f in enumerate(sorted(fdir.glob("f_*.jpg")))]


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
