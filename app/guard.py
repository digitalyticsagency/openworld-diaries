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
    try:
        chase = int(float(scene.get("wanted_level") or 0)) >= 2        # a police chase is a real event
    except (TypeError, ValueError):
        chase = False
    return bool(scene.get("interactions") or scene.get("moral_events") or chase)


def callback_ok(line, scenes):
    """A reference to an earlier event must point at a real, earlier event."""
    c = line.get("callback_t")
    if c is None:
        return True
    return c < float(line.get("start", 0)) and any(
        abs(s.get("t", -999) - c) <= EVENT_WINDOW and _has_event(s) for s in scenes)


GREETING = re.compile(r"\b(welcome to|welcome back|hello everyone|hi everyone|hey everyone|hey guys|howdy folks|howdy everyone|"
                      r"like and subscribe|subscribe|smash that)\b", re.I)
CANON_NAMES = ("mary eliza isaac dutch hosea john abigail jack sadie micah javier bill karen tilly charles molly pearson strauss sean lenny "
               "trelawny kieran susan leopold annabelle franklin trevor lamar lester amanda tracey devin simeon lucia jason marston morgan "
               "williamson ross").split()
# Names are matched as names: capitalised, whole words. "my uncle", "the bill", "a jack" and "a ross of..." are ordinary words and
# must not be blocked (a plain story about an uncle and a bill is not a story about a game character).
CANON = re.compile(r"\b(?:" + "|".join(n.capitalize() for n in CANON_NAMES) + r")\b")


CLICKBAIT = re.compile(r"(you won'?t believe|shocking|mind[- ]?blow|never seen anything like|changed everything|nobody expected|"
                       r"what happens next|gone wrong|you need to see|must[- ]watch|goes viral|jaw[- ]?dropping)", re.I)


TIME_SPAN = re.compile(r"\b(an hour ago|hours ago|hours (?:now|in|into)|been riding for|been driving for|been walking for|"
                       r"since this morning|since dawn)\b", re.I)      # claims about THIS ride; idioms and memories ("for years") are left alone
NARRATOR = re.compile(r"^\W*(we can see|you can see|as you can see|as we (?:ride|go|move|travel|walk|head)|here we (?:are|see)|and here|behold|"
                      r"now we|let's take a look|ladies and gentlemen)\b", re.I)          # talking to a viewer: nobody is listening to a mind
STORY_NARRATOR = re.compile(r"^\W*(look at (?:that|this|those|these|the)\b|check out\b|there goes\b|what a (?:beautiful|lovely|great|nice|fine)\b)", re.I)
# What a line says is there right now must be there. Things are grouped with their near-synonyms; a group named in the line must also
# appear in the scene notes close to the line (or the line must be about the past).
THINGS = {
    "horse": r"horse|mare|stallion|steed|hooves?|saddle|reins|stirrups?|gallop|trot",
    "car": r"cars?|trucks?|vans?|pickups?|sedans?|coupe|engine|steering|dashboard|windshield|tyres?|tires?|highway|traffic",
    "bike": r"motorcycles?|motorbikes?|bicycles?|bikes?|handlebars",
    "wagon": r"wagons?|carts?|stagecoach|carriage",
    "train": r"trains?|locomotive|railcars?|boxcars?|caboose",
    "boat": r"boats?|canoe|ferry|ship|raft|rowing",
    "air": r"planes?|aeroplane|aircraft|helicopter|chopper|cockpit",
}
SKY = {
    "rain": (r"rain|raining|rained|drizzle|downpour|storm|thunder", ("rain", "storm", "drizzle", "thunder", "wet")),
    "snow": (r"snow|snowing|snowflakes?|blizzard|frost", ("snow", "blizzard", "frost", "ice", "winter")),
    "fog": (r"fog|foggy|mist|misty", ("fog", "mist")),
    "night": (r"night|moon|moonlight|stars|starlit|midnight", ("night", "dark", "moon")),
}                                  # dawn, dusk and day shade into each other, so only night, rain, snow and fog are judged
PAST_MARK = re.compile(r"\b(used to|once|remember|remembered|back in|back when|when i|years? ago|long ago|before|ago|never was|was a|were a|did i|"
                       r"i'?d|i had|i was|wish i|if i|someday|one day|maybe i|would|sleep|wake|wish|hope|hoping|want|could|might|gonna|going to|"
                       r"let me|let's|should|if)\b", re.I)           # memories, hopes and wishes may be about anything
SCENE_WINDOW = 15.0
FUNCTION_END = {"a", "an", "the", "my", "your", "his", "her", "our", "their", "and", "but", "or", "of", "because", "if", "while", "than"}   # words no sentence can end on


def unfinished(text, min_words=3):
    """True for a line that stops mid-thought: it trails off with dots, does not end in a full stop, a question mark or an exclamation,
    ends on a joining word, or is too short to be a sentence."""
    t = text.strip()
    if re.search(r"(\.\.\.|\u2026)\s*[\"')\]]*$", t) or not re.search(r"[.!?][\"')\]]*$", t):
        return True
    words = re.findall(r"[a-z']+", t.lower())
    return len(words) < min_words or (words[-1] in FUNCTION_END if words else True)


LONGEST_PLAUSIBLE = 3600.0      # a video this short cannot show hours or days passing


def _scene_text(scenes, t):
    """Everything the scan says within SCENE_WINDOW seconds of t, lower-cased, as one string."""
    near = [s for s in scenes if abs(s.get("t", -999) - t) <= SCENE_WINDOW]
    return " ".join(__import__("json").dumps(s, default=str).lower() for s in near), near


def scene_mismatch(ln, text, scenes):
    """The first thing the line says is here right now that the scan does not show, or None. Only the present is judged: a memory
    ("used to", "once", "I'd") may be about anything, and a line with no scene notes near it is not judged at all."""
    if not scenes or ln.get("opp_id") in ("intro", "outro") or set((ln.get("beat_kinds") or "").split(",")) & {"travel_memory", "recap", "story_told"}:
        return None
    blob, near = _scene_text(scenes, float(ln.get("start", 0)))
    if not near or PAST_MARK.search(text):
        return None
    low = text.lower()
    for name, pattern in THINGS.items():
        if re.search(rf"\b({pattern})\b", low) and not re.search(rf"\b({pattern})\b", blob):
            if name == "car" and re.search(r"\b(train|locomotive|railcar|boxcar)\b", blob):
                continue                  # a train car
            if name == "horse" and re.search(r"\briding\b", blob):
                continue                  # he is riding, so there is a mount
            return {"horse": "a horse", "car": "a car", "bike": "a bike", "wagon": "a wagon", "train": "a train", "boat": "a boat", "air": "an aircraft"}[name]
    sky = " ".join(str(n.get("time_weather") or "").lower() for n in near)
    if sky.strip():
        for name, (pattern, shown) in SKY.items():
            if re.search(rf"\b({pattern})\b", low) and not any(w in sky for w in shown) and not re.search(rf"\b({pattern})\b", blob):
                return {"rain": "rain", "snow": "snow", "fog": "fog", "night": "night"}[name]
    return None


def content_reason(ln, text, length=None, scenes=None):
    """Rules about what a line may say, beyond when and where it may be."""
    if ln.get("opp_id") not in ("intro", "outro") and GREETING.search(text):
        return "greeting or subscribe line outside the intro and outro"
    kinds_early = set((ln.get("beat_kinds") or "").split(","))
    if "travel_story" in kinds_early and unfinished(text, 5 if "story_told" in kinds_early else 3):
        return "unfinished sentence"
    if length and length < LONGEST_PLAUSIBLE and TIME_SPAN.search(text) and ln.get("opp_id") not in ("intro", "outro") \
            and "story_told" not in kinds_early:
        return "claims a time span the video does not show"
    kinds0 = set((ln.get("beat_kinds") or "").split(","))
    if ln.get("opp_id") not in ("intro", "outro") and (NARRATOR.search(text) or ("travel_story" in kinds0 and STORY_NARRATOR.search(text))):
        return "narrates the scene instead of thinking"
    if kinds0 & {"hook", "cliffhanger"} and CLICKBAIT.search(text):
        return "clickbait wording in a teaser"
    mismatch = scene_mismatch(ln, text, scenes)
    if mismatch:
        return f"mentions {mismatch} the scene does not show"
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


SAME_PASSAGE_GAP = 0.6       # lines of one told passage follow each other closely, with a breath between


def enforce(lines, windows, scenes, min_gap=None, max_words=MAX_WORDS, blocks=None):
    """Return (kept, dropped). Dropped entries carry a 'reason'."""
    min_gap = MIN_GAP if min_gap is None else min_gap
    length = (max((s.get("t", 0) for s in scenes), default=0) + 3.0) if scenes else None      # about how long the video is
    kept, dropped = [], []
    prev_end = prev_silent_end = -1e9
    prev_passage = None
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
                reason = content_reason(ln, text, length, scenes)
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
            elif start < prev_end + (SAME_PASSAGE_GAP if ln.get("passage") is not None and ln.get("passage") == prev_passage else min_gap):
                reason = "too close to previous line"
            elif not grounded(ln, scenes):
                reason = "emotion not grounded in an on-screen event"
            elif not callback_ok(ln, scenes):
                reason = "refers to an event that did not happen"
            elif content_reason(ln, text, length, scenes):
                reason = content_reason(ln, text, length, scenes)
            else:
                ln = {**ln, "line": text, "max_duration": round(maxd, 2)}
                kept.append(ln)
                prev_end = end
                prev_passage = ln.get("passage")
                continue
        dropped.append({**ln, "reason": reason})
    return kept, dropped


def clip_fits(start, duration, windows):
    """Final check on real TTS audio length."""
    return not in_window(start, start + duration, windows)
