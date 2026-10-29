"""The clip download only remuxes URLs on the configured cloud region (VICOHOME_API)."""
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

import vico_server


class DownloadHostTest(AioHTTPTestCase):
    async def get_application(self):
        return vico_server.make_app()

    async def test_other_hosts_are_refused(self):
        r = await self.client.get("/api/clip/download", params={"url": "https://evil.example/x.m3u8"})
        self.assertEqual(r.status, 400)

    async def test_configured_region_passes_the_host_check(self):
        url = vico_server.API + "/some/clip.m3u8?token=t"
        with patch("shutil.which", return_value=None):   # stops right after the host check
            r = await self.client.get("/api/clip/download", params={"url": url})
        self.assertEqual(r.status, 501)


if __name__ == "__main__":
    import unittest
    unittest.main()
