"""vico_pull: the ffmpeg remux into the archive (skipped without ffmpeg) and the sidecars."""
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import vico_pull
from vico_archive import clip_path
from vico_pull import download, keep_sidecars

TIT = {"objectType": "bird", "objectName": "Great Tit", "birdStdName": "Parus major", "url": "https://x/crop"}
CLIP = {"traceId": "0797t1", "timestamp": 1793282709, "period": 10.0, "tags": "bird",
        "videoUrl": "https://x/v.m3u8", "imageUrl": "https://x/thumb", "subcategoryInfoList": [TIT]}


def fake_save(url, dest):
    dest.write_bytes(b"jpeg:" + url.encode())


class SidecarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.dest = clip_path(self.root, datetime.fromtimestamp(CLIP["timestamp"]), "bird", "0797t1")
        self.dest.parent.mkdir()
        self.dest.write_bytes(b"mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_json_and_bird_crop_once(self):
        with patch.object(vico_pull, "save_thumb", fake_save):
            self.assertEqual(keep_sidecars(self.dest, CLIP), 2)
            self.assertEqual(keep_sidecars(self.dest, CLIP), 0)
        meta = json.loads(self.dest.with_suffix(".json").read_text())
        self.assertEqual(meta, {"trace": "0797t1", "time": 1793282709, "period": 10.0, "tags": "bird",
                                "species": {"name": "Great Tit", "latin": "Parus major"}})
        self.assertEqual(self.dest.with_suffix(".bird.jpg").read_bytes(), b"jpeg:https://x/crop")

    def test_no_bird_means_json_only(self):
        clip = dict(CLIP, subcategoryInfoList=[])
        with patch.object(vico_pull, "save_thumb", fake_save):
            self.assertEqual(keep_sidecars(self.dest, clip), 1)
        self.assertIsNone(json.loads(self.dest.with_suffix(".json").read_text())["species"])
        self.assertFalse(self.dest.with_suffix(".bird.jpg").exists())

    def test_main_backfills_archived_clips_without_downloading(self):
        def no_download(url, dest):
            raise AssertionError("archived clip downloaded again")
        with patch.object(vico_pull.VicoCloud, "from_env_file", lambda **kw: object()), \
             patch.object(vico_pull, "fetch_clips", lambda vc, days: [CLIP]), \
             patch.object(vico_pull, "download", no_download), \
             patch.object(vico_pull, "save_thumb", fake_save):
            self.assertEqual(vico_pull.main(["--out", str(self.root)]), 0)
        self.assertTrue(self.dest.with_suffix(".json").exists())
        self.assertTrue(self.dest.with_suffix(".bird.jpg").exists())

    def test_dry_run_touches_nothing(self):
        with patch.object(vico_pull.VicoCloud, "from_env_file", lambda **kw: object()), \
             patch.object(vico_pull, "fetch_clips", lambda vc, days: [CLIP]):
            self.assertEqual(vico_pull.main(["--out", str(self.root), "--dry-run"]), 0)
        self.assertEqual([p.name for p in self.dest.parent.iterdir()], [self.dest.name])


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.src = self.root / "src.mp4"   # ffmpeg reads a local file like it reads an HLS URL
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc=duration=1:size=64x64:rate=5", "-pix_fmt", "yuv420p",
                        str(self.src)], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_remux_lands_as_mp4_with_no_part_file_left(self):
        dest = self.root / "2026-10" / "2026-10-29_14-05-09_bird_t1.mp4"
        self.assertTrue(download(str(self.src), dest))
        self.assertGreater(dest.stat().st_size, 0)
        self.assertEqual(sorted(p.name for p in dest.parent.iterdir()), [dest.name])

    def test_bad_source_fails_and_leaves_nothing(self):
        dest = self.root / "2026-10" / "x.mp4"
        self.assertFalse(download(str(self.root / "missing.m3u8"), dest))
        self.assertEqual(list(dest.parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
