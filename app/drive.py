import io
import re
from pathlib import Path

from googleapiclient.http import MediaIoBaseDownload

from .google_auth import service

SUB_EXT = (".srt", ".vtt")


def folder_id(value):
    """Accept a raw id or a full Drive folder URL."""
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", value or "")
    return m.group(1) if m else (value or "").strip()


def list_folder(fid):
    drive = service("drive", "v3")
    files, token = [], None
    while True:
        r = drive.files().list(q=f"'{fid}' in parents and trashed=false",
                               fields="nextPageToken, files(id,name,mimeType,size)",
                               pageToken=token).execute()
        files += r["files"]
        token = r.get("nextPageToken")
        if not token:
            return files


def videos_and_sidecars(fid):
    files = list_folder(fid)
    vids = [f for f in files if f["mimeType"].startswith("video/")]
    subs = {Path(f["name"]).stem: f for f in files if f["name"].lower().endswith(SUB_EXT)}
    return vids, subs


def download(file_id, dest):
    drive = service("drive", "v3")
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, drive.files().get_media(fileId=file_id), chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()


def read_text(file_id):
    drive = service("drive", "v3")
    buf = io.BytesIO()
    dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=file_id))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return buf.getvalue().decode("utf-8", "replace")
