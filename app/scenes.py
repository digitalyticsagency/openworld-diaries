import json
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
    fdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vf",
                    f"fps=1/{FRAME_EVERY},scale=768:-1", "-q:v", "4",
                    str(fdir / "f_%05d.jpg")], check=True, capture_output=True)
    return [(i * FRAME_EVERY, f) for i, f in enumerate(sorted(fdir.glob("f_*.jpg")))]


def analyze(video, work, game, hints=""):
    tpl = (PROMPTS / "scene.md").read_text()
    frames = extract_frames(video, work)
    notes = []
    for i in range(0, len(frames), FRAME_BATCH):
        chunk = frames[i:i + FRAME_BATCH]
        parts = [(f.read_bytes(), "image/jpeg") for _, f in chunk]
        parts.append(tpl.format(game=game, hints=hints or "anything the player touches, gains, loses or decides", times=[t for t, _ in chunk]))
        batch = gemini_json(parts)
        for (t, _), n in zip(chunk, batch):  # trust our timestamps, not the model's
            n["t"] = t
        notes += batch
        print(f"scenes {min(i + FRAME_BATCH, len(frames))}/{len(frames)}", file=sys.stderr)
    (Path(work) / "scenes.json").write_text(json.dumps(notes, indent=2))
    return notes
