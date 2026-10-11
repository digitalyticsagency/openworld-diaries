"""Optional background ambience for Red Dead and GTA videos: a quiet bed that fits the scene, in quiet moments only.

Opt-in. The beds are short ElevenLabs sound effects made once from a text prompt and kept, then looped under the
game's own audio. A bed is only ever placed where the scan shows nothing happening (travel or camp, nobody near, no
fight, no chase), never over in-game speech or a cutscene, and it fades in and out. Planning is pure code."""
from pathlib import Path

from . import knowledge, voice

BED_SECONDS = 22.0              # the longest sound effect the service makes in one go
TARGET_DB = -34.0               # a bed is brought to about this average level: far below the game audio
MUSIC_TARGET_DB = -26.0         # the owner's own track sits a little higher: it replaces the game's music
MIN_SEGMENT = 8.0               # a bed shorter than this is not worth starting
SPEECH_MARGIN = 0.6
FADE = 1.5

BEDS = {
    "rdr": {
        "camp_night": "A campfire crackling softly at night with crickets and a distant owl",
        "snow_wind": "Cold wind over a snowy mountain, soft steady gusts, no voices",
        "rain": "Light steady rain on leaves and wet ground with faint distant thunder",
        "fog_morning": "A quiet misty morning with distant birds and a soft breeze",
        "plains_day": "A warm breeze over open grassland with birds and insects, no voices",
        "town": "A small old-west town: faint distant chatter, creaking wooden boards, a far-off piano",
        "swamp": "A humid swamp at dusk with frogs and insects",
        "desert": "Dry desert wind with a distant hawk, no voices",
    },
    "gta": {
        "city_day": "A busy city street: distant traffic, car horns and the murmur of crowds",
        "city_night": "A city at night with distant sirens and a faint bass thump from a club",
        "highway": "A steady car engine hum on a highway with cars passing",
        "beach": "Ocean waves on a beach with distant gulls and a soft crowd",
        "country": "Quiet countryside with crickets and a distant dog",
        "rain_city": "Rain on a city street with tyres hissing on wet asphalt",
    },
}


def family_for(game):
    """'rdr' for Red Dead, 'gta' for Grand Theft Auto, None for any other game."""
    name = knowledge.name_for_game(game) or ""
    if "Red Dead" in name:
        return "rdr"
    if "Grand Theft Auto" in name:
        return "gta"
    return None


def _text(n, *keys):
    return " ".join(str(n.get(k) or "") for k in keys).lower()


def calm(n):
    """True when the scan shows nothing happening: travel or camp, nobody near, no fight, no chase."""
    if n.get("interactions") or n.get("moral_events") or n.get("animals") or (n.get("casualty") or {}).get("who") not in (None, "", "none"):
        return False
    if any(npc.get("behavior") in ("hostile", "afraid") for npc in n.get("npcs") or []):
        return False
    try:
        if int(float(n.get("wanted_level") or 0)) >= 1:
            return False
    except (TypeError, ValueError):
        pass
    if n.get("is_cutscene") is True or n.get("player_state") in ("aiming", "in dialogue", "in menu", "cutscene", "sprinting"):
        return False
    return (n.get("activity") or "travel") in ("travel", "camp", "other")


def bed_for(n, family):
    """The bed that fits one calm moment, or None. First rule that fits wins."""
    sky, where = _text(n, "time_weather"), _text(n, "location")
    night = "night" in sky or "dusk" in sky
    if family == "rdr":
        if n.get("activity") == "camp":
            return "camp_night"
        if "snow" in sky or "blizzard" in sky or "snow" in where:
            return "snow_wind"
        if "rain" in sky or "storm" in sky:
            return "rain"
        if "fog" in sky or "mist" in sky:
            return "fog_morning"
        if "swamp" in where or "bayou" in where or "lagoon" in where:
            return "swamp"
        if "desert" in where or "cholla" in where or "gaptooth" in where:
            return "desert"
        if any(w in where for w in ("town", "city", "saint denis", "valentine", "rhodes", "strawberry", "blackwater", "tumbleweed", "armadillo")):
            return "town"
        return "camp_night" if night else "plains_day"
    if family == "gta":
        if "rain" in sky or "storm" in sky:
            return "rain_city"
        if "beach" in where or "pier" in where or "ocean" in where or "coast" in where or "boardwalk" in where:
            return "beach"
        if any(w in where for w in ("countryside", "farm", "mountain", "desert", "forest", "hill", "canyon")):
            return "country"
        if n.get("player_state") == "driving" and any(w in where for w in ("highway", "freeway", "road", "route")):
            return "highway"
        return "city_night" if night else "city_day"
    return None


def _minus(a, b, ranges):
    """[a, b] with the given ranges (and a little margin) taken out: a list of pieces."""
    pieces = [(a, b)]
    for x, y in sorted(ranges):
        x, y = x - SPEECH_MARGIN, y + SPEECH_MARGIN
        nxt = []
        for p, q in pieces:
            if y <= p or x >= q:
                nxt.append((p, q))
                continue
            if x > p:
                nxt.append((p, x))
            if y < q:
                nxt.append((y, q))
        pieces = nxt
    return pieces


def plan(notes, length, windows, family, quiet_only=True):
    """[{start, end, bed}]: where a bed plays. By default only in calm moments, never in speech or a cutscene, each at least
    MIN_SEGMENT long. With quiet_only False (ambience replaces the game audio) it plays through action too, still never
    over speech or a cutscene."""
    if not family or not notes:
        return []
    notes = sorted(notes, key=lambda n: n["t"])
    spans, busy = [], []
    for i, n in enumerate(notes):
        lo = (notes[i - 1]["t"] + n["t"]) / 2 if i else max(0.0, n["t"] - 1.5)
        hi = (n["t"] + notes[i + 1]["t"]) / 2 if i + 1 < len(notes) else min(length, n["t"] + 1.5)
        bed = bed_for(n, family) if (calm(n) or not quiet_only) else None
        if not calm(n) and quiet_only:
            busy.append([lo, hi])           # a fight or a chase keeps its own sound, with a little room either side
        if bed:
            if spans and spans[-1]["bed"] == bed and lo - spans[-1]["end"] <= 0.5:
                spans[-1]["end"] = hi
            else:
                spans.append({"start": lo, "end": hi, "bed": bed})
    out = []
    for s in spans:
        for a, b in _minus(s["start"], min(s["end"], length), list(windows) + busy):
            if b - a >= MIN_SEGMENT:
                out.append({"start": round(a, 2), "end": round(b, 2), "bed": s["bed"]})
    return out


def _level(path, target=TARGET_DB):
    import subprocess
    m = voice.mean_db(path)
    if m is None:
        return
    tmp = Path(path).with_suffix(".lvl.mp3")
    subprocess.run(["ffmpeg", "-y", "-i", str(path), "-af", f"volume={max(-45.0, min(24.0, target - m)):.2f}dB", str(tmp)],
                   check=True, capture_output=True)
    tmp.replace(path)


def generate(prompt, out):
    """One bed from a text prompt, through ElevenLabs sound effects."""
    from . import llm
    r = voice._post("https://api.elevenlabs.io/v1/sound-generation", {"xi-api-key": llm.key("ELEVENLABS_API_KEY")},
                    {"text": prompt, "duration_seconds": BED_SECONDS, "prompt_influence": 0.6})
    r.raise_for_status()
    Path(out).write_bytes(r.content)
    _level(out)


def prepare(segments, family, cache_dir):
    """[(start, end, file)]: the beds this video needs, made once and kept for every later video."""
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    out, files = [], {}
    for s in segments:
        if s["bed"] not in files:
            f = cache / f"{family}_{s['bed']}.mp3"
            if not f.exists():
                generate(BEDS[family][s["bed"]], f)
            files[s["bed"]] = f
        out.append((s["start"], s["end"], files[s["bed"]]))
    return out
