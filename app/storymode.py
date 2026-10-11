"""Inner story mode: only a running train of thought while the character rides, walks or runs.

No reactions to fights, shops, animals or conversations; those stay silent. Lines come at a steady pace through the travel
stretches (a lines-a-minute the owner sets), grouped in short threads on the owner's themes so each line picks up from the
last. A passing sight may be offered now and then as a trigger, never as something to describe. Pure functions, no network."""
from . import gaps as gapmod
from . import thinking, travel
from .config import LEAD, MIN_GAP_FLOOR, MIN_USABLE_GAP, TAIL

THREAD_LINES = 4              # lines in one thread of thought before a new one begins
TRIGGER_EVERY = 3             # a passing sight is offered on every third thought, when the scan shows one
MIN_LPM, MAX_LPM, DEFAULT_LPM = 1.5, 10.0, 6.0
MIN_WORDS = 2
SETTLE = 2.5               # after a fight, a shop or a conversation the story waits this long before it speaks again
LEAVE = 1.0                # and it stops this long before the next one starts
MAX_SECONDS = 11.0
BLOCKING = {"damage_taken", "damage_dealt", "kill", "fall", "crash", "death", "loot", "harvest", "craft", "build",
            "npc_contact", "animal_contact", "reward", "score"}      # doing something else: the story waits


def clamp_lpm(value):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return DEFAULT_LPM
    return max(MIN_LPM, min(MAX_LPM, v))


def travelling(n):
    """True while the character is riding, walking or running with nothing else going on. A horse or a passing animal nearby does
    not break it; a fight, a chase, a shop, a conversation or a menu does."""
    if (n.get("activity") or "travel") not in ("travel", "other"):
        return False
    if n.get("is_cutscene") is True or n.get("player_state") in ("aiming", "in dialogue", "in menu", "cutscene"):
        return False
    if n.get("moral_events") or (n.get("casualty") or {}).get("who") not in (None, "", "none"):
        return False
    if any(i.get("kind") in BLOCKING for i in n.get("interactions") or []):
        return False
    if any(npc.get("behavior") in ("hostile", "afraid") for npc in n.get("npcs") or []):
        return False
    if any((a.get("behavior") in ("approaching", "attacking")) and a.get("predator") for a in n.get("animals") or []):
        return False
    try:
        if int(float(n.get("wanted_level") or 0)) >= 1:
            return False
    except (TypeError, ValueError):
        pass
    return True


def stretches(notes, length):
    """[(start, end)] of consecutive travelling frames, each frame covering the time up to its neighbours' midpoints."""
    notes = sorted(notes, key=lambda n: n["t"])
    out = []
    for i, n in enumerate(notes):
        if not travelling(n):
            continue
        lo = (notes[i - 1]["t"] + n["t"]) / 2 if i else max(0.0, n["t"] - 1.5)
        hi = (n["t"] + notes[i + 1]["t"]) / 2 if i + 1 < len(notes) else min(length, n["t"] + 1.5)
        if i and not travelling(notes[i - 1]):
            lo += SETTLE                      # something else just ended: let it breathe
        if i + 1 < len(notes) and not travelling(notes[i + 1]):
            hi -= LEAVE                       # something else is about to start
        if out and lo - out[-1][1] <= 0.5:
            out[-1][1] = hi
        else:
            out.append([lo, hi])
    return [(a, min(b, length)) for a, b in out if min(b, length) > a]


FORMS = ["a question he asks himself", "a plain honest statement", "a half-finished thought that trails off",
         "a small dry joke at his own expense", "a scrap of memory", "a quiet answer to the line before"]
LENGTHS = [("medium", 14), ("short", 8), ("medium", 14), ("a word or two", 4), ("longer", 22)]


def _wobble(n):
    """A fixed, repeatable 0..1 for line n, so the gaps are not a metronome but the same video always plans the same."""
    return ((n * 37 + 11) % 100) / 100.0


def spots(notes, free, length, lpm):
    """[(t, max_seconds)] where a line of the story can go: inside a free gap and a travel stretch, about the pace asked for apart
    (each gap a little shorter or longer than the next, so it does not tick like a clock)."""
    base = max(6.0, min(40.0, 60.0 / clamp_lpm(lpm)))
    out, n = [], 0
    step = base
    for a, b in stretches(notes, length):
        for g in free:
            lo, hi = max(a, g["start"]), min(b, g["end"])
            if hi - lo < MIN_USABLE_GAP + LEAD + TAIL:
                continue
            t = lo + LEAD
            while t + MIN_USABLE_GAP <= hi - TAIL:
                step = max(MIN_GAP_FLOOR + MIN_USABLE_GAP, base * (0.75 + 0.5 * _wobble(n)))
                nxt = t + step
                n += 1
                cap = min(hi - t - TAIL, (nxt - t - MIN_GAP_FLOOR) if nxt + MIN_USABLE_GAP <= hi - TAIL else 1e9, MAX_SECONDS)
                if gapmod.words_for(cap) >= MIN_WORDS:
                    out.append((round(t, 2), round(cap, 2)))
                t = nxt
    return sorted(out)


def plan(notes, free, length, lpm, theme_ids=None, note=""):
    """The story's opportunities, in time order. Every one is kind 'daydream' (a quiet-travel thought), so the writer's travel-thought rule
    and the no-real-names check apply to it; its why says which thread it belongs to and what step it takes."""
    ids = thinking.clean_ids(theme_ids) or list(thinking.THEMES)
    seeds = thinking.prompts(ids)
    note = thinking.clean_note(note)
    out = []
    for i, (t, cap) in enumerate(spots(notes, free, length, lpm)):
        label, seed = seeds[(i // THREAD_LINES) % len(seeds)]
        k = i % THREAD_LINES
        mood, why_m = travel.mood_for(notes, t)
        facts = travel.notice_facts(notes, t) if i % TRIGGER_EVERY == 0 else []
        start = (f"Start a new thread of thought about {label.lower()}: {seed}. If there was a thread before, let it hand over naturally."
                 if k == 0 else
                 f"Continue the same thread ({label.lower()}: {seed}), picking up from your previous line and moving it one small step: "
                 "a doubt, a memory, a hope, an answer to yourself. Do not repeat it.")
        why = (f"INNER STORY, line {k + 1} of {THREAD_LINES} in this thread. Quiet travel, his mind wandering. {start} "
               f"Tone: he feels {mood}" + (f" ({'; '.join(why_m)})" if why_m else "") + ". "
               + (f"A passing sight may start the thought, but do not describe it, only what it stirs: {'; '.join(facts)}. " if facts else "")
               + (f'The owner also asked for these thoughts to touch on: "{note}". Treat that as a feeling, never as a fact, and never name anyone. ' if note and k == 0 else "")
               + "One or two short first-person sentences. Never name a real character and never state a story fact. Use emotion calm, awe, longing, amusement or none.")
        form = FORMS[(i + i // 3) % len(FORMS)]
        size, limit = LENGTHS[i % len(LENGTHS)]
        mw = min(gapmod.words_for(cap), limit)
        why += f" Shape: {form}. Length: {size}, at most {mw} words."
        out.append({"id": f"s{i}", "t": t, "beat_t": None, "salience": 4.6, "kinds": ["daydream", "travel_story"], "kind": "daydream",
                    "why": why, "max_words": mw, "max_duration": cap, "travel": "story", "thread": label})
    return out
