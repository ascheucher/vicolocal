"""vico_archive: file naming, listing and the path check behind /api/archive/file."""
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from vico_archive import archive_file, clip_name, clip_path, list_archive

WHEN = datetime(2026, 10, 29, 14, 5, 9)


class ClipNameTest(unittest.TestCase):
    def test_tags_and_id_are_made_filename_safe(self):
        self.assertEqual(clip_name(WHEN, "bird,small animal", "ab-c/12"), "2026-10-29_14-05-09_bird+small-animal_abc12")

    def test_no_tags_gives_clip(self):
        self.assertEqual(clip_name(WHEN, "", "x1"), "2026-10-29_14-05-09_clip_x1")

    def test_path_is_under_the_month(self):
        self.assertEqual(clip_path("/a", WHEN, "bird", "t1"), Path("/a/2026-10/2026-10-29_14-05-09_bird_t1.mp4"))


class ListArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel, data=b"x"):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_missing_directory_is_empty(self):
        self.assertEqual(list_archive(self.root / "nope"), [])
        self.assertEqual(list_archive(None), [])

    def test_empty_directory_is_empty(self):
        self.assertEqual(list_archive(self.root), [])

    def test_three_files_give_three_entries_newest_first(self):
        self._touch("2026-10/2026-10-29_14-05-09_bird+small-animal_t1.mp4", b"abc")
        self._touch("2026-10/2026-10-29_14-05-09_bird+small-animal_t1.jpg")
        self._touch("2026-09/2026-09-01_08-00-00_clip_t2.mp4")
        other = self._touch("misc/holiday.mp4")
        os.utime(other, (0, 0))
        self._touch("2026-10/notes.txt")
        files = list_archive(self.root)
        self.assertEqual([f["file"] for f in files], [
            "2026-10/2026-10-29_14-05-09_bird+small-animal_t1.mp4",
            "2026-09/2026-09-01_08-00-00_clip_t2.mp4",
            "misc/holiday.mp4"])
        first = files[0]
        self.assertEqual(first["time"], int(datetime(2026, 10, 29, 14, 5, 9).timestamp()))
        self.assertEqual(first["tags"], ["bird", "small animal"])
        self.assertEqual(first["size"], 3)
        self.assertEqual(first["thumb"], "2026-10/2026-10-29_14-05-09_bird+small-animal_t1.jpg")
        self.assertEqual(files[1]["tags"], [])
        self.assertIsNone(files[1]["thumb"])
        self.assertEqual(files[2]["time"], 0)   # unknown naming: modification time

    def test_archive_file_accepts_clips_and_thumbnails(self):
        clip = self._touch("2026-10/a.mp4")
        thumb = self._touch("2026-10/a.jpg")
        self.assertEqual(archive_file(self.root, "2026-10/a.mp4"), clip.resolve())
        self.assertEqual(archive_file(self.root, "2026-10/a.jpg"), thumb.resolve())

    def test_archive_file_refuses_traversal(self):
        outside = Path(self.tmp.name).parent / "vico-outside.mp4"
        outside.write_bytes(b"x")
        try:
            self.assertIsNone(archive_file(self.root, "../vico-outside.mp4"))
            self.assertIsNone(archive_file(self.root, "2026-10/../../vico-outside.mp4"))
            self.assertIsNone(archive_file(self.root, str(outside)))
        finally:
            outside.unlink()

    def test_archive_file_refuses_other_files_and_directories(self):
        self._touch("2026-10/notes.txt")
        self._touch("2026-10/a.mp4")
        self.assertIsNone(archive_file(self.root, "2026-10/notes.txt"))
        self.assertIsNone(archive_file(self.root, "2026-10"))
        self.assertIsNone(archive_file(self.root, "2026-10/missing.mp4"))
        self.assertIsNone(archive_file(self.root, ""))
        self.assertIsNone(archive_file(None, "2026-10/a.mp4"))


if __name__ == "__main__":
    unittest.main()
