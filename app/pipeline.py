import json
import shutil
import traceback
from pathlib import Path

from . import db, evolve, guard, memory, publisher, scenes, sources, transcript, voice, writer
from .config import DATA


def options():
    return {k: db.get(k, d) for k, d in [
        ("game", "Red Dead Redemption 2"), ("persona", "mixed"), ("character", ""),
        ("story_point", ""), ("voice_notes", "")]}


def amount():
    return int(db.get("amount", 5))


def youtube_on():
    return db.get("youtube_publish", "0") == "1"


def workdir(vid):
    return DATA / "work" / str(vid)


def final_path(vid):
    return workdir(vid) / "final.mp4"


def set_status(vid, status, error=None):
    db.run("UPDATE videos SET status=?, error=? WHERE id=?", (status, error, vid))


def _guarded(vid, fn):
    try:
        fn()
    except Exception as e:
        traceback.print_exc()
        set_status(vid, "failed", f"{type(e).__name__}: {e}")


def _title(v):
    return Path(v["name"]).stem + " | Inner Voice"


def _load(vid):
    w = workdir(vid)
    return (json.loads((w / "scenes.json").read_text()),
            json.loads((w / "transcript.json").read_text()))


def _style(v):
    row = db.one("SELECT * FROM prompt_versions WHERE id=?", (v["variant_id"],)) if v["variant_id"] else None
    if not row:
        row = evolve.pick_variant()
        db.run("UPDATE videos SET variant_id=? WHERE id=?", (row["id"], v["id"]))
    return row


def process(vid):
    """New video: fetch, listen, watch (slow, paid steps, cached), then generate commentary."""
    def run():
        v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        work = workdir(vid)
        work.mkdir(parents=True, exist_ok=True)
        src = work / "source.mp4"
        fid = v["drive_id"]
        if not src.exists():
            set_status(vid, "downloading")
            sources.fetch(fid, src)
        if not (work / "transcript.json").exists():
            set_status(vid, "listening")
            folder = str(Path(fid).parent) if sources.local_file(fid) else db.get("folder_id")
            sidecar = None
            try:
                _, subs = sources.list_videos(folder)
                sub = subs.get(Path(v["name"]).stem)
                sidecar = sources.read_text(sub) if sub else None
            except Exception:
                pass
            segs = transcript.get_transcript(src, work, sidecar)
            (work / "transcript.json").write_text(json.dumps(segs, indent=2))
        if not (work / "scenes.json").exists():
            set_status(vid, "watching")
            scenes.analyze(src, work, options()["game"])
            shutil.rmtree(work / "frames", ignore_errors=True)
        _generate(vid)
    _guarded(vid, run)


def _generate(vid, amt=None, persona=None):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    work = workdir(vid)
    src = work / "source.mp4"
    notes, segs = _load(vid)
    windows = guard.speech_windows(segs)
    length = scenes.video_length(src)
    opts = options()
    if persona:
        opts["persona"] = persona
    amt = int(amt or amount())
    gap, _ = guard.amount_profile(amt)

    set_status(vid, "writing")
    system = writer.build_system(_style(v)["text"], opts)
    lines, dropped = writer.write_lines(notes, segs, windows, system, length, amount=amt,
                                        profile=memory.profile())
    lines = evolve.refine(lines, notes, windows, system, min_gap=gap)
    (work / "dropped.json").write_text(json.dumps(dropped, indent=2))

    set_status(vid, "voicing")
    clips = voice.make_clips(lines, windows, system, work)
    voice.mix(src, clips, final_path(vid))

    db.run("DELETE FROM lines WHERE video_id=?", (vid,))
    for ln in lines:
        db.run("INSERT INTO lines(video_id,start,persona,tone,emotion,line,self_score,max_duration,"
               "dropped,trigger_t,callback_t) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (vid, ln["start"], ln["persona"], ln.get("tone"), ln.get("emotion"), ln["line"],
                ln.get("self_score"), ln.get("max_duration"), 1 if ln.get("dropped") else 0,
                ln.get("trigger_t"), ln.get("callback_t")))
    evolve.rescore_video(vid)
    set_status(vid, "ready")
    try:
        memory.update(vid)
        evolve.cycle()
    except Exception:
        traceback.print_exc()  # learning must never break a finished video
    if youtube_on() and db.get("active_channel"):
        _publish(vid)


def regenerate(vid, amt=None, persona=None):
    """New commentary from the saved scan: no Gemini calls, no re-download."""
    _guarded(vid, lambda: _generate(vid, amt, persona))


def rerender(vid):
    """Re-voice and re-mix the lines as they are now (after the user edited or deleted some)."""
    def run():
        v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        notes, segs = _load(vid)
        windows = guard.speech_windows(segs)
        rows = db.rows("SELECT * FROM lines WHERE video_id=? ORDER BY start", (vid,))
        lines = []
        for i, r in enumerate(rows):
            room = guard.next_lock_start(r["start"], windows) - r["start"] - 0.3
            nxt = rows[i + 1]["start"] - r["start"] - 0.5 if i + 1 < len(rows) else 1e9
            lines.append({"id": r["id"], "start": r["start"], "persona": r["persona"],
                          "emotion": r["emotion"], "line": r["line"],
                          "max_duration": max(1.0, min(room, nxt, 20.0))})
        set_status(vid, "voicing")
        system = writer.build_system(_style(v)["text"], options())
        clips = voice.make_clips(lines, windows, system, workdir(vid))
        voice.mix(workdir(vid) / "source.mp4", clips, final_path(vid))
        for ln in lines:
            db.run("UPDATE lines SET line=?, dropped=? WHERE id=?",
                   (ln["line"], 1 if ln.get("dropped") else 0, ln["id"]))
        set_status(vid, "ready")
    _guarded(vid, run)


def _publish(vid):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not final_path(vid).exists():
        raise ValueError("No finished video to upload yet.")
    channel = db.get("active_channel")
    if not channel:
        raise ValueError("Connect a YouTube channel first.")
    set_status(vid, "uploading")
    yt_id = publisher.upload_private(final_path(vid), _title(v),
                                     f"Inner-voice commentary over {options()['game']} gameplay.", channel)
    db.run("UPDATE videos SET yt_video_id=?, channel_id=? WHERE id=?", (yt_id, channel, vid))
    set_status(vid, "awaiting_approval")


def publish(vid):
    """On-demand upload to the active channel as private. Making it public is a separate step."""
    _guarded(vid, lambda: _publish(vid))


def approve(vid):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v or v["status"] != "awaiting_approval":
        raise ValueError("Video is not waiting for approval.")
    publisher.make_public(v["yt_video_id"], v["channel_id"])
    set_status(vid, "published")


def delete(vid):
    """Remove the video from the app and its working files. The original file is left alone."""
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v:
        return
    db.run("DELETE FROM lines WHERE video_id=?", (vid,))
    db.run("DELETE FROM metrics WHERE video_id=?", (vid,))
    db.run("DELETE FROM videos WHERE id=?", (vid,))
    shutil.rmtree(workdir(vid), ignore_errors=True)
    # remember it was removed so a file still sitting in the folder is not picked up again
    db.put(f"ignored:{v['drive_id']}", "1")
