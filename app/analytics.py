"""Learn from the audience-retention curve which kinds of moments keep people watching.

For every line in a published video the curve says how many viewers were still there when it started and ten seconds
later. A kind whose lines lose fewer viewers than the video's usual ten seconds is doing its job; one that loses more is not.
The result nudges how strongly each kind of moment is weighted when the next video is planned. Pure functions
apart from the one YouTube call, and nothing changes until a kind has enough lines to mean something."""
import datetime
import json

from . import db

WINDOW = 10.0          # seconds after a line in which the drop in viewers is measured
MIN_N = 5              # lines of one kind before it is allowed to move a weight
GAIN = 6.0             # how strongly a mean effect moves the weight
LOW, HIGH = 0.85, 1.2
UNLEARNABLE = {"intro", "outro", "ambient", "micro", "full"}


def fetch_curve(channel_id, yt_id):
    """[(share of the video, share of viewers still watching)] from YouTube Analytics."""
    from .google_auth import service
    r = service("youtubeAnalytics", "v2", channel_id).reports().query(
        ids="channel==MINE", startDate="2020-01-01", endDate=datetime.date.today().isoformat(),
        metrics="audienceWatchRatio", dimensions="elapsedVideoTimeRatio",
        filters=f"video=={yt_id};audienceType==ORGANIC").execute()
    return sorted((float(a), float(b)) for a, b in (r.get("rows") or []))


def at(curve, ratio):
    """The curve's value at a point, by straight-line interpolation, held flat beyond the ends."""
    if not curve:
        return None
    ratio = max(curve[0][0], min(curve[-1][0], ratio))
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= ratio <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return curve[-1][1]


def kind_of(beat_kinds):
    """The one kind a line counts for: the specific travel kind, a hook or suspense line, else the moment's main kind."""
    ks = [k for k in (beat_kinds or "").split(",") if k]
    for k in ks:
        if k.startswith("travel_") or k in ("hook", "cliffhanger", "recap"):
            return k
    ks = [k for k in ks if k not in UNLEARNABLE and k != "daydream"]
    return ks[0] if ks else None


def effects(lines, curve, length):
    """[(kind, effect)]: how much better (+) or worse (-) than the video's usual window the viewers held after each line."""
    if not curve or length < 3 * WINDOW:
        return []
    drops = [at(curve, (t + WINDOW) / length) - at(curve, t / length) for t in [i * WINDOW for i in range(int(length // WINDOW) - 1)]]
    base = sum(drops) / len(drops)
    out = []
    for ln in lines:
        k = kind_of(ln.get("beat_kinds"))
        t = ln.get("start") or 0
        if not k or ln.get("dropped") or t + WINDOW > length:
            continue
        out.append((k, (at(curve, (t + WINDOW) / length) - at(curve, t / length)) - base))
    return out


def _totals():
    try:
        return json.loads(db.get("retention_kinds", "{}"))
    except ValueError:
        return {}


def learn():
    """Fold every published video's curve in once. Returns how many videos were added."""
    done = set(json.loads(db.get("retention_done", "[]")))
    totals, added = _totals(), 0
    for v in db.rows("SELECT id, yt_video_id, channel_id FROM videos WHERE yt_video_id IS NOT NULL AND status='published'"):
        if v["id"] in done:
            continue
        try:
            curve = fetch_curve(v["channel_id"], v["yt_video_id"])
        except Exception as e:             # not enough viewers yet, or the scope is missing: try again next time
            print("retention skipped:", v["yt_video_id"], e)
            continue
        from .pipeline import scenes as scenes_mod, workdir      # imported here: pipeline imports evolve, which imports this
        src = workdir(v["id"]) / "source.mp4"
        if not curve or not src.exists():
            continue
        lines = db.rows("SELECT start, beat_kinds, COALESCE(dropped,0) dropped FROM lines WHERE video_id=?", (v["id"],))
        for k, e in effects(lines, curve, scenes_mod.video_length(src)):
            t = totals.setdefault(k, {"n": 0, "sum": 0.0})
            t["n"] += 1
            t["sum"] += e
        done.add(v["id"])
        added += 1
    db.put("retention_kinds", json.dumps(totals))
    db.put("retention_done", json.dumps(sorted(done)))
    return added


def weights():
    """{kind: multiplier} for kinds with enough lines, 1.0 elsewhere (left out)."""
    out = {}
    for k, t in _totals().items():
        if t["n"] >= MIN_N:
            out[k] = round(max(LOW, min(HIGH, 1 + GAIN * t["sum"] / t["n"])), 2)
    return out


def report():
    """For the app: each learned kind with its count, mean effect and weight."""
    w = weights()
    rows = [{"kind": k, "n": t["n"], "mean": round(t["sum"] / t["n"], 4), "weight": w.get(k)} for k, t in _totals().items()]
    return sorted(rows, key=lambda r: -r["mean"])
