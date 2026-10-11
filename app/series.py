"""Series mode: numbered episodes with a "previously" recap of the last one.

The recap comes from a note written when the last episode finished, itself built only from that episode's real facts.
Pure functions apart from the note, which is one Claude call."""
import json
import re

from . import db
from . import gaps as gapmod
from .config import LEAD, TAIL
from .guard import CLICKBAIT
from .llm import claude, parse_json
from .config import PROMPTS

RECAP_WITHIN = 90.0
RECAP_MIN_WORDS = 10
MAX_NOTE = 320


def enabled():
    return db.get("series_on", "0") == "1"


def name():
    return (db.get("series_name") or "").strip()[:60]


def assign_episode(vid):
    """The next episode number, once. Returns it (or None when series mode is off)."""
    if not enabled():
        return None
    row = db.one("SELECT episode FROM videos WHERE id=?", (vid,))
    if row and row["episode"]:
        return row["episode"]
    top = db.one("SELECT COALESCE(MAX(episode), 0) m FROM videos")["m"]
    db.run("UPDATE videos SET episode=? WHERE id=?", (top + 1, vid))
    return top + 1


def previous(vid):
    """The latest earlier episode that has a note: {episode, summary} or None."""
    me = db.one("SELECT episode FROM videos WHERE id=?", (vid,))
    if not enabled() or not me or not me["episode"]:
        return None
    return db.one("SELECT episode, summary FROM videos WHERE episode < ? AND summary IS NOT NULL AND summary != '' "
                  "ORDER BY episode DESC LIMIT 1", (me["episode"],))


def recap_opportunity(gaps, after, prev):
    """A short 'previously' line soon after the welcome. None without a note or without room."""
    if not prev:
        return None
    for g in gaps:
        t = max(g["start"] + LEAD, after)
        if t > RECAP_WITHIN:
            break
        cap = g["end"] - t - TAIL
        mw = min(24, gapmod.words_for(cap))
        if mw >= RECAP_MIN_WORDS:
            return {"id": "recap", "t": round(t, 2), "beat_t": None, "salience": 96.0, "kinds": ["recap"], "kind": "full",
                    "why": (f"PREVIOUSLY. Remind the viewers, in one or two short sentences, of last episode (episode {prev['episode']}). "
                            f"Use only this note: {prev['summary']} Do not greet anyone and do not promise anything about this episode."),
                    "max_words": mw, "max_duration": round(cap, 2), "must_fit": RECAP_MIN_WORDS}
    return None


def note_for(fs):
    """The story note for a finished episode, from its fact sheet. Never longer than a few sentences."""
    raw = parse_json(claude((PROMPTS / "series.md").read_text(), json.dumps(fs), 600))
    raw = raw if isinstance(raw, dict) else {}
    s = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
    th = re.sub(r"\s+", " ", str(raw.get("thread") or "")).strip()
    if not s or CLICKBAIT.search(s):
        return ""
    text = s + (f" Left open: {th}" if th and not CLICKBAIT.search(th) else "")
    return text[:MAX_NOTE]


def title_prefix(title, episode, series):
    """'Series Ep. N: title', kept inside 100 characters; the title is shortened rather than the numbering."""
    if not episode:
        return title
    head = f"{series + ' ' if series else ''}Ep. {episode}: "
    if title.lower().startswith(head.lower()):
        return title
    return (head + title)[:100].rstrip()
