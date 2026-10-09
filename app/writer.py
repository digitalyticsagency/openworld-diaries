import json

from . import guard, styles
from .config import PROMPTS, WINDOW_SECONDS
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
            .replace("{STYLE}", style)
            .replace("{PERSONA_MODE_RULE}", PERSONA_RULE.get(opts["persona"], PERSONA_RULE["mixed"]))
            .replace("{GAME}", opts["game"])
            .replace("{CHARACTER}", opts.get("character") or "the protagonist")
            .replace("{STORY_POINT}", opts.get("story_point") or "unspecified, avoid spoilers")
            .replace("{VOICE_NOTES}", opts.get("voice_notes") or "natural"))


def _relevant(scenes, lo, hi):
    """Quiet moments and interaction events only, to keep the prompt small."""
    return [s for s in scenes if lo <= s["t"] < hi and (s.get("is_quiet_moment") or s.get("interactions"))]


def _earlier_events(scenes, lo, limit=25):
    ev = [{"t": s["t"], "interactions": s.get("interactions"), "moral_events": s.get("moral_events")}
          for s in scenes if s["t"] < lo and (s.get("interactions") or s.get("moral_events"))]
    return ev[-limit:]


def write_lines(scenes, segments, windows, system, video_len, amount=5, profile=None, progress=None):
    gap, lpm = guard.amount_profile(amount)
    total_windows = max(1, int(-(-video_len // WINDOW_SECONDS)))
    all_kept, all_dropped = [], []
    lo = 0.0
    while lo < video_len:
        hi = lo + WINDOW_SECONDS
        sc = _relevant(scenes, lo, hi)
        if sc:
            seg = [s for s in segments if s["end"] > lo and s["start"] < hi]
            win = [w for w in windows if w[1] > lo and w[0] < hi]
            user = json.dumps({
                "window": [lo, min(hi, video_len)],
                "scene_notes": sc,
                "speech_segments": seg,
                "locked_windows": win,
                "earlier_lines": [k["line"] for k in all_kept[-8:]],
                "earlier_events": _earlier_events(scenes, lo),
                "commentary": {"min_gap_seconds": gap, "target_lines_per_minute": lpm},
                "player_profile": profile or [],
            })
            raw = parse_json(claude(system, user))
            kept, dropped = guard.enforce(all_kept[-1:] + raw, windows, scenes, min_gap=gap) if all_kept else guard.enforce(raw, windows, scenes, min_gap=gap)
            # keep only this window's new lines (the carried line is already in all_kept)
            new = [k for k in kept if k["start"] >= lo]
            all_kept += new
            all_dropped += [d for d in dropped if d["start"] >= lo]
        lo = hi
        if progress:
            progress(min(int(lo // WINDOW_SECONDS), total_windows), total_windows)
    return all_kept, all_dropped
