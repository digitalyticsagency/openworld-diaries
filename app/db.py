import sqlite3
import threading
import time

from .config import DATA, PROMPTS

_lock = threading.RLock()
_c = sqlite3.connect(DATA / "app.db", check_same_thread=False)
_c.row_factory = sqlite3.Row

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  drive_id TEXT UNIQUE, name TEXT, status TEXT, error TEXT,
  variant_id INTEGER, self_score REAL, combined_score REAL,
  yt_video_id TEXT, channel_id TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  video_id INTEGER, start REAL, persona TEXT, tone TEXT, emotion TEXT,
  line TEXT, self_score REAL, thumb INTEGER
);
CREATE TABLE IF NOT EXISTS prompt_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT, status TEXT, n INTEGER DEFAULT 0, score_sum REAL DEFAULT 0,
  parent_id INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS metrics (
  video_id INTEGER PRIMARY KEY, views INTEGER, likes INTEGER,
  avg_view_pct REAL, fetched REAL
);
"""


def run(sql, args=()):
    with _lock:
        cur = _c.execute(sql, args)
        _c.commit()
        return cur


def rows(sql, args=()):
    with _lock:
        return [dict(r) for r in _c.execute(sql, args).fetchall()]


def one(sql, args=()):
    r = rows(sql, args)
    return r[0] if r else None


def get(key, default=None):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def put(key, value):
    run("INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def init():
    with _lock:
        _c.executescript(SCHEMA)
        _c.commit()
    if not one("SELECT id FROM prompt_versions LIMIT 1"):
        run("INSERT INTO prompt_versions(text,status,created) VALUES(?,?,?)",
            ((PROMPTS / "style_v1.md").read_text(), "champion", time.time()))


init()
