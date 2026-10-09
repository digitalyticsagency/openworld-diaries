from googleapiclient.http import MediaFileUpload

from .google_auth import service

DISCLOSURE = ("\n\nInner-voice commentary: the voice-over is AI-generated from the gameplay. "
              "Game footage belongs to its publisher.")


def upload_private(path, title, description, channel_id):
    yt = service("youtube", "v3", channel_id)
    body = {"snippet": {"title": title[:100], "description": description + DISCLOSURE,
                        "categoryId": "20"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def make_public(video_id, channel_id):
    yt = service("youtube", "v3", channel_id)
    yt.videos().update(part="status", body={"id": video_id, "status": {
        "privacyStatus": "public", "selfDeclaredMadeForKids": False}}).execute()


def make_private(video_id, channel_id):
    yt = service("youtube", "v3", channel_id)
    yt.videos().update(part="status", body={"id": video_id, "status": {
        "privacyStatus": "private", "selfDeclaredMadeForKids": False}}).execute()
