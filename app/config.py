import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = Path(os.environ.get("OWD_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
PROMPTS = ROOT / "prompts"
TOKENS = DATA / "tokens"
TOKENS.mkdir(exist_ok=True)
CLIENT_SECRET = Path(os.environ.get("GOOGLE_CLIENT_SECRET", ROOT / "client_secret.json"))

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
ELEVEN_MODEL = os.environ.get("ELEVEN_MODEL", "eleven_multilingual_v2")

FRAME_EVERY = 3          # seconds between sampled frames
FRAME_BATCH = 10         # frames per Gemini call
WINDOW_SECONDS = 300     # video chunk size sent to Claude
SPEECH_BUFFER = 1.5      # silence kept around any speech, seconds
MIN_GAP = 6.0            # minimum silence between inner-voice lines
MAX_WORDS = 25
WORDS_PER_SEC = 2.5
POLL_SECONDS = 60
CHALLENGER_SHARE = 0.3   # share of videos that use the challenger prompt
PROMOTE_MARGIN = 0.3     # score points a challenger must win by
PROMOTE_MIN_VIDEOS = 3
MUTATE_EVERY = 5         # scored videos between new challengers
