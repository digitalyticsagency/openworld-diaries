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
- **30 genre styles and 6 personalities:** ten general styles, ten for Grand Theft Auto (the heist crew, police chases and wanted stars, the street hustler, car culture, pure chaos, story missions, planes and boats, off the grid, nightlife and neon, and sunshine state crime for GTA VI) plus ten for Red Dead Redemption 2 (honorable outlaw, ruthless gunslinger, hunter and trapper, bounty hunter, camp life and the gang, the horse bond, wilderness wanderer, town and city life, gang missions and shootouts, treasure and secrets). The general ones are Western outlaw, Crime city, Survival horror, Fantasy RPG, Shooter and battle royale, Racing, Sports broadcast, Sandbox survival, Sci-fi space, and Cozy and indie. Each style sets the vocabulary, what to react to, the tone, and default voices. A personality (Balanced, Sarcastic, Poetic, Hype, Wholesome, Deadpan) is layered on top. Neither can weaken the hard guardrails.
- **Game detection:** when a new video arrives the app looks at four frames, guesses the game and the matching style, then waits for you to confirm or change it before the paid scan starts. Turn the confirmation off to start with the detected style straight away.
- **Voices per style:** pick the player, character and companion voices for each genre style from your ElevenLabs account, with a Preview button. Your own voice is the default player voice everywhere.
- **Memory:** it remembers your playstyle across videos (habits, places, running jokes). You can see and **Forget** any entry, or turn learning off.

## Pacing, grounding and game knowledge

- **Quiet by default.** A line needs something real: an action (a purchase, feeding or calming a horse, petting or hunting an animal, a predator, a fight, a choice), something said to Arthur (praise, thanks, a plea, an insult or a threat), or a mission or place change. The slider is a ceiling, never a target to fill, and generic "atmosphere" filler no longer exists.
- **Daydreams, only while travelling.** While Arthur is just walking or riding with nothing and nobody to react to, the voice may drift into a private, generic thought about a quiet future (a farm, a family someday). Never a named story character, never twice in a row on the same theme, never closer than 20 seconds apart.
- **Conscience.** An innocent casualty brings quiet remorse; an armed bandit, gang member or lawman who attacked brings grim outlaw justice with no gloating and no remorse; when it is unclear who died, it stays factual. The game's own cues (honor, bounty, labels) decide first, then what the scan sees, or only the game's cues if you choose that in settings.
- **One welcome, one like-and-subscribe, a few catchphrases.** Placed once by code, with wording you can edit, so they can never repeat between parts of the video. Nothing else may greet or ask for subscribes.
- **Continuity.** Each part of the video is told it is one continuous piece, what was already said, and how earlier lines started, and any repeated line is removed.
- **Game knowledge.** For Red Dead: shops and trading, horse and animal care, hunting and predators, honor and the gang, written down in `prompts/knowledge/rdr2.md`. Used only for what the scene confirms. Videos scanned before these details existed show a **Scan again for new details** button.

## Thinking while travelling
When he is only walking or riding, with nothing and nobody to react to, the brain has a thought every so often. It rotates five kinds, never the same kind twice in a row:
- **Noticing:** something the scan really saw (a landmark, an animal, the weather).
- **Mood:** how the trip feels, worked out by code from the hour, the weather, how long he has ridden without a stop and what just happened (a knock, a win).
- **Horse:** a short warm thought, only while riding, from the pace and the horse's state in the scan.
- **Memory:** something that happened earlier in the same video, tied to a real event time so the guard can check it.
- **Wistful:** a generic daydream about a quiet future, never naming a story character.

A kind with no real facts behind it is skipped. **Travel thoughts a minute** in Voice settings sets the pace (0 = automatic, about one every 30 seconds, never closer than 10).

The scan is adaptive: the busiest third of a video is looked at every 2 seconds, quiet stretches every 6 (busy means the picture changes a lot). Scans started before this keep their old 3 second grid so they can resume.

Each finished video has a **Brain report**: what it saw, what it said, how each of the biggest moments ended (said, silent because someone was speaking, removed by a check, or not strong enough), and why each long quiet stretch was quiet.

## Hooks, suspense and chapters
- **Cold open:** in the first seconds, one short teaser for the strongest real moment later in the video (a scan event, never something someone said), then the welcome. It never spoils the outcome. Off with the switch in Channel lines.
- **Suspense lines:** a few seconds before a big moment, a short tense line, only when the frames just before it really show something building (a hostile close by, an animal approaching, aiming, a fight starting). With no real cue there is no line, and the moment's own reaction always outranks it.
- **Honest only:** teasers and suspense lines are dropped if they use clickbait wording ("you won't believe", "shocking", "what happens next" and similar), and they may only use the facts in their reason.
- **Chapters:** timestamped chapters for the YouTube description, from the activity runs in the scan (riding, hunting, shopping, gunfight, camp...). Shown in the Brain report with a Copy button. At least three are needed, the first starts at 0:00, and never more than twelve.

## Packaging for YouTube
Every finished video gets **Packaging** (button on the video, or Make packaging): 5 title options, a description with the chapters and a note that the commentary is AI-generated, tags, a pinned-comment question, and three thumbnails (white text bottom-left, yellow on a dark band, centred on a vignette) cut from the strongest real moment. Each piece has a Copy or Download button, and Make new packaging writes a fresh set.

The model sees only a fact sheet built from the scan (game, chapters, strongest moments, animals and places really seen, a few spoken lines) and everything it returns is checked in code: no clickbait wording, titles under 100 characters and not shouting, thumbnail text of one to four words, a question that really ends in a question mark, and a plain true fallback for anything that fails.

## Clips: a best-moments reel and vertical Shorts
**Clips** on a finished video (Make clips) cuts, from the finished video:
- a **best-moments reel** of about a minute: the strongest real events, a few seconds each, in time order, with the video's own commentary and captions;
- up to five **vertical Shorts** (9:16, under a minute each): the game picture centred over a blurred copy of itself, one moment with its lead-up and reaction, with the captions drawn again large for the vertical frame (inner voice in the upper third, game dialogue in the lower).

A window never runs into a cutscene, never cuts a spoken line in half, and never overlaps another. Moments are scan events, never something someone said. While it works the video shows "Cutting the highlight clips", and it goes back to ready afterwards, even if something fails (the reason is shown).

## Growing the channel
- **Series mode** (switch and optional series name in Channel lines): each new video is numbered as an episode, every title gets "Series Ep. N:", and the video opens with a short "previously" recap right after the welcome. The recap is written only from a story note saved when the last episode finished, itself built from that episode's real facts.
- **Comments** (button on a video that is on YouTube): Check for new comments fetches them and drafts a reply for each in the channel voice. Abusive, spam, link or off-topic comments are skipped with a reason. You can edit each draft, and **nothing is posted until you press Post on that one reply** and confirm. Replies with links, hashtags, clickbait or over 280 characters are refused.
- **Watch-time learning:** after videos are published, the audience-retention curve shows which kinds of moments kept viewers watching. Once a kind has at least five lines, it nudges how strongly that kind is weighted for the next video (never more than +20% or less than -15%). Needs YouTube connected.
- **What to make next:** paste what you have seen is popular, press Suggest 5 ideas. Ideas come only from your notes, your past videos, the player memory and what kept viewers watching. Nothing is looked up online and no popularity is claimed.

## Grand Theft Auto
GTA V and GTA VI have their own knowledge packs and ten styles. The scan also reads the **wanted stars** shown on screen: two stars or more make a moment, a rise makes it stronger, a chase is never "quiet travel", and fear during a chase counts as grounded. The GTA VI pack is deliberately thin: it says only what the scene shows and claims nothing about its story, characters or map. The saved character and voice notes are the Red Dead ones by default, so for another game with a pack they are swapped for that game's own unless you set your own. Driving chapters are named "Driving".

## John Marston
Red Dead Redemption (the first game) has its own knowledge pack and speaks as **John Marston**: laconic, weathered, loyal to his family. Red Dead Redemption 2's pack also has a short note for the epilogue, where the player controls him. Every video can also have its own **Character** (optional box when you confirm the style, and next to Regenerate), for example John Marston for the epilogue of a Red Dead 2 video, while other videos keep your saved character. A character chosen for one video never changes the saved one, and John's surname is kept out of daydreams like the other story names.

## A different voice for each character
Under **Voices**, "A voice for each character" lets Arthur Morgan and John Marston have voices of their own. When a video's character is one of them, every line is spoken in that voice instead of the style's, so John sounds different from Arthur. Left on "The style's voice", the style's own is used. A video as John with no voice chosen shows a reminder.

Each of them also has his own ten daydream themes (Arthur: a cabin by a lake, a horse he never has to sell, a quiet porch; John: a ranch with a fence line of his own, a table with his family at it, teaching a boy to ride). They stay generic: real names are still never used in a daydream.

## Background ambience and the game's own audio
Under Voice settings, **Sound**:
- **Background ambience for Red Dead and GTA** (off by default): a very quiet bed that fits the scene under the game audio: a campfire at night, wind over snow, rain, fog, swamp, desert, a town for Red Dead; city traffic, a night city, a highway, a beach, countryside, rain on asphalt for GTA. It is placed only in quiet moments (travel or camp, nobody near, no fight, no chase), never over in-game speech or a cutscene, and fades in and out. The beds are short ElevenLabs sound effects, made once and kept for every later video. Other games get nothing. If a bed cannot be made, the video is made without it, and the Brain report says why.
- **The game's own audio:** a slider from 100% (as recorded) down to 0% (removed), for any game.
Both apply to new videos and to Rebuild video.

## Free roam and the game's music
For people who make a lot of free-roam videos.
- **Free-roam styles** (8): the slow wanderer, the wildlife watcher, the scenic ride, the night rider, the lawless roamer, man and horse with no destination, GTA cruise the city, and one for any open-world game. They ask for fewer, longer thoughts and let the silence do the work. The general one never triggers the "this game normally uses another style" hint.
- **Calm free-roam pace** (switch in Pacing): only the strongest moments get a line, and thoughts on the road are at least 55 seconds apart, whatever the sliders say. It is for quiet videos only: a video with more than about 4 real moments a minute (a dash, a chase, a busy town) keeps the normal pace even with the switch on, and its stats say "calm pace skipped".
- **Wandering goals:** a new travel thought that picks a small aim for the ride from something really in view ("head for the bridge", "one more ridge"). It needs a real landmark or detail in the scan, so without one it is skipped.
- **Journey chapters and titles:** a travel chapter is named by when, the weather and where it can tell ("Night ride through Grizzlies West", "Snowy ride", "Dusk drive"). Packaging knows when a video is mostly a journey and builds its titles around the journey.
- **The game's background music** (Sound, applies to new videos and Rebuild video):
  - *Keep it as it is.*
  - *Lower the music:* levels the game audio and gently expands it downward so the steady bed falls while hooves, shots and voices keep their level. It is a filter, not source separation: it works best on soft, steady music.
  - *Replace it with scene ambience* (Red Dead and GTA): the game audio is kept only where someone is speaking or a cutscene plays, and scene-matched beds play through the rest.
  - *Replace it with my own music:* upload one track (at least 5 seconds). It is levelled, looped under the whole video, and dips under the voice. The game audio is again kept only for speech and cutscenes.
  - When there is nothing to replace the music with (no ambience for the game, no track uploaded, or a bed that could not be made), the music is lowered instead and the Brain report says so. It is never left silent.

## What he is thinking about
When you confirm a video's style (next to Character), **What is he thinking about on quiet travel?** lets you tick any of eight themes: family and home, friends and loneliness, future and dreams, past and regrets, fear and doubt, gratitude and small joys, faith, luck and meaning, work, money and survival. You can add a note in your own words ("missing his brother"). After a video is made, **Thinking about...** next to Regenerate changes it for that video. A shortcut ticks every theme (**Mix of everything**), and **Save as my favourites** (or the favourites box under Pacing) makes your picks the default: new videos start with them ticked, and any video you have not chosen for uses them too. A video where you cleared the themes on purpose stays without.

Only the quiet thoughts while riding or walking follow it: every other one becomes a reflection on your themes (one theme after another, line by line), and the rest stay grounded in what the scan shows. Reactions to fights, shops, animals and speech are untouched. Every reflection is generic (no real character's name, no story fact), your note is treated as a feeling to colour the thought and never as a fact, and the choice belongs to that one video.

## Small rules that keep the commentary honest
- **A manual lines-a-minute beats the calm pace.** If you ask for a number of lines a minute, the calm free-roam pace stands aside (it does not cap the eagerness or spread the road thoughts), and the video's stats say so.
- **The closing line is never lost for want of a gap.** A video with no speech is one long gap; the outro now goes near its end instead of being skipped. When no gap has room for a sentence, a very short "Like and subscribe." is used.
- **No invented time.** A line that claims this ride has gone on for hours ("an hour ago", "been riding for hours", "since this morning") in a video that is minutes long is dropped, and the writer is told not to. Idioms and memories ("I could do this all day", "for years") are left alone, and very long recordings are not judged.

## Caption looks

Dialogue subtitles and the inner voice each have their own style and position, chosen in Voice with a live preview on a frame of your own video.

- **Styles:** Cinematic (white serif, soft outline and shadow, no box, like the game's own subtitles, the default), Classic box, Clean outline, Pop, Minimal, Storybook, Typewriter.
- **Position:** Top, Upper third, Middle, Lower third or Bottom, separately for each track. If both are set to the same place, the commentary moves just toward the middle of the screen so the two never overlap.
- **Size:** Small, Medium or Large.
- **Silent thoughts** are always shown in italics inside quotation marks, whatever the style.
- A finished video's **Redo captions** button redraws the captions in the current look using the same lines and voice clips (no new speech, so it is cheaper and quicker than Rebuild video).
- If the game footage already shows its own subtitles at the bottom, set the dialogue captions to a different position or switch them off to avoid doubled text.

## Sound

- **The audio always runs the whole video.** The voice track is padded to full length and the result follows the game audio, and every finished video is checked: if its sound ends earlier than its picture, it is reported as failed instead of being delivered.
- **One consistent voice.** On by default: every line is spoken in the Character voice of the genre style (for Red Dead, Arthur) and written in the first person. Turn it off in Voice to alternate between the three personas.
- **Even loudness.** Each spoken line is brought to the same level (spread under 1 dB), and the voice settings are steady with a fixed seed, so the same voice sounds like the same person.
- **Ducking.** While the voice speaks, the game audio is lowered so the voice sits about 12 dB above it.

## Cutscenes

Cinematics play untouched: no voice, no silent thoughts and no captions. They are found from three signals, and any one counts (when unsure, it assumes a cutscene): cinematic black bars across the top and bottom of the frame (a quick free check of the video), the vision scan's cutscene flag, and the scan's player state. Right after a long cutscene ends there is one short reaction, built only from what was said in it. On each video, the **Cutscenes** button lists the ranges so you can mark one as not a cutscene, add one by hand (for example 1:05 to 2:30), or look again. After changing ranges, press Rebuild video (lines inside new cutscenes are dropped) or Regenerate commentary.

## Captions and silent thoughts

- **Captions burned into the video:** the commentary (spoken lines in bold, silent thoughts in italic quotes, both with a colored bar for the persona) near the top, and the in-game dialogue as subtitles at the bottom. Each can be switched off in Voice. If the game footage already has its own subtitles, switch the dialogue captions off to avoid repeats.
- **Silent thoughts:** while characters or the player are speaking, the inner voice never talks, but it can appear as an on-screen thought timed to its reading speed. This is how the commentary gets denser without ever speaking over speech.
- **How much commentary:** Automatic lets the emotion of each moment decide. Manual aims for a number of lines a minute (spoken plus silent) as far as the video's room allows, and each video card tells you what its ceiling was.
- **This Mac's ffmpeg has no text drawing,** so captions are drawn with Pillow as transparent pictures and laid over the video. Adding captions takes roughly as long as the video's length divided by two.

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
