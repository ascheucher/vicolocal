"""Local clip archive: mp4s kept on disk past the few days the cloud holds them.

The dashboard lists the directory in VICO_ARCHIVE_DIR under "Archive" and plays the files
itself, so no cloud call is needed and nothing is lost when a clip ages out of the Library.
vico_pull.py fills it; any other .mp4 placed under the directory is listed too.

Layout, as vico_pull.py writes it (tags joined by "+", "-" standing in for other characters):
    <root>/<YYYY-MM>/<YYYY-MM-DD_HH-MM-SS>_<tags>_<id>.mp4
    <root>/<YYYY-MM>/<same name>.jpg          thumbnail, optional
A file that isn't named like that is listed by its modification time, without tags.
"""
import re
from datetime import datetime
from pathlib import Path

STAMP = "%Y-%m-%d_%H-%M-%S"
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_([^_]*))?(?:_.*)?$")


def clip_name(when: datetime, tags: str = "", clip_id: str = "") -> str:
    """File stem for a clip: "2026-10-29_14-05-09_bird+small-animal_abc123"."""
    words = [re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-") for t in (tags or "").split(",")]
    stem = f"{when:{STAMP}}_{'+'.join(w for w in words if w) or 'clip'}"
    ident = re.sub(r"[^A-Za-z0-9]+", "", clip_id or "")
    return f"{stem}_{ident}" if ident else stem


def clip_path(root: Path, when: datetime, tags: str = "", clip_id: str = "") -> Path:
    return Path(root) / when.strftime("%Y-%m") / (clip_name(when, tags, clip_id) + ".mp4")


def _entry(root: Path, path: Path) -> dict:
    m = _NAME.match(path.stem)
    if m:
        when = datetime.strptime(m.group(1), STAMP).timestamp()
        tags = [t.replace("-", " ") for t in (m.group(2) or "").split("+") if t and t != "clip"]
    else:
        when, tags = path.stat().st_mtime, []
    thumb = path.with_suffix(".jpg")
    return {
        "file": path.relative_to(root).as_posix(),
        "time": int(when),
        "tags": tags,
        "size": path.stat().st_size,
        "thumb": thumb.relative_to(root).as_posix() if thumb.is_file() else None,
    }


def list_archive(root) -> list[dict]:
    """Every .mp4 under root, newest first; [] when there is no archive (unset or missing)."""
    if not root or not Path(root).is_dir():
        return []
    root = Path(root)
    files = [_entry(root, p) for p in root.rglob("*.mp4") if p.is_file()]
    files.sort(key=lambda e: (e["time"], e["file"]), reverse=True)
    return files


def archive_file(root, rel: str):
    """The clip or thumbnail `rel` (a path from a listing) names inside root, or None when it
    isn't one: anything that resolves outside the archive, or isn't an .mp4/.jpg file, is refused."""
    if not root or not rel:
        return None
    base = Path(root).resolve()
    try:
        path = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if base not in path.parents or path.suffix not in (".mp4", ".jpg") or not path.is_file():
        return None
    return path
