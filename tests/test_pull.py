"""vico_pull.download: the ffmpeg remux into the archive (skipped without ffmpeg)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from vico_pull import download


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
