"""Which moments deserve a line? Score emotional weight, place reactions in real gaps, then pick
the strongest ones for the current eagerness. Pure functions, no network."""
import re

from . import gaps as gapmod
from .config import MIN_GAP_FLOOR, REACTION_WINDOW, TAIL

MIN_START_SPACING = MIN_GAP_FLOOR + 2.0   # room for the floor of silence plus a 3-word line

KIND_W = {"death": 10, "kill": 6, "damage_taken": 5, "damage_dealt": 4, "fall": 5, "crash": 6,
          "overtake": 5, "score": 6, "discovery": 5, "reward": 5, "loot": 3, "pickup": 2, "harvest": 2,
          "craft": 2, "build": 3, "animal_contact": 3, "npc_contact": 3, "door": 1, "other": 2}
MORAL_W = 4.0
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


def scene_score(n, prev=None, kw=None):
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
    if prev and _new_place(prev, n):
        s += 2.0 * kw.get("discovery", 1.0)
        kinds.append("new_place")
        why.append(f"arrived somewhere new: {n.get('location')}")
    s += min(1.2, 0.4 * len(n.get("notable_details") or []))
    if n.get("player_state") in ("aiming", "sprinting"):
        s += 0.8
    return min(10.0, round(s, 2)), kinds, why


def find_beats(scenes, kw=None):
    """Local emotional peaks: moments scoring above the floor that beat their neighbours."""
    scored = []
    for i, n in enumerate(scenes):
        sc, kinds, why = scene_score(n, scenes[i - 1] if i else None, kw)
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


def opportunities(beats, gaps, length, eager=6.0):
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
    step = max(14.0, min(45.0, 70.0 - 6.0 * eager))   # more eager: atmosphere spots come closer together
    for j, g in enumerate(gaps):
        span = g["end"] - g["start"]
        if span < (20 if eager < 8.5 else 8):          # at high eagerness even short gaps may host a line
            continue
        n = max(1, int(span // step))
        for k in range(n):
            pos = g["start"] + (k + 1) * span / (n + 1)
            cap = min(g["end"] - pos - TAIL, 12.0)
            mw = min(18, gapmod.words_for(cap))
            if mw >= 5:
                opps.append({"id": f"a{j}_{k}", "t": round(pos, 2), "beat_t": None,
                             "salience": round(3.0 + min(span / 25, 3.0), 2), "kinds": ["ambient"],
                             "why": "a quiet stretch: atmosphere, a thought, a small observation",
                             "max_words": mw, "max_duration": round(cap, 2), "kind": "ambient"})
    opps.sort(key=lambda o: o["t"])
    return opps, blocked


def threshold(eager):
    return 8.2 - 0.62 * (eager - 1)


def base_gap(eager):
    return max(5.0, min(24.0, 26 - 2.2 * eager))


def _need(o, eager):
    """Stronger moments are allowed to follow each other more closely."""
    return max(MIN_START_SPACING, base_gap(eager) * (1 - 0.06 * o["salience"]))


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
                and all(abs(o["t"] - c["t"]) >= MIN_START_SPACING for c in chosen)]
        if not pool:
            break
        chosen.append(max(pool, key=lambda o: o["salience"]))
    chosen.sort(key=lambda o: o["t"])
    skipped = [o for o in opps if o not in chosen]
    return _fit_to_neighbours(chosen), skipped


def _fit_to_neighbours(chosen):
    """Cap each line to the room before the next chosen line, so the silence floor between lines
    always holds and the writer is told the exact size it can use."""
    fitted = []
    for i, o in enumerate(chosen):
        o = dict(o)
        if i + 1 < len(chosen):
            room = chosen[i + 1]["t"] - o["t"] - MIN_GAP_FLOOR
            if room < o["max_duration"]:
                o["max_duration"] = round(room, 2)
                o["max_words"] = min(o["max_words"], gapmod.words_for(room))
                o["kind"] = "micro" if o["max_words"] < 9 and o["kind"] != "ambient" else o["kind"]
        fitted.append(o)
    return [o for o in fitted if o["max_words"] >= 3]
