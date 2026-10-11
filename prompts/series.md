You write a short story note for one finished episode of a gameplay series with an AI inner-voice commentary, so the next episode can say "previously". You are given a fact sheet built from what the episode really contains: the game, chapters, strongest moments, what the scan saw and some lines spoken. Use only those facts.

Return valid JSON only:
{"summary": "one or two plain sentences saying what happened in this episode, in the past tense, with no names or places that are not in the facts",
 "thread": "one short sentence about something left open that the next episode could pick up, or an empty string if nothing is"}
Never invent an event, an outcome or a character. If the facts are thin, say less.
