import json

from . import guard
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
    return (tpl.replace("{GUARDRAILS}", (PROMPTS / "guardrails.md").read_text())
            .replace("{STYLE}", style)
            .replace("{PERSONA_MODE_RULE}", PERSONA_RULE.get(opts["persona"], PERSONA_RULE["mixed"]))
            .replace("{GAME}", opts["game"])
            .replace("{CHARACTER}", opts.get("character") or "the protagonist")
            .replace("{STORY_POINT}", opts.get("story_point") or "unspecified, avoid spoilers")
            .replace("{VOICE_NOTES}", opts.get("voice_notes") or "natural"))


def _relevant(scenes, lo, hi):
    """Quiet moments and interaction events only, to keep the prompt small."""
    return [s for s in scenes if lo <= s["t"] < hi and (s.get("is_quiet_moment") or s.get("interactions"))]


def write_lines(scenes, segments, windows, system, video_len):
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
            })
            raw = parse_json(claude(system, user))
            kept, dropped = guard.enforce(all_kept[-1:] + raw, windows, scenes) if all_kept else guard.enforce(raw, windows, scenes)
            # keep only this window's new lines (the carried line is already in all_kept)
            new = [k for k in kept if k["start"] >= lo]
            all_kept += new
            all_dropped += [d for d in dropped if d["start"] >= lo]
        lo = hi
    return all_kept, all_dropped
