"""Which ElevenLabs voice speaks each persona in each genre pack."""
import os
import time

import requests

from . import db, styles

_cache = {"at": 0, "voices": []}


def resolve(pack, persona):
    """Your choice for this pack, else the pack default, else the voice from .env."""
    return (db.get(f"voice:{styles.pack_id(pack)}:{persona}")
            or styles.default_voice(pack, persona)
            or os.environ.get(f"ELEVEN_VOICE_{persona.upper()}"))


def assigned(pack):
    return {p: resolve(pack, p) for p in ("player", "character", "companion")}


def assign(pack, persona, voice_id):
    db.put(f"voice:{styles.pack_id(pack)}:{persona}", voice_id)


def list_voices():
    """Voices on the account, cached for 10 minutes."""
    if _cache["voices"] and time.time() - _cache["at"] < 600:
        return _cache["voices"]
    r = requests.get("https://api.elevenlabs.io/v2/voices", params={"page_size": 100},
                     headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, timeout=30)
    r.raise_for_status()
    _cache["voices"] = [{"id": v["voice_id"], "name": v["name"], "category": v.get("category")}
                        for v in r.json().get("voices", [])]
    _cache["at"] = time.time()
    return _cache["voices"]


def preview(voice_id, text):
    r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                      headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                      json={"text": text[:200], "model_id": os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2")},
                      timeout=60)
    r.raise_for_status()
    return r.content
