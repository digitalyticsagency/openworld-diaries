import json
import os
import re

from .config import CLAUDE_MODEL, GEMINI_MODEL


_ESCAPE = re.compile(r'(\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))|\\')


def parse_json(text):
    """Parse model output as JSON. Models sometimes emit an invalid escape such as \\' or \\_ ;
    a lone backslash is doubled so the text still parses, and valid escapes are left alone."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_ESCAPE.sub(lambda m: m.group(1) or "\\\\", text), strict=False)


def claude(system, user, max_tokens=8000):
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}])
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def gemini_json(parts):
    """parts: list of str or (bytes, mime_type). Returns parsed JSON."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    contents = [types.Part.from_bytes(data=p[0], mime_type=p[1]) if isinstance(p, tuple) else p
                for p in parts]
    last = None
    for _ in range(3):  # the model is not deterministic: ask again if the answer is unreadable
        r = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        try:
            return parse_json(r.text or "")
        except json.JSONDecodeError as e:
            last = e
    raise last
