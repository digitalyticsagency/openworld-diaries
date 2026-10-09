import os
import subprocess
from pathlib import Path

import requests

from . import guard, voices
from .config import MAX_WORDS, WORDS_PER_SEC
from .llm import claude

STRONG = guard.STRONG_EMOTIONS


def _settings(emotion):
    if emotion in STRONG:
        return {"stability": 0.3, "similarity_boost": 0.75, "style": 0.6}
    return {"stability": 0.55, "similarity_boost": 0.75, "style": 0.2}


def tts(text, voice_id, emotion, out):
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
        json={"text": text, "model_id": os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2"), "voice_settings": _settings(emotion)},
        timeout=120)
    r.raise_for_status()
    Path(out).write_bytes(r.content)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def make_clips(lines, windows, system, work, pack=None, progress=None):
    """Render each line. Real audio length is re-checked against the speech windows."""
    cdir = Path(work) / "clips"
    cdir.mkdir(exist_ok=True)
    clips = []
    for n, ln in enumerate(lines):
        if progress:
            progress(n, len(lines))
        if ln.get("silent"):
            continue  # an on-screen thought: it is captioned, never voiced
        voice = voices.resolve(pack, ln["persona"])
        if not voice:
            raise RuntimeError(f"No voice set for {ln['persona']}. Choose one in the app or set ELEVEN_VOICE_{ln['persona'].upper()} in .env")
        path = cdir / f"c_{n:03d}.mp3"
        tts(ln["line"], voice, ln.get("emotion"), path)
        d = duration(path)
        if d > ln["max_duration"]:
            budget = max(3, int(ln["max_duration"] * WORDS_PER_SEC))
            ln["line"] = claude(system, f"Shorten to at most {min(budget, MAX_WORDS)} words, same meaning "
                                        f"and emotion. Return only the line text: {ln['line']}", 300).strip().strip('"')
            tts(ln["line"], voice, ln.get("emotion"), path)
            d = duration(path)
        if d > ln["max_duration"] or not guard.clip_fits(ln["start"], d, windows):
            ln["dropped"] = True   # never talk over speech
            continue
        clips.append((ln["start"], path, d))
    return clips


def mix(video, clips, out):
    if not clips:
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-c", "copy", str(out)],
                       check=True, capture_output=True)
        return
    clips = [(s, p) for s, p, *_ in clips]
    inputs = ["-i", str(video)]
    for _, p in clips:
        inputs += ["-i", str(p)]
    f = [f"[{i}:a]adelay={int(s * 1000)}|{int(s * 1000)}[v{i}]" for i, (s, _) in enumerate(clips, 1)]
    labels = "".join(f"[v{i}]" for i in range(1, len(clips) + 1))
    f += [f"{labels}amix=inputs={len(clips)}:normalize=0[voice]",
          "[voice]asplit=2[vsc][vmix]",
          "[0:a][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[duck]",
          "[duck][vmix]amix=inputs=2:normalize=0[aout]"]
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(f),
                    "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", str(out)],
                   check=True, capture_output=True)
