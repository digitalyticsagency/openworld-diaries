import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

from . import guard, llm, voices
from .config import MAX_WORDS, WORDS_PER_SEC
from .llm import claude

STRONG = guard.STRONG_EMOTIONS


VOICE_SEED = int(os.environ.get("ELEVEN_SEED", "20241009"))
TARGET_MEAN_DB = -17.0   # every spoken line is brought to about this average level
MAX_BOOST_DB = 24.0      # some takes arrive very quiet
FADE = 1.5               # a background bed fades in and out over this long, seconds


STORY_SETTINGS = {"stability": 0.42, "similarity_boost": 0.9, "style": 0.35, "use_speaker_boost": True, "speed": 0.94}


def _settings(emotion, story=False):
    """Steady, so the same voice sounds like the same person on every line. Emotion only nudges expressiveness. A story is told a little
    slower and looser, so it sounds like someone talking rather than reading."""
    if story:
        return dict(STORY_SETTINGS)
    if emotion in STRONG:
        return {"stability": 0.5, "similarity_boost": 0.9, "style": 0.3, "use_speaker_boost": True}
    return {"stability": 0.65, "similarity_boost": 0.9, "style": 0.1, "use_speaker_boost": True}


RETRY_WAITS = (2, 5, 10, 20)         # seconds before each new try when the connection or the service hiccups
TRANSIENT = {429, 500, 502, 503, 504}
TIMEOUT = (15, 120)                  # connect, read


def _post(url, headers, body):
    """One request, tried again after a short wait when the network or the service has a hiccup. A wrong key or a
    bad request is not retried: it would only fail the same way."""
    last = None
    for wait in (0,) + RETRY_WAITS:
        if wait:
            time.sleep(wait)
        try:
            r = requests.post(url, headers=headers, json=body, timeout=TIMEOUT)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last = e
            continue
        if r.status_code in TRANSIENT:
            last = requests.exceptions.HTTPError(f"{r.status_code} from the voice service", response=r)
            continue
        return r
    raise last


def tts(text, voice_id, emotion, out, story=False, prev=None, nxt=None):
    body = {"text": text, "model_id": os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2"),
            "voice_settings": _settings(emotion, story), "seed": VOICE_SEED}   # a fixed seed keeps takes repeatable
    if story and prev:                  # the lines either side, so the voice carries the sentence on instead of starting cold
        body["previous_text"] = prev[-300:]
    if story and nxt:
        body["next_text"] = nxt[:300]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {"xi-api-key": llm.key("ELEVENLABS_API_KEY")}
    r = _post(url, headers, body)
    if r.status_code == 422 and "seed" in r.text:       # a model that does not take a seed
        body.pop("seed")
        r = _post(url, headers, body)
    if r.status_code == 422 and any(k in r.text for k in ("speed", "previous_text", "next_text")):    # or speed or neighbouring text
        body["voice_settings"].pop("speed", None)
        body.pop("previous_text", None)
        body.pop("next_text", None)
        r = _post(url, headers, body)
    r.raise_for_status()
    Path(out).write_bytes(r.content)


def _cache_key(text, voice_id, emotion, story=False, prev=None, nxt=None):
    raw = "|".join([voice_id or "", text, emotion or "", os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2"), str(VOICE_SEED),
                    repr(_settings(emotion, story)), str(TARGET_MEAN_DB), str(story), prev or "", nxt or ""])
    return hashlib.sha1(raw.encode()).hexdigest()


def spoken(text, voice_id, emotion, out, cache_dir, story=False, prev=None, nxt=None):
    """A leveled take of this line at `out`. The same line in the same voice is only ever paid for once: after a failure
    the retry reuses every take that already succeeded."""
    cache = Path(cache_dir) / f"{_cache_key(text, voice_id, emotion, story, prev, nxt)}.mp3"
    if cache.exists():
        shutil.copyfile(cache, out)
        return
    if story:
        tts(text, voice_id, emotion, out, story=True, prev=prev, nxt=nxt)
    else:
        tts(text, voice_id, emotion, out)
    level_clip(out)
    cache.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, cache)


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


def _neighbours(lines, n):
    """(the story line before, the story line after) for a line of a told story, when they follow within a few seconds; else (None, None)."""
    ln = lines[n]
    if not ln.get("story"):
        return None, None
    prev = next((l for l in reversed(lines[:n]) if l.get("story") and not l.get("dropped") and not l.get("silent")), None)
    nxt = next((l for l in lines[n + 1:] if l.get("story") and not l.get("dropped") and not l.get("silent")), None)
    near = lambda a, b: a is not None and abs(float(b["start"]) - float(a["start"])) <= 14.0
    return (prev["line"] if near(prev, ln) else None), (nxt["line"] if near(nxt, ln) else None)


def make_clips(lines, windows, system, work, pack=None, progress=None, character=None):
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
        voice = voices.resolve(pack, ln["persona"], character)
        if not voice:
            raise RuntimeError(f"No voice set for {ln['persona']}. Choose one in the app or set ELEVEN_VOICE_{ln['persona'].upper()} in .env")
        path = cdir / f"c_{n:03d}.mp3"
        prev, nxt = _neighbours(lines, n)
        spoken(ln["line"], voice, ln.get("emotion"), path, Path(work) / "voice_cache", story=bool(ln.get("story")), prev=prev, nxt=nxt)
        d = duration(path)
        if d > ln["max_duration"]:
            budget = max(3, int(ln["max_duration"] * WORDS_PER_SEC))
            ln["line"] = claude(system, f"Shorten to at most {min(budget, MAX_WORDS)} words, same meaning "
                                        f"and emotion. Return only the line text: {ln['line']}", 300).strip().strip('"')
            spoken(ln["line"], voice, ln.get("emotion"), path, Path(work) / "voice_cache", story=bool(ln.get("story")), prev=prev, nxt=nxt)
            d = duration(path)
        if d > ln["max_duration"] or not guard.clip_fits(ln["start"], d, windows):
            ln["dropped"] = True   # never talk over speech
            ln["note"] = "no room for the voice"
            continue
        clips.append((ln["start"], path, d))
    return clips


def _keep_only(windows):
    """ffmpeg `enable` expression that is true everywhere except inside the windows, so a volume filter switched on by it
    silences the game audio outside them (the windows keep the game's own speech and cutscenes)."""
    if not windows:
        return "1"
    return "1-(" + "+".join(f"between(t,{max(0.0, a - 0.08):.2f},{b + 0.08:.2f})" for a, b in windows) + ")"


# The music lowerer: level the game audio, then gently expand it downward so the steady quiet bed (music) falls and the louder
# sounds (hooves, shots, voices) keep their level. A heuristic, not source separation: it helps most with soft, steady music.
LOWER_FILTER = "loudnorm=I=-23:TP=-2:LRA=11,compand=attacks=0.02:decays=0.35:points=-90/-120|-38/-62|-22/-24|-8/-8|0/0:soft-knee=6"


def mix(video, clips, out, ambience=(), game_level=1.0, only_in=None, lower_music=False):
    """Game audio plus voice. The audio always runs the whole length of the video: the voice track is padded
    to full length and the result follows the game audio, so it can never stop when the last line ends.
    ambience: [(start, end, file)] quiet beds laid under the game audio, each looped and faded in and out.
    game_level: how loud the game's own audio is (1.0 as it is, 0.0 silent).
    only_in: [(start, end)] windows where the game audio is kept and silent everywhere else (in-game speech and cutscenes
    stay; the music and everything else go). None keeps it everywhere.
    lower_music: run the music lowerer over the game audio first."""
    ambience = list(ambience)
    if not clips and not ambience and game_level == 1.0 and only_in is None and not lower_music:
        subprocess.run(["ffmpeg", "-y", "-i", str(video), "-c", "copy", str(out)],
                       check=True, capture_output=True)
        return
    total = duration(video)
    clips = [(s, p) for s, p, *_ in clips]
    inputs = ["-i", str(video)]
    for _, p in clips:
        inputs += ["-i", str(p)]
    for _, _, p in ambience:
        inputs += ["-stream_loop", "-1", "-i", str(p)]
    chain = ([LOWER_FILTER] if lower_music else []) + ([f"volume=0:enable='{_keep_only(only_in)}'"] if only_in is not None else []) \
        + ([f"volume={game_level:.3f}"] if game_level != 1.0 else [])
    f = [f"[0:a]{','.join(chain) or 'anull'}[game0]"]
    f += [f"[{i}:a]adelay={int(s * 1000)}|{int(s * 1000)}[v{i}]" for i, (s, _) in enumerate(clips, 1)]
    base = len(clips) + 1
    for k, (a, b, _) in enumerate(ambience):
        span = max(0.5, b - a)
        fade = min(FADE, span / 3)
        f.append(f"[{base + k}:a]atrim=0:{span:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={fade:.2f},"
                 f"afade=t=out:st={span - fade:.3f}:d={fade:.2f},adelay={int(a * 1000)}|{int(a * 1000)}[amb{k}]")
    if ambience:
        tags = "".join(f"[amb{k}]" for k in range(len(ambience)))
        f += [f"{tags}amix=inputs={len(ambience)}:normalize=0[amb]", "[game0][amb]amix=inputs=2:normalize=0:duration=first[game]"]
    else:
        f.append("[game0]anull[game]")
    if not clips:
        f.append("[game]alimiter=limit=0.95[aout]")
    else:
        labels = "".join(f"[v{i}]" for i in range(1, len(clips) + 1))
        f += [f"{labels}amix=inputs={len(clips)}:normalize=0[voice0]",
              f"[voice0]apad=whole_dur={total:.3f}[voice]",
              "[voice]asplit=2[vsc][vmix]",
              "[game][vsc]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=450[duck]",
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
