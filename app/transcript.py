"""Who is speaking and when. Used for two jobs: lock windows, and give Claude context."""
import re
import subprocess
from pathlib import Path

from .llm import gemini_json

TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})"
CUE = re.compile(rf"{TS}\s*-->\s*{TS}[^\n]*\n(.*?)(?:\n\s*\n|\Z)", re.S)

PROMPT = """Transcribe all speech in this gameplay audio with timestamps in seconds.
Label each segment's speaker as "player" (the person playing, usually closest to the mic),
"game" (a game character, NPC or cutscene voice) or "unknown".
Include shouts, grunts and laughs as segments with a short text like "[laughs]" or "[grunts]".
Ignore music and sound effects. Return JSON only:
[{"start": seconds, "end": seconds, "speaker": "player|game|unknown", "text": "..."}]
Return [] if nobody speaks."""


def _secs(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_subtitles(text):
    segs = []
    for m in CUE.finditer(text.replace("\r\n", "\n")):
        g = m.groups()
        body = re.sub(r"<[^>]+>", "", g[8]).replace("\n", " ").strip()
        if body:
            segs.append({"start": _secs(*g[0:4]), "end": _secs(*g[4:8]),
                         "speaker": "unknown", "text": body})
    return segs


def from_audio(video, work):
    mp3 = Path(work) / "speech.mp3"
    subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                    "-b:a", "48k", str(mp3)], check=True, capture_output=True)
    segs = gemini_json([PROMPT, (mp3.read_bytes(), "audio/mp3")])
    return [s for s in segs if s.get("end", 0) > s.get("start", 0)]


def get_transcript(video, work, sidecar_text=None):
    """Prefer a caption file placed next to the video in Drive; else transcribe the audio."""
    if sidecar_text:
        segs = parse_subtitles(sidecar_text)
        if segs:
            return segs
    return from_audio(video, work)
