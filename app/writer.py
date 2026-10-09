import json

from . import guard, knowledge, styles
from .config import MIN_GAP_FLOOR, PROMPTS, WINDOW_SECONDS
from .llm import claude, parse_json

PERSONA_RULE = {
    "mixed": "Choose the persona per line and favour variety. The same persona may not speak more than twice in a row.",
    "player": "Use ONLY the [player] persona.",
    "character": "Use ONLY the [character] persona.",
    "companion": "Use ONLY the [companion] persona.",
}


def build_system(style, opts):
    tpl = (PROMPTS / "master.md").read_text()
    pack_text, personality_text = styles.pack_prompt(opts.get("pack"), opts.get("personality"))
    return (tpl.replace("{GUARDRAILS}", (PROMPTS / "guardrails.md").read_text())
            .replace("{GENRE_PACK}", pack_text)
            .replace("{PERSONALITY}", personality_text)
            .replace("{KNOWLEDGE}", knowledge.for_game(opts.get("game")) if opts.get("knowledge", True) else "")
            .replace("{STYLE}", style)
            .replace("{PERSONA_MODE_RULE}", PERSONA_RULE.get(opts["persona"], PERSONA_RULE["mixed"]))
            .replace("{GAME}", opts["game"])
            .replace("{CHARACTER}", opts.get("character") or "the protagonist")
            .replace("{STORY_POINT}", opts.get("story_point") or "unspecified, avoid spoilers")
            .replace("{VOICE_NOTES}", opts.get("voice_notes") or "natural"))


def _earlier_events(scenes, lo, limit=25):
    ev = [{"t": s["t"], "interactions": s.get("interactions"), "moral_events": s.get("moral_events")}
          for s in scenes if s["t"] < lo and (s.get("interactions") or s.get("moral_events"))]
    return ev[-limit:]


def _notes_for(scenes, opps):
    """Scene notes from just before each opportunity up to it, so the model sees what led there."""
    keep = {}
    for o in opps:
        end = o["t"]
        begin = (o["beat_t"] if o["beat_t"] is not None else o["t"]) - 9
        for n in scenes:
            if begin <= n["t"] <= end:
                keep[n["t"]] = n
    return [keep[t] for t in sorted(keep)]


def _lines_from(raw, by_id):
    """Turn the model's answers into timed lines. Times and room come from the plan, not the model."""
    out = []
    for r in raw if isinstance(raw, list) else []:
        o = by_id.get(r.get("opportunity"))
        if not o or not (r.get("line") or "").strip():
            continue
        ln = {**r, "start": o["t"], "max_duration": o["max_duration"], "max_words": o["max_words"],
              "opp_id": o["id"], "beat_kinds": ",".join(o["kinds"]), "salience": o["salience"],
              "silent": o["kind"] == "silent", "daydream": o["kind"] == "daydream"}
        if ln.get("trigger_t") is None and o["beat_t"] is not None:
            ln["trigger_t"] = o["beat_t"]
        out.append(ln)
    return out


def _ask(system, opps, scenes, segments, windows, kept, profile, lo, hi, note="", chunk=None):
    by_id = {o["id"]: o for o in opps}
    user = {
        "window": [lo, hi],
        "opportunities": [{"id": o["id"], "at_seconds": o["t"], "max_words": o["max_words"], "kind": o["kind"],
                           "salience": o["salience"], "why": o["why"]} for o in opps],
        "scene_notes": _notes_for(scenes, opps),
        "speech_segments": [s for s in segments if s["end"] > lo and s["start"] < hi],
        "locked_windows": [w for w in windows if w[1] > lo and w[0] < hi],
        "earlier_lines": [k["line"] for k in kept[-12:]],
        "already_said": {"openers": [" ".join(k["line"].split()[:4]) for k in kept[-40:]],
                         "note": "Do not start a line the way any of these start, and do not repeat an idea already said."},
        "earlier_events": _earlier_events(scenes, lo),
        "player_profile": profile or [],
    }
    if chunk:
        user["chunk"] = chunk
    if note:
        user["note"] = note
    return _lines_from(parse_json(claude(system, json.dumps(user))), by_id)


def _shorten(system, dropped, opps_by_id):
    """A line that was only too long is not lost: ask for the same thought in fewer words."""
    items = [{"opportunity": d["opp_id"], "max_words": d["max_words"], "line": d["line"]}
             for d in dropped if d.get("reason") == "too long" and d.get("opp_id") in opps_by_id]
    if not items:
        return []
    try:
        raw = parse_json(claude(system, "Each line below is over its word limit. Rewrite each as the same thought in at most "
                                        "max_words words. Return JSON [{\"opportunity\": id, \"line\": text}] only.\n"
                                        + json.dumps(items), 1500))
    except Exception:
        return []
    by_old = {d["opp_id"]: d for d in dropped if d.get("opp_id")}
    out = []
    for r in raw if isinstance(raw, list) else []:
        old = by_old.get(r.get("opportunity"))
        if old and (r.get("line") or "").strip():
            out.append({k: v for k, v in old.items() if k != "reason"} | {"line": r["line"].strip()})
    return out


def write_lines(scenes, segments, windows, system, video_len, opps, profile=None, progress=None, blocks=None):
    """One line per chosen opportunity at most. Returns (kept, dropped)."""
    total_windows = max(1, int(-(-video_len // WINDOW_SECONDS)))
    kept, dropped = [], []
    lo = 0.0
    while lo < video_len:
        hi = lo + WINDOW_SECONDS
        sel = [o for o in opps if lo <= o["t"] < hi]
        if sel:
            idx = int(lo // WINDOW_SECONDS)
            chunk = {"index": idx, "of": total_windows, "first": idx == 0,
                     "note": "This is one continuous video cut into parts only for you. Continue mid-thought. Never greet, "
                             "introduce yourself or restart; the only greeting is an opportunity with id intro."}
            new = _ask(system, sel, scenes, segments, windows, kept, profile, lo, min(hi, video_len), chunk=chunk)
            k, d = guard.enforce(kept[-1:] + new if kept else new, windows, scenes, min_gap=MIN_GAP_FLOOR, blocks=blocks)
            fixed = _shorten(system, d, {o["id"]: o for o in sel})
            if fixed:
                k, d2 = guard.enforce(k + fixed, windows, scenes, min_gap=MIN_GAP_FLOOR, blocks=blocks)
                d = [x for x in d if x.get("reason") != "too long"] + d2
            fresh, repeats = guard.dedupe([x for x in k if x["start"] >= lo], kept)
            kept += fresh
            dropped += [x for x in d if x["start"] >= lo] + repeats
        lo = hi
        if progress:
            progress(min(int(lo // WINDOW_SECONDS), total_windows), total_windows)
    return kept, dropped


def fill_missed(scenes, segments, windows, system, kept, opps, profile=None, min_salience=0.0, limit=12, blocks=None):
    """Self-check: strong moments the first pass left without a line get one more chance."""
    done = {k.get("opp_id") for k in kept}
    missed = sorted((o for o in opps if o["id"] not in done and o["salience"] >= min_salience),
                    key=lambda o: -o["salience"])[:limit]
    if not missed:
        return kept, 0
    new = _ask(system, sorted(missed, key=lambda o: o["t"]), scenes, segments, windows, kept, profile,
               missed[0]["t"] - 1, max(o["t"] for o in missed) + 1,
               note="These planned moments were left without a line. Write one short line for each of them now. Do not skip any.")
    merged, _ = guard.enforce(kept + new, windows, scenes, min_gap=MIN_GAP_FLOOR, blocks=blocks)
    have = {k.get("opp_id") for k in kept}
    fresh, _ = guard.dedupe([m for m in merged if m.get("opp_id") not in have], kept)
    return sorted(kept + fresh, key=lambda x: x["start"]), len(fresh)
