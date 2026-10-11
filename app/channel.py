"""The channel voice: one welcome at the start, one like-and-subscribe near the end, a few spoken catchphrases,
and the rotating daydream themes. All of it is placed by code, once, so it can never repeat across chunks."""
import random

from . import gaps as gapmod
from .config import LEAD, TAIL

THEMES = ["a small farm of his own", "a family someday", "waking up beside someone he loves", "a quiet porch at dusk",
          "teaching a child to ride", "a life without running", "someone waiting by a lit window", "a garden and a dog by the fire",
          "a morning with nowhere to be", "being someone's reason to come home"]
DEFAULTS = {
    "intro_text": "Welcome to the channel, folks. Let's see where the trail takes us.",
    "outro_text": "Thanks for riding along. Like and subscribe, and I'll see you on the next trail.",
    "catchphrases": "Let's get into it\nStay tuned\nHere we go\nLet's see how this plays out\nStill with me?",
}


def settings(get):
    """Channel settings from the app's saved settings, with sensible defaults."""
    def on(key):
        return get(key, "1") == "1"
    phrases = [p.strip() for p in (get("catchphrases") or DEFAULTS["catchphrases"]).splitlines() if p.strip()]
    return {"intro_on": on("intro_on"), "outro_on": on("outro_on"), "catch_on": on("catch_on"),
            "hook_on": on("hook_on"), "cliff_on": on("cliff_on"),
            "channel": (get("channel_name") or "").strip(),
            "intro_text": (get("intro_text") or DEFAULTS["intro_text"]).strip(),
            "outro_text": (get("outro_text") or DEFAULTS["outro_text"]).strip(), "phrases": phrases}


def _spot(g, t, want_words=18):
    cap = g["end"] - t - TAIL
    return cap, min(22, gapmod.words_for(cap))


def intro_opportunity(gaps, length, cfg, after=0.0):
    """The first gap that can hold a welcome, after the cold open if there is one."""
    for g in gaps:
        if g["start"] > length * 0.5:
            break
        t = max(g["start"] + LEAD, 1.0, after)
        cap, mw = _spot(g, t)
        if mw >= 8:
            name = cfg["channel"] or "the channel"
            return {"id": "intro", "t": round(t, 2), "beat_t": None, "salience": 99.0, "kinds": ["intro"], "kind": "full",
                    "why": f"INTRO. Welcome the viewers to {name} in your own words, warmly, in a sentence or two. You must use "
                           "the words 'welcome' and 'channel'. This is the only greeting in the whole video.",
                    "max_words": mw, "max_duration": round(cap, 2), "must_include": ["welcome", "channel"]}
    return None


def outro_opportunity(gaps, length, cfg, intro=None):
    """The last gap that can hold a thank-you and a like-and-subscribe."""
    for g in reversed(gaps):
        if g["start"] < length * 0.5:
            break
        t = max(g["start"] + LEAD, g["end"] - 9.0)
        cap, mw = _spot(g, t)
        if mw >= 8 and (not intro or t - intro["t"] > 30):
            return {"id": "outro", "t": round(t, 2), "beat_t": None, "salience": 98.0, "kinds": ["outro"], "kind": "full",
                    "why": "OUTRO. Thank the viewers and ask them to like and subscribe, in your own words. You must use the "
                           "words 'like' and 'subscribe'. This is the only time you ask for that.",
                    "max_words": mw, "max_duration": round(cap, 2), "must_include": ["like", "subscribe"]}
    return None


def assign_themes(chosen):
    """Daydreams rotate through different themes so no two in a row are the same."""
    n = 0
    for o in sorted(chosen, key=lambda o: o["t"]):
        if o.get("kind") == "daydream":
            o["why"] += f" Theme for this one: {THEMES[n % len(THEMES)]}."
            n += 1
    return chosen


def assign_catchphrases(chosen, phrases, length, seed=0):
    """A few strong moments get a spoken catchphrase to work in. Each phrase is used at most once."""
    if not phrases:
        return 0
    phrases = list(phrases)
    random.Random(seed).shuffle(phrases)
    want = min(len(phrases), max(1, int(length / 240)))
    used = []
    for o in sorted((o for o in chosen if o.get("kind") in ("full", "micro") and o["id"] not in ("intro", "outro")
                     and o["salience"] >= 5 and o["max_words"] >= 6), key=lambda o: -o["salience"]):
        if len(used) >= want:
            break
        if all(abs(o["t"] - u) >= 75 for u in used):
            o["why"] += f' Work this phrase in naturally, adapting it if you must: "{phrases[len(used)]}".'
            o["catch"] = phrases[len(used)]
            used.append(o["t"])
    return len(used)


def _trim(text, max_words):
    words = text.split()
    return text if len(words) <= max_words else " ".join(words[:max_words]).rstrip(",;:") + "."


SHORTEST = {"intro": "Welcome to the channel.", "outro": "Like and subscribe."}


def template_for(opp, cfg):
    """The saved wording, trimmed to fit. If trimming would lose the words that make it a welcome or an outro,
    the shortest form that still says it is used instead."""
    text = cfg["intro_text"] if opp["id"] == "intro" else cfg["outro_text"]
    text = _trim(text.replace("{channel}", cfg["channel"] or "the channel"), opp["max_words"])
    if all(w in text.lower() for w in opp.get("must_include", [])):
        return text
    return SHORTEST[opp["id"]]      # never cut: it is the least that still says it


def fix_lines(lines, opps, cfg):
    """Intro and outro must really say welcome / like and subscribe; if the writer drifted, the saved wording
    is used. If the writer left one out, it is added. Returns the lines."""
    by_id = {o["id"]: o for o in opps if o["id"] in ("intro", "outro")}
    have = {l.get("opp_id"): l for l in lines if l.get("opp_id") in by_id}
    for oid, o in by_id.items():
        ln = have.get(oid)
        if ln is None:
            lines.append({"start": o["t"], "max_duration": o["max_duration"], "max_words": o["max_words"], "persona": "character",
                          "tone": "emotional", "emotion": "calm", "trigger_t": None, "opp_id": oid, "beat_kinds": oid,
                          "salience": o["salience"], "silent": False, "line": template_for(o, cfg)})
        elif not all(w in ln["line"].lower() for w in o["must_include"]):
            ln["line"] = template_for(o, cfg)
    lines.sort(key=lambda l: l["start"])
    return lines
