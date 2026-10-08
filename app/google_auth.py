"""Google login for Drive + YouTube. One token file per YouTube channel."""
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from . import db
from .config import CLIENT_SECRET, TOKENS

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _save(creds, channel_id):
    (TOKENS / f"{channel_id}.json").write_text(creds.to_json())


def connect():
    """Opens the Google consent screen in the browser. Pick the account/channel there."""
    if not CLIENT_SECRET.exists():
        raise RuntimeError(f"Missing {CLIENT_SECRET}. See README step 3.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    yt = build("youtube", "v3", credentials=creds)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("That Google account has no YouTube channel.")
    ch = items[0]
    _save(creds, ch["id"])
    names = json.loads(db.get("channel_names", "{}"))
    names[ch["id"]] = ch["snippet"]["title"]
    db.put("channel_names", json.dumps(names))
    if not db.get("active_channel"):
        db.put("active_channel", ch["id"])
    return ch["id"]


def channels():
    names = json.loads(db.get("channel_names", "{}"))
    return [{"id": i, "title": t} for i, t in names.items() if (TOKENS / f"{i}.json").exists()]


def get_creds(channel_id=None):
    channel_id = channel_id or db.get("active_channel")
    if not channel_id:
        raise RuntimeError("No YouTube channel connected.")
    path = TOKENS / f"{channel_id}.json"
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds, channel_id)
    return creds


def service(name, version, channel_id=None):
    return build(name, version, credentials=get_creds(channel_id), cache_discovery=False)
