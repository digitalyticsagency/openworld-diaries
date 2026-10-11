"""YouTube packaging for a finished video: titles, description, tags, a pinned comment and thumbnail texts.

The model only writes from a fact sheet built from the real scan, and everything it returns is checked in code:
no clickbait, no invented length or shape, and a safe fallback for anything that fails the check."""
import json
import re

from .config import PROMPTS
from .guard import CLICKBAIT
from .llm import claude, parse_json

MAX_TITLE = 100
TITLES = 5
MAX_TAGS_CHARS = 480
MAX_COMMENT = 200
THUMB_WORDS = 4
DISCLOSURE = "The inner-voice commentary in this video is AI-generated."


def _mmss(t):
    t = int(max(0, t))
    return f"{t // 60}:{t % 60:02d}"


def facts(game, pack_name, length, notes, beats, chapters, lines):
    """The fact sheet: only things the scan and the saved lines really contain."""
    seen_animals, places, acts = {}, {}, {}
    for n in notes:
        for a in n.get("animals") or []:
            sp = (a.get("species") or "").strip().lower()
            if sp and sp != "none":
                seen_animals[sp] = seen_animals.get(sp, 0) + 1
        loc = (n.get("location") or "").strip()
        if loc and loc.lower() != "unknown":
            places[loc] = places.get(loc, 0) + 1
        a = n.get("activity") or "other"
        acts[a] = acts.get(a, 0) + 1
    top = sorted((b for b in beats if not any(k.startswith("npc_") for k in b["kinds"])), key=lambda b: -b["salience"])[:8]
    top.sort(key=lambda b: b["t"])
    spoken = sorted((l for l in lines if not l.get("silent") and not l.get("dropped")), key=lambda l: -(l.get("salience") or 0))[:8]
    return {"game": game, "style": pack_name, "length": _mmss(length),
            "chapters": [f"{_mmss(c['t'])} {c['title']}" for c in chapters],
            "moments": [{"i": i, "at": _mmss(b["t"]), "t": b["t"], "salience": b["salience"], "what": b["why"]} for i, b in enumerate(top)],
            "animals_seen": sorted(seen_animals, key=lambda k: -seen_animals[k])[:6],
            "places_seen": sorted(places, key=lambda k: -places[k])[:4],
            "activities": sorted(acts, key=lambda k: -acts[k])[:5],
            "moral_choices": sum(1 for n in notes if n.get("moral_events")),
            "sample_lines": [l["line"] for l in sorted(spoken, key=lambda l: l["start"])]}


def _caps_ratio(text):
    letters = [c for c in text if c.isalpha()]
    return sum(c.isupper() for c in letters) / len(letters) if letters else 0.0


def _clean_title(t):
    t = re.sub(r"\s+", " ", t.strip().strip('"'))
    return t if t and len(t) <= MAX_TITLE and not CLICKBAIT.search(t) and _caps_ratio(t) < 0.6 else None


def _clean_thumb(t):
    t = re.sub(r"[^A-Za-z0-9 ?!']", "", t or "").strip().upper()
    words = t.split()
    return " ".join(words) if 1 <= len(words) <= THUMB_WORDS and not CLICKBAIT.search(t) else None


def _clean_tags(tags, game):
    out, total = [], 0
    for t in [game.lower(), "gameplay", "inner voice"] + [str(x) for x in tags or []]:
        t = re.sub(r"\s+", " ", t.strip().lower().lstrip("#"))
        if t and len(t) <= 30 and t not in out and total + len(t) + 1 <= MAX_TAGS_CHARS:
            out.append(t)
            total += len(t) + 1
    return out


def validate(raw, fs):
    """Keep only what passes the checks; fall back to plain true wording for anything that does not."""
    raw = raw if isinstance(raw, dict) else {}
    titles = []
    for t in raw.get("titles") or []:
        c = _clean_title(str(t))
        if c and c not in titles:
            titles.append(c)
    base = fs["chapters"][0].split(" ", 1)[1] if fs["chapters"] else "free roam"
    for fb in (f"{fs['game']}: {base}, with an inner voice", f"{fs['game']} gameplay with a live inner voice"):
        if len(titles) < 1 and _clean_title(fb):
            titles.append(fb)
    desc = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    if not desc or CLICKBAIT.search(desc) or len(desc) > 900:
        desc = f"{fs['game']} gameplay with an AI inner-voice commentary, from what really happens on screen."
    body = desc + "\n\n" + ("\n".join(["Chapters:"] + fs["chapters"]) + "\n\n" if len(fs["chapters"]) >= 3 else "") + DISCLOSURE
    pin = re.sub(r"\s+", " ", str(raw.get("pinned_comment") or "")).strip()
    if not pin.endswith("?") or len(pin) > MAX_COMMENT or CLICKBAIT.search(pin):
        pin = (f"What would you have done at {fs['moments'][0]['at']}?" if fs["moments"] else "What would you have done differently?")
    thumbs = []
    for t in raw.get("thumb_texts") or []:
        c = _clean_thumb(str(t))
        if c and c not in thumbs:
            thumbs.append(c)
    for fb in (fs["game"], "INNER VOICE", "FREE ROAM"):
        c = _clean_thumb(fb[:24])
        if len(thumbs) < 3 and c and c not in thumbs:
            thumbs.append(c)
    mi = raw.get("thumb_moment")
    if not isinstance(mi, int) or not 0 <= mi < len(fs["moments"]):
        mi = max(range(len(fs["moments"])), key=lambda i: fs["moments"][i]["salience"]) if fs["moments"] else None
    return {"titles": titles[:TITLES], "description": body, "tags": _clean_tags(raw.get("tags"), fs["game"]),
            "pinned_comment": pin, "thumb_texts": thumbs[:3], "thumb_moment": mi}


def generate(fs):
    system = (PROMPTS / "packaging.md").read_text()
    raw = parse_json(claude(system, json.dumps(fs), 2500))
    return validate(raw, fs)
