"""What to make next: ideas built from what the owner pasted about what is popular, plus the channel's own history.

Nothing here looks anything up on the internet. The only 'trend' input is the owner's own pasted text, and every idea
has to name which input it comes from."""
import json
import re

from . import analytics, db, memory, styles
from .config import PROMPTS
from .guard import CLICKBAIT
from .llm import claude, parse_json

MAX_PASTE = 4000
IDEAS = 5


def pasted():
    return (db.get("trends_text") or "")[:MAX_PASTE]


def inputs(game):
    past = db.rows("SELECT name, combined_score, episode FROM videos WHERE status IN ('ready','awaiting_approval','published') "
                   "ORDER BY id DESC LIMIT 10")
    return {"game": game, "owner_notes_on_what_is_popular": pasted(),
            "past_videos": [{"name": p["name"], "score": p["combined_score"], "episode": p["episode"]} for p in past],
            "player_memory": memory.profile(), "what_kept_viewers": analytics.report()[:6],
            "style_ids": {k: v["name"] for k, v in styles.PACKS.items()}}


def clean(raw):
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        title = re.sub(r"\s+", " ", str(r.get("title") or "")).strip()
        why = re.sub(r"\s+", " ", str(r.get("why") or "")).strip()
        hook = re.sub(r"\s+", " ", str(r.get("hook") or "")).strip()
        if not title or len(title) > 80 or not why or CLICKBAIT.search(title + " " + hook):
            continue
        out.append({"title": title, "why": why[:240], "hook": hook[:160], "style": styles.pack_id(r.get("style"))})
    return out[:IDEAS]


def suggest(game):
    ideas = clean(parse_json(claude((PROMPTS / "trends.md").read_text(), json.dumps(inputs(game)), 2500)))
    db.put("trend_ideas", json.dumps(ideas))
    return ideas


def saved():
    try:
        return json.loads(db.get("trend_ideas", "[]"))
    except ValueError:
        return []
