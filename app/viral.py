"""Hooks and structure that keep people watching, all built from what really happens in the video.

  cold open   a teaser for the strongest real moment, placed in the first seconds, before the welcome
  cliffhanger a short suspense line a few seconds before a big moment, only when the scan shows something building
  chapters    timestamped chapters for the YouTube description, from the activity runs in the scan

Nothing here invents anything: a teaser points at a real beat, a suspense line needs a real cue in the frames just before
it, and chapters are named after what the scan saw. Pure functions, no network."""
from . import gaps as gapmod
from .config import LEAD, MIN_USABLE_GAP, TAIL

HOOK_WITHIN = 12.0        # the cold open must start inside the first seconds
HOOK_MAX_SECONDS = 6.5
HOOK_MAX_WORDS = 14
HOOK_MIN_WORDS = 6
TEASE_AFTER = 30.0        # the teased moment is really later in the video, not right here
CLIFF_MIN_SALIENCE = 6.5
CLIFF_LEAD = (4.0, 12.0)  # the suspense line starts this many seconds before the moment
CLIFF_SPACING = 60.0
CLIFF_MAX = 6
CLIFF_AFTER = 20.0        # none in the opening seconds, where the hook and the welcome live

CHAPTER_TITLES = {"shop": "Shopping", "horse_care": "Horse care", "animal_interaction": "Animals", "hunting": "Hunting",
                  "fishing": "Fishing", "combat": "Gunfight", "npc_talk": "Conversation", "camp": "Camp",
                  "mission": "Mission", "cutscene": "Cutscene"}
CHAPTER_MIN = 30.0
CHAPTER_MAX = 12


def _mmss(t):
    t = int(max(0, t))
    return f"{t // 60}:{t % 60:02d}"


# ------------------------------------------------------------------ cold open

def strongest_moment(all_beats, length, after=TEASE_AFTER):
    """The most dramatic real event later in the video: a scan beat, not something someone said."""
    pool = [b for b in all_beats if b["t"] >= after and b["t"] <= length - 5
            and not any(k.startswith("npc_") for k in b["kinds"]) and b["kinds"] != ["detail"]]
    return max(pool, key=lambda b: b["salience"]) if pool else None


def hook_opportunity(gaps, beat):
    """A teaser line in the first seconds. None if there is no moment to tease or no room to say it."""
    if not beat:
        return None
    for g in gaps:
        if g["start"] > HOOK_WITHIN:
            break
        t = max(g["start"] + LEAD, 0.5)
        cap = min(g["end"] - t - TAIL, HOOK_MAX_SECONDS)
        mw = min(HOOK_MAX_WORDS, gapmod.words_for(cap))
        if mw >= HOOK_MIN_WORDS:
            return {"id": "hook", "t": round(t, 2), "beat_t": None, "salience": 97.0, "kinds": ["hook"], "kind": "full",
                    "why": ("COLD OPEN. Tease something that really happens later in this video, without saying how it ends and "
                            f"without any greeting. What really happens at {_mmss(beat['t'])}: {beat['why']}. Write one short, curious "
                            "line that makes a viewer want to see it. Promise nothing beyond that, and never use phrases such as "
                            "'you won't believe'. Use emotion awe, calm or none."),
                    "max_words": mw, "max_duration": round(cap, 2), "must_fit": HOOK_MIN_WORDS, "teaser_t": beat["t"]}
    return None


# ------------------------------------------------------------------ cliffhangers

def buildup_cue(scenes, t):
    """What the frames just before a moment really show building toward it, or None."""
    for n in sorted((n for n in scenes if t - 15 <= n["t"] < t), key=lambda n: -n["t"]):
        for npc in n.get("npcs") or []:
            if npc.get("behavior") == "hostile":
                return f"a hostile {npc.get('role') or 'stranger'} is close"
        for a in n.get("animals") or []:
            if a.get("behavior") in ("approaching", "attacking") and (a.get("species") or "").strip():
                return f"a {a['species']} is {a['behavior']}"
        if n.get("player_state") == "aiming":
            return "he is aiming at something"
        if n.get("activity") == "combat":
            return "a fight is starting"
    return None


def cliffhangers(all_beats, scenes, gaps, length):
    """Short suspense lines a few seconds before big moments. Only where the scan shows a real build-up."""
    out, last = [], -1e9
    for b in sorted((b for b in all_beats if b["salience"] >= CLIFF_MIN_SALIENCE and b["t"] >= CLIFF_AFTER
                     and not any(k.startswith("npc_") for k in b["kinds"])), key=lambda b: -b["salience"]):
        if len(out) >= CLIFF_MAX:
            break
        if any(abs(b["t"] - o["beat_t"]) < CLIFF_SPACING for o in out):
            continue
        cue = buildup_cue(scenes, b["t"])
        if not cue:
            continue
        lo, hi = b["t"] - CLIFF_LEAD[1], b["t"] - CLIFF_LEAD[0]
        for g in gaps:
            t = max(g["start"] + LEAD, lo)
            cap = min(g["end"] - t - TAIL, b["t"] - 1.0 - t)
            if t > hi or g["end"] <= lo or cap < MIN_USABLE_GAP:
                continue
            mw = min(10, gapmod.words_for(cap))
            if mw < 4:
                continue
            out.append({"id": f"c{len(out)}", "t": round(t, 2), "beat_t": b["t"], "salience": round(b["salience"] - 0.5, 2),
                        "kinds": ["cliffhanger"], "kind": "micro" if mw < 9 else "full",
                        "why": (f"SUSPENSE. Something is building: {cue}. One short, tense line that builds anticipation. "
                                "Do not say what happens next and never use phrases such as 'you won't believe'. "
                                "Use emotion awe, calm or none."),
                        "max_words": mw, "max_duration": round(cap, 2)})
            break
    return sorted(out, key=lambda o: o["t"])


# ------------------------------------------------------------------ chapters

def _label(n):
    act = n.get("activity")
    if n.get("is_cutscene") is True or act == "cutscene":
        return "Cutscene"
    if act in CHAPTER_TITLES:
        return CHAPTER_TITLES[act]
    if act in (None, "travel", "other"):
        riding = n.get("player_state") == "riding" or "horse" in (n.get("mount_or_vehicle") or "").lower()
        return "Riding" if riding else "Exploring on foot"
    return None          # menus and unknowns do not make a chapter


def chapters(scenes, length):
    """[{t, title}] from the activity runs, for the YouTube description. Empty when there are fewer than three real chapters
    (YouTube needs three), and the first always starts at 0:00."""
    frames = [(n["t"], _label(n)) for n in sorted(scenes, key=lambda n: n["t"])]
    frames = [(t, lab) for t, lab in frames if lab]
    if not frames:
        return []
    runs = []                                       # [start, title]
    for t, lab in frames:
        if not runs or runs[-1][1] != lab:
            runs.append([t, lab])
    sized = [(start, title, (runs[i + 1][0] if i + 1 < len(runs) else length) - start) for i, (start, title) in enumerate(runs)]
    keep = [r for i, r in enumerate(sized) if i == 0 or r[2] >= CHAPTER_MIN]      # a short blip stays inside the chapter before it
    if len(keep) > CHAPTER_MAX:                     # too many: the longest ones are the real chapters
        longest = {id(r) for r in sorted(keep[1:], key=lambda r: -r[2])[:CHAPTER_MAX - 1]}
        keep = [r for i, r in enumerate(keep) if i == 0 or id(r) in longest]
    out, prev = [], None
    for i, (start, title, _) in enumerate(keep):
        if title != prev:
            out.append({"t": 0.0 if i == 0 else round(start, 1), "title": title})
        prev = title
    return out if len(out) >= 3 else []


def chapters_text(chs):
    return "\n".join(f"{_mmss(c['t'])} {c['title']}" for c in chs)
