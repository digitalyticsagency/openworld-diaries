import asyncio
import io
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import captions, cutscenes, db, envfile, evolve, google_auth, knowledge, pipeline, sources, styles, voices
from . import channel as channel_lines
from .config import POLL_SECONDS

STATIC = Path(__file__).parent / "static"
_watcher = {"thread": None}
_busy = threading.Lock()
WORKING = pipeline.WORKING


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


def ingest():
    """Add any new files from the watched folders to the list, as queued."""
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


def poll_once():
    if pipeline.youtube_on() and not db.get("active_channel"):
        return
    ingest()
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
    marks = ",".join("?" * len(WORKING))
    db.run(f"UPDATE videos SET status='queued' WHERE status IN ({marks})", tuple(WORKING))
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
    ask_style: bool | None = None
    ab_test: bool | None = None
    one_voice: bool | None = None
    daydream: bool | None = None
    intro_on: bool | None = None
    outro_on: bool | None = None
    catch_on: bool | None = None
    knowledge_on: bool | None = None
    channel_name: str | None = None
    intro_text: str | None = None
    outro_text: str | None = None
    catchphrases: str | None = None
    moral_mode: str | None = None
    cap_style_dialogue: str | None = None
    cap_style_commentary: str | None = None
    cap_pos_dialogue: str | None = None
    cap_pos_commentary: str | None = None
    cap_size: str | None = None
    cutscene_quiet: bool | None = None
    cap_commentary: bool | None = None
    cap_dialogue: bool | None = None
    target_lpm: float | None = None
    personality: str | None = None
    learn: bool | None = None
    amount: int | None = None


class Regen(BaseModel):
    amount: int | None = None
    persona: str | None = None
    pack: str | None = None
    personality: str | None = None
    game: str | None = None


class KeyValue(BaseModel):
    value: str


class Span(BaseModel):
    start: float
    end: float


class Density(BaseModel):
    value: int


class Start(BaseModel):
    pack: str
    personality: str = "balanced"
    game: str | None = None


class Assign(BaseModel):
    pack: str
    persona: str
    voice_id: str


class Preview(BaseModel):
    voice_id: str
    text: str = "Easy now. We are nearly there."


class LineEdit(BaseModel):
    line: str


class Channel(BaseModel):
    id: str


class Thumb(BaseModel):
    value: int


@app.get("/")
def index():
    # never let the browser keep an old copy of the page after the app is updated
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/state")
def state():
    return {
        "enabled": db.get("enabled") == "1",
        "folder_id": db.get("folder_id") or str(sources.inbox_dir()),
        "options": pipeline.options(),
        "channels": google_auth.channels(),
        "active_channel": db.get("active_channel"),
        "server_time": time.time(),
        "youtube_publish": pipeline.youtube_on(),
        "ask_style": db.get("ask_style", "1") == "1",
        "ab_test": db.get("ab_test", "0") == "1",
        "cutscene_quiet": pipeline.cutscene_quiet(),
        "one_voice": pipeline.one_voice(),
        "daydream": pipeline.daydream_on(),
        "moral_mode": pipeline.moral_mode(),
        "knowledge_on": db.get("knowledge_on", "1") == "1",
        "channel": {**{k: v for k, v in channel_lines.settings(db.get).items() if k != "phrases"},
                    "catchphrases": "\n".join(channel_lines.settings(db.get)["phrases"]),
                    "defaults": {"intro_text": channel_lines.DEFAULTS["intro_text"], "outro_text": channel_lines.DEFAULTS["outro_text"]}},
        "cap_cfg": pipeline.caption_cfg(),
        **captions.catalog(),
        "cap_commentary": pipeline.cap_commentary(),
        "cap_dialogue": pipeline.cap_dialogue(),
        "target_lpm": pipeline.target_lpm() or 0,
        "personality": styles.personality_id(db.get("personality")),
        **styles.catalog(),
        "amount": pipeline.amount(),
        "learn": db.get("learn", "1") == "1",
        "videos": [{**v, "has_video": pipeline.final_path(v["id"]).exists(),
                    "voices": {p: voices.resolve(v["pack"], p) for p in ("player", "character", "companion")},
                    "knowledge_name": knowledge.name_for_game(v["game"]),
                    "scan_has_actions": _scan_has_actions(v["id"]), "known_pack": styles.pack_for_game(v["game"]),
                    "cut_count": len(cutscenes.effective(cutscenes.load(v["cutscenes"]))),
                    "cut_seconds": cutscenes.total_seconds(cutscenes.effective(cutscenes.load(v["cutscenes"])))} for v in db.rows(
            "SELECT id,name,status,error,self_score,combined_score,yt_video_id,pack,personality,game,suggestion,cutscenes,"
            "status_at,started_at,progress,density_rating,stats,arm "
            "FROM videos ORDER BY id DESC LIMIT 50")],
        "brain": {"bias": {p: evolve.density_bias(p) for p in styles.PACKS if evolve.density_bias(p)},
                  "kind_weights": evolve.kind_weights()},
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


@app.post("/api/analyze")
def analyze():
    """The Start button: pick up what was uploaded and run the AI analysis on it, one video at a time."""
    if pipeline.youtube_on() and not db.get("active_channel"):
        raise HTTPException(400, "Connect YouTube first, or switch YouTube publishing off.")
    ingest()
    ids = [v["id"] for v in db.rows("SELECT id FROM videos WHERE status='queued' ORDER BY id")]
    if not ids:
        raise HTTPException(400, "Nothing to analyse. Upload a video first.")

    def go():
        for i in ids:
            with _busy:
                v = db.one("SELECT status FROM videos WHERE id=?", (i,))
                if v and v["status"] == "queued":
                    pipeline.process(i)
    threading.Thread(target=go, daemon=True).start()
    return {"started": len(ids)}


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
        elif k == "pack":
            v = styles.pack_id(v)
        elif k == "personality":
            v = styles.personality_id(v)
        elif k in ("youtube_publish", "learn", "ask_style", "ab_test", "cap_commentary", "cap_dialogue", "cutscene_quiet", "one_voice",
                    "daydream", "intro_on", "outro_on", "catch_on", "knowledge_on"):
            v = "1" if v else "0"
        elif k == "amount":
            v = max(1, min(10, v))
        elif k == "moral_mode":
            if v not in ("both", "cues"):
                raise HTTPException(400, "moral_mode must be both or cues.")
        elif k in ("channel_name", "intro_text", "outro_text"):
            v = " ".join(v.split())[:300]
        elif k == "catchphrases":
            v = "\n".join(p.strip()[:80] for p in v.splitlines() if p.strip())[:1200]
        elif k in ("cap_style_dialogue", "cap_style_commentary"):
            if v not in captions.STYLES:
                raise HTTPException(400, "Unknown caption style.")
        elif k in ("cap_pos_dialogue", "cap_pos_commentary"):
            if v not in captions.POSITIONS:
                raise HTTPException(400, "Unknown caption position.")
        elif k == "cap_size":
            if v not in captions.SIZES:
                raise HTTPException(400, "Unknown caption size.")
        elif k == "target_lpm":
            v = "" if v <= 0 else min(12.0, v)   # 0 means automatic: the emotion beats decide
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
                   "COALESCE(dropped,0) dropped, COALESCE(silent,0) silent, note FROM lines WHERE video_id=? ORDER BY start", (vid,))


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
    run_bg(pipeline.regenerate, vid, r.amount, r.persona, r.pack, r.personality, r.game)
    return {"ok": True}


def local_only(request: Request):
    """Key settings are only for this page: refuse requests that another website could send."""
    origin = request.headers.get("origin")
    if origin and origin not in ("http://localhost:8000", "http://127.0.0.1:8000"):
        raise HTTPException(403, "Only the app's own page can change keys.")


@app.get("/api/keys")
def keys_status():
    return envfile.status()


@app.post("/api/keys/{name}")
def keys_save(name: str, k: KeyValue, request: Request):
    local_only(request)
    if name not in envfile.KEYS:
        raise HTTPException(404, "Unknown setting.")
    try:
        envfile.set_value(name, k.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/keys/{name}")
def keys_clear(name: str, request: Request):
    local_only(request)
    if name not in envfile.KEYS:
        raise HTTPException(404, "Unknown setting.")
    envfile.clear(name)
    return {"ok": True}


@app.post("/api/keys/{name}/test")
def keys_test(name: str, request: Request):
    local_only(request)
    if name not in envfile.KEYS:
        raise HTTPException(404, "Unknown setting.")
    ok, message = envfile.test(name)
    return {"ok": ok, "message": message}


def _scan_has_actions(vid):
    """Scans made before the richer action details existed lack them; the page offers a fresh scan for those."""
    f = pipeline.workdir(vid) / "scenes.json"
    if not f.exists():
        return None
    try:
        return any("activity" in n for n in __import__("json").loads(f.read_text())[:20])
    except ValueError:
        return None


@app.post("/api/videos/{vid}/rescan")
def rescan(vid: int):
    """Look at the video again for the newer action details (shops, animals, who died). Costs a new scan."""
    idle_video(vid)
    work = pipeline.workdir(vid)
    for name in ("scenes.json", "scenes.partial.json"):
        (work / name).unlink(missing_ok=True)
    pipeline.set_status(vid, "watching")
    run_bg(pipeline.process, vid)
    return {"ok": True}


def _cuts_payload(vid):
    c = pipeline.cut_state(vid)
    eff = cutscenes.effective(c)
    return {**c, "effective": eff, "seconds": cutscenes.total_seconds(eff), "quiet": pipeline.cutscene_quiet(),
            "detected": (db.one("SELECT cutscenes FROM videos WHERE id=?", (vid,)) or {}).get("cutscenes") is not None}


@app.get("/api/videos/{vid}/cutscenes")
def get_cutscenes(vid: int):
    return _cuts_payload(vid)


def _edit_cuts(vid, span, fn):
    if span.end <= span.start or span.start < 0:
        raise HTTPException(400, "The end must come after the start.")
    c = pipeline.cut_state(vid)
    fn(c, span.start, span.end)
    pipeline.save_cuts(vid, c)
    return _cuts_payload(vid)


@app.post("/api/videos/{vid}/cutscenes/add")
def add_cutscene(vid: int, span: Span):
    return _edit_cuts(vid, span, cutscenes.add_range)


@app.post("/api/videos/{vid}/cutscenes/remove")
def remove_cutscene(vid: int, span: Span):
    return _edit_cuts(vid, span, cutscenes.remove_range)


@app.post("/api/videos/{vid}/cutscenes/reset")
def reset_cutscene_edits(vid: int):
    c = pipeline.cut_state(vid)
    c["add"], c["remove"] = [], []
    pipeline.save_cuts(vid, c)
    return _cuts_payload(vid)


@app.post("/api/videos/{vid}/cutscenes/redetect")
def redetect_cutscenes(vid: int):
    """Look at the video again. Hand edits are kept."""
    idle_video(vid)
    c = pipeline.cut_state(vid)
    db.run("UPDATE videos SET cutscenes=NULL WHERE id=?", (vid,))
    pipeline.set_status(vid, "cutscenes")

    def run():
        try:
            notes, _ = pipeline._load(vid)
            fresh = pipeline.ensure_cuts(vid, notes, pipeline.workdir(vid) / "source.mp4")
            fresh["add"], fresh["remove"] = c["add"], c["remove"]
            pipeline.save_cuts(vid, fresh)
            pipeline.set_status(vid, "ready")
        except Exception as e:
            pipeline.set_status(vid, "failed", f"{type(e).__name__}: {e}")
    run_bg(run)
    return {"ok": True}


@app.post("/api/videos/{vid}/density")
def density(vid: int, d: Density):
    """How was the amount of commentary? -1 too quiet, 0 just right, 1 too chatty."""
    if d.value not in (-1, 0, 1):
        raise HTTPException(400, "value must be -1, 0 or 1")
    idle_video(vid)
    db.run("UPDATE videos SET density_rating=? WHERE id=?", (d.value, vid))
    return {"ok": True}


@app.post("/api/videos/{vid}/start")
def start_video(vid: int, s: Start):
    v = idle_video(vid)
    if v["status"] != "choose_style":
        raise HTTPException(400, "This video is not waiting for a style.")
    pipeline.start(vid, s.pack, s.personality, s.game)
    pipeline.set_status(vid, "queued")
    run_bg(pipeline.process, vid)
    return {"ok": True}


@app.get("/api/voices")
def list_voices():
    try:
        return voices.list_voices()
    except Exception as e:
        raise HTTPException(502, f"Could not read ElevenLabs voices: {e}")


@app.get("/api/voices/assigned")
def assigned(pack: str):
    return voices.assigned(pack)


@app.post("/api/voices/assign")
def assign_voice(a: Assign):
    if a.persona not in ("player", "character", "companion"):
        raise HTTPException(400, "persona must be player, character or companion")
    voices.assign(a.pack, a.persona, a.voice_id)
    return {"ok": True}


@app.post("/api/voices/preview")
def preview_voice(p: Preview):
    try:
        return Response(voices.preview(p.voice_id, p.text), media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(502, f"Preview failed: {e}")


@app.post("/api/videos/{vid}/recaption")
def recaption(vid: int):
    idle_video(vid)
    if not pipeline.final_path(vid).exists():
        raise HTTPException(400, "Make the video first.")
    pipeline.set_status(vid, "voicing")
    run_bg(pipeline.recaption, vid)
    return {"ok": True}


@app.get("/api/captions/preview")
def caption_preview(ds: str = "", dp: str = "", cs: str = "", cp: str = "", size: str = "", second: str = "voiced"):
    """A still frame with sample captions in the chosen look. Unspecified values fall back to the saved settings."""
    saved = pipeline.caption_cfg()
    cfg = captions.clean_cfg({"dialogue": {"style": ds or saved["dialogue"]["style"], "pos": dp or saved["dialogue"]["pos"]},
                              "commentary": {"style": cs or saved["commentary"]["style"], "pos": cp or saved["commentary"]["pos"]},
                              "size": size or saved["size"]})
    base = _preview_frame()
    buf = io.BytesIO()
    captions.preview(base, cfg, second).save(buf, "JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _preview_frame():
    """A frame from the newest video on this Mac, so the preview looks like the real thing."""
    from PIL import Image
    for v in db.rows("SELECT id FROM videos ORDER BY id DESC"):
        src = pipeline.workdir(v["id"]) / "source.mp4"
        if src.exists():
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", "40", "-i", str(src), "-frames:v", "1", "-f", "image2pipe",
                                "-vcodec", "png", "-"], capture_output=True)
            if r.returncode == 0 and r.stdout:
                return Image.open(io.BytesIO(r.stdout))
    return Image.new("RGB", (1280, 720), (38, 52, 46))


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
