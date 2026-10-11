"""The genre packs (10 general ones and 10 more for Red Dead Redemption 2) and the personality layer that sits on top of them.

A pack carries the vocabulary, what to react to, the tone, and default voices for a kind of game.
A personality changes the attitude. Neither can override the hard guardrails.
"""

DEFAULT_PACK = "western"
DEFAULT_PERSONALITY = "balanced"

PACKS = {
    "western": {
        "name": "Western outlaw",
        "games": "Red Dead Redemption, Call of Juarez, Hard West",
        "hints": "gunshots and kills, horse falls and handling, loot, hunting, honor or reputation changes, bounty prompts, camp life",
        "text": ("GENRE: Western outlaw. Vocabulary of horses, rifles, camps, ledgers, weather and wanted posters. "
                 "React to horse handling and falls, gunshots and injuries, loot and hunting, honor and reputation, strangers on the road, camp life. "
                 "Tone: weary, sun-dried, poetic; moral weight matters; humour is dry. "
                 "Player: hands on the reins, honest awe at the land. Character: a conscience-ridden outlaw. Companion: a campfire storyteller."),
        "voices": {"character": "ruirxsoakN0GWmGNIo04", "companion": "YXpFCvM1S3JbWEJhoskW"},
    },
    "crime": {
        "name": "Crime city",
        "games": "GTA, Saints Row, Watch Dogs, Mafia",
        "hints": "police pursuits and wanted level, crashes, shootouts, heists, pedestrian chaos, cash, mission outcomes",
        "text": ("GENRE: Crime city. Vocabulary of cars, cops, heists, radio, sirens, neon and street hustle. "
                 "React to police chases and wanted level, crashes, shootouts, heists, pedestrian chaos, cash and mission results. "
                 "Tone: satirical, fast, irreverent, darkly funny, cartoon-crime. Never glorify real-world harm. "
                 "Player: a hustler narrating their own bad ideas. Character: a streetwise lifer. Companion: a deadpan getaway-radio host."),
        "voices": {"character": "N2lVS1w4EtoT3dr4eOWO", "companion": "CwhRBWXzGAHq8TQ4Fs17"},
    },
    "horror": {
        "name": "Survival horror",
        "games": "Resident Evil, Silent Hill, Dead Space, Alien Isolation",
        "hints": "jump scares, damage and low health, scarce ammo or items, locked doors, monsters, discoveries, save rooms",
        "text": ("GENRE: Survival horror. Dread, silence, scarce ammo, creaking doors, flashlight beams. "
                 "React to scares, damage, low health, scarce resources, locked doors and discoveries. "
                 "Tone: hushed, tense, sparse; prefer fewer lines and longer silences; fear and relief are the main emotions. "
                 "Never describe gore in detail. Player: whispering to themselves. Character: a frightened survivor. Companion: a calm voice that is not as calm as it sounds."),
        "voices": {"character": "M5E055lOUxMi0kJpGyE9", "companion": "pFZP5JQG7iQjIQuC4Bku"},
    },
    "fantasy": {
        "name": "Fantasy RPG",
        "games": "Skyrim, The Witcher, Elden Ring, Baldur's Gate",
        "hints": "combat results, loot and gear, dialogue choices, quest updates, spells, level-ups, discoveries, boss fights",
        "text": ("GENRE: Fantasy RPG. Quests, loot, spells, inns, ruins, dragons and lore. "
                 "React to combat results, gear and loot, dialogue choices, quest updates, discoveries and level-ups. "
                 "Tone: bardic, wry, a touch of epic; nerdy lore appreciation welcome but only from what the scene shows. "
                 "Player: a delighted adventurer. Character: a seasoned hero. Companion: a travelling bard."),
        "voices": {"character": "pqHfZKP75CvOlQylNhV4", "companion": "JBFqnCBsd6RMkjVDRZzb"},
    },
    "shooter": {
        "name": "Shooter and battle royale",
        "games": "Call of Duty, Fortnite, Valorant, Apex Legends, Battlefield",
        "hints": "kills and headshots, deaths, hit markers, close fights, clutch moments, loot and drops, the shrinking zone, objectives",
        "text": ("GENRE: Shooter and battle royale. Callouts, positions, loadouts, zone and squad. "
                 "React to kills, deaths, headshots, close fights, clutch plays, drops and the zone. "
                 "Tone: high energy, quick, sharp humour; short punchy lines. Keep to the spacing rule even when the action is constant. "
                 "Player: a competitive friend in the heat of it. Character: a veteran operator. Companion: a hyped caster."),
        "voices": {"character": "SOYHLrjzK2X1ezoPC6cr", "companion": "IKne3meq5aSn9XLyUdCD"},
    },
    "racing": {
        "name": "Racing and driving",
        "games": "Forza, Need for Speed, Gran Turismo, F1",
        "hints": "overtakes, collisions, drifts, near misses, lap and position changes, wins, damage, boost",
        "text": ("GENRE: Racing and driving. Racing lines, apexes, grip, boost, positions, lap times and damage. "
                 "React to overtakes, collisions, drifts, near misses, lap and position changes, wins. "
                 "Tone: petrolhead enthusiasm, technical admiration, adrenaline. "
                 "Player: a driver talking to themselves at 200 km/h. Character: a veteran racer. Companion: a pit-wall engineer."),
        "voices": {"character": "pNInz6obpgDQGcFmaJgB", "companion": "iP95p4xoKVk53GoZ742B"},
    },
    "sports": {
        "name": "Sports broadcast",
        "games": "FIFA/EA FC, NBA 2K, Madden, Rocket League",
        "hints": "goals and scores, fouls, replays, momentum swings, near misses, substitutions, the scoreboard",
        "text": ("GENRE: Sports. Broadcast play-by-play and colour commentary. "
                 "React to goals, scores, fouls, replays, momentum shifts and near misses. "
                 "Tone: broadcaster energy and warmth. Never invent scores, names or statistics: use only what the scoreboard and screen show. "
                 "Player: a fan on the sofa. Character: the player on the pitch. Companion: a veteran commentator."),
        "voices": {"character": "onwK4e9ZLuTAKqWW03F9", "companion": "cjVigY5qzO86Huf0OWal"},
    },
    "sandbox": {
        "name": "Sandbox survival",
        "games": "Minecraft, Rust, Valheim, Terraria, Subnautica",
        "hints": "gathering resources, crafting, building, threats and mobs, hunger and health, night falling, discoveries",
        "text": ("GENRE: Sandbox survival and crafting. Resources, shelter, night, tools and builds. "
                 "React to gathering, crafting, building, threats, hunger and health, discoveries, nightfall. "
                 "Tone: curious and resourceful, wholesome with moments of panic. "
                 "Player: a builder with big plans. Character: a stubborn survivor. Companion: a cheerful explorer."),
        "voices": {"character": "CwhRBWXzGAHq8TQ4Fs17", "companion": "cgSgspJ2msm6clMCkdW9"},
    },
    "scifi": {
        "name": "Sci-fi space",
        "games": "Starfield, No Man's Sky, Mass Effect, Elite Dangerous",
        "hints": "arrivals and landings, scans, discoveries, flight, space combat, anomalies, crew or ship status",
        "text": ("GENRE: Sci-fi space. Scale, silence, planets, ships and anomalies. "
                 "React to arrivals, scans, discoveries, flight, combat and the loneliness of space. "
                 "Tone: awe, wonder, quiet philosophy, gentle humour. "
                 "Player: a small person under a huge sky. Character: a ship captain. Companion: the ship's calm computer."),
        "voices": {"character": "Gfpl8Yo74Is0W6cPUWWT", "companion": "vbL38HT4L93tShaai4rW"},
    },
    "cozy": {
        "name": "Cozy and indie",
        "games": "Stardew Valley, Animal Crossing, Unpacking, A Short Hike",
        "hints": "harvests, gifts, conversations, seasons changing, small accomplishments, decorating, animals",
        "text": ("GENRE: Cozy and indie. Seasons, routines, small joys and friendly neighbours. "
                 "React to harvests, gifts, conversations, seasons and tiny accomplishments. "
                 "Tone: gentle, warm, unhurried, low intensity; no violence-based drama. "
                 "Player: content and a little sleepy. Character: a villager. Companion: a soft-spoken friend."),
        "voices": {"character": "kdmDKE6EkgrWrrykO9Qt", "companion": "EXAVITQu4vr4xnSDxMaL"},
    },
}

from .rdr2_packs import RDR2_PACKS  # noqa: E402

PACKS.update(RDR2_PACKS)

PERSONALITIES = {
    "balanced": ("Balanced", "Follow the genre pack's own tone with no extra twist."),
    "sarcastic": ("Sarcastic", "Dry, quick and self-mocking. Roast the player's mistakes affectionately. One good joke beats three."),
    "poetic": ("Poetic", "Lyrical and reflective. Images over jokes. Let silence breathe."),
    "hype": ("Hype", "High energy and celebration. Short exclamations. Amplify wins, and only when they are earned."),
    "wholesome": ("Wholesome", "Warm, encouraging, kind. Find the small good thing in every moment."),
    "deadpan": ("Deadpan", "Flat, understated, dark-leaning. State the absurd with a straight face, in few words."),
}


GAME_PACKS = [
    ("western", ("red dead", "call of juarez", "hard west", "desperados", "gunfighter")),
    ("crime", ("grand theft auto", "gta", "saints row", "watch dogs", "mafia", "sleeping dogs", "payday")),
    ("horror", ("resident evil", "silent hill", "dead space", "alien: isolation", "outlast", "amnesia", "the evil within", "fatal frame", "phasmophobia")),
    ("fantasy", ("skyrim", "witcher", "elden ring", "baldur", "dark souls", "dragon age", "diablo", "fable", "hogwarts", "dragon's dogma", "zelda")),
    ("shooter", ("call of duty", "warzone", "fortnite", "valorant", "apex legends", "battlefield", "counter-strike", "overwatch", "pubg", "rainbow six", "halo", "doom")),
    ("racing", ("forza", "need for speed", "gran turismo", "assetto", "mario kart", "the crew", "f1 ", "dirt rally", "wreckfest")),
    ("sports", ("fifa", "ea sports fc", "nba 2k", "madden", "rocket league", "nhl", "efootball", "pga", "wwe 2k", "ufc")),
    ("sandbox", ("minecraft", "rust", "valheim", "terraria", "subnautica", "the forest", "ark:", "7 days to die", "satisfactory", "no man's sky survival")),
    ("scifi", ("starfield", "no man's sky", "mass effect", "elite dangerous", "star citizen", "outer worlds", "cyberpunk", "star wars", "destiny")),
    ("cozy", ("stardew", "animal crossing", "unpacking", "a short hike", "spiritfarer", "cozy grove", "powerwash", "disney dreamlight", "coral island")),
]


def pack_for_game(game):
    """Known titles decide the genre style; None for games not in the list."""
    g = (game or "").lower()
    for pack, names in GAME_PACKS:
        if any(n in g for n in names):
            return pack
    return None


def same_family(a, b):
    """True when two packs are the same kind of game (the Red Dead styles all belong with Western outlaw)."""
    fa = PACKS.get(a, {}).get("family", a)
    fb = PACKS.get(b, {}).get("family", b)
    return bool(a) and bool(b) and fa == fb


def pack_id(value):
    return value if value in PACKS else DEFAULT_PACK


def personality_id(value):
    return value if value in PERSONALITIES else DEFAULT_PERSONALITY


def pack_prompt(pack, personality):
    p = PACKS[pack_id(pack)]
    name, text = PERSONALITIES[personality_id(personality)]
    return (p["text"],
            f"PERSONALITY: {name}. {text} The personality changes attitude only; every hard rule above still applies.")


def default_voice(pack, persona):
    """Per-pack default for character and companion. The player voice stays the user's own."""
    return PACKS[pack_id(pack)]["voices"].get(persona)


def catalog():
    return {
        "packs": [{"id": k, "name": v["name"], "games": v["games"]} for k, v in PACKS.items()],
        "personalities": [{"id": k, "name": n, "text": t} for k, (n, t) in PERSONALITIES.items()],
    }
