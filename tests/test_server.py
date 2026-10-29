"""vico_server: the archive endpoints (no cloud needed)."""
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


if __name__ == "__main__":
    import unittest
    unittest.main()
