import json
import shutil
import traceback
from pathlib import Path

from . import db, drive, evolve, guard, publisher, scenes, transcript, voice, writer
from .config import DATA


def options():
    return {k: db.get(k, d) for k, d in [
        ("game", "Red Dead Redemption 2"), ("persona", "mixed"), ("character", ""),
        ("story_point", ""), ("voice_notes", "")]}


def set_status(vid, status, error=None):
    db.run("UPDATE videos SET status=?, error=? WHERE id=?", (status, error, vid))


def process(vid):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    work = DATA / "work" / str(vid)
    work.mkdir(parents=True, exist_ok=True)
    try:
        set_status(vid, "downloading")
        src = work / "source.mp4"
        drive.download(v["drive_id"], src)
        sidecar = None
        _, subs = drive.videos_and_sidecars(drive.folder_id(db.get("folder_id")))
        sub = subs.get(Path(v["name"]).stem)
        if sub:
            sidecar = drive.read_text(sub["id"])

        set_status(vid, "listening")
        length = scenes.video_length(src)
        segs = transcript.get_transcript(src, work, sidecar)
        windows = guard.speech_windows(segs)
        (work / "transcript.json").write_text(json.dumps(segs, indent=2))

        opts = options()
        set_status(vid, "watching")
        notes = scenes.analyze(src, work, opts["game"])

        set_status(vid, "writing")
        variant = evolve.pick_variant()
        db.run("UPDATE videos SET variant_id=? WHERE id=?", (variant["id"], vid))
        system = writer.build_system(variant["text"], opts)
        lines, dropped = writer.write_lines(notes, segs, windows, system, length)
        lines = evolve.refine(lines, notes, windows, system)
        (work / "dropped.json").write_text(json.dumps(dropped, indent=2))

        set_status(vid, "voicing")
        clips = voice.make_clips(lines, windows, system, work)
        out = work / "final.mp4"
        voice.mix(src, clips, out)

        for ln in lines:
            if not ln.get("dropped"):
                db.run("INSERT INTO lines(video_id,start,persona,tone,emotion,line,self_score) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (vid, ln["start"], ln["persona"], ln.get("tone"), ln.get("emotion"),
                        ln["line"], ln.get("self_score")))
        evolve.rescore_video(vid)

        set_status(vid, "uploading")
        channel = db.get("active_channel")
        yt_id = publisher.upload_private(
            out, Path(v["name"]).stem + " | Inner Voice",
            f"Inner-voice commentary over {opts['game']} gameplay.", channel)
        db.run("UPDATE videos SET yt_video_id=?, channel_id=? WHERE id=?", (yt_id, channel, vid))
        set_status(vid, "awaiting_approval")
        shutil.rmtree(work / "frames", ignore_errors=True)
        evolve.cycle()
    except Exception as e:
        traceback.print_exc()
        set_status(vid, "failed", f"{type(e).__name__}: {e}")


def approve(vid):
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    if not v or v["status"] != "awaiting_approval":
        raise ValueError("Video is not waiting for approval.")
    publisher.make_public(v["yt_video_id"], v["channel_id"])
    set_status(vid, "published")
