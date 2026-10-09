"""Find the cinematic parts of a game video, so no commentary or captions land on them.

Three signals, and any one of them counts (when unsure, assume it is a cutscene and stay silent):
  1. the picture has cinematic black bars across the top and bottom (checked here, free)
  2. the vision scan said the frame is a cutscene or a menu
  3. the scan's player state says cutscene
The user can then add or remove ranges by hand; edits are kept apart from the automatic result.
"""
import json
import subprocess

from PIL import Image, ImageStat

from .config import CUT_MERGE_GAP, CUT_MIN_SECONDS, CUT_PAD, CUT_STEP

W, H = 128, 72
BAR_ROWS = max(2, int(H * 0.07))
BAR_MEAN, BAR_STD = 16.0, 12.0
CENTER_MIN_MEAN = 6.0          # an all-black frame is a fade, not a letterboxed picture


def has_bars(img):
    """True if the top and bottom strips are flat black but the middle of the frame is not."""
    top = ImageStat.Stat(img.crop((0, 0, W, BAR_ROWS)))
    bottom = ImageStat.Stat(img.crop((0, H - BAR_ROWS, W, H)))
    middle = ImageStat.Stat(img.crop((0, int(H * 0.3), W, int(H * 0.7))))
    flat = all(s.mean[0] < BAR_MEAN and s.stddev[0] < BAR_STD for s in (top, bottom))
    return flat and middle.mean[0] > CENTER_MIN_MEAN


def letterbox_flags(video):
    """{second: True/False} for one tiny grayscale frame every CUT_STEP seconds."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf",
                        f"fps=1/{CUT_STEP},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                       capture_output=True, check=True)
    size = W * H
    return {i * CUT_STEP: has_bars(Image.frombytes("L", (W, H), p.stdout[i * size:(i + 1) * size]))
            for i in range(len(p.stdout) // size)}


def frame_flags(notes, bars):
    """Per frame: (t, is_cutscene, signals). Union of the signals: any one is enough."""
    by_t = {n["t"]: n for n in notes}
    out = []
    for t in sorted(set(by_t) | set(bars)):
        n, sig = by_t.get(t, {}), []
        if bars.get(t):
            sig.append("black bars")
        if n.get("is_cutscene") is True:
            sig.append("scan says cutscene")
        if n.get("player_state") in ("cutscene", "menu"):
            sig.append(f"state {n['player_state']}")
        out.append((t, bool(sig), sig))
    return out


def intervals(flags, merge_gap=CUT_MERGE_GAP, pad=0.0):
    """Runs of flagged frames, joined across small gaps. A lone frame only counts with black bars."""
    runs, cur = [], None
    for t, flagged, sig in flags:
        if flagged:
            cur = cur or {"a": t, "b": t + CUT_STEP, "n": 0, "bars": False}
            cur["b"], cur["n"] = t + CUT_STEP, cur["n"] + 1
            cur["bars"] = cur["bars"] or "black bars" in sig
        elif cur:
            runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    merged = []
    for r in runs:
        if merged and r["a"] - merged[-1]["b"] <= merge_gap:
            merged[-1].update(b=r["b"], n=merged[-1]["n"] + r["n"], bars=merged[-1]["bars"] or r["bars"])
        else:
            merged.append(dict(r))
    return [[max(0.0, m["a"] - pad), m["b"] + pad] for m in merged
            if m["b"] - m["a"] >= CUT_MIN_SECONDS or m["bars"]]


def detect(video, notes):
    """Automatic result: {"ranges": [[a, b], ...], "bars_checked": n}. Costs one quick pass over the video."""
    bars = letterbox_flags(video)
    return {"ranges": intervals(frame_flags(notes, bars)), "bars_checked": len(bars)}


# ---------------------------------------------------------------- manual edits on top of the automatic result

def empty():
    return {"auto": [], "add": [], "remove": []}


def load(text):
    try:
        c = json.loads(text) if text else empty()
    except ValueError:
        c = empty()
    return {k: [list(map(float, r)) for r in c.get(k, [])] for k in ("auto", "add", "remove")}


def merge(ranges):
    out = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _subtract(ranges, cuts):
    out = []
    for a, b in ranges:
        pieces = [[a, b]]
        for x, y in cuts:
            nxt = []
            for p in pieces:
                if y <= p[0] or x >= p[1]:
                    nxt.append(p)
                    continue
                if x > p[0]:
                    nxt.append([p[0], x])
                if y < p[1]:
                    nxt.append([y, p[1]])
            pieces = nxt
        out += [p for p in pieces if p[1] - p[0] > 0.2]
    return out


def effective(c):
    """What the brain actually obeys: the automatic ranges minus the removed ones, plus the added ones."""
    return merge(_subtract(c["auto"], c["remove"]) + c["add"])


def overlaps(ranges, a, b):
    return any(a < y and b > x for x, y in ranges)


def padded(ranges, pad=CUT_PAD):
    return merge([[max(0.0, a - pad), b + pad] for a, b in ranges])


def subtract_ranges(windows, cuts):
    """Speech windows with the cutscene parts cut out (silent thoughts only live outside cutscenes)."""
    return _subtract([list(w) for w in windows], cuts)


def add_range(c, a, b):
    c["add"] = merge(c["add"] + [[a, b]])
    c["remove"] = _subtract(c["remove"], [[a, b]])


def remove_range(c, a, b):
    c["remove"] = merge(c["remove"] + [[a, b]])
    c["add"] = _subtract(c["add"], [[a, b]])


def total_seconds(ranges):
    return round(sum(b - a for a, b in ranges), 1)
