"""Highlight clips from a finished video: one best-moments reel (about a minute) and vertical Shorts.

Planning is pure code: strongest real events, windows that never run into a cutscene, never cut a spoken line in half,
and never overlap each other. The ffmpeg builders only cut and lay out; the commentary and captions are the ones
the video already has (the reel) or are drawn again large for the vertical frame (the Shorts)."""
import subprocess

REEL_BUDGET = 60.0
REEL_BEFORE, REEL_AFTER = 3.0, 5.0
REEL_MAX = 8
REEL_MIN = 3
SHORT_BEFORE, SHORT_AFTER = 5.0, 25.0
SHORT_MAX, SHORT_MIN = 59.0, 12.0
SHORTS = 5
APART = 25.0           # chosen moments are at least this far apart
EDGE = 0.3             # clearance kept from a cutscene


def _mmss(t):
    t = int(max(0, t))
    return f"{t // 60}:{t % 60:02d}"


def moments(all_beats, length, cuts, n):
    """The strongest real events: scan beats (never something someone said), outside cutscenes, spread apart."""
    chosen = []
    for b in sorted((b for b in all_beats if not any(k.startswith("npc_") for k in b["kinds"]) and b["kinds"] != ["detail"]
                     and 2 <= b["t"] <= length - 2), key=lambda b: -b["salience"]):
        if any(x <= b["t"] <= y for x, y in cuts) or any(abs(b["t"] - c["t"]) < APART for c in chosen):
            continue
        chosen.append(b)
        if len(chosen) >= n:
            break
    return sorted(chosen, key=lambda b: b["t"])


def window(b, before, after, length, cuts, spans, max_len):
    """[start, end] around a moment: clear of cutscenes, long enough to finish any line that starts inside it.
    spans: [(start, end)] of the lines and thoughts, so none is cut in half. None if nothing sensible is left."""
    a, e = max(0.0, b["t"] - before), min(length, b["t"] + after)
    for x, y in sorted(cuts):
        if x < e and y > a:
            if x <= b["t"] <= y:
                return None
            if x > b["t"]:
                e = min(e, x - EDGE)
            else:
                a = max(a, y + EDGE)
    for s, f in spans:                                   # a line that starts inside the window is finished
        if a <= s < e and f > e:
            e = f + 0.4
        if s < a < f:                                     # one that was already running at the start: begin after it
            a = min(f + 0.2, b["t"] - 0.5)
    e = min(e, length, a + max_len)
    for x, y in cuts:
        if x < e and y > a:
            e = min(e, x - EDGE)
    return [round(a, 2), round(e, 2)] if e - a >= 4.0 else None


def plan_reel(all_beats, length, cuts, spans):
    """The best moments, a few seconds each, about a minute in all, in time order."""
    picks = moments(all_beats, length, cuts, REEL_MAX)
    wins = []
    for b in sorted(picks, key=lambda b: -b["salience"]):
        w = window(b, REEL_BEFORE, REEL_AFTER, length, cuts, spans, 14.0)
        if w and sum(y - x for x, y in (v["win"] for v in wins)) + (w[1] - w[0]) <= REEL_BUDGET:
            wins.append({"win": w, "t": b["t"], "why": b["why"]})
    wins.sort(key=lambda v: v["win"][0])
    out = []
    for v in wins:
        if out and v["win"][0] < out[-1]["win"][1]:
            continue
        out.append(v)
    return out if len(out) >= REEL_MIN else []


def plan_shorts(all_beats, length, cuts, spans, n=SHORTS):
    """Each Short is one moment with its lead-up and the reaction, under a minute, none overlapping."""
    out = []
    for b in moments(all_beats, length, cuts, n * 2):
        w = window(b, SHORT_BEFORE, SHORT_AFTER, length, cuts, spans, SHORT_MAX)
        if not w or w[1] - w[0] < SHORT_MIN or any(w[0] < v["win"][1] and w[1] > v["win"][0] for v in out):
            continue
        out.append({"win": w, "t": b["t"], "why": b["why"], "salience": b["salience"]})
    out = sorted(out, key=lambda v: -v["salience"])[:n]
    return sorted(out, key=lambda v: v["win"][0])


def cut_reel(final, wins, out):
    """The windows from the finished video joined in order, picture and sound together."""
    parts, tags = [], []
    for i, (a, b) in enumerate(wins):
        parts.append(f"[0:v]trim={a}:{b},setpts=PTS-STARTPTS[v{i}];[0:a]atrim={a}:{b},asetpts=PTS-STARTPTS[a{i}]")
        tags.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(tags) + f"concat=n={len(wins)}:v=1:a=1[v][a]"
    _run(["ffmpeg", "-y", "-i", str(final), "-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
          "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])


def vertical(source, final, win, out):
    """A 9:16 frame: the game picture centred over a blurred, darkened copy of itself, with the finished video's sound
    (game audio and the voice). Captions are drawn on afterwards."""
    a, b = win
    graph = ("[0:v]split[x][y];[x]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=40:6,"
             "eq=brightness=-0.12[bg];[y]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
    _run(["ffmpeg", "-y", "-ss", str(a), "-t", str(round(b - a, 2)), "-i", str(source), "-ss", str(a), "-t", str(round(b - a, 2)),
          "-i", str(final), "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("Cutting the clip failed: " + p.stderr[-300:])
