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
SCAN_FINE = 2.0         # busiest moments are scanned this often, seconds
SCAN_CALM = 6.0         # quiet stretches are scanned this often at most
SCAN_BUSY_SHARE = 0.35  # share of the video that counts as busy, judged by how much the picture changes
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

# commentary planner
BUFFER_GAME = 0.6        # silence kept around in-game dialogue (the player's own voice keeps SPEECH_BUFFER)
BUFFER_OTHER = 0.8       # caption files and unlabelled speech
LEAD = 0.35              # pause after speech before a line starts
TAIL = 0.3               # pause kept before the next speech
MIN_USABLE_GAP = 1.8     # gaps shorter than this cannot hold even a micro-line
MIN_GAP_FLOOR = 2.5      # hard floor between any two lines, whatever the planner decides
REACTION_WINDOW = 10.0   # a line may come this long after the beat it reacts to
ARM_SHARE = 0.25         # share of videos used to test a slightly different eagerness
ARM_OFFSET = 0.8

# captions and silent thoughts
READ_WORDS_PER_SEC = 3.0   # how fast an on-screen thought can be read
READ_LEAD = 0.8            # minimum time a thought stays up on top of the reading time
SILENT_MAX_WORDS = 12
CAPTION_FPS = 24

# cutscenes
CUT_STEP = FRAME_EVERY       # seconds between checked frames
CUT_MERGE_GAP = 6.0          # cutscene pieces closer than this are one cutscene
CUT_PAD = 1.2                # extra silence kept before and after a cutscene
CUT_MIN_SECONDS = 4.0        # shorter blips need the black bars to count
AFTER_CUT_MIN = 8.0          # a cutscene this long earns a short reaction when it ends

# pacing and moral logic
DAYDREAM_SPACING = 20.0      # a quiet-travel daydream is never closer than this to another daydream

# travel thoughts
TRAVEL_GAP_DEFAULT = 32.0    # seconds between thoughts in pure walking or riding, when the user has not set a pace
TRAVEL_GAP_MIN = 10.0        # never closer than this, whatever the slider says
