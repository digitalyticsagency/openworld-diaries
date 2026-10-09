"""Game knowledge packs: mechanics and psychology that make the commentary sound like it knows the game."""
from .config import PROMPTS

PACKS = [
    # (substrings of the game name, file, display name)
    (("red dead",), "rdr2.md", "Red Dead Redemption 2"),
]


def _match(game):
    g = (game or "").lower()
    return next((p for p in PACKS if any(s in g for s in p[0])), None)


def for_game(game):
    """The knowledge text for this game, or '' for games without a pack."""
    m = _match(game)
    return (PROMPTS / "knowledge" / m[1]).read_text() if m else ""


def name_for_game(game):
    m = _match(game)
    return m[2] if m else None
