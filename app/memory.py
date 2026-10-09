"""What the AI remembers about the player's gameplay across videos."""
import json
import time
from pathlib import Path

from . import db
from .config import DATA, PROMPTS
from .llm import claude, parse_json

KINDS = {"habit", "place", "character", "joke"}


def enabled():
    return db.get("learn", "1") == "1"


def profile(limit=12):
    if not enabled():
        return []
    rows = db.rows("SELECT text FROM memory ORDER BY count DESC, updated DESC LIMIT ?", (limit,))
    return [r["text"] for r in rows]


def update(video_id):
    """Fold one finished video into memory. Safe to fail: learning must never block a video."""
    if not enabled():
        return 0
    work = DATA / "work" / str(video_id)
    notes = json.loads((work / "scenes.json").read_text())
    segs = json.loads((work / "transcript.json").read_text())
    lines = db.rows("SELECT line FROM lines WHERE video_id=? AND COALESCE(dropped,0)=0 ORDER BY start", (video_id,))
    events = [{"t": n["t"], "interactions": n.get("interactions"), "moral": n.get("moral_events")}
              for n in notes if n.get("interactions") or n.get("moral_events")][:60]
    places = sorted({n.get("location") for n in notes if n.get("location") and n["location"] != "unknown"})[:25]
    existing = [r["text"] for r in db.rows("SELECT text FROM memory")]
    raw = claude((PROMPTS / "memory.md").read_text(), json.dumps({
        "existing": existing, "events": events, "places": places,
        "speech": [s.get("text") for s in segs if s.get("speaker") == "player"][:40],
        "lines": [l["line"] for l in lines]}), 1500)
    added = 0
    for item in parse_json(raw):
        text = (item.get("text") or "").strip()[:200]
        kind = item.get("kind") if item.get("kind") in KINDS else "habit"
        if not text:
            continue
        db.run("INSERT INTO memory(kind,text,count,updated) VALUES(?,?,1,?) "
               "ON CONFLICT(text) DO UPDATE SET count=count+1, updated=excluded.updated",
               (kind, text, time.time()))
        added += 1
    return added
