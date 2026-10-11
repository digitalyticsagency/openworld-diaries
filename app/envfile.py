"""Read and write the project's .env so API keys can be managed from the app.

The values never leave this machine and are never sent back to the page: the page only
learns whether a key is set and its last four characters.
"""
import os
import re
import tempfile

from .config import KEEP_ENV, ROOT

ENV_PATH = ROOT / ".env"

KEYS = {
    "GEMINI_API_KEY": {"label": "Gemini API key", "secret": True, "help": "aistudio.google.com, Get API key"},
    "ANTHROPIC_API_KEY": {"label": "Anthropic API key", "secret": True, "help": "console.anthropic.com, API keys"},
    "ELEVENLABS_API_KEY": {"label": "ElevenLabs API key", "secret": True, "help": "elevenlabs.io, Profile, API keys"},
    "GEMINI_MODEL": {"label": "Gemini model", "secret": False, "help": "Reads the video. Default gemini-3.8-flash"},
    "CLAUDE_MODEL": {"label": "Claude model", "secret": False, "help": "Writes the commentary. Default claude-opus-5-5"},
    "ELEVEN_MODEL": {"label": "ElevenLabs model", "secret": False, "help": "Speaks the lines. Default eleven_multilingual_v2"},
}
DEFAULTS = {"GEMINI_MODEL": "gemini-3.8-flash", "CLAUDE_MODEL": "claude-opus-5-5", "ELEVEN_MODEL": "eleven_multilingual_v2"}
_VALID = re.compile(r"^[A-Za-z0-9._\-:/+=~]+$")


def clean(value):
    value = (value or "").strip().strip("'\"")
    if not value or not _VALID.match(value):
        raise ValueError("That value has spaces or characters a key or model name never contains.")
    return value


def _lines():
    return ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []


def get(name):
    return os.environ.get(name) or DEFAULTS.get(name, "")


def set_value(name, value):
    """Save to .env (kept private) and apply immediately without a restart."""
    if name not in KEYS:
        raise KeyError(name)
    value = clean(value)
    lines, done = _lines(), False
    for i, line in enumerate(lines):
        if re.match(rf"^\s*{re.escape(name)}\s*=", line):
            lines[i], done = f"{name}={value}", True
    if not done:
        lines.append(f"{name}={value}")
    _write(lines)
    os.environ[name] = value


def clear(name):
    if name not in KEYS:
        raise KeyError(name)
    _write([ln for ln in _lines() if not re.match(rf"^\s*{re.escape(name)}\s*=", ln)])
    os.environ.pop(name, None)


def _write(lines):
    _put(ENV_PATH, lines)
    if ENV_PATH == ROOT / ".env":       # the real file, not a test's: keep a copy that survives the project folder being wiped
        try:
            _put(KEEP_ENV, lines)
        except OSError:
            pass


def _put(path, lines):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.")
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def status():
    out = []
    for name, meta in KEYS.items():
        v = os.environ.get(name, "")
        item = {"name": name, "label": meta["label"], "help": meta["help"], "secret": meta["secret"], "set": bool(v)}
        if meta["secret"]:
            item["hint"] = ("…" + v[-4:]) if len(v) >= 8 else ""
        else:
            item["value"] = v or DEFAULTS.get(name, "")
        out.append(item)
    return out


def _gemini_models(key):
    from google import genai
    client = genai.Client(api_key=key)  # keep a reference: the list is read lazily and needs the client open
    return [m.name.replace("models/", "") for m in client.models.list()]


def test(name):
    """A cheap real call that proves the value works. Returns (ok, message). Never echoes the key."""
    import requests
    v = os.environ.get(name, "")
    if name.endswith("_API_KEY") and not v:
        return False, "Not set yet."
    try:
        if name == "GEMINI_API_KEY":
            names = _gemini_models(v)
            want = get("GEMINI_MODEL")
            return (True, f"Works. Model {want} is available.") if want in names else (
                True, f"Works, but the model {want} is not on this key. Pick one in the Gemini model box.")
        if name == "ANTHROPIC_API_KEY":
            import anthropic
            anthropic.Anthropic(api_key=v).messages.create(
                model=get("CLAUDE_MODEL"), max_tokens=8, messages=[{"role": "user", "content": "Say ok"}])
            return True, f"Works with {get('CLAUDE_MODEL')}."
        if name == "ELEVENLABS_API_KEY":
            r = requests.get("https://api.elevenlabs.io/v1/user", headers={"xi-api-key": v}, timeout=20)
            return (r.ok, "Works." if r.ok else f"ElevenLabs refused it (HTTP {r.status_code}).")
        if name == "GEMINI_MODEL":
            names = _gemini_models(os.environ["GEMINI_API_KEY"])
            return (get(name) in names, "Found." if get(name) in names else "Not available on your Gemini key.")
        if name == "CLAUDE_MODEL":
            return test("ANTHROPIC_API_KEY")
        if name == "ELEVEN_MODEL":
            return True, "Used when a voice is generated."
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:160]}".replace(v, "***") if v else f"{type(e).__name__}"
    return False, "Unknown setting."
