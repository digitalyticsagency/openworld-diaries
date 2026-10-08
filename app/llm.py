import json
import os
import re

from .config import CLAUDE_MODEL, GEMINI_MODEL


def parse_json(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def claude(system, user, max_tokens=8000):
    import anthropic
    r = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}])
    return r.content[0].text


def gemini_json(parts):
    """parts: list of str or (bytes, mime_type). Returns parsed JSON."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    contents = [types.Part.from_bytes(data=p[0], mime_type=p[1]) if isinstance(p, tuple) else p
                for p in parts]
    r = client.models.generate_content(
        model=GEMINI_MODEL, contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return parse_json(r.text)
