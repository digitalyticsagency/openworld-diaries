# Open World Diaries

Drop a gameplay video in a Google Drive folder. An AI inner voice watches it, reads what is said, and speaks only in the quiet moments, feeling the world as you move through it. The finished video is uploaded to your YouTube channel as **private**, and you approve each one.

```
Drive folder ──► download ──► transcript (who speaks, when) ──► scene + interaction notes (Gemini)
      ▲                                  │                                   │
      │                                  └─────────► locked silence windows ─┤
      │                                                                      ▼
 AUTO on/off                        inner-voice lines (Claude) ──► guardrails (code) ──► editor pass
                                                                             │
 YouTube (private) ◄── mix + duck (ffmpeg) ◄── voice (ElevenLabs) ◄──────────┘
      │
      └── your 👍/👎 + view retention ──► score ──► prompt A/B ──► better prompts
```

## Guardrails

- **Silence during speech.** Every stretch of speech, from the player or a game character, locks a window plus 1.5 s either side. The model is told not to speak there, and code removes any line that touches one. After text-to-speech, the real audio length is checked again.
- **Speech is context.** The transcript is read so the voice understands mood and running jokes, then reacts in a later gap. It never repeats what was said.
- **Grounded feeling.** Pain, joy, fear, grief, relief, pride and disgust are only allowed next to an on-screen event the vision pass actually found (damage, loot, a fall, an encounter). No event, no strong emotion.
- **Spacing and length.** 6 s minimum between lines, 25 words maximum.
- These rules live in `prompts/guardrails.md` and `app/guard.py`. The self-improvement loop **cannot edit them**; it only evolves the style block.

## Self-improvement

Each video is scored three ways: an editor pass by Claude (weak lines get one rewrite), your thumbs up/down on each line, and YouTube's average view percentage. A challenger style prompt is generated every 5 scored videos and used on about 30% of new videos. If it beats the champion by 0.3 points over at least 3 videos, it becomes the champion. Versions are kept in the database.

## Setup

1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (also needs `ffmpeg` on your PATH).
2. `cp .env.example .env` and add your Gemini, Anthropic and ElevenLabs keys and three ElevenLabs voice IDs.
3. Google Cloud console: create a project, enable **Google Drive API**, **YouTube Data API v3** and **YouTube Analytics API**. Create an OAuth client of type *Desktop app*, download it as `client_secret.json` into this folder, and add your Google account as a test user on the consent screen.
4. Run `.venv/bin/uvicorn app.server:app --port 8000` and open http://localhost:8000.
5. Click **Connect YouTube**, pick the Google account and channel on the consent screen. Connect again to add another channel, then choose the active one from the dropdown.
6. Paste the Drive folder link, set the game and voice options, and press the big **AUTO** button.

To give the player's words to the AI exactly, put an `.srt` or `.vtt` file with the same name as the video in the same Drive folder. Without one, the audio is transcribed automatically.

## Things to know

- A Google Cloud project that has not passed Google's API audit can only upload as private. That matches the approval flow here. The "Approve" button calls the public switch and may be refused by YouTube until your project is verified.
- Transcript timestamps from audio transcription are approximate. The 1.5 s buffer absorbs small errors; an `.srt` file is more precise.
- Gameplay footage belongs to its publisher. Check the game's content policy before monetising.
- The Gemini model default is `gemini-3.8-flash`. Change `GEMINI_MODEL` in `.env` if your ID differs.

## Tests

`.venv/bin/python -m unittest discover -s tests` (no network or keys needed; the AI calls are faked).

## Working on it in Google Antigravity

Open this folder as a project. `AGENTS.md` describes the layout and the rules an agent must not break.
