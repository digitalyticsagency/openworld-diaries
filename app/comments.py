"""Viewer comments: fetch them, draft replies in the channel's voice, and post one only when the owner approves it.

Nothing is ever posted from here on its own: a draft becomes a posted reply only through post(), which the app calls
when the owner presses Post on that one reply."""
import json
import re
import time

from . import db
from .config import PROMPTS
from .guard import CLICKBAIT
from .llm import claude, parse_json

MAX_REPLY = 280
BATCH = 15
MAX_COMMENTS = 60
STATUSES = ("draft", "skipped", "posted")
LINK = re.compile(r"https?://|www\.", re.I)


def fetch(channel_id, yt_video_id, limit=MAX_COMMENTS):
    """Top-level comments, newest first: [{id, author, text, likes}]. Replies and the channel's own comments are left out."""
    from .google_auth import service
    yt = service("youtube", "v3", channel_id)
    out, token = [], None
    while len(out) < limit:
        r = yt.commentThreads().list(part="snippet", videoId=yt_video_id, maxResults=min(50, limit), order="time",
                                     textFormat="plainText", **({"pageToken": token} if token else {})).execute()
        for it in r.get("items", []):
            top = it["snippet"]["topLevelComment"]["snippet"]
            if (top.get("authorChannelId") or {}).get("value") == channel_id:
                continue
            out.append({"id": it["snippet"]["topLevelComment"]["id"], "author": top.get("authorDisplayName", ""),
                        "text": top.get("textDisplay", ""), "likes": top.get("likeCount", 0)})
        token = r.get("nextPageToken")
        if not token:
            break
    return out[:limit]


def clean_reply(text):
    """A reply that is fit to show the owner, or '' if it is not."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or len(t) > MAX_REPLY or CLICKBAIT.search(t) or LINK.search(t) or "#" in t:
        return ""
    return t


def draft(comments, character, game):
    """[{id, reply, skip}] for the comments: the model drafts, code checks every reply."""
    out = []
    system = (PROMPTS / "replies.md").read_text()
    for i in range(0, len(comments), BATCH):
        batch = comments[i:i + BATCH]
        raw = parse_json(claude(system, json.dumps({"character": character, "game": game,
                                                    "comments": [{"id": c["id"], "text": c["text"][:500]} for c in batch]}), 3000))
        by_id = {r.get("id"): r for r in raw if isinstance(r, dict)} if isinstance(raw, list) else {}
        for c in batch:
            r = by_id.get(c["id"]) or {}
            reply = clean_reply(r.get("reply"))
            skip = "" if reply else (str(r.get("skip") or "").strip()[:120] or "no safe reply")
            out.append({"id": c["id"], "reply": reply, "skip": skip})
    return out


def save(video_id, comments, drafts):
    """Store new comments with their drafts. A comment already in the list is left alone. Returns how many were added."""
    by_id = {d["id"]: d for d in drafts}
    added = 0
    for c in comments:
        if db.one("SELECT id FROM replies WHERE comment_id=?", (c["id"],)):
            continue
        d = by_id.get(c["id"], {"reply": "", "skip": "no draft"})
        db.run("INSERT INTO replies(video_id,comment_id,author,comment,draft,status,note,created) VALUES(?,?,?,?,?,?,?,?)",
               (video_id, c["id"], c["author"], c["text"][:1000], d["reply"], "draft" if d["reply"] else "skipped", d["skip"], time.time()))
        added += 1
    return added


def post(reply_id, channel_id):
    """Post one approved reply. Only a draft with text can be posted, and only once."""
    from .google_auth import service
    r = db.one("SELECT * FROM replies WHERE id=?", (reply_id,))
    if not r:
        raise ValueError("No such reply.")
    if r["status"] != "draft":
        raise ValueError("This reply is not waiting to be posted.")
    text = clean_reply(r["draft"])
    if not text:
        raise ValueError("The reply is empty or not allowed (too long, a link, or clickbait). Edit it first.")
    service("youtube", "v3", channel_id).comments().insert(
        part="snippet", body={"snippet": {"parentId": r["comment_id"], "textOriginal": text}}).execute()
    db.run("UPDATE replies SET status='posted', draft=? WHERE id=?", (text, reply_id))
    return text
