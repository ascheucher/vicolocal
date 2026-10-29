"""Keep the cloud clips: download every recording of the last N days as mp4 into the archive
directory the dashboard lists under "Archive" (VICO_ARCHIVE_DIR, see vico_archive.py).

The cloud holds clips only a few days (3 on the free plan), so a daily run with --days 3
never misses one. It is idempotent: a clip whose file exists is skipped, so re-runs cost one
API call. ffmpeg remuxes the HLS stream to mp4 (no re-encode), and the clip's thumbnail is
saved beside it as .jpg. Exit status 1 when any clip failed, for the cron job to alert on.

    python vico_pull.py --days 3 --out /clips [--dry-run]
    docker exec vicolocal python vico_pull.py --days 3        # container: --out defaults to VICO_ARCHIVE_DIR

Credentials as for the dashboard: VICO_USER2/VICO_PASS2 (or .vico.env), VICO_ACCOUNT picks the suffix.
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from vico_api import VicoCloud
from vico_archive import clip_path

ACCOUNT = os.environ.get("VICO_ACCOUNT", "2")
PAGE = 60   # the library endpoint's page size


def fetch_clips(vc: VicoCloud, days: int) -> list[dict]:
    now = int(time.time())
    clips, offset = [], 0
    while True:
        page = vc.library(start=now - days * 86400, end=now, offset=offset, limit=PAGE)
        clips += page
        if len(page) < PAGE:
            return clips
        offset += PAGE


def save_thumb(url: str, dest: Path):
    """Best effort: a missing thumbnail is no reason to count the clip as failed."""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        dest.write_bytes(r.content)
    except Exception as e:  # noqa: BLE001
        print(f"thumbnail {dest.name}: {e}", file=sys.stderr, flush=True)


def download(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")   # not .mp4, so a half-written file is never listed
    try:
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", url,
                            "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)], timeout=300)
        ok = r.returncode == 0 and tmp.stat().st_size > 0
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"ffmpeg: {e}", file=sys.stderr, flush=True)
        ok = False
    if ok:
        tmp.rename(dest)
    else:
        tmp.unlink(missing_ok=True)
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description="download the cloud recordings of the last days")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--out", default=os.environ.get("VICO_ARCHIVE_DIR"),
                    help="archive directory (default: $VICO_ARCHIVE_DIR)")
    ap.add_argument("--dry-run", action="store_true", help="only list what would be downloaded")
    a = ap.parse_args(argv)
    if not a.out:
        ap.error("no archive directory: pass --out or set VICO_ARCHIVE_DIR")

    vc = VicoCloud.from_env_file(account=ACCOUNT)
    clips = fetch_clips(vc, a.days)
    new = failed = 0
    for c in clips:
        url, trace = c.get("videoUrl"), c.get("traceId")
        if not url or not trace:
            continue
        when = datetime.fromtimestamp(c.get("timestamp") or 0)
        dest = clip_path(a.out, when, c.get("tags") or "", trace)
        if dest.exists():
            continue
        print(f"get {dest.relative_to(a.out)} ({c.get('period') or 0:.0f}s)", flush=True)
        if a.dry_run:
            continue
        if download(url, dest):
            new += 1
            if c.get("imageUrl"):
                save_thumb(c["imageUrl"], dest.with_suffix(".jpg"))
        else:
            failed += 1
            print(f"FAILED {trace}", file=sys.stderr, flush=True)
    print(f"{len(clips)} clips in the last {a.days} d, {new} downloaded, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
