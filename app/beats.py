"""Which moments deserve a line? Score emotional weight, place reactions in real gaps, then pick
the strongest ones for the current eagerness. Pure functions, no network."""
import re

from . import gaps as gapmod
from .config import AFTER_CUT_MIN, DAYDREAM_SPACING, MIN_GAP_FLOOR, READ_LEAD, READ_WORDS_PER_SEC, REACTION_WINDOW, SILENT_MAX_WORDS, TAIL, TRAVEL_GAP_MIN

MUST_FIT_WORDS = 9    # the welcome and the outro always keep room for at least this many words
MIN_START_SPACING = MIN_GAP_FLOOR + 2.0   # room for the floor of silence plus a 3-word line

KIND_W = {"death": 10, "kill": 6, "damage_taken": 5, "damage_dealt": 4, "fall": 5, "crash": 6,
          "overtake": 5, "score": 6, "discovery": 5, "reward": 5, "loot": 3, "pickup": 2, "harvest": 2,
          "craft": 2, "build": 3, "animal_contact": 3, "npc_contact": 3, "door": 1, "other": 2}
MORAL_W = 4.0
ACT_W = {"shop": 3.5, "horse_care": 3.5, "animal_interaction": 4.0, "hunting": 4.5, "fishing": 3.0,
         "camp": 2.5, "mission": 5.0, "npc_talk": 3.0}
PREDATORS = ("wolf", "cougar", "panther", "bear", "alligator", "gator", "snake", "coyote", "lion")
CUE_WORDS = ("honor", "wanted", "bounty", "label", "prompt", "icon", "marker", "hud", "text", "banner")
TONE_W = {"praise": 5.0, "thanks": 4.5, "insult": 6.0, "threat": 7.0, "plea": 6.5}
MIN_BEAT = 2.5
PEAK_RADIUS = 6.0


def _words(text):
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def _new_place(prev, cur):
    a, b = prev.get("location"), cur.get("location")
    if not a or not b or "unknown" in (a.lower(), b.lower()):
        return False
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) < 0.25


def casualty_beat(c, moral_mode="both"):
    """(kind, weight, instruction) for a death in the frame, or None. Innocents and armed outlaws are told apart
    by the game's own cues first; with no cue, vision alone decides in 'both' mode and nothing is claimed in
    'cues' mode. When it is unclear, stay neutral."""
    if not c:
        return None
    who = (c.get("who") or "none").lower().replace(" ", "_")
    ev = (c.get("evidence") or "seen on screen").strip()
    if who in ("none", ""):
        return None
    cue = any(w in ev.lower() for w in CUE_WORDS)
    if moral_mode == "cues" and who in ("civilian", "bandit", "gang_member", "lawman") and not cue:
        who = "unknown"
    if who == "civilian":
        return ("casualty_innocent", 9.0, f"An innocent bystander was killed ({ev}). Arthur feels remorse: quiet, heavy, "
                                          "no excuses, no jokes.")
    if who in ("bandit", "gang_member", "lawman"):
        return ("justice_outlaw", 6.0, f"A {who.replace('_', ' ')} who was attacking has died ({ev}). Outlaw justice: "
                                       "grim, steady, matter of fact. No gloating and no remorse.")
    if who == "horse":
        return ("casualty_animal", 8.0, f"A horse died ({ev}). A real loss: short and heartfelt.")
    if who == "animal":
        return ("casualty_animal", 4.0, f"An animal was killed ({ev}). If it is hunting for food or pelts it is work, "
                                        "not cruelty; do not dramatise it.")
    return ("casualty_unclear", 5.0, f"Someone or something died ({ev}) but who it was is not clear. Stay factual; claim "
                                     "neither guilt nor justice.")


def scene_score(n, prev=None, kw=None, moral_mode="both"):
    """Emotional weight of one scanned moment, 0 to 10, with the kinds and reasons behind it."""
    kw = kw or {}
    s, kinds, why = 0.0, [], []
    for it in n.get("interactions") or []:
        k = it.get("kind") or "other"
        inten = it.get("intensity")
        inten = 1.0 if inten is None else max(0.0, min(3.0, float(inten)))
        s += KIND_W.get(k, 2.0) * kw.get(k, 1.0) * (0.6 + 0.2 * inten)
        kinds.append(k)
        why.append(f"{k}: {it.get('object') or 'something'} ({it.get('evidence') or 'seen on screen'})")
    for m in n.get("moral_events") or []:
        s += MORAL_W * kw.get("moral", 1.0)
        kinds.append("moral")
        why.append(f"choice: {m.get('kind')} {m.get('who') or ''} ({m.get('evidence') or ''})".strip())
    act = n.get("activity")
    if act in ACT_W:
        s += ACT_W[act] * kw.get(act, 1.0)
        kinds.append(act)
        why.append(act.replace("_", " "))
    sh = n.get("shop") or {}
    if (sh.get("action") or "none") not in ("none", ""):
        s += 1.5 * kw.get("shop", 1.0)
        kinds.append("shop")
        why.append(f"{sh['action']} {sh.get('item') or ''}".strip())
    for a in n.get("animals") or []:
        sp, beh = (a.get("species") or "animal").lower(), (a.get("behavior") or "").lower()
        predator = bool(a.get("predator")) or any(p in sp for p in PREDATORS)
        if predator and beh in ("approaching", "attacking"):
            s += (8.0 if beh == "attacking" else 7.0) * kw.get("predator", 1.0)
            kinds.append("predator")
            why.append(f"a {sp} is {beh}: danger first, so alert and short")
        elif predator:
            s += 4.5 * kw.get("predator", 1.0)
            kinds.append("predator")
            why.append(f"a {sp} is nearby: a predator")
        elif (a.get("size") or "") == "large":
            s += 3.5 * kw.get("big_animal", 1.0)
            kinds.append("big_animal")
            why.append(f"a large {sp} ({beh or 'in view'}): respect")
    cb = casualty_beat(n.get("casualty"), moral_mode)
    if cb:
        s += cb[1] * kw.get(cb[0], 1.0)
        kinds.append(cb[0])
        why.append(cb[2])
    if prev and _new_place(prev, n):
        s += 2.0 * kw.get("discovery", 1.0)
        kinds.append("new_place")
        why.append(f"arrived somewhere new: {n.get('location')}")
    s += min(1.2, 0.4 * len(n.get("notable_details") or []))
    if n.get("player_state") in ("aiming", "sprinting"):
        s += 0.8
    return min(10.0, round(s, 2)), list(dict.fromkeys(kinds)), why


def find_beats(scenes, kw=None, moral_mode="both"):
    """Local emotional peaks: moments scoring above the floor that beat their neighbours."""
    scored = []
    for i, n in enumerate(scenes):
        sc, kinds, why = scene_score(n, scenes[i - 1] if i else None, kw, moral_mode)
        scored.append((n["t"], sc, kinds, why))
    beats = []
    for t, sc, kinds, why in scored:
        if sc < MIN_BEAT:
            continue
        near = [x for x in scored if abs(x[0] - t) <= PEAK_RADIUS]
        top = max(x[1] for x in near)
        if sc == top and not any(b["t"] == t or (abs(b["t"] - t) <= PEAK_RADIUS and b["salience"] >= sc) for b in beats):
            beats.append({"t": t, "salience": sc, "kinds": kinds or ["detail"], "why": "; ".join(why) or "something worth noticing"})
    return beats


def opportunities(beats, gaps, length, eager=6.0, quiet_fn=None, travel_gap=None):
    """Turn beats into places a line can actually go, plus atmosphere spots in long silences."""
    opps, blocked = [], []
    for i, b in enumerate(beats):
        loc = gapmod.locate(gaps, b["t"], REACTION_WINDOW)
        if not loc:
            blocked.append(b)
            continue
        start, g = loc
        cap = g["end"] - start - TAIL
        mw = gapmod.words_for(cap)
        if mw < 3:
            blocked.append(b)
            continue
        opps.append({"id": f"b{i}", "t": round(start, 2), "beat_t": b["t"], "salience": b["salience"],
                     "kinds": b["kinds"], "why": b["why"], "max_words": mw, "max_duration": round(cap, 2),
                     "kind": "micro" if mw < 9 else "full"})
    step = max(10.0, min(45.0, 70.0 - 6.0 * eager))   # more eager: atmosphere spots come closer together
    tgap = max(TRAVEL_GAP_MIN, travel_gap or DAYDREAM_SPACING)
    for j, g in enumerate(gaps):
        span = g["end"] - g["start"]
        if span < (20 if eager < 8.5 else 8):          # at high eagerness even short gaps may host a line
            continue
        n = max(1, int(span // (tgap if quiet_fn is not None else step)))
        for k in range(n):
            pos = g["start"] + (k + 1) * span / (n + 1)
            cap = min(g["end"] - pos - TAIL, 12.0)
            mw = min(18, gapmod.words_for(cap))
            if mw < 5:
                continue
            if quiet_fn is None:     # older behaviour: generic atmosphere
                opps.append({"id": f"a{j}_{k}", "t": round(pos, 2), "beat_t": None,
                             "salience": round(3.0 + min(span / 25, 3.0), 2), "kinds": ["ambient"],
                             "why": "a quiet stretch: atmosphere, a thought, a small observation",
                             "max_words": mw, "max_duration": round(cap, 2), "kind": "ambient"})
            elif quiet_fn(pos):      # only quiet travel gets a line, and it is a daydream, never filler
                opps.append({"id": f"d{j}_{k}", "t": round(pos, 2), "beat_t": None,
                             "salience": round(4.2 + min(span / 50, 1.0), 2), "kinds": ["daydream"],
                             "why": "Quiet travel: nothing is happening and nobody is near. A wistful private thought "
                                    "about a quiet future. Keep it generic and never name a real character.",
                             "max_words": min(mw, 16), "max_duration": round(cap, 2), "kind": "daydream",
                             "spacing": tgap})
    opps.sort(key=lambda o: o["t"])
    return opps, blocked


def threshold(eager):
    return 8.2 - 0.62 * (eager - 1)


def base_gap(eager):
    return max(5.0, min(24.0, 26 - 2.2 * eager))


def _need(o, eager):
    """Stronger moments are allowed to follow each other more closely; daydreams never crowd each other."""
    need = max(MIN_START_SPACING, base_gap(eager) * (1 - 0.06 * o["salience"]))
    return max(need, o.get("spacing", DAYDREAM_SPACING)) if o.get("kind") == "daydream" else need


def select(opps, eager, length):
    """Strongest opportunities first, spaced by eagerness, then no long droughts. Returns
    (chosen sorted by time, skipped)."""
    thr = threshold(eager)
    chosen = []
    for o in sorted((o for o in opps if o["salience"] >= thr), key=lambda o: -o["salience"]):
        if all(abs(o["t"] - c["t"]) >= _need(o, eager) for c in chosen):
            chosen.append(o)
    max_silence = max(25.0, 150.0 - 12.0 * eager) if eager >= 3 else 1e9
    for _ in range(60):
        stops = [0.0] + sorted(c["t"] for c in chosen) + [length]
        gap_a, gap_b = max(zip(stops, stops[1:]), key=lambda p: p[1] - p[0])
        if gap_b - gap_a <= max_silence:
            break
        pool = [o for o in opps if o not in chosen and gap_a + 5 <= o["t"] <= gap_b - 5
                and all(abs(o["t"] - c["t"]) >= (o.get("spacing", DAYDREAM_SPACING) if o.get("kind") == "daydream" else MIN_START_SPACING)
                        for c in chosen)]
        if not pool:
            break
        chosen.append(max(pool, key=lambda o: o["salience"]))
    chosen.sort(key=lambda o: o["t"])
    skipped = [o for o in opps if o not in chosen]
    return _fit_to_neighbours(chosen), skipped


def _fit_to_neighbours(chosen):
    """Cap each line to the room before the next chosen line, so the silence floor between lines
    always holds and the writer is told the exact size it can use."""
    chosen = list(chosen)
    fitted = []
    i = 0
    while i < len(chosen):
        o = dict(chosen[i])
        if i + 1 < len(chosen):
            room = chosen[i + 1]["t"] - o["t"] - MIN_GAP_FLOOR
            need = o.get("must_fit") or (MUST_FIT_WORDS if o.get("must_include") else 0)
            if need and gapmod.words_for(room) < need and not (chosen[i + 1].get("must_include") or chosen[i + 1].get("must_fit")):
                chosen.pop(i + 1)       # the welcome and the outro outrank a neighbour that would squeeze them
                continue
            if room < o["max_duration"]:
                o["max_duration"] = round(room, 2)
                o["max_words"] = min(o["max_words"], gapmod.words_for(room))
                o["kind"] = "micro" if o["max_words"] < 9 and o["kind"] not in ("ambient", "daydream") else o["kind"]
        fitted.append(o)
        i += 1
    return [o for o in fitted if o["max_words"] >= 3]


# ---------------------------------------------------------------- silent thoughts (on screen only)

def _silent_words(room):
    return min(SILENT_MAX_WORDS, int((room - READ_LEAD) * READ_WORDS_PER_SEC))


def silent_opportunities(beats, windows, length, eager=6.0):
    """Places for unspoken on-screen thoughts: inside the speech that blocks the voice."""
    opps = []
    for i, b in enumerate(beats):
        w = next((w for w in windows if w[0] <= b["t"] <= w[1]), None)
        if not w:
            continue
        start = max(w[0] + 0.4, b["t"] + 0.3)
        room = w[1] - start - 0.2
        mw = _silent_words(room)
        if room >= 2.0 and mw >= 3:
            opps.append({"id": f"s{i}", "t": round(start, 2), "beat_t": b["t"], "salience": b["salience"],
                         "kinds": b["kinds"], "why": b["why"] + " (during dialogue)", "max_words": mw,
                         "max_duration": round(room, 2), "kind": "silent"})
    step = max(6.0, min(34.0, 46.0 - 3.2 * eager))
    for j, w in enumerate(windows):
        span = w[1] - w[0]
        if span < 6:
            continue
        n = max(1, int(span // step))
        for k in range(n):
            pos = w[0] + (k + 1) * span / (n + 1)
            room = min(w[1] - pos - 0.2, 7.0)
            mw = _silent_words(room)
            if mw >= 3:
                opps.append({"id": f"sa{j}_{k}", "t": round(pos, 2), "beat_t": None,
                             "salience": round(2.8 + min(span / 20, 2.5), 2), "kinds": ["ambient"],
                             "why": "someone is talking: an unspoken thought about what is being said or where we are",
                             "max_words": mw, "max_duration": round(room, 2), "kind": "silent"})
    opps.sort(key=lambda o: o["t"])
    return opps


SILENT_MIN_SPACING = 3.5


def select_silent(opps, eager):
    thr = threshold(eager)
    chosen = []
    for o in sorted((o for o in opps if o["salience"] >= thr), key=lambda o: -o["salience"]):
        need = max(SILENT_MIN_SPACING, base_gap(eager) * 0.7 * (1 - 0.06 * o["salience"]))
        if all(abs(o["t"] - c["t"]) >= need for c in chosen):
            chosen.append(o)
    chosen.sort(key=lambda o: o["t"])
    fitted = []
    for i, o in enumerate(chosen):
        o = dict(o)
        if i + 1 < len(chosen):
            room = chosen[i + 1]["t"] - o["t"] - 0.5
            if room < o["max_duration"]:
                o["max_duration"] = round(room, 2)
                o["max_words"] = min(o["max_words"], _silent_words(room))
        fitted.append(o)
    return [o for o in fitted if o["max_words"] >= 3]


def plan(voiced, silent, eager, length, target_lpm=None):
    """The chosen opportunities, spoken and silent, sorted by time. With a target (lines a minute) the
    brain gets as eager as needed to reach it, and trims the weakest if it overshoots."""
    if not target_lpm:
        return sorted(select(voiced, eager, length)[0] + select_silent(silent, eager), key=lambda o: o["t"])
    want = max(1, round(target_lpm * length / 60.0))
    chosen = []
    for e in [x * 0.5 for x in range(12, 31)]:
        chosen = select(voiced, e, length)[0] + select_silent(silent, e)
        if len(chosen) >= want:
            break
    while len(chosen) > want:
        chosen.remove(min(chosen, key=lambda o: o["salience"]))
    return sorted(chosen, key=lambda o: o["t"])


def after_cutscene(cuts, gaps, segments, length=None):
    """One short reaction in the first gap after a real cutscene ends, drawn only from what was said."""
    out = []
    for i, (a, b) in enumerate(cuts):
        if b - a < AFTER_CUT_MIN:
            continue
        loc = gapmod.locate(gaps, b + 0.2, 8.0)
        if not loc:
            continue
        start, g = loc
        cap = g["end"] - start - TAIL
        mw = min(9, gapmod.words_for(cap))
        if mw < 3:
            continue
        said = [s["text"].strip() for s in segments
                if a <= s["start"] <= b and (s.get("text") or "").strip() and not s["text"].strip().startswith("[")][-3:]
        out.append({"id": f"c{i}", "t": round(start, 2), "beat_t": None, "salience": 7.5, "kinds": ["cutscene_end"],
                    "why": f"A cutscene of {b - a:.0f} seconds has just ended. React in a few words to what it meant, "
                           f"using only what was said: " + (" / ".join(said) if said else "(nothing was transcribed)"),
                    "max_words": mw, "max_duration": round(cap, 2), "kind": "micro"})
    return out


def speech_beats(segments, tones, kw=None):
    """Beats from what other people say TO the player: praise, thanks, insults, threats and pleas."""
    kw = kw or {}
    out = []
    for t in tones or []:
        i = t.get("i")
        if not isinstance(i, int) or not 0 <= i < len(segments) or not t.get("to_player"):
            continue
        tone = (t.get("tone") or "neutral").lower()
        if tone not in TONE_W:
            continue
        seg = segments[i]
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        kind = f"npc_{tone}"
        out.append({"t": seg["end"], "salience": round(min(10.0, TONE_W[tone] * kw.get(kind, 1.0)), 2), "kinds": [kind],
                    "why": f"Someone said this to Arthur ({tone}): \"{text}\". React to how it lands, in your own words."})
    out.sort(key=lambda b: b["t"])
    merged = []
    for b in out:   # several lines close together are one moment
        if merged and b["t"] - merged[-1]["t"] < 4.0:
            if b["salience"] > merged[-1]["salience"]:
                merged[-1] = b
        else:
            merged.append(b)
    return merged


def travel_quiet_fn(scenes):
    """Returns f(t): True when Arthur is just walking or riding, with nothing and nobody to react to."""
    def travelling(n):
        calm = not (n.get("interactions") or n.get("moral_events") or n.get("animals")
                    or (n.get("casualty") or {}).get("who") not in (None, "", "none"))
        act = n.get("activity")
        if act:
            return act == "travel" and calm
        return n.get("player_state") in ("walking", "riding", "sprinting") and calm   # scans from before activity existed
    marks = [(n["t"], travelling(n)) for n in scenes]

    def quiet(t):
        near = [ok for tt, ok in marks if abs(tt - t) <= 4.6]
        return bool(near) and all(near)
    return quiet
