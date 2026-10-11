"""Deterministic guardrails. Code enforces the rules even if the model slips.

Pure functions, no network. See tests/test_guard.py.
"""
import re

from .config import (BUFFER_GAME, BUFFER_OTHER, MAX_WORDS, MIN_GAP, READ_LEAD, READ_WORDS_PER_SEC,
                     SILENT_MAX_WORDS, SPEECH_BUFFER, WORDS_PER_SEC)

STRONG_EMOTIONS = {"pain", "joy", "fear", "grief", "relief", "pride", "disgust", "guilt", "regret"}

# amount 1..10 -> (minimum seconds between lines, target lines per minute)
_GAPS = [22, 20, 17, 15, 12, 10, 9, 7, 6, 5]
_LPM = [0.5, 0.8, 1.2, 1.6, 2.0, 2.5, 3.0, 3.5, 4.2, 5.0]


def amount_profile(amount):
    a = max(1, min(10, int(amount)))
    return _GAPS[a - 1], _LPM[a - 1]


EVENT_WINDOW = 4.0  # seconds a scene event may sit away from trigger_t


def _buffer(seg):
    sp = seg.get("speaker")
    if sp == "player" or sp is None:
        return SPEECH_BUFFER
    return BUFFER_GAME if sp == "game" else BUFFER_OTHER


def speech_windows(segments, buffer=None):
    """Merge speech segments into locked (start, end) windows with a safety buffer.
    The player's own voice keeps the widest buffer; game dialogue gets a tighter one so lines can
    fit between it. Pass an explicit buffer to override."""
    spans = sorted((max(0.0, s["start"] - (_buffer(s) if buffer is None else buffer)),
                    s["end"] + (_buffer(s) if buffer is None else buffer))
                   for s in segments if s.get("end", 0) > s.get("start", 0))
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def read_seconds(text):
    """How long an on-screen thought needs to stay up to be read."""
    return READ_LEAD + len(text.split()) / READ_WORDS_PER_SEC


MIN_SILENT_GAP = 1.5


def est_duration(text):
    return len(text.split()) / WORDS_PER_SEC + 0.4


def next_lock_start(t, windows):
    for a, _ in windows:
        if a > t:
            return a
    return float("inf")


def in_window(a, b, windows):
    return any(a < wb and b > wa for wa, wb in windows)


def grounded(line, scenes):
    """Strong emotions need a real interaction event near trigger_t."""
    if line.get("emotion") not in STRONG_EMOTIONS:
        return True
    t = line.get("trigger_t")
    if t is None:
        return False
    return any(abs(s.get("t", -999) - t) <= EVENT_WINDOW and _has_event(s) for s in scenes)


def _has_event(scene):
    return bool(scene.get("interactions") or scene.get("moral_events"))


def callback_ok(line, scenes):
    """A reference to an earlier event must point at a real, earlier event."""
    c = line.get("callback_t")
    if c is None:
        return True
    return c < float(line.get("start", 0)) and any(
        abs(s.get("t", -999) - c) <= EVENT_WINDOW and _has_event(s) for s in scenes)


GREETING = re.compile(r"\b(welcome to|welcome back|hello everyone|hi everyone|hey everyone|hey guys|howdy folks|howdy everyone|"
                      r"like and subscribe|subscribe|smash that)\b", re.I)
CANON = re.compile(r"\b(mary|eliza|isaac|dutch|hosea|john|abigail|jack|sadie|micah|javier|bill|karen|tilly|charles|molly|uncle|"
                   r"pearson|strauss|sean|lenny|trelawny|kieran|susan|leopold|annabelle)\b", re.I)


CLICKBAIT = re.compile(r"(you won'?t believe|shocking|mind[- ]?blow|never seen anything like|changed everything|nobody expected|"
                       r"what happens next|gone wrong|you need to see|must[- ]watch|goes viral|jaw[- ]?dropping)", re.I)


def content_reason(ln, text):
    """Rules about what a line may say, beyond when and where it may be."""
    if ln.get("opp_id") not in ("intro", "outro") and GREETING.search(text):
        return "greeting or subscribe line outside the intro and outro"
    kinds0 = set((ln.get("beat_kinds") or "").split(","))
    if kinds0 & {"hook", "cliffhanger"} and CLICKBAIT.search(text):
        return "clickbait wording in a teaser"
    if ln.get("daydream") and CANON.search(text):
        return "daydream names a real character"
    kinds, emo = set((ln.get("beat_kinds") or "").split(",")), ln.get("emotion")
    if "justice_outlaw" in kinds and emo in ("guilt", "regret"):
        return "remorse over an armed outlaw"
    if "casualty_innocent" in kinds and emo in ("pride", "joy", "amusement"):
        return "celebrating an innocent's death"
    if "casualty_unclear" in kinds and emo in ("guilt", "regret", "pride", "joy"):
        return "claims guilt or justice when it is unclear"
    return None


def _words(text):
    return re.findall(r"[a-z']+", text.lower())


def similar(a, b):
    """The same line said again: same first three words, or mostly the same words."""
    wa, wb = _words(a), _words(b)
    if len(wa) >= 3 and len(wb) >= 3 and wa[:3] == wb[:3]:
        return True
    sa, sb = set(wa), set(wb)
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) > 0.6


def dedupe(lines, previous):
    """Drop lines that repeat something already said anywhere earlier in the video."""
    seen = [p["line"] for p in previous]
    kept, dropped = [], []
    for ln in sorted(lines, key=lambda x: x.get("start", 0)):
        if ln.get("opp_id") not in ("intro", "outro") and any(similar(ln["line"], s) for s in seen):
            dropped.append({**ln, "reason": "repeats an earlier line"})
        else:
            kept.append(ln)
            seen.append(ln["line"])
    return kept, dropped


def enforce(lines, windows, scenes, min_gap=None, max_words=MAX_WORDS, blocks=None):
    """Return (kept, dropped). Dropped entries carry a 'reason'."""
    min_gap = MIN_GAP if min_gap is None else min_gap
    kept, dropped = [], []
    prev_end = prev_silent_end = -1e9
    for ln in sorted(lines, key=lambda x: x.get("start", 0)):
        text = (ln.get("line") or "").strip()
        start = float(ln.get("start", 0))
        reason = None
        if blocks and text:
            shown = read_seconds(text) if ln.get("silent") else est_duration(text)
            if in_window(start, start + shown, blocks):   # cutscene: no voice, no thoughts, no captions
                dropped.append({**ln, "reason": "during a cutscene"})
                continue
        if ln.get("silent"):
            # On-screen only: it makes no sound, so speech windows do not apply. Its own rules:
            # short, one at a time, grounded, and a callback must be real.
            if not text:
                reason = "empty"
            elif len(text.split()) > (ln.get("max_words") or SILENT_MAX_WORDS):
                reason = "too long"
            elif start < prev_silent_end + MIN_SILENT_GAP:
                reason = "too close to previous thought"
            elif not grounded(ln, scenes):
                reason = "emotion not grounded in an on-screen event"
            elif not callback_ok(ln, scenes):
                reason = "refers to an event that did not happen"
            else:
                reason = content_reason(ln, text)
            if reason:
                dropped.append({**ln, "reason": reason})
            else:
                kept.append({**ln, "line": text})
                prev_silent_end = start + min(read_seconds(text), ln.get("max_duration") or 99)
            continue
        if not text:
            reason = "empty"
        elif len(text.split()) > (ln.get("max_words") or max_words):
            reason = "too long"
        else:
            room = next_lock_start(start, windows) - start - 0.3
            maxd = min(float(ln.get("max_duration") or 1e9), room)
            end = start + est_duration(text)
            if in_window(start, end, windows):
                reason = "overlaps speech"
            elif maxd < est_duration(text):
                reason = "no room before speech"
            elif start < prev_end + min_gap:
                reason = "too close to previous line"
            elif not grounded(ln, scenes):
                reason = "emotion not grounded in an on-screen event"
            elif not callback_ok(ln, scenes):
                reason = "refers to an event that did not happen"
            elif content_reason(ln, text):
                reason = content_reason(ln, text)
            else:
                ln = {**ln, "line": text, "max_duration": round(maxd, 2)}
                kept.append(ln)
                prev_end = end
                continue
        dropped.append({**ln, "reason": reason})
    return kept, dropped


def clip_fits(start, duration, windows):
    """Final check on real TTS audio length."""
    return not in_window(start, start + duration, windows)
