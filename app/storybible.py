"""A different personal story every time, for the inner story to tell: its premise and one beat for each passage.

The constraints are drawn at random (an unseeded generator) from large tables, steered by the owner's chosen themes, and the
premises of earlier stories are given to the writer to avoid. Everything the model returns is checked in code."""
import json
import random
import re
import time

from . import db
from .config import PROMPTS
from .llm import claude, parse_json

RELATIONS = {
    "family": ["his older brother", "his father", "his mother", "a younger sister", "an uncle who took him in", "his grandmother"],
    "friends": ["an old friend from the same town", "a boy he grew up beside", "a man he once shared a camp with", "a neighbour's daughter", "a friend who left"],
    "future": ["a woman he almost married", "an old man who had a plan", "a farmer who offered him work", "a boy he once looked after"],
    "past": ["the man who taught him to shoot", "a preacher", "his first employer", "a stranger who helped him once"],
    "fear": ["a night watchman", "a doctor", "the man he was afraid of", "a child who was not afraid"],
    "gratitude": ["a woman who ran a boarding house", "a cook", "a stranger who shared a meal", "a neighbour with a kind habit"],
    "meaning": ["a gravedigger", "a travelling preacher", "an old woman at a well", "a man who built a bridge"],
    "work": ["his first employer", "a foreman", "a blacksmith", "the man who paid him short"],
}
PLACES = ["a mill town", "a ranch in dry country", "a river crossing", "a railroad camp", "a small farm", "a boarding house", "a church on a hill",
          "a ferry landing", "a mining camp", "a general store at a crossroads"]
SEASONS = ["a hard winter", "the summer of a drought", "a wet spring", "one autumn harvest", "a long dry August"]
TURNS = ["a promise he broke", "a lie he told to protect someone", "a debt he never paid", "a night he left without saying goodbye",
         "a thing he took and later returned", "a kindness that cost him more than he expected", "a fight over something small",
         "a letter he never answered", "a chance he turned down", "a job he did badly and was forgiven for"]
OBJECTS = ["a pocket watch", "a borrowed coat", "a cracked mug", "a hand-drawn map", "a jar of nails", "a saddle blanket", "a harmonica", "a pair of boots"]
TONES = ["bittersweet", "wry", "quietly regretful", "tender", "rueful", "plain and matter of fact"]
AGES = ["when he was a boy of about ten", "at fifteen", "in his early twenties", "a few years back", "the year after he left home"]
MAX_PARTS = 30
MIN_PARTS = 2


def pick_seed(theme_ids=None, rng=None):
    """Random constraints for one story. The themes the owner chose decide who the story is about."""
    rng = rng or random.SystemRandom()
    keys = [k for k in (theme_ids or []) if k in RELATIONS] or list(RELATIONS)
    key = rng.choice(keys)
    return {"about": rng.choice(RELATIONS[key]), "theme": key, "place": rng.choice(PLACES), "season": rng.choice(SEASONS),
            "turning point": rng.choice(TURNS), "an object in it": rng.choice(OBJECTS), "told in a tone that is": rng.choice(TONES),
            "when": rng.choice(AGES)}


def used_premises(limit=12):
    return [r["premise"] for r in db.rows("SELECT premise FROM stories ORDER BY id DESC LIMIT ?", (limit,))]


def _clean(text, limit):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def validate(raw, parts):
    """The bible with exactly `parts` beats (or fewer if the model gave fewer), or None if it is not usable."""
    if not isinstance(raw, dict):
        return None
    raw_beats = raw.get("beats")
    beats = [_clean(b, 300) for b in (raw_beats if isinstance(raw_beats, list) else []) if isinstance(b, str) and _clean(b, 300)]
    premise, title = _clean(raw.get("premise"), 400), _clean(raw.get("title"), 60)
    if len(beats) < MIN_PARTS or not premise or not title:
        return None
    titles = [_clean(t, 40) for t in (raw.get("part_titles") if isinstance(raw.get("part_titles"), list) else []) if isinstance(t, str)]
    names = [(titles[i] if i < len(titles) and titles[i] else f"Part {i + 1}") for i in range(len(beats[:parts]))]
    return {"title": title, "premise": premise, "beats": beats[:parts], "part_titles": names, "ending": _clean(raw.get("ending"), 300), "seed": raw.get("seed")}


def make(parts, theme_ids=None, idea="", rng=None):
    """A fresh story with one beat per passage. Asked for twice at most; None if the model cannot give a usable one."""
    parts = max(MIN_PARTS, min(MAX_PARTS, int(parts)))
    seed = pick_seed(theme_ids, rng)
    user = {"parts": parts, "constraints": seed, "owner_idea": _clean(idea, 200) or None, "already_used_premises": used_premises()}
    for _ in range(2):
        try:
            bible = validate(parse_json(claude((PROMPTS / "storybible.md").read_text(), json.dumps(user), 7000)), parts)
        except ValueError:
            bible = None
        if bible:
            bible["seed"] = seed
            return bible
    return None


def save(video_id, bible):
    db.run("INSERT INTO stories(video_id,title,premise,seed,created) VALUES(?,?,?,?,?)",
           (video_id, bible["title"], bible["premise"], json.dumps(bible.get("seed") or {}), time.time()))
