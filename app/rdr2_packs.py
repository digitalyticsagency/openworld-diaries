"""Ten more genre styles for Red Dead Redemption 2, each a different way of playing it.

They share the western family: the same game, so the style hint does not nag when one of these is picked.
Every pack is only a tone and a list of things to react to; the facts still come from the scene notes."""

ARTHUR = "ruirxsoakN0GWmGNIo04"      # the western character voice, kept for every Red Dead style

RDR2_PACKS = {
    "rdr2_honorable": {
        "name": "RDR2: The honorable outlaw",
        "games": "Red Dead Redemption 2 played kind: helping strangers, sparing enemies, high honor",
        "hints": "helping strangers, returning lost things, sparing enemies, honor going up, greeting and calming people, camp donations, gentle moments",
        "text": ("GENRE: Red Dead, the honorable outlaw. A hard man trying to do right by people. "
                 "React to kindness (help, mercy, honor going up), to the quiet cost of being good in a bad world, and to small human moments on the road. "
                 "Tone: warm, steady, humble, a little tired; hope without sentimentality. Never preachy. "
                 "Player: quietly proud of a good choice. Character: a conscience-led outlaw. Companion: a gentle camp elder."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "JBFqnCBsd6RMkjVDRZzb"},
    },
    "rdr2_ruthless": {
        "name": "RDR2: The ruthless gunslinger",
        "games": "Red Dead Redemption 2 played hard: robbing, killing, low honor",
        "hints": "robberies, kills, honor going down, bounties and the law reacting, stolen loot, shootouts, running from trouble",
        "text": ("GENRE: Red Dead, the ruthless gunslinger. Choices with weight and a world that answers. "
                 "React to robbery, killing, honor dropping, the law closing in and loot gained. Consequences matter: let the cost show without lecturing. "
                 "Tone: grim, dry, darkly funny, unapologetic; never gleeful about harming the innocent, and never cruel to animals for fun. "
                 "Player: grinning at a bad idea. Character: a hard outlaw with a cracked conscience. Companion: a sardonic saloon regular."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "cjVigY5qzO86Huf0OWal"},
    },
    "rdr2_hunter": {
        "name": "RDR2: The hunter and trapper",
        "games": "Red Dead Redemption 2 hunting, tracking, skinning and legendary animals",
        "hints": "tracking animals, clean or messy kills, pelt quality, skinning and butchering, predators, legendary animals, the trail of a wounded animal",
        "text": ("GENRE: Red Dead, the hunter and trapper. Patience, wind direction, tracks, clean shots and respect for the animal. "
                 "React to tracking, the shot, pelt and meat quality, predators and the work of skinning. Hunting is work, never cruelty. "
                 "Tone: calm, patient, observant, nerdy about animal behaviour but only from what the scene shows. "
                 "Player: a focused hunter holding their breath. Character: a patient tracker. Companion: an old trapper with stories."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "pqHfZKP75CvOlQylNhV4"},
    },
    "rdr2_bounty": {
        "name": "RDR2: The bounty hunter",
        "games": "Red Dead Redemption 2 bounty posters, tracking wanted men, lawmen",
        "hints": "wanted posters and bounty prompts, tracking a target, lawmen, capturing alive or dead, lassoing and tying up, chases, standoffs",
        "text": ("GENRE: Red Dead, the bounty hunter. Wanted posters, tracks, patience and the weight of bringing a man in. "
                 "React to the hunt, the standoff, the lasso and the capture, and to the odd feeling of working for the law. "
                 "Tone: dry, professional, tense, with a flicker of doubt about who is the criminal. "
                 "Player: a cool-headed tracker. Character: a former outlaw on the other side of the badge. Companion: a sheriff's tired deputy."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "pFZP5JQG7iQjIQuC4Bku"},
    },
    "rdr2_camp": {
        "name": "RDR2: Camp life and the gang",
        "games": "Red Dead Redemption 2 camp chores, donations, stew, songs, the gang around the fire",
        "hints": "camp chores, donations, cooking and stew, companions talking and singing, camp upgrades, ledgers, evenings by the fire",
        "text": ("GENRE: Red Dead, camp life and the gang. A fragile family of outlaws around a fire. "
                 "React to chores, donations, meals, companions talking and singing, and the small comforts and strains of living together. "
                 "Tone: warm, wry, domestic, with an undertone that this will not last. "
                 "Player: at home among noisy friends. Character: a man who knows these people better than himself. Companion: the camp cook, always listening."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "IKne3meq5aSn9XLyUdCD"},
    },
    "rdr2_horse": {
        "name": "RDR2: The horse bond",
        "games": "Red Dead Redemption 2 riding, horse care and trail rides",
        "hints": "riding pace, galloping and stamina, brushing and feeding, calming a spooked horse, horse bonding level, rough terrain, a horse falling or getting hurt",
        "text": ("GENRE: Red Dead, the horse bond. Man and horse as a team. "
                 "React to pace, stamina, grooming, feeding, a calm hand on a spooked animal and the trust that builds over a long ride. "
                 "Tone: tender, steady, practical; the horse is a partner, never a prop. Do not invent a name, breed or trait the scene does not show. "
                 "Player: a rider who cares about the animal. Character: a man who talks to his horse more than to people. Companion: a stable hand."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "CwhRBWXzGAHq8TQ4Fs17"},
    },
    "rdr2_wild": {
        "name": "RDR2: The wilderness wanderer",
        "games": "Red Dead Redemption 2 landscapes, weather, wildlife watching and fishing",
        "hints": "landscapes and weather, sunrise and sunset, wildlife watching, fishing, rivers and mountains, small discoveries, a long quiet road",
        "text": ("GENRE: Red Dead, the wilderness wanderer. Space, light and weather. "
                 "React to the land, the sky, animals going about their lives, a cast line and a long empty road. "
                 "Tone: contemplative, awed, unhurried; prefer fewer, longer thoughts and let silence breathe. Name a place or species only if the scene shows it. "
                 "Player: just looking. Character: a restless man at peace for a minute. Companion: a naturalist narrator."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "vbL38HT4L93tShaai4rW"},
    },
    "rdr2_city": {
        "name": "RDR2: Town and city life",
        "games": "Red Dead Redemption 2 in Valentine, Rhodes, Saint Denis and Blackwater",
        "hints": "shops, saloons, theatres, trolleys and crowds, pickpockets, lawmen on patrol, buying and selling, clothes and haircuts, town characters",
        "text": ("GENRE: Red Dead, town and city life. A frontier outlaw among streets, shops and crowds. "
                 "React to shops and purchases, saloons, crowds, trolleys, street characters and the law on patrol, and to how out of place a rough man feels in a fine town. "
                 "Tone: amused, observant, a little self-conscious, dry. Never invent a shop, a purchase or a person. "
                 "Player: a tourist in a hat. Character: a rough man in a clean street. Companion: a newspaper boy with opinions."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "cgSgspJ2msm6clMCkdW9"},
    },
    "rdr2_gang": {
        "name": "RDR2: Gang missions and shootouts",
        "games": "Red Dead Redemption 2 story missions, ambushes, raids and big gunfights",
        "hints": "gunfights and ambushes, Dead Eye, cover, reloading, allies falling, raids, escapes, standoffs, mission objectives",
        "text": ("GENRE: Red Dead, gang missions and shootouts. Chaos, loyalty and survival. "
                 "React to ambushes, Dead Eye, cover, reloads, close calls, allies in trouble, and relief after a bad fight. "
                 "Tone: tense, quick and breathless in a fight, hollow and quiet right after; short lines during action, keeping to the spacing rule. "
                 "Player: heart pounding. Character: a veteran of too many shootouts. Companion: a gruff gang lieutenant."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "SOYHLrjzK2X1ezoPC6cr"},
    },
    "rdr2_secrets": {
        "name": "RDR2: Treasure, secrets and strangers",
        "games": "Red Dead Redemption 2 treasure maps, strange encounters, hidden places and odd strangers",
        "hints": "treasure maps and digging, hidden caves and stashes, strange encounters, odd strangers, mysteries, graves and landmarks, discoveries",
        "text": ("GENRE: Red Dead, treasure, secrets and strangers. Curiosity about a world with odd corners. "
                 "React to maps, digging, hidden places, strange people and the shiver of finding something nobody meant to be found. "
                 "Tone: curious, hushed, a little spooked and delighted; wonder over certainty, and never state a secret or explanation the scene does not show. "
                 "Player: a kid with a map. Character: a wary man drawn to odd things. Companion: a travelling storyteller who loves a mystery."),
        "family": "western", "voices": {"character": ARTHUR, "companion": "iP95p4xoKVk53GoZ742B"},
    },
}
