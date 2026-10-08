# Agent guide

Open World Diaries: Drive folder in, inner-voice gameplay video out, uploaded to YouTube as private.

## Layout
- `app/pipeline.py` runs one video end to end. `app/server.py` is the FastAPI app and Drive watcher. `app/static/index.html` is the one-button UI.
- `app/guard.py` is pure code that enforces the guardrails. `prompts/guardrails.md` is the same rules for the model.
- `app/evolve.py` scores videos, runs the editor pass, and evolves prompts. `prompts/` holds all prompt text.
- `tests/` runs offline: `.venv/bin/python -m unittest discover -s tests`.

## Rules that must never be weakened
1. No inner-voice line may start in, run into, or sit inside a speech window. Keep `guard.enforce` and `guard.clip_fits` in the pipeline.
2. Strong emotions need a matching interaction event within 4 s in the scene notes.
3. The evolution loop may change only the style block (`prompts/style_*.md` and `prompt_versions`). It must never read, write, or replace `guardrails.md` or `master.md`.
4. New uploads are private. Making a video public is only done by the Approve button.
5. Never commit `.env`, `client_secret*.json`, `data/` or tokens.

## Before finishing a change
Run the tests. If you change a prompt or the guard, add or update a test in `tests/`.
