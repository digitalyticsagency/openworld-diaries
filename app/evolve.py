"""Self-improvement loop.

Four signals feed one score per video:
  self   - an editor pass (Claude) scores every line, weak lines get one rewrite
  human  - your thumbs up/down on individual lines
  yt     - YouTube average view percentage
Prompt style blocks compete (champion vs challenger). Hard guardrails never evolve.
"""
import datetime
import json
import random
import time

from . import db, guard
from .config import (CHALLENGER_SHARE, MUTATE_EVERY, PROMOTE_MARGIN,
                     PROMOTE_MIN_VIDEOS, PROMPTS)
from .llm import claude, parse_json

WEIGHTS = {"human": 0.5, "yt": 0.3, "self": 0.2}
WEAK = 6


def combined(human=None, yt=None, self_=None):
    """Weighted mean over whichever signals exist (0-10), None if none."""
    parts = {"human": human, "yt": yt, "self": self_}
    have = {k: v for k, v in parts.items() if v is not None}
    if not have:
        return None
    w = sum(WEIGHTS[k] for k in have)
    return round(sum(WEIGHTS[k] * v for k, v in have.items()) / w, 3)


def pick_variant():
    champ = db.one("SELECT * FROM prompt_versions WHERE status='champion'")
    chal = db.one("SELECT * FROM prompt_versions WHERE status='challenger'")
    if chal and random.random() < CHALLENGER_SHARE:
        return chal
    return champ


def refine(lines, scenes, windows, system):
    """Editor pass: score lines, rewrite the weak ones once, re-apply guardrails."""
    if not lines:
        return lines
    idx = [{"i": i, "start": l["start"], "persona": l["persona"], "emotion": l.get("emotion"),
            "line": l["line"]} for i, l in enumerate(lines)]
    near = [s for s in scenes if any(abs(s["t"] - l["start"]) <= 4 for l in lines)]
    scores = parse_json(claude((PROMPTS / "judge.md").read_text(),
                               json.dumps({"lines": idx, "scene_notes": near,
                                           "locked_windows": windows})))
    by_i = {s["i"]: s for s in scores}
    for i, l in enumerate(lines):
        l["self_score"] = float(by_i.get(i, {}).get("overall", 6))
    weak = [{"i": i, "line": l["line"], "fix": by_i.get(i, {}).get("fix", "")}
            for i, l in enumerate(lines) if l["self_score"] < WEAK]
    if weak:
        rew = parse_json(claude(system, "Rewrite these weak lines. Keep each one's time, persona and "
                                        "emotion, apply the fix, obey every hard rule. Return JSON "
                                        '[{"i": index, "line": "new text"}] only.\n' + json.dumps(weak)))
        for r in rew:
            if 0 <= r["i"] < len(lines) and r.get("line"):
                lines[r["i"]]["line"] = r["line"]
                lines[r["i"]]["self_score"] = WEAK  # unproven until a human rates it
    kept, _ = guard.enforce(lines, windows, scenes)
    return kept


def rescore_video(video_id):
    v = db.one("SELECT * FROM videos WHERE id=?", (video_id,))
    if not v:
        return
    ls = db.rows("SELECT self_score, thumb FROM lines WHERE video_id=?", (video_id,))
    self_vals = [l["self_score"] for l in ls if l["self_score"] is not None]
    thumbs = [10 if l["thumb"] == 1 else 0 for l in ls if l["thumb"] in (1, -1)]
    m = db.one("SELECT avg_view_pct FROM metrics WHERE video_id=?", (video_id,))
    yt = min(10.0, m["avg_view_pct"] / 10) if m and m["avg_view_pct"] is not None else None
    sc = combined(sum(thumbs) / len(thumbs) if thumbs else None, yt,
                  sum(self_vals) / len(self_vals) if self_vals else None)
    db.run("UPDATE videos SET self_score=?, combined_score=? WHERE id=?",
           (sum(self_vals) / len(self_vals) if self_vals else None, sc, video_id))
    if v["variant_id"]:
        recompute_variant(v["variant_id"])


def recompute_variant(variant_id):
    r = db.one("SELECT COUNT(*) n, COALESCE(SUM(combined_score),0) s FROM videos "
               "WHERE variant_id=? AND combined_score IS NOT NULL", (variant_id,))
    db.run("UPDATE prompt_versions SET n=?, score_sum=? WHERE id=?", (r["n"], r["s"], variant_id))


def _mean(v):
    return v["score_sum"] / v["n"] if v and v["n"] else None


def maybe_promote():
    champ = db.one("SELECT * FROM prompt_versions WHERE status='champion'")
    chal = db.one("SELECT * FROM prompt_versions WHERE status='challenger'")
    if not chal or chal["n"] < PROMOTE_MIN_VIDEOS:
        return None
    mc, mh = _mean(chal), _mean(champ)
    if mh is None or mc > mh + PROMOTE_MARGIN:
        db.run("UPDATE prompt_versions SET status='retired' WHERE id=?", (champ["id"],))
        db.run("UPDATE prompt_versions SET status='champion' WHERE id=?", (chal["id"],))
        return "promoted"
    if chal["n"] >= PROMOTE_MIN_VIDEOS * 2:
        db.run("UPDATE prompt_versions SET status='retired' WHERE id=?", (chal["id"],))
        return "retired"
    return None


def maybe_mutate():
    if db.one("SELECT id FROM prompt_versions WHERE status='challenger'"):
        return None
    total = db.one("SELECT COUNT(*) n FROM videos WHERE combined_score IS NOT NULL")["n"]
    if total - int(db.get("last_mutate_total", 0)) < MUTATE_EVERY:
        return None
    champ = db.one("SELECT * FROM prompt_versions WHERE status='champion'")
    best = db.rows("SELECT line, emotion, thumb, self_score FROM lines WHERE thumb=1 OR self_score>=8 "
                   "ORDER BY thumb DESC, self_score DESC LIMIT 8")
    worst = db.rows("SELECT line, emotion, thumb, self_score FROM lines WHERE thumb=-1 OR self_score<=4 "
                    "ORDER BY thumb ASC, self_score ASC LIMIT 8")
    version = db.one("SELECT COUNT(*) n FROM prompt_versions")["n"] + 1
    text = claude((PROMPTS / "mutate.md").read_text(),
                  json.dumps({"champion_style": champ["text"], "next_version": version,
                              "best_lines": best, "worst_lines": worst})).strip()
    if not text or len(text) > 2500:
        return None
    db.run("INSERT INTO prompt_versions(text,status,parent_id,created) VALUES(?,?,?,?)",
           (text, "challenger", champ["id"], time.time()))
    db.put("last_mutate_total", total)
    return "mutated"


def fetch_analytics():
    from .google_auth import service
    today = datetime.date.today().isoformat()
    for v in db.rows("SELECT id, yt_video_id, channel_id FROM videos "
                     "WHERE yt_video_id IS NOT NULL AND status='published'"):
        try:
            r = service("youtubeAnalytics", "v2", v["channel_id"]).reports().query(
                ids="channel==MINE", startDate="2020-01-01", endDate=today,
                metrics="views,likes,averageViewPercentage",
                filters=f"video=={v['yt_video_id']}").execute()
            row = (r.get("rows") or [[0, 0, None]])[0]
            db.run("INSERT INTO metrics(video_id,views,likes,avg_view_pct,fetched) VALUES(?,?,?,?,?) "
                   "ON CONFLICT(video_id) DO UPDATE SET views=excluded.views, likes=excluded.likes, "
                   "avg_view_pct=excluded.avg_view_pct, fetched=excluded.fetched",
                   (v["id"], row[0], row[1], row[2], time.time()))
            rescore_video(v["id"])
        except Exception as e:  # analytics lag / missing scope should not stop the loop
            print("analytics skipped:", v["yt_video_id"], e)


def cycle():
    """Run after every video and from the UI button."""
    fetch_analytics()
    return {"promote": maybe_promote(), "mutate": maybe_mutate()}
