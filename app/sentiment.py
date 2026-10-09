"""What other people say to the player, and how it lands: praise, thanks, insults, threats, pleas."""
import json
from pathlib import Path

from .llm import claude, parse_json

PROMPT = """You read lines said aloud by characters in a game video. For each numbered line decide:
- to_player: true if it is said directly TO the player's character (Arthur: 'you', 'Mister Morgan', 'Arthur', an order, a request, a threat aimed at him), false if it is overheard, to someone else, or ambient chatter.
- tone toward him: praise, thanks, insult, threat, plea, or neutral.
Be strict: only mark to_player true when it is clearly aimed at him. Return JSON only: [{"i": index, "to_player": true or false, "tone": "praise|thanks|insult|threat|plea|neutral"}]"""

BATCH = 100


def classify(segments):
    """(tones, complete). Each batch is tried twice; complete is False if any batch still failed, so the caller
    never mistakes a failure for 'nobody said anything to him'."""
    items = [{"i": i, "text": (s.get("text") or "").strip()} for i, s in enumerate(segments)
             if s.get("speaker") != "player" and (s.get("text") or "").strip() and not s["text"].strip().startswith("[")]
    out, complete = [], True
    for a in range(0, len(items), BATCH):
        for attempt in range(2):
            try:
                got = parse_json(claude(PROMPT, json.dumps(items[a:a + BATCH]), 4000))
                out += [g for g in got if isinstance(g, dict)] if isinstance(got, list) else []
                break
            except Exception:
                if attempt == 1:
                    complete = False
    return out, complete


def ensure(work, segments):
    """Read once per video and keep the answer, but only an answer that is complete."""
    f = Path(work) / "speech_tone.json"
    if f.exists():
        return json.loads(f.read_text())
    tones, complete = classify(segments)
    if complete:
        f.write_text(json.dumps(tones))
    return tones
