# Open World Diaries

Drop a gameplay video in a watch folder (a folder on your Mac or in Google Drive). An AI inner voice watches it, reads what is said, and speaks only in the quiet moments, feeling the world as you move through it. The finished video is uploaded to your YouTube channel as **private**, and you approve each one.

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

## Using the app

- **YouTube switch:** off by default. Videos stay on your Mac and you click **Download video** on the card. Switch it on to upload each finished video as private, or use **Upload to YouTube (private)** on a single video. Making it public is always a separate click.
- **Commentary amount:** a 1 to 10 slider (Sparse, Balanced, Chatty presets). It sets the minimum gap between lines and a target number of lines per minute. Each video can override it with **Regenerate**.
- **Regenerate and edit:** Regenerate writes new commentary from the saved scan, with no new Gemini cost. In **Lines** you can edit or remove any line, then **Re-render video**.
- **Consequences and morality:** the voice can refer back to earlier events and react to choices such as honor changes, but code rejects any reference to something that did not happen on screen.
- **10 genre styles and 6 personalities:** Western outlaw, Crime city, Survival horror, Fantasy RPG, Shooter and battle royale, Racing, Sports broadcast, Sandbox survival, Sci-fi space, and Cozy and indie. Each style sets the vocabulary, what to react to, the tone, and default voices. A personality (Balanced, Sarcastic, Poetic, Hype, Wholesome, Deadpan) is layered on top. Neither can weaken the hard guardrails.
- **Game detection:** when a new video arrives the app looks at four frames, guesses the game and the matching style, then waits for you to confirm or change it before the paid scan starts. Turn the confirmation off to start with the detected style straight away.
- **Voices per style:** pick the player, character and companion voices for each genre style from your ElevenLabs account, with a Preview button. Your own voice is the default player voice everywhere.
- **Memory:** it remembers your playstyle across videos (habits, places, running jokes). You can see and **Forget** any entry, or turn learning off.

## How the brain decides when to speak

1. **Gap planner:** speech is found first (the player's own voice keeps a 1.5 s safety buffer, game dialogue 0.6 s). The free stretches between speech are measured and sized in words, so a 3 second gap gets a micro-line of a few words and a long gap gets a full line.
2. **Emotion beats:** every scanned moment gets an emotional weight from the events found (a death or a kill weighs more than a pickup), moral choices, arriving somewhere new and small details. Local peaks become beats.
3. **Opportunities:** each beat is placed in a real gap, at or just after the moment (a beat that lands inside speech is answered right after it). Long silences get quiet atmosphere spots.
4. **Eagerness:** the slider (plus what the brain has learned) sets a bar. Only moments above the bar are chosen, strong moments may follow each other closer than weak ones, and very long droughts are broken by the best available spot.
5. **Writing:** Claude writes at most one line per chosen opportunity, sized to its room, and may skip one if nothing earns a line. Code sets the times, so lines can never land on speech.
6. **Self-check:** strong moments that ended up with no line get one more chance.

**How it evolves:** rate each video too quiet, just right or too chatty (it adjusts eagerness separately for each genre style); 👍/👎 on lines teaches which kinds of moments you like; about one video in four tries a slightly more or less eager setting and keeps it if it scores better.

## Self-improvement

Each video is scored three ways: an editor pass by Claude (weak lines get one rewrite), your thumbs up/down on each line, and YouTube's average view percentage. A challenger style prompt is generated every 5 scored videos and used on about 30% of new videos. If it beats the champion by 0.3 points over at least 3 videos, it becomes the champion. Versions are kept in the database.

## Setup

1. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` (also needs `ffmpeg` on your PATH).
2. `cp .env.example .env` and add your Gemini, Anthropic and ElevenLabs keys and three ElevenLabs voice IDs.
3. Google Cloud console: create a project, enable **Google Drive API**, **YouTube Data API v3** and **YouTube Analytics API**. Create an OAuth client of type *Desktop app*, download it as `client_secret.json` into this folder, and add your Google account as a test user on the consent screen.
4. Run `.venv/bin/uvicorn app.server:app --port 8000` and open http://localhost:8000.
5. Click **Connect YouTube**, pick the Google account and channel on the consent screen. Connect again to add another channel, then choose the active one from the dropdown.
6. Set the video folder: either a path on this Mac (the project's `inbox/` folder works) or a Google Drive folder link. Set the game and voice options, then press the big **AUTO** button.

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
