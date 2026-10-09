"""Cheap first look: which game is this, and which genre pack fits? Four frames, one Gemini call."""
import subprocess
from pathlib import Path

from . import styles
from .llm import gemini_json
from .scenes import video_length

PROMPT = """These are frames from one gameplay video. Identify the game if you can, and pick the single best-fitting genre pack.
Genre packs: {packs}
Return JSON only: {{"game": "title or 'unknown'", "pack": "one pack id", "confidence": 0.0-1.0, "reason": "one short sentence"}}
Say "unknown" for the game rather than guessing."""


def sample_frames(video, work, n=4):
    length = video_length(video)
    paths = []
    for i in range(n):
        out = Path(work) / f"detect_{i}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", str(length * (0.15 + 0.2 * i)), "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=640:-1", str(out)], check=True, capture_output=True)
        paths.append(out)
    return paths


def detect(video, work):
    frames = sample_frames(video, work)
    packs = ", ".join(f"{k} ({v['name']}: {v['games']})" for k, v in styles.PACKS.items())
    parts = [(p.read_bytes(), "image/jpeg") for p in frames] + [PROMPT.format(packs=packs)]
    r = gemini_json(parts)
    if isinstance(r, list):
        r = r[0] if r else {}
    for p in frames:
        p.unlink(missing_ok=True)
    game = r.get("game") or "unknown"
    known = styles.pack_for_game(game)
    return {"game": game, "pack": known or styles.pack_id(r.get("pack")),
            "confidence": r.get("confidence"),
            "reason": (f"Recognized {game}, so the genre comes from the game. " if known else "") + (r.get("reason") or "")}
