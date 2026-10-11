"""Game knowledge packs: mechanics and psychology that make the commentary sound like it knows the game."""
from .config import PROMPTS

PACKS = [
    # the first pack whose words appear in the game name wins, so the narrower names come first
    {"match": ("red dead redemption 2", "red dead redemption ii", "rdr2", "rdr 2", "red dead 2", "red dead online"),
     "file": "rdr2.md", "name": "Red Dead Redemption 2", "character": "Arthur Morgan",
     "notes": "Weary outlaw, loyal, brave, rescuing, winning"},
    {"match": ("red dead redemption", "rdr1", "rdr 1", "red dead 1", "undead nightmare"), "file": "rdr1.md", "name": "Red Dead Redemption",
     "character": "John Marston", "notes": "Gravelly, laconic, weathered, loyal to his family, dry and honest about his past"},
    {"match": ("red dead",), "file": "rdr2.md", "name": "Red Dead Redemption 2", "character": "Arthur Morgan",
     "notes": "Weary outlaw, loyal, brave, rescuing, winning"},
    {"match": ("grand theft auto vi", "grand theft auto 6", "gta vi", "gta 6", "gta6"), "file": "gta6.md", "name": "Grand Theft Auto VI",
     "character": "", "notes": "Sun-bleached, wry, satirical, real feeling under the jokes"},
    {"match": ("grand theft auto", "gta"), "file": "gta5.md", "name": "Grand Theft Auto V", "character": "",
     "notes": "Streetwise, sardonic, tired of the city, quick with a dry joke"},
]
FALLBACK_CHARACTER = "the player's current character"


def _match(game):
    g = (game or "").lower()
    return next((p for p in PACKS if any(s in g for s in p["match"])), None)


def for_game(game):
    """The knowledge text for this game, or '' for games without a pack."""
    m = _match(game)
    return (PROMPTS / "knowledge" / m["file"]).read_text() if m else ""


def name_for_game(game):
    m = _match(game)
    return m["name"] if m else None


def defaults_for(game):
    """The pack's own idea of who is speaking and in what voice, or None for a game without a pack."""
    return _match(game)
