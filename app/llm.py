import json
import os
import re

LABELS = {"GEMINI_API_KEY": "Gemini", "ANTHROPIC_API_KEY": "Claude", "ELEVENLABS_API_KEY": "ElevenLabs"}


def key(name):
    """The saved key, or a plain message saying where to add it."""
    v = os.environ.get(name)
    if not v:
        raise RuntimeError(f"The {LABELS.get(name, name)} key is not set. Add it in API keys at the top of the app, then press Retry.")
    return v


_ESCAPE = re.compile(r'(\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))|\\')


def parse_json(text):
    """Parse model output as JSON. Models sometimes emit an invalid escape such as \\' or \\_ ;
    a lone backslash is doubled so the text still parses, and valid escapes are left alone."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    fixed = _ESCAPE.sub(lambda m: m.group(1) or "\\\\", text)
    decoder = json.JSONDecoder(strict=False)
    for t in (text, fixed):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
        # Models sometimes add a note after the answer (or before it): the first JSON value is the answer.
        starts = [i for i in (t.find("["), t.find("{")) if i >= 0]
        if starts:
            try:
                return decoder.raw_decode(t[min(starts):])[0]
            except json.JSONDecodeError:
                pass
    return json.loads(fixed, strict=False)       # still unreadable: raise the real error


CLAUDE_TIMEOUT = (180.0, 20.0)     # read, connect: a call that has not answered in three minutes is tried again, never waited on for half an hour
CLAUDE_RETRIES = 2
GEMINI_TIMEOUT_MS = 300_000


def claude(system, user, max_tokens=8000):
    import anthropic
    client = anthropic.Anthropic(api_key=key("ANTHROPIC_API_KEY"), max_retries=CLAUDE_RETRIES,
                                 timeout=anthropic.Timeout(CLAUDE_TIMEOUT[0], connect=CLAUDE_TIMEOUT[1]))
    r = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"), max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def gemini_json(parts):
    """parts: list of str or (bytes, mime_type). Returns parsed JSON."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=key("GEMINI_API_KEY"), http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    contents = [types.Part.from_bytes(data=p[0], mime_type=p[1]) if isinstance(p, tuple) else p
                for p in parts]
    last = None
    for _ in range(3):  # the model is not deterministic: ask again if the answer is unreadable
        r = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"), contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        try:
            return parse_json(r.text or "")
        except json.JSONDecodeError as e:
            last = e
    raise last
