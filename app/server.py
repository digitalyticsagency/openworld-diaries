import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import db, evolve, google_auth, pipeline, sources
from .config import POLL_SECONDS

STATIC = Path(__file__).parent / "static"
_watcher = {"thread": None}
_busy = threading.Lock()
WORKING = {"downloading", "listening", "watching", "writing", "voicing", "uploading"}


def watched_folders():
    """The configured folder plus the local inbox (where uploads land), without duplicates."""
    inbox = str(sources.inbox_dir())
    folders = [db.get("folder_id") or inbox, inbox]
    return list(dict.fromkeys(folders))


def run_bg(fn, *args):
    """Run a slow video action in the background, one at a time."""
    def go():
        with _busy:
            fn(*args)
    threading.Thread(target=go, daemon=True).start()


def idle_video(vid):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v:
        raise HTTPException(404, "No such video.")
    if v["status"] in WORKING:
        raise HTTPException(409, "This video is still being processed.")
    return v


def poll_once():
    if pipeline.youtube_on() and not db.get("active_channel"):
        return
    for folder in watched_folders():
        try:
            vids, _ = sources.list_videos(folder)
        except Exception as e:
            print("cannot read folder", folder, e)
            continue
        for f in vids:
            if not db.one("SELECT id FROM videos WHERE drive_id=?", (f["id"],)) \
                    and not db.get(f"ignored:{f['id']}"):
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
    youtube_publish: bool | None = None
    learn: bool | None = None
    amount: int | None = None


class Regen(BaseModel):
    amount: int | None = None
    persona: str | None = None


class LineEdit(BaseModel):
    line: str


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
        "folder_id": db.get("folder_id") or str(sources.inbox_dir()),
        "options": pipeline.options(),
        "channels": google_auth.channels(),
        "active_channel": db.get("active_channel"),
        "youtube_publish": pipeline.youtube_on(),
        "amount": pipeline.amount(),
        "learn": db.get("learn", "1") == "1",
        "videos": [{**v, "has_video": pipeline.final_path(v["id"]).exists()} for v in db.rows(
            "SELECT id,name,status,error,self_score,combined_score,yt_video_id "
            "FROM videos ORDER BY id DESC LIMIT 50")],
        "memory": db.rows("SELECT id,kind,text,count FROM memory ORDER BY count DESC, updated DESC LIMIT 40"),
        "ratings": db.one("SELECT COALESCE(SUM(thumb=1),0) up, COALESCE(SUM(thumb=-1),0) down FROM lines"),
        "prompts": db.rows("SELECT id,status,n,score_sum,created FROM prompt_versions ORDER BY id DESC LIMIT 10"),
    }


@app.post("/api/toggle")
def toggle(t: Toggle):
    if t.on and pipeline.youtube_on() and not db.get("active_channel"):
        raise HTTPException(400, "Connect YouTube first, or switch YouTube publishing off.")
    db.put("enabled", "1" if t.on else "0")
    if t.on:
        start_watcher()
    return {"enabled": t.on}


@app.post("/api/upload")
async def upload(request: Request, name: str):
    """Raw-body upload from the browser. Saved into the inbox, then picked up like any file."""
    base = Path(name).name
    if base.startswith(".") or Path(base).suffix.lower() not in sources.VIDEO_EXT | sources.SUB_EXT:
        raise HTTPException(400, "Use a video (.mp4 .mov .mkv .webm .m4v) or a caption file (.srt .vtt).")
    inbox = sources.inbox_dir()
    dest, n = inbox / base, 1
    while dest.exists():
        dest = inbox / f"{Path(base).stem}-{n}{Path(base).suffix}"
        n += 1
    part = inbox / f".{dest.name}.part"
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                await asyncio.to_thread(f.write, chunk)
        part.rename(dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"saved": dest.name, "bytes": dest.stat().st_size}


@app.post("/api/settings")
def settings(s: Settings):
    for k, v in s.model_dump(exclude_none=True).items():
        if k == "folder_id":
            v = sources.normalize(v)
        elif k in ("youtube_publish", "learn"):
            v = "1" if v else "0"
        elif k == "amount":
            v = max(1, min(10, v))
        db.put(k, v)
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
    return db.rows("SELECT id,start,persona,tone,emotion,line,self_score,thumb,"
                   "COALESCE(dropped,0) dropped FROM lines WHERE video_id=? ORDER BY start", (vid,))


@app.post("/api/videos/{vid}/approve")
def approve(vid: int):
    try:
        pipeline.approve(vid)
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/videos/{vid}/retry")
def retry(vid: int):
    idle_video(vid)
    pipeline.set_status(vid, "queued")
    run_bg(pipeline.process, vid)
    return {"ok": True}


@app.get("/api/videos/{vid}/download")
def download(vid: int):
    v = db.one("SELECT name FROM videos WHERE id=?", (vid,))
    path = pipeline.final_path(vid)
    if not v or not path.exists():
        raise HTTPException(404, "No finished video yet.")
    return FileResponse(path, media_type="video/mp4", filename=f"{Path(v['name']).stem}_inner.mp4")


@app.post("/api/videos/{vid}/regenerate")
def regenerate(vid: int, r: Regen):
    idle_video(vid)
    if not (pipeline.workdir(vid) / "scenes.json").exists():
        raise HTTPException(400, "The saved scan is missing; use Retry to process it again.")
    pipeline.set_status(vid, "writing")
    run_bg(pipeline.regenerate, vid, r.amount, r.persona)
    return {"ok": True}


@app.post("/api/videos/{vid}/rerender")
def rerender(vid: int):
    idle_video(vid)
    pipeline.set_status(vid, "voicing")
    run_bg(pipeline.rerender, vid)
    return {"ok": True}


@app.post("/api/videos/{vid}/publish")
def publish(vid: int):
    v = idle_video(vid)
    if not db.get("active_channel"):
        raise HTTPException(400, "Connect YouTube first.")
    if v["status"] not in ("ready", "failed"):
        raise HTTPException(400, "Only a finished video can be uploaded.")
    pipeline.set_status(vid, "uploading")
    run_bg(pipeline.publish, vid)
    return {"ok": True}


@app.delete("/api/videos/{vid}")
def delete_video(vid: int):
    idle_video(vid)
    pipeline.delete(vid)
    return {"ok": True}


@app.patch("/api/lines/{lid}")
def edit_line(lid: int, e: LineEdit):
    text = e.line.strip()
    if not text or len(text.split()) > 40:
        raise HTTPException(400, "A line needs 1 to 40 words.")
    db.run("UPDATE lines SET line=?, dropped=0 WHERE id=?", (text, lid))
    return {"ok": True}


@app.delete("/api/lines/{lid}")
def delete_line(lid: int):
    db.run("DELETE FROM lines WHERE id=?", (lid,))
    return {"ok": True}


@app.delete("/api/memory/{mid}")
def forget(mid: int):
    db.run("DELETE FROM memory WHERE id=?", (mid,))
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
