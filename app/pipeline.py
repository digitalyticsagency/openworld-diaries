import hashlib
import json
import shutil
import time
import traceback
from pathlib import Path

from . import (beats, captions, channel, cutscenes, db, detect, evolve, gaps, guard, memory, publisher, scenes, sentiment,
               sources, styles, transcript, travel, voice, writer)
from . import report as brain_report
from . import clips as clipmod
from . import packaging, thumbnail, viral
from .config import DATA, MIN_GAP_FLOOR, TRAVEL_GAP_DEFAULT, TRAVEL_GAP_MIN


def options():
    return {k: db.get(k, d) for k, d in [
        ("game", "Red Dead Redemption 2"), ("persona", "mixed"), ("character", ""),
        ("story_point", ""), ("voice_notes", "")]}


def video_opts(v):
    """Global options, overridden by what was chosen for this particular video."""
    o = options()
    if v.get("game"):
        o["game"] = v["game"]
    o["pack"] = styles.pack_id(v.get("pack"))
    if one_voice() and o["persona"] == "mixed":
        o["persona"] = "character"   # one steady first-person voice, not three alternating ones
    o["personality"] = styles.personality_id(v.get("personality") or db.get("personality"))
    o["knowledge"] = db.get("knowledge_on", "1") == "1"
    return o


def moral_mode():
    """'both': the game's own cues first, then what the scan sees. 'cues': only what the game itself shows."""
    return "cues" if db.get("moral_mode", "both") == "cues" else "both"


def travel_gap():
    """Seconds between thoughts in pure walking or riding: from the travel pace slider (thoughts a minute), else the default."""
    try:
        v = float(db.get("travel_lpm", "") or 0)
    except ValueError:
        v = 0
    return max(TRAVEL_GAP_MIN, 60.0 / v) if v > 0 else TRAVEL_GAP_DEFAULT


def daydream_on():
    return db.get("daydream", "1") == "1"


def start(vid, pack, personality, game):
    """The user confirmed (or changed) the style: save it and let processing continue."""
    db.run("UPDATE videos SET pack=?, personality=?, game=? WHERE id=?",
           (styles.pack_id(pack), styles.personality_id(personality), (game or "").strip() or None, vid))


def amount():
    return int(db.get("amount", 8))


def youtube_on():
    return db.get("youtube_publish", "0") == "1"


def workdir(vid):
    return DATA / "work" / str(vid)


def final_path(vid):
    return workdir(vid) / "final.mp4"


def caption_cfg():
    """The caption look chosen in Voice settings."""
    return captions.clean_cfg({
        "dialogue": {"style": db.get("cap_style_dialogue"), "pos": db.get("cap_pos_dialogue")},
        "commentary": {"style": db.get("cap_style_commentary"), "pos": db.get("cap_pos_commentary")},
        "size": db.get("cap_size")})


def one_voice():
    """One consistent voice for all commentary (the Character voice). On by default."""
    return db.get("one_voice", "1") == "1"


def target_lpm():
    """Manual lines per minute, or None when the emotion beats decide."""
    try:
        v = float(db.get("target_lpm", "") or 0)
    except ValueError:
        return None
    return v if v > 0 else None


def cap_commentary():
    return db.get("cap_commentary", "1") == "1"


def cap_dialogue():
    return db.get("cap_dialogue", "1") == "1"


def cutscene_quiet():
    """Stay silent and hide captions during cutscenes. On by default."""
    return db.get("cutscene_quiet", "1") == "1"


def cut_state(vid):
    return cutscenes.load((db.one("SELECT cutscenes FROM videos WHERE id=?", (vid,)) or {}).get("cutscenes"))


def save_cuts(vid, c):
    db.run("UPDATE videos SET cutscenes=? WHERE id=?", (json.dumps(c), vid))


def ensure_cuts(vid, notes, src):
    """Detect cutscenes once per video (the automatic result is kept apart from the user's edits)."""
    if (db.one("SELECT cutscenes FROM videos WHERE id=?", (vid,)) or {}).get("cutscenes") is None:
        c = cutscenes.empty()
        set_status(vid, "cutscenes")
        c["auto"] = cutscenes.detect(src, notes)["ranges"]
        save_cuts(vid, c)
    return cut_state(vid)


def active_cuts(c):
    return cutscenes.effective(c) if cutscene_quiet() else []


def drop_in_blocks(lines, blocks):
    """Mark lines that fall inside a cutscene as not used. Returns how many were dropped."""
    n = 0
    for ln in lines:
        if ln.get("dropped") or not blocks:
            continue
        shown = guard.read_seconds(ln["line"]) if ln.get("silent") else guard.est_duration(ln["line"])
        if cutscenes.overlaps(blocks, ln["start"], ln["start"] + shown):
            ln["dropped"], ln["note"] = True, "during a cutscene"
            n += 1
    return n


def plan_commentary(all_beats, free, windows, length, eager, target, silent_on, extra=(), quiet_fn=None, travel_gap=None):
    """Pick what to say and where. With a manual target the possible spots are built at full eagerness, so
    the target can actually be reached; the result also says what the video's room allows at most."""
    supply = max(eager, 15.0) if target else eager
    opps, blocked = beats.opportunities(all_beats, free, length, supply, quiet_fn, travel_gap)
    opps = sorted(opps + list(extra), key=lambda o: o["t"])
    silent = beats.silent_opportunities(all_beats, windows, length, supply) if silent_on else []
    chosen = beats.plan(opps, silent, eager, length, target)
    most = beats.plan(*[beats_ for beats_ in (beats.opportunities(all_beats, free, length, 15.0, quiet_fn, travel_gap)[0] + list(extra),
                                              beats.silent_opportunities(all_beats, windows, length, 15.0) if silent_on else [])],
                      15.0, length, 999)
    return chosen, blocked, round(len(most) / (length / 60.0), 1)


def _finish(vid, lines, clips, segs, src, blocks=None):
    """Mix the voice into the game audio, then burn the captions on top."""
    work = workdir(vid)
    mixed = work / "mixed.mp4"
    voice.mix(src, clips, mixed)
    set_status(vid, "captioning")
    secs = {round(s, 2): d for s, _, d in clips}
    com = captions.commentary_items(lines, secs) if cap_commentary() else []
    dlg = captions.dialogue_items(segs, blocks) if cap_dialogue() else []
    captions.burn(mixed, final_path(vid), dlg, com, work, progress=progress_cb(vid, "Captions"), cfg=caption_cfg())
    mixed.unlink(missing_ok=True)
    voice.verify_av(final_path(vid))


WORKING = {"downloading", "detecting", "listening", "watching", "cutscenes", "writing", "voicing", "captioning", "uploading", "clipping"}


def set_status(vid, status, error=None):
    """Record the status and when it began. started_at marks the start of a whole working spell."""
    now = time.time()
    prev = db.one("SELECT status, started_at FROM videos WHERE id=?", (vid,))
    if status in WORKING:
        started = prev["started_at"] if prev and prev["status"] in WORKING and prev["started_at"] else now
    else:
        started = None
    db.run("UPDATE videos SET status=?, error=?, status_at=?, started_at=?, progress=NULL WHERE id=?",
           (status, error, now, started, vid))


def _fmt(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def progress_cb(vid, noun):
    """Returns f(done, total) that stores 'noun done/total, about M:SS left'."""
    t0 = time.time()

    def cb(done, total):
        done, total = (int(done), int(total)) if total > 60 and noun == "Captions" else (done, total)
        left = (time.time() - t0) / done * (total - done) if done else None
        text = f"{noun} {done}/{total}" + (f" · about {_fmt(left)} left" if left is not None and done < total else "")
        db.run("UPDATE videos SET progress=? WHERE id=?", (text, vid))
    return cb


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


def fingerprint(path):
    """Size plus a hash of the first and last megabytes: identifies the same file uploaded twice."""
    size = Path(path).stat().st_size
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 20))
        f.seek(max(0, size - (1 << 20)))
        h.update(f.read(1 << 20))
    return h.hexdigest()


def _reuse_scan(vid, src, work):
    """If this exact file was already listened to and scanned, copy those results instead of paying again."""
    fp = fingerprint(src)
    db.run("UPDATE videos SET fingerprint=? WHERE id=?", (fp, vid))
    for o in db.rows("SELECT id FROM videos WHERE fingerprint IS NULL AND id!=?", (vid,)):
        other_src = workdir(o["id"]) / "source.mp4"  # videos from before fingerprints existed
        if other_src.exists():
            db.run("UPDATE videos SET fingerprint=? WHERE id=?", (fingerprint(other_src), o["id"]))
    twin = db.one("SELECT id FROM videos WHERE fingerprint=? AND id!=? ORDER BY id", (fp, vid))
    while twin:
        other = workdir(twin["id"])
        if (other / "scenes.json").exists() and (other / "transcript.json").exists():
            for name in ("scenes.json", "transcript.json"):
                if not (work / name).exists():
                    shutil.copyfile(other / name, work / name)
            return twin["id"]
        twin = db.one("SELECT id FROM videos WHERE fingerprint=? AND id>? AND id!=? ORDER BY id", (fp, twin["id"], vid))
    return None


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
        _reuse_scan(vid, src, work)
        if not v["pack"]:
            if not v["suggestion"]:
                set_status(vid, "detecting")
                sug = detect.detect(src, work)
                db.run("UPDATE videos SET suggestion=?, game=COALESCE(game, ?) WHERE id=?",
                       (json.dumps(sug), None if sug["game"] == "unknown" else sug["game"], vid))
                v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
            if db.get("ask_style", "1") == "1":
                set_status(vid, "choose_style")
                return
            sug = json.loads(v["suggestion"])
            start(vid, sug["pack"], db.get("personality"), v["game"])
            v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
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
            o = video_opts(v)
            scenes.analyze(src, work, o["game"], styles.PACKS[o["pack"]]["hints"],
                           progress=progress_cb(vid, "Frames"))
            shutil.rmtree(work / "frames", ignore_errors=True)
        _generate(vid)
    _guarded(vid, run)


def _generate(vid, amt=None, persona=None, pack=None, personality=None, game=None):
    if pack or personality or game:
        cur = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        start(vid, pack or cur["pack"], personality or cur["personality"], game or cur["game"])
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    work = workdir(vid)
    src = work / "source.mp4"
    notes, segs = _load(vid)
    length = scenes.video_length(src)
    segs = transcript.within(segs, length)
    windows = guard.speech_windows(segs)
    opts = video_opts(v)
    if persona:
        opts["persona"] = persona
    explicit = bool(amt)
    amt = int(amt or amount())

    # Cutscenes first: nothing is said, thought or captioned over them
    cuts = active_cuts(ensure_cuts(vid, notes, src))
    blocks = cutscenes.padded(cuts) if cuts else []
    speech = windows
    windows = cutscenes.merge([list(w) for w in speech] + blocks)      # where the voice may not speak

    # Plan: where can lines go, which moments are worth one, how eager should the brain be
    set_status(vid, "writing")
    kw = evolve.kind_weights()
    try:
        tones = sentiment.ensure(work, segs)           # how other people's words land on Arthur
    except Exception:
        tones = []
    all_beats = sorted(beats.find_beats(notes, kw, moral_mode()) + beats.speech_beats(segs, tones, kw), key=lambda b: b["t"])
    all_beats = [b for b in all_beats if not cutscenes.overlaps(blocks, b["t"], b["t"] + 0.01)]
    free = gaps.free_gaps(windows, length)
    after = beats.after_cutscene(cuts, free, segs)
    cfg = channel.settings(db.get)
    hook = viral.hook_opportunity(free, viral.strongest_moment(all_beats, length)) if cfg["hook_on"] else None
    after_hook = hook["t"] + hook["max_duration"] + 0.8 if hook else 0.0      # the welcome follows the cold open
    intro = channel.intro_opportunity(free, length, cfg, after=after_hook) if cfg["intro_on"] else None
    outro = channel.outro_opportunity(free, length, cfg, intro) if cfg["outro_on"] else None
    cliffs = viral.cliffhangers(all_beats, notes, free, length) if cfg["cliff_on"] else []
    extra = after + [o for o in (hook, intro, outro) if o] + cliffs
    quiet_fn = beats.travel_quiet_fn(notes) if daydream_on() else (lambda t: False)
    arm, offset = evolve.choose_arm(explicit)
    eager = amt + evolve.density_bias(opts["pack"]) + offset
    chosen, blocked, possible = plan_commentary(all_beats, free, cutscenes.subtract_ranges(speech, blocks), length,
                                                eager, target_lpm(), cap_commentary(), extra=extra, quiet_fn=quiet_fn,
                                                travel_gap=travel_gap())
    travel_kinds = travel.assign(chosen, notes)
    n_catch = channel.assign_catchphrases(chosen, cfg["phrases"], length, vid) if cfg["catch_on"] else 0
    db.run("UPDATE videos SET arm=? WHERE id=?", (arm, vid))

    system = writer.build_system(_style(v)["text"], opts)
    profile = memory.profile()
    lines, dropped = writer.write_lines(notes, segs, windows, system, length, chosen, profile=profile,
                                        progress=progress_cb(vid, "Sections"), blocks=blocks)
    lines, filled = writer.fill_missed(notes, segs, windows, system, lines, chosen, profile=profile, blocks=blocks)
    lines = evolve.refine(lines, notes, windows, system, min_gap=MIN_GAP_FLOOR, blocks=blocks)
    lines = channel.fix_lines(lines, chosen, cfg)      # the welcome and the like-and-subscribe really say so, once
    db.run("UPDATE videos SET stats=? WHERE id=?", (json.dumps({
        "beats": len(all_beats), "blocked_by_speech": len(blocked), "gaps": len(free),
        "micro_gaps": sum(1 for g in free if g["kind"] == "micro"), "planned": len(chosen),
        "lines": len(lines), "filled": filled, "eagerness": round(eager, 1), "arm": arm,
        "slider": amt, "learned": round(eager - amt - offset, 1),
        "silent": sum(1 for l in lines if l.get("silent")), "target_lpm": target_lpm(),
        "per_minute": round(len(lines) / (length / 60), 1), "possible_per_minute": possible,
        "cutscenes": len(cuts), "cutscene_seconds": cutscenes.total_seconds(cuts), "reactions": len(after),
        "daydreams": sum(1 for l in lines if "daydream" in (l.get("beat_kinds") or "")),
        "hook": any(l.get("opp_id") == "hook" for l in lines),
        "cliffhangers": sum(1 for l in lines if "cliffhanger" in (l.get("beat_kinds") or "")),
        "intro": any(l.get("opp_id") == "intro" for l in lines), "outro": any(l.get("opp_id") == "outro" for l in lines),
        "travel": travel_kinds, "travel_gap": travel_gap(), "catchphrases": n_catch, "speech_reactions": sum(1 for l in lines if "npc_" in (l.get("beat_kinds") or "")),
        "knowledge": bool(opts.get("knowledge")) and bool(__import__("app.knowledge", fromlist=["x"]).name_for_game(opts["game"]))}), vid))
    (work / "dropped.json").write_text(json.dumps(dropped, indent=2))
    chs = viral.chapters(notes, length)
    (work / "chapters.json").write_text(json.dumps({"chapters": chs, "text": viral.chapters_text(chs)}, indent=2))
    try:
        (work / "report.json").write_text(json.dumps(brain_report.build(
            notes, lines, dropped, all_beats, blocked, length, speech, cuts, travel_kinds), indent=2))
    except Exception:
        traceback.print_exc()   # the report is a courtesy and must never break a video

    set_status(vid, "voicing")
    clips = voice.make_clips(lines, windows, system, work, pack=opts["pack"],
                             progress=progress_cb(vid, "Voice lines"))
    # Save the lines before the long caption step: if anything stops during captioning, the commentary and the
    # voice clips survive and Redo captions can finish the video without writing or speaking anything again.
    _save_lines(vid, lines)
    _finish(vid, lines, clips, segs, src, blocks)
    evolve.rescore_video(vid)
    set_status(vid, "ready")
    try:
        package(vid)
    except Exception:
        traceback.print_exc()   # packaging is a courtesy and must never break a finished video
    try:
        memory.update(vid)
        evolve.cycle()
    except Exception:
        traceback.print_exc()  # learning must never break a finished video
    if youtube_on() and db.get("active_channel"):
        _publish(vid)


def _save_lines(vid, lines):
    db.run("DELETE FROM lines WHERE video_id=?", (vid,))
    for ln in lines:
        db.run("INSERT INTO lines(video_id,start,persona,tone,emotion,line,self_score,max_duration,"
               "dropped,trigger_t,callback_t,beat_kinds,salience,silent,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (vid, ln["start"], ln["persona"], ln.get("tone"), ln.get("emotion"), ln["line"],
                ln.get("self_score"), ln.get("max_duration"), 1 if ln.get("dropped") else 0,
                ln.get("trigger_t"), ln.get("callback_t"), ln.get("beat_kinds"), ln.get("salience"),
                1 if ln.get("silent") else 0, ln.get("note")))


def package(vid):
    """Titles, description, tags, a pinned comment and three thumbnails, from what the video really contains."""
    v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
    work = workdir(vid)
    notes, _ = _load(vid)
    length = scenes.video_length(work / "source.mp4")
    o = video_opts(v)
    rows = [dict(r) for r in db.rows("SELECT start,line,silent,dropped,salience FROM lines WHERE video_id=? ORDER BY start", (vid,))]
    fs = packaging.facts(o["game"], styles.PACKS[o["pack"]]["name"], length, notes,
                         beats.find_beats(notes, evolve.kind_weights(), moral_mode()), viral.chapters(notes, length), rows)
    pkg = packaging.generate(fs)
    mi = pkg["thumb_moment"]
    at = fs["moments"][mi]["t"] if mi is not None else length / 2
    frame = thumbnail.grab(work / "source.mp4", at, work / "thumb_src.jpg")
    for var, text in zip(thumbnail.VARIANTS, pkg["thumb_texts"]):
        thumbnail.render(frame, text, var, work / f"thumb_{var}.jpg")
    pkg["thumb_at"] = round(at, 1)
    (work / "packaging.json").write_text(json.dumps(pkg, indent=2))
    return pkg


def _saved_clips(vid, rows):
    """[(start, file, seconds)] for the voiced lines, if the saved voice files still match them, else []."""
    voiced = [r for r in rows if not r["dropped"] and not r["silent"]]
    files = sorted((workdir(vid) / "clips").glob("c_*.mp3"))
    return [(r["start"], f, voice.duration(f)) for r, f in zip(voiced, files)] if len(files) == len(voiced) else []


def make_clips(vid):
    """The best-moments reel and vertical Shorts, from the finished video. The commentary is the one already written."""
    prev = db.one("SELECT status FROM videos WHERE id=?", (vid,))["status"]

    def run():
        work = workdir(vid)
        notes, segs = _load(vid)
        length = scenes.video_length(work / "source.mp4")
        rows = [dict(r) for r in db.rows("SELECT * FROM lines WHERE video_id=? ORDER BY start", (vid,))]
        saved = _saved_clips(vid, rows)
        secs = {round(s, 2): d for s, _, d in saved}
        spans = [(r["start"], r["start"] + (secs.get(round(r["start"], 2)) or guard.read_seconds(r["line"])))
                 for r in rows if not r["dropped"] and (r["silent"] or secs.get(round(r["start"], 2)))]
        cuts = active_cuts(cut_state(vid))
        blocks = cutscenes.padded(cuts) if cuts else []
        found = [b for b in beats.find_beats(notes, evolve.kind_weights(), moral_mode()) if not cutscenes.overlaps(blocks, b["t"], b["t"] + 0.01)]
        reel, shorts = clipmod.plan_reel(found, length, list(cuts), spans), clipmod.plan_shorts(found, length, list(cuts), spans)
        for old in work.glob("short_*.mp4"):
            old.unlink()
        (work / "reel.mp4").unlink(missing_ok=True)
        total, done = (1 if reel else 0) + len(shorts), 0
        cb = progress_cb(vid, "Clips")
        if reel:
            cb(0, total)
            clipmod.cut_reel(final_path(vid), [r["win"] for r in reel], work / "reel.mp4")
            done += 1
        cfg = caption_cfg()
        cfg = captions.clean_cfg({"dialogue": {"style": cfg["dialogue"]["style"], "pos": "lower"},
                                  "commentary": {"style": cfg["commentary"]["style"], "pos": "upper"}, "size": "medium"})
        for i, s in enumerate(shorts, 1):
            cb(done, total)
            a, b = s["win"]
            raw = work / f"short_{i}_raw.mp4"
            clipmod.vertical(work / "source.mp4", final_path(vid), s["win"], raw)
            shift = lambda items: [{**it, "start": it["start"] - a, "end": it["end"] - a} for it in items if it["end"] > a and it["start"] < b]
            com = shift(captions.commentary_items(rows, secs)) if cap_commentary() else []
            dlg = shift(captions.dialogue_items(segs, blocks)) if cap_dialogue() else []
            captions.burn(raw, work / f"short_{i}.mp4", dlg, com, work, cfg=cfg)
            raw.unlink(missing_ok=True)
            done += 1
        cb(done, total)
        (work / "clips.json").write_text(json.dumps({
            "reel": {"moments": [{"t": r["t"], "win": r["win"], "why": r["why"]} for r in reel],
                     "seconds": round(sum(y - x for x, y in (r["win"] for r in reel)), 1)} if reel else None,
            "shorts": [{"n": i, "win": s["win"], "t": s["t"], "why": s["why"], "seconds": round(s["win"][1] - s["win"][0], 1)}
                       for i, s in enumerate(shorts, 1)]}, indent=2))
        set_status(vid, prev)
    set_status(vid, "clipping")
    try:
        run()
    except Exception as e:
        traceback.print_exc()
        set_status(vid, prev, f"Clips failed: {type(e).__name__}: {e}"[:300])


def recaption(vid):
    """New caption look on the same lines and the same voice clips: no new speech is made."""
    def run():
        v = db.one("SELECT * FROM videos WHERE id=?", (vid,))
        notes, segs = _load(vid)
        work = workdir(vid)
        rows = db.rows("SELECT * FROM lines WHERE video_id=? ORDER BY start", (vid,))
        voiced = [r for r in rows if not r["dropped"] and not r["silent"]]
        files = sorted((work / "clips").glob("c_*.mp3"))
        if len(files) != len(voiced):
            raise ValueError("The saved voice clips do not match the lines, so use Rebuild video instead.")
        clips = [(r["start"], f, voice.duration(f)) for r, f in zip(voiced, files)]
        lines = [{"start": r["start"], "line": r["line"], "persona": r["persona"], "silent": bool(r["silent"]),
                  "dropped": bool(r["dropped"]), "max_duration": r["max_duration"]} for r in rows]
        cuts = active_cuts(cut_state(vid))
        blocks = cutscenes.padded(cuts) if cuts else []
        set_status(vid, "voicing")
        _finish(vid, lines, clips, segs, work / "source.mp4", blocks)
        set_status(vid, "ready")
    _guarded(vid, run)


def regenerate(vid, amt=None, persona=None, pack=None, personality=None, game=None):
    """New commentary from the saved scan: no Gemini calls, no re-download."""
    _guarded(vid, lambda: _generate(vid, amt, persona, pack, personality, game))


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
            if r.get("silent"):
                lines.append({"id": r["id"], "start": r["start"], "persona": r["persona"], "emotion": r["emotion"],
                              "line": r["line"], "silent": True, "max_duration": r["max_duration"] or 6.0})
                continue
            lines.append({"id": r["id"], "start": r["start"], "persona": r["persona"],
                          "emotion": r["emotion"], "line": r["line"],
                          "max_duration": max(1.0, min(room, nxt, 20.0))})
        cuts = active_cuts(ensure_cuts(vid, notes, workdir(vid) / "source.mp4"))
        blocks = cutscenes.padded(cuts) if cuts else []
        windows = cutscenes.merge([list(w) for w in windows] + blocks)
        # lines are judged afresh: one dropped for a cutscene that was since removed gets its place back
        drop_in_blocks(lines, blocks)
        set_status(vid, "voicing")
        opts = video_opts(v)
        system = writer.build_system(_style(v)["text"], opts)
        clips = voice.make_clips(lines, windows, system, workdir(vid), pack=opts["pack"],
                                 progress=progress_cb(vid, "Voice lines"))
        _finish(vid, lines, clips, segs, workdir(vid) / "source.mp4", blocks)
        for ln in lines:
            db.run("UPDATE lines SET line=?, dropped=?, note=? WHERE id=?",
                   (ln["line"], 1 if ln.get("dropped") else 0, ln.get("note"), ln["id"]))
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
                                     f"Inner-voice commentary over {video_opts(v)['game']} gameplay.", channel)
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
