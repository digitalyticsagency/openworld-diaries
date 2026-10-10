import os
import re
import subprocess
from pathlib import Path

import requests

from . import guard, llm, voices
from .config import MAX_WORDS, WORDS_PER_SEC
from .llm import claude

STRONG = guard.STRONG_EMOTIONS


VOICE_SEED = int(os.environ.get("ELEVEN_SEED", "20241009"))
TARGET_MEAN_DB = -17.0   # every spoken line is brought to about this average level
MAX_BOOST_DB = 24.0      # some takes arrive very quiet


def _settings(emotion):
    """Steady, so the same voice sounds like the same person on every line. Emotion only nudges expressiveness."""
    if emotion in STRONG:
        return {"stability": 0.5, "similarity_boost": 0.9, "style": 0.3, "use_speaker_boost": True}
    return {"stability": 0.65, "similarity_boost": 0.9, "style": 0.1, "use_speaker_boost": True}


def tts(text, voice_id, emotion, out):
    body = {"text": text, "model_id": os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2"),
            "voice_settings": _settings(emotion), "seed": VOICE_SEED}   # a fixed seed keeps takes repeatable
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": llm.key("ELEVENLABS_API_KEY")}
    r = requests.post(url, headers=headers, json=body, timeout=120)
    if r.status_code == 422 and "seed" in r.text:       # a model that does not take a seed
        body.pop("seed")
        r = requests.post(url, headers=headers, json=body, timeout=120)
    r.raise_for_status()
    Path(out).write_bytes(r.content)


def mean_db(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.search(r"mean_volume: (-?[\d.]+) dB", p.stderr)
    return float(m.group(1)) if m else None


def level_clip(path, target=TARGET_MEAN_DB):
    """Bring one spoken line to the common level, so no line is quieter or louder than the rest.
    Some takes come back very quiet, so the boost is large; a peak limiter keeps it from clipping, and a
    second pass nudges any line the first pass could not fully reach."""
    for _ in range(2):
        m = mean_db(path)
        if m is None or abs(target - m) < 1.0:
            return
        gain = max(-6.0, min(MAX_BOOST_DB, target - m))
        tmp = Path(path).with_suffix(".lvl.mp3")
        subprocess.run(["ffmpeg", "-y", "-i", str(path), "-af",
                        f"volume={gain:.2f}dB,alimiter=limit=0.9:attack=5:release=60:level=0", str(tmp)],
                       check=True, capture_output=True)
        tmp.replace(path)


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True)
    return float(r.stdout.strip())


def make_clips(lines, windows, system, work, pack=None, progress=None):
    """Render each line. Real audio length is re-checked against the speech windows."""
    cdir = Path(work) / "clips"
    cdir.mkdir(exist_ok=True)
    for stale in cdir.glob("c_*"):   # clips from an earlier run must never be mistaken for this one's
        stale.unlink()
    clips = []
    for n, ln in enumerate(lines):
        if progress:
            progress(n, len(lines))
        if ln.get("silent") or ln.get("dropped"):
            continue  # a thought is captioned, never voiced; a dropped line is not used at all
        voice = voices.resolve(pack, ln["persona"])
        if not voice:
            raise RuntimeError(f"No voice set for {ln['persona']}. Choose one in the app or set ELEVEN_VOICE_{ln['persona'].upper()} in .env")
        path = cdir / f"c_{n:03d}.mp3"
        tts(ln["line"], voice, ln.get("emotion"), path)
        level_clip(path)
        d = duration(path)
        if d > ln["max_duration"]:
            budget = max(3, int(ln["max_duration"] * WORDS_PER_SEC))
            ln["line"] = claude(system, f"Shorten to at most {min(budget, MAX_WORDS)} words, same meaning "
                                        f"and emotion. Return only the line text: {ln['line']}", 300).strip().strip('"')
            tts(ln["line"], voice, ln.get("emotion"), path)
            level_clip(path)
            d = duration(path)
        if d > ln["max_duration"] or not guard.clip_fits(ln["start"], d, windows):
            ln["dropped"] = True   # never talk over speech
            ln["note"] = "no room for the voice"
            continue
        clips.append((ln["start"], path, d))
    return clips


def mix(video, clips, out):
    """Game audio plus voice. The audio always runs the whole length of the video: the voice track is padded
    to full length and the result follows the game audio, so it can never stop when the last line ends."""
    if not clips:
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-c", "copy", str(out)],
                       check=True, capture_output=True)
        return
    total = duration(video)
    clips = [(s, p) for s, p, *_ in clips]
    inputs = ["-i", str(video)]
    for _, p in clips:
        inputs += ["-i", str(p)]
    f = [f"[{i}:a]adelay={int(s * 1000)}|{int(s * 1000)}[v{i}]" for i, (s, _) in enumerate(clips, 1)]
    labels = "".join(f"[v{i}]" for i in range(1, len(clips) + 1))
    f += [f"{labels}amix=inputs={len(clips)}:normalize=0[voice0]",
          f"[voice0]apad=whole_dur={total:.3f}[voice]",
          "[voice]asplit=2[vsc][vmix]",
          "[0:a][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=450[duck]",
          "[duck][vmix]amix=inputs=2:normalize=0:duration=first,alimiter=limit=0.95[aout]"]
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(f),
                    "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", str(out)],
                   check=True, capture_output=True)


def stream_seconds(path, kind):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", f"{kind}:0", "-show_entries", "stream=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return None


def verify_av(path, tolerance=1.5):
    """A finished video whose sound ends early is a failure, never something to deliver."""
    v, a = stream_seconds(path, "v"), stream_seconds(path, "a")
    if v is not None and a is not None and a < v - tolerance:
        raise RuntimeError(f"The finished video's audio ends at {a:.0f}s but the picture runs to {v:.0f}s.")
