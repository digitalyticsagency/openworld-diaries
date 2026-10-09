"""Where can commentary physically go? The free stretches between speech, sized in words."""
from .config import LEAD, MAX_WORDS, MIN_USABLE_GAP, TAIL, WORDS_PER_SEC


def words_for(seconds):
    """How many words fit in this many seconds (est_duration in guard adds 0.4 s)."""
    return max(0, min(MAX_WORDS, int((seconds - 0.4) * WORDS_PER_SEC)))


def free_gaps(windows, length):
    """Gaps between locked windows that can hold a line. Each has start, end, max_words, kind."""
    gaps, prev = [], 0.0
    for a, b in list(windows) + [(length, length)]:
        start, end = prev, min(a, length)
        usable = end - start - LEAD - TAIL
        if usable >= MIN_USABLE_GAP:
            n = words_for(usable)
            if n >= 3:
                gaps.append({"start": round(start, 2), "end": round(end, 2), "max_words": n,
                             "kind": "micro" if n < 9 else "full"})
        prev = max(prev, b)
    return gaps


def locate(gaps, t, reaction=10.0):
    """Where a reaction to an event at time t can be spoken: inside the gap that holds t, or the
    first gap starting soon after it. Returns (line_start, gap) or None."""
    for g in gaps:
        if g["start"] <= t < g["end"]:
            start = max(g["start"] + LEAD, t + 0.4)
            if g["end"] - start - TAIL >= MIN_USABLE_GAP:
                return start, g
            continue
        if g["start"] > t and g["start"] - t <= reaction:
            return g["start"] + LEAD, g
    return None
