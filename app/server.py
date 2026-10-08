import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, drive, evolve, google_auth, pipeline
from .config import POLL_SECONDS

STATIC = Path(__file__).parent / "static"
_watcher = {"thread": None}
_busy = threading.Lock()


def poll_once():
    fid = drive.folder_id(db.get("folder_id"))
    if not fid or not db.get("active_channel"):
        return
    vids, _ = drive.videos_and_sidecars(fid)
    for f in vids:
        if not db.one("SELECT id FROM videos WHERE drive_id=?", (f["id"],)):
            db.run("INSERT INTO videos(drive_id,name,status,created) VALUES(?,?,?,?)",
                   (f["id"], f["name"], "queued", time.time()))
    for v in db.rows("SELECT id FROM videos WHERE status='queued' ORDER BY id"):
        if db.get("enabled") != "1":
            return
        with _busy:
            pipeline.process(v["id"])


def watch_loop():
    while db.get("enabled") == "1":
        try:
            poll_once()
        except Exception as e:
            print("watcher error:", e)
        for _ in range(POLL_SECONDS):
            if db.get("enabled") != "1":
                return
            time.sleep(1)


def start_watcher():
    t = _watcher["thread"]
    if not (t and t.is_alive()):
        _watcher["thread"] = threading.Thread(target=watch_loop, daemon=True)
        _watcher["thread"].start()


@asynccontextmanager
async def lifespan(app):
    # Anything stuck mid-run when the app last stopped goes back in the queue.
    db.run("UPDATE videos SET status='queued' WHERE status IN "
           "('downloading','listening','watching','writing','voicing','uploading')")
    if db.get("enabled") == "1":
        start_watcher()
    yield


app = FastAPI(lifespan=lifespan)


class Toggle(BaseModel):
    on: bool


class Settings(BaseModel):
    folder_id: str | None = None
    game: str | None = None
    persona: str | None = None
    character: str | None = None
    story_point: str | None = None
    voice_notes: str | None = None


class Channel(BaseModel):
    id: str


class Thumb(BaseModel):
    value: int


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state():
    return {
        "enabled": db.get("enabled") == "1",
        "folder_id": db.get("folder_id", ""),
        "options": pipeline.options(),
        "channels": google_auth.channels(),
        "active_channel": db.get("active_channel"),
        "videos": db.rows("SELECT id,name,status,error,self_score,combined_score,yt_video_id "
                          "FROM videos ORDER BY id DESC LIMIT 50"),
        "prompts": db.rows("SELECT id,status,n,score_sum,created FROM prompt_versions ORDER BY id DESC LIMIT 10"),
    }


@app.post("/api/toggle")
def toggle(t: Toggle):
    if t.on and not (db.get("folder_id") and db.get("active_channel")):
        raise HTTPException(400, "Connect YouTube and set the Drive folder first.")
    db.put("enabled", "1" if t.on else "0")
    if t.on:
        start_watcher()
    return {"enabled": t.on}


@app.post("/api/settings")
def settings(s: Settings):
    for k, v in s.model_dump(exclude_none=True).items():
        db.put(k, drive.folder_id(v) if k == "folder_id" else v)
    return {"ok": True}


@app.post("/api/auth/connect")
def connect():
    try:
        return {"channel": google_auth.connect()}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/channel")
def channel(c: Channel):
    if c.id not in [x["id"] for x in google_auth.channels()]:
        raise HTTPException(404, "Unknown channel.")
    db.put("active_channel", c.id)
    return {"ok": True}


@app.get("/api/videos/{vid}/lines")
def lines(vid: int):
    return db.rows("SELECT id,start,persona,tone,emotion,line,self_score,thumb FROM lines "
                   "WHERE video_id=? ORDER BY start", (vid,))


@app.post("/api/videos/{vid}/approve")
def approve(vid: int):
    try:
        pipeline.approve(vid)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/videos/{vid}/retry")
def retry(vid: int):
    pipeline.set_status(vid, "queued")
    return {"ok": True}


@app.post("/api/lines/{lid}/thumb")
def thumb(lid: int, t: Thumb):
    if t.value not in (-1, 0, 1):
        raise HTTPException(400, "value must be -1, 0 or 1")
    db.run("UPDATE lines SET thumb=? WHERE id=?", (t.value or None, lid))
    row = db.one("SELECT video_id FROM lines WHERE id=?", (lid,))
    if row:
        evolve.rescore_video(row["video_id"])
    return {"ok": True}


@app.post("/api/evolve/run")
def evolve_run():
    return evolve.cycle()
