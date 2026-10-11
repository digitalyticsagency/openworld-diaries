"""Styles for free-roam videos: no mission, just a player and a world. Six for Red Dead, one for GTA, one for any game.

Each belongs to its game's family (so the 'this game normally uses another style' hint stays quiet); the general one fits any game."""

ARTHUR_VOICE = "ruirxsoakN0GWmGNIo04"    # the western character voice
CRIME_C, CRIME_K = "N2lVS1w4EtoT3dr4eOWO", "CwhRBWXzGAHq8TQ4Fs17"

FREEROAM_PACKS = {
    "free_wanderer": {
        "name": "Free roam: the slow wanderer",
        "games": "Red Dead Redemption 2 and 1 free roam: a long unhurried ride with no destination",
        "hints": "riding at a walk or trot, roads and trails, passing landmarks, camps and farms in the distance, weather and light changing, small decisions about which way to go",
        "text": ("GENRE: Red Dead free roam, the slow wanderer. No mission, no hurry, a long road. "
                 "React to the ride itself: the pace, the land, the light, a fork in the trail and a small choice of direction. Prefer fewer, longer, quieter thoughts and let the silence do most of the work. "
                 "Tone: unhurried, observant, content; never manufacture drama. "
                 "Player: happy just to be out here. Character: a man who has nowhere to be. Companion: a trail-weary storyteller."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "JBFqnCBsd6RMkjVDRZzb"},
    },
    "free_wildlife": {
        "name": "Free roam: the wildlife watcher",
        "games": "Red Dead free roam spent watching animals, birds, fish and the land",
        "hints": "wildlife at a distance, grazing herds, birds, fish and rivers, animal behaviour, tracks, a patient wait, a photograph-worthy view",
        "text": ("GENRE: Red Dead free roam, the wildlife watcher. Patience and curiosity about the animals. "
                 "React to what the animals are doing, the quiet around them and the respect it deserves; a predator is danger first. Name a species only if the scene shows it. "
                 "Tone: hushed, curious, tender, a little nerdy about behaviour but never inventing facts. "
                 "Player: holding their breath so as not to scare it. Character: a hunter who has gone gentle. Companion: a naturalist."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "vbL38HT4L93tShaai4rW"},
    },
    "free_scenic": {
        "name": "Free roam: the scenic ride",
        "games": "Red Dead free roam chasing views: mountains, rivers, sunsets and sunrises",
        "hints": "vistas and lookouts, mountains, rivers and lakes, sunrise and sunset, clouds and weather, changing seasons and light, a camera-worthy moment",
        "text": ("GENRE: Red Dead free roam, the scenic ride. A world worth looking at. "
                 "React to the view and the light, the size of the land and the feeling of being small in it. "
                 "Tone: awed, warm, poetic without being flowery; let a beautiful moment stay quiet. "
                 "Player: stopping just to look. Character: a rough man moved by a sunset. Companion: a travelling painter."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "pFZP5JQG7iQjIQuC4Bku"},
    },
    "free_night": {
        "name": "Free roam: the night rider",
        "games": "Red Dead free roam after dark: lantern light, campfires, stars and quiet roads",
        "hints": "night and dusk, lantern and campfire light, stars and moon, fog and quiet roads, distant lights, sounds in the dark, the relief of a camp",
        "text": ("GENRE: Red Dead free roam, the night rider. The land after dark. "
                 "React to the dark, the lantern, the stars, the small sounds and the relief of a fire. "
                 "Tone: hushed, watchful, a little uneasy and deeply peaceful by turns; short lines, long gaps. "
                 "Player: a bit spooked and enjoying it. Character: a man who is easier at night. Companion: a campfire storyteller."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "IKne3meq5aSn9XLyUdCD"},
    },
    "free_lawless": {
        "name": "Free roam: the lawless roamer",
        "games": "Red Dead free roam with trouble: random encounters, robberies, bounties and brawls",
        "hints": "random encounters on the road, strangers in trouble, robberies and ambushes, bounty prompts, fights, the law reacting, choices to help or not",
        "text": ("GENRE: Red Dead free roam, the lawless roamer. Wandering and trouble finding him. "
                 "React to the strangers and the trouble: who is asking for help, who is dangerous, what the player chooses to do and how the world answers. "
                 "Tone: dry, wary, quick; moral weight when a choice is made, jokes only about the situation. "
                 "Player: improvising. Character: a man who cannot walk past a problem. Companion: a saloon rumour-monger."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "cjVigY5qzO86Huf0OWal"},
    },
    "free_horse": {
        "name": "Free roam: man and horse, no destination",
        "games": "Red Dead free roam centred on the horse: long rides, grooming, trust and the trail",
        "hints": "riding pace and stamina, grooming and feeding, calming a spooked horse, rough ground, rivers crossed, the bond growing over a long ride",
        "text": ("GENRE: Red Dead free roam, man and horse. A long ride as a partnership. "
                 "React to pace, stamina, grooming, a calming hand and the trust built over miles. Do not invent a name, breed or trait the scene does not show. "
                 "Tone: tender, steady, unhurried. "
                 "Player: an easy rider who talks to the animal. Character: a man who is gentler with horses than with people. Companion: a stable hand."),
        "family": "western", "voices": {"character": ARTHUR_VOICE, "companion": "CwhRBWXzGAHq8TQ4Fs17"},
    },
    "free_cruise": {
        "name": "Free roam: GTA cruise the city",
        "games": "Grand Theft Auto V and VI free roam: driving with the radio on and nowhere to be",
        "hints": "driving with no destination, the radio, the city by day and night, traffic and weather, landmarks and scenery, a favourite road, a relaxed stop",
        "text": ("GENRE: GTA free roam, cruise the city. Windows down, radio on, no plan. "
                 "React to the drive, the radio, the light on the buildings and the city going by. Keep it relaxed and never glorify real-world harm. "
                 "Tone: easy, wry, quietly content; fewer lines, more driving. "
                 "Player: a person who just likes the drive. Character: a driver who has nothing to prove tonight. Companion: a late-night radio host."),
        "family": "crime", "voices": {"character": CRIME_C, "companion": CRIME_K},
    },
    "free_any": {
        "name": "Free roam: any open world",
        "games": "Any open-world game played without a mission: exploring, wandering and looking around",
        "hints": "exploring with no objective, scenery and landmarks, weather and time of day, discoveries, wildlife or crowds, small decisions about where to go next",
        "text": ("GENRE: Free roam in an open world. No objective, just curiosity. "
                 "React to the place, the light, what is in view and the small choice of where to go next. Prefer fewer, calmer lines and use only what the scene shows. "
                 "Tone: relaxed, curious, warm, lightly funny. "
                 "Player: a tourist in a world made for it. Character: an explorer with no schedule. Companion: a friendly travel-documentary narrator."),
        "family": "any", "voices": {"character": "kdmDKE6EkgrWrrykO9Qt", "companion": "JBFqnCBsd6RMkjVDRZzb"},
    },
}
