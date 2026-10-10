"""The brain report: a short, plain account of what the brain saw and why it spoke or stayed silent. Pure functions."""
BUSY_STEP = 2.5        # frames this close together were scanned in a busy moment
LONG_QUIET = 45.0      # a stretch without any line this long is explained
TOP_MOMENTS = 10
REACTION = 12.0


def _covered(ranges, a, b):
    """Share of [a, b] inside the given ranges."""
    if b <= a:
        return 0.0
    total = sum(max(0.0, min(b, y) - max(a, x)) for x, y in ranges)
    return min(1.0, total / (b - a))


def seen(notes):
    ts = sorted(n["t"] for n in notes)
    busy = sum(1 for a, b in zip(ts, ts[1:]) if b - a <= BUSY_STEP)
    acts = {}
    for n in notes:
        a = n.get("activity") or "unknown"
        acts[a] = acts.get(a, 0) + 1
    paces = {}
    for n in notes:
        p = n.get("pace")
        if p and p not in ("none", "unknown"):
            paces[p] = paces.get(p, 0) + 1
    return {"frames": len(ts), "busy_frames": busy, "calm_frames": max(0, len(ts) - 1 - busy),
            "activities": dict(sorted(acts.items(), key=lambda kv: -kv[1])[:6]), "paces": paces}


def moments(all_beats, lines, dropped, blocked):
    blocked_t = {b["t"] for b in blocked}
    out = []
    for b in sorted(all_beats, key=lambda b: -b["salience"])[:TOP_MOMENTS]:
        said = next((l for l in sorted(lines, key=lambda l: l["start"])
                     if 0 <= l["start"] - b["t"] <= REACTION and not l.get("dropped")), None)
        if said:
            result = ("thought on screen: " if said.get("silent") else "said: ") + said["line"]
        elif b["t"] in blocked_t:
            result = "stayed silent: someone was speaking"
        else:
            d = next((d for d in dropped if 0 <= d.get("start", -99) - b["t"] <= REACTION and d.get("reason")), None)
            result = f"line removed: {d['reason']}" if d else "skipped: not strong enough for the amount chosen"
        out.append({"t": round(b["t"], 1), "what": b["why"], "result": result})
    return sorted(out, key=lambda m: m["t"])


def quiet_stretches(lines, length, speech, cuts):
    stops = [0.0] + sorted(l["start"] for l in lines if not l.get("dropped")) + [length]
    out = []
    for a, b in zip(stops, stops[1:]):
        if b - a < LONG_QUIET:
            continue
        if _covered(cuts, a, b) >= 0.5:
            why = "a cutscene, so nothing is said"
        elif _covered(speech, a, b) >= 0.5:
            why = "people were talking"
        else:
            why = "nothing worth reacting to"
        out.append({"from": round(a, 1), "to": round(b, 1), "why": why})
    return out


def build(notes, lines, dropped, all_beats, blocked, length, speech, cuts, travel_kinds):
    kept = [l for l in lines if not l.get("dropped")]
    return {"seen": seen(notes),
            "said": {"voiced": sum(1 for l in kept if not l.get("silent")), "silent": sum(1 for l in kept if l.get("silent")),
                     "travel": travel_kinds},
            "moments": moments(all_beats, kept, dropped, blocked),
            "quiet": quiet_stretches(kept, length, speech, cuts),
            "removed": len(dropped)}
