"""vico_server: the archive and birds endpoints (cloud patched out)."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

import vico_server


class ArchiveApiTest(AioHTTPTestCase):
    async def get_application(self):
        return vico_server.make_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "2026-10").mkdir()
        (self.root / "2026-10" / "2026-10-29_14-05-09_bird_t1.mp4").write_bytes(b"mp4-bytes")
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.tmp.cleanup()

    async def test_off_by_default(self):
        self.assertFalse(vico_server.CONFIG["archive"])
        r = await self.client.get("/api/archive")
        self.assertEqual(await r.json(), [])

    async def test_lists_and_serves_clips(self):
        with patch.object(vico_server, "ARCHIVE_DIR", self.root):
            r = await self.client.get("/api/archive")
            files = await r.json()
            self.assertEqual([f["file"] for f in files], ["2026-10/2026-10-29_14-05-09_bird_t1.mp4"])
            r = await self.client.get("/api/archive/file", params={"path": files[0]["file"]})
            self.assertEqual(r.status, 200)
            self.assertEqual(r.headers["Content-Type"], "video/mp4")
            self.assertEqual(await r.read(), b"mp4-bytes")

    async def test_traversal_is_rejected(self):
        with patch.object(vico_server, "ARCHIVE_DIR", self.root):
            for bad in ("../../etc/passwd", "2026-10/../../x.mp4", "/etc/passwd", ""):
                r = await self.client.get("/api/archive/file", params={"path": bad})
                self.assertEqual(r.status, 404, bad)


TIT = {"name": "Great Tit", "latin": "Parus major", "image": "https://x/crop"}
SPARROW = {"name": "Eurasian Tree Sparrow", "latin": "Passer montanus", "image": None}


class BirdsApiTest(AioHTTPTestCase):
    async def get_application(self):
        return vico_server.make_app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        month = self.root / "2026-10"
        month.mkdir()
        (month / "2026-10-28_09-00-00_bird_t1.mp4").write_bytes(b"a")
        (month / "2026-10-28_09-00-00_bird_t1.json").write_text(json.dumps(
            {"trace": "t1", "species": {"name": "Great Tit", "latin": "Parus major"}}))
        (month / "2026-10-28_09-00-00_bird_t1.bird.jpg").write_bytes(b"crop")
        (month / "2026-10-27_09-00-00_bird_t0.mp4").write_bytes(b"b")   # no sidecar: unidentified
        self.cloud = [
            {"trace": "t1", "time": 1793264400, "video": "hls1", "species": TIT},        # archived too
            {"trace": "t2", "time": 1793350800, "video": "hls2", "species": SPARROW},
            {"trace": "t3", "time": 1793350900, "video": "hls3", "species": None},
        ]
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.tmp.cleanup()

    async def test_groups_cloud_and_archive_by_species(self):
        with patch.object(vico_server, "ARCHIVE_DIR", self.root), \
             patch.object(vico_server, "_fetch_clips", lambda serial, days: self.cloud):
            r = await self.client.get("/api/birds")
            data = await r.json()
        self.assertIsNone(data["error"])
        self.assertEqual([(g["name"], g["count"]) for g in data["species"]],   # same count: latest visit first
                         [("Eurasian Tree Sparrow", 1), ("Great Tit", 1)])
        tit = data["species"][1]["clips"][0]
        self.assertEqual(tit["file"], "2026-10/2026-10-28_09-00-00_bird_t1.mp4")   # the archived copy wins
        self.assertEqual(tit["bird"], "2026-10/2026-10-28_09-00-00_bird_t1.bird.jpg")
        self.assertEqual(data["species"][0]["clips"][0]["video"], "hls2")

    async def test_cloud_failure_leaves_the_archive_and_an_error(self):
        def boom(serial, days):
            raise RuntimeError("no cloud today")
        with patch.object(vico_server, "ARCHIVE_DIR", self.root), patch.object(vico_server, "_fetch_clips", boom):
            data = await (await self.client.get("/api/birds")).json()
        self.assertEqual(data["error"], "no cloud today")
        self.assertEqual([g["name"] for g in data["species"]], ["Great Tit"])

    async def test_without_archive_it_is_cloud_only(self):
        with patch.object(vico_server, "_fetch_clips", lambda serial, days: self.cloud):
            data = await (await self.client.get("/api/birds?days=7")).json()
        self.assertEqual([(g["name"], g["clips"][0]["video"]) for g in data["species"]],
                         [("Eurasian Tree Sparrow", "hls2"), ("Great Tit", "hls1")])


if __name__ == "__main__":
    import unittest
    unittest.main()
