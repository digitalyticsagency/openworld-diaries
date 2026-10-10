"""Where videos come from: a folder on this Mac, or a Google Drive folder.

A value starting with / or ~ is a local path; anything else is a Drive folder link or ID.
"""
import shutil
import time
from pathlib import Path

from . import drive
from .config import ROOT

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
SUB_EXT = {".srt", ".vtt"}
SETTLE_SECONDS = 10  # ignore files still being copied in


def is_local(value):
    return bool(value) and value.strip()[0] in "/~"


def inbox_dir():
    d = ROOT / "inbox"
    d.mkdir(exist_ok=True)
    return d


def local_file(file_id):
    return str(file_id).startswith("/")


def normalize(value):
    value = (value or "").strip()
    return str(Path(value).expanduser()) if is_local(value) else drive.folder_id(value)


def list_videos(value, settle=SETTLE_SECONDS):
    """Return (videos, sidecars): videos [{id,name}], sidecars {stem: id}."""
    if not is_local(value):
        vids, subs = drive.videos_and_sidecars(value)
        return vids, {k: v["id"] for k, v in subs.items()}
    folder = Path(value).expanduser()
    if not folder.is_dir():
        raise RuntimeError(f"Folder not found: {folder}")
    vids, subs = [], {}
    now = time.time()
    for p in sorted(folder.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in VIDEO_EXT and now - p.stat().st_mtime > settle:
            vids.append({"id": str(p), "name": p.name})
        elif ext in SUB_EXT:
            subs[p.stem] = str(p)
    return vids, subs


def fetch(file_id, dest):
    if local_file(file_id):
        shutil.copyfile(file_id, dest)
    else:
        drive.download(file_id, dest)


def read_text(file_id):
    if local_file(file_id):
        return Path(file_id).read_text(encoding="utf-8", errors="replace")
    return drive.read_text(file_id)
