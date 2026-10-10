"""Travel thoughts: what a person thinks while just walking or riding. Five kinds, rotated so no two in a row match.

  notice   something actually seen nearby (a landmark, an animal, the weather)
  mood     how the trip feels, worked out by code from the hour, the weather, how long it has been and what just happened
  horse    a short warm thought about the horse, only while riding
  memory   something that happened earlier in this same video
  wistful  a generic quiet daydream about a different life (never a real character)

The facts come only from the scan, so a thought can never invent one. Pure functions, no network."""
from .channel import THEMES

ORDER = ["notice", "mood", "horse", "memory", "wistful"]
NEAR = 7.0            # a notice or horse fact must be within this many seconds of the thought
RECENT = 90.0         # events this recent colour the mood
MEMORY_MIN_AGE = 45.0


def _near(scenes, t, radius):
    return [n for n in scenes if abs(n["t"] - t) <= radius]


def _clean(x):
    x = x.strip() if isinstance(x, str) else ""
    return "" if x.lower() in ("", "none", "unknown", "n/a") else x


def mood_for(scenes, t):
    """A mood and the reasons for it, from the scan alone."""
    here = _near(scenes, t, NEAR) or [n for n in scenes if n["t"] <= t][-1:]
    sky = " ".join(_clean(n.get("time_weather")).lower() for n in here)
    reasons, mood = [], "settled"
    if any(w in sky for w in ("night", "dusk")):
        mood, reasons = "uneasy and watchful", reasons + ["it is " + ("night" if "night" in sky else "dusk")]
    elif any(w in sky for w in ("fog", "rain", "storm", "snow")):
        mood, reasons = "quiet and a little low", reasons + ["the weather is heavy"]
    elif "dawn" in sky:
        mood, reasons = "hopeful", reasons + ["it is early morning"]
    elif sky:
        mood, reasons = "easy and open", reasons + ["it is a clear day"]
    run = 0.0
    for n in reversed([s for s in scenes if s["t"] <= t]):
        if n.get("activity") not in (None, "travel") or n.get("interactions") or n.get("moral_events"):
            break
        run = t - n["t"]
    if run >= 240:
        mood, reasons = "weary", reasons + [f"about {int(run // 60)} minutes of travelling without a stop"]
    recent = [n for n in scenes if t - RECENT <= n["t"] < t]
    hurt = any(i.get("kind") in ("damage_taken", "fall", "crash") for n in recent for i in n.get("interactions") or [])
    won = any(i.get("kind") in ("reward", "score", "discovery", "kill") and i.get("outcome") != "negative"
              for n in recent for i in n.get("interactions") or [])
    if hurt:
        mood, reasons = "sore and careful", reasons + ["he took a knock a little while ago"]
    elif won:
        mood, reasons = "lifted", reasons + ["something went well a little while ago"]
    return mood, reasons


def notice_facts(scenes, t):
    facts = []
    for n in _near(scenes, t, NEAR):
        v = _clean(n.get("landmark"))
        if v:
            facts.append(v)
        for d in n.get("notable_details") or []:
            if _clean(d):
                facts.append(d.strip())
        for a in n.get("animals") or []:
            if a.get("behavior") in ("grazing", "calm", "fleeing") and _clean(a.get("species")):
                facts.append(f"a {a['species']} ({a['behavior']})")
    seen, out = set(), []
    for f in facts:
        if f.lower() not in seen:
            seen.add(f.lower())
            out.append(f)
    return out[:3]


def horse_facts(scenes, t):
    out = []
    for n in _near(scenes, t, NEAR):
        riding = n.get("player_state") == "riding" or "horse" in (n.get("mount_or_vehicle") or "").lower() \
            or n.get("activity") == "horse_care"
        if not riding:
            continue
        h = n.get("horse") or {}
        bits = [_clean(n.get("mount_or_vehicle")), _clean(n.get("pace")), _clean(h.get("state")),
                "stamina low" if h.get("stamina") == "low" else ""]
        out += [b for b in bits if b]
    seen, res = set(), []
    for b in out:
        if b.lower() not in seen:
            seen.add(b.lower())
            res.append(b)
    return res[:4]


def memory_event(scenes, t, used):
    """The strongest earlier event not yet used: (time, words) or None."""
    best = None
    for n in scenes:
        if n["t"] > t - MEMORY_MIN_AGE or n["t"] in used:
            continue
        for i in n.get("interactions") or []:
            score = (i.get("intensity") or 0) + (2 if i.get("kind") in ("kill", "reward", "discovery", "death", "fall") else 0)
            if score >= 2 and (best is None or score > best[0]):
                best = (score, n["t"], f"{i.get('kind')}: {_clean(i.get('object')) or 'something'} ({i.get('outcome', 'neutral')})")
        for m in n.get("moral_events") or []:
            if best is None or 4 > best[0]:
                best = (4, n["t"], f"a moral moment: {m.get('kind')} ({_clean(m.get('who')) or 'someone'})")
    return (best[1], best[2]) if best else None


def assign(chosen, scenes):
    """Give every quiet-travel thought a kind and the real facts behind it. Rewrites the opportunity's why."""
    spots = sorted((o for o in chosen if o.get("kind") == "daydream"), key=lambda o: o["t"])
    last, theme_n, used_mem, counts = None, 0, set(), {}
    for o in spots:
        t = o["t"]
        facts, horse = notice_facts(scenes, t), horse_facts(scenes, t)
        mem = memory_event(scenes, t, used_mem)
        ready = {"notice": bool(facts), "mood": True, "horse": bool(horse), "memory": bool(mem), "wistful": True}
        start = (ORDER.index(last) + 1) % len(ORDER) if last else 0
        kind = next(ORDER[(start + k) % len(ORDER)] for k in range(len(ORDER)) if ready[ORDER[(start + k) % len(ORDER)]])
        if kind == "notice":
            why = ("Quiet travel. Notice something that is really there and let it become a small private thought. "
                   "What the scan saw: " + "; ".join(facts) + ". Mention only these.")
        elif kind == "mood":
            mood, why_m = mood_for(scenes, t)
            why = (f"Quiet travel. Let the way the trip feels show: he feels {mood}" + (f" ({'; '.join(why_m)})" if why_m else "")
                   + ". A short private thought in that mood, with no invented cause.")
        elif kind == "horse":
            why = ("Quiet travel on horseback. A short warm thought about the horse. What the scan saw: " + "; ".join(horse)
                   + ". Do not invent a name, a breed or a trait.")
        elif kind == "memory":
            used_mem.add(mem[0])
            o["memory_t"] = mem[0]
            why = (f"Quiet travel. His mind drifts back to something earlier in this same video: {mem[1]}. "
                   f"One short private thought about it, and put {mem[0]} in callback_t.")
        else:
            why = ("Quiet travel: nothing is happening and nobody is near. A wistful private thought about a quiet future. "
                   f"Keep it generic and never name a real character. Theme for this one: {THEMES[theme_n % len(THEMES)]}.")
            theme_n += 1
        o["why"], o["travel"] = why, kind
        o["kinds"] = ["daydream", f"travel_{kind}"]
        counts[kind] = counts.get(kind, 0) + 1
        last = kind
    return counts
