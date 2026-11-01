import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.planetemu import PlanetEmuResolver


class PlanetEmuResolverTests(unittest.TestCase):
    """PlanetEmu 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = PlanetEmuResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://www.planetemu.net/en/rom/nec-pc-engine-cd-turbografx-cd/far-east-of-eden-tengai-makyou-ziria-japan-rev-7",
            "https://planetemu.net/rom/sega-mega-drive-genesis/sonic-the-hedgehog-usa-europe",
            "https://www.planetemu.net/fr/rom/arcade/pac-man",
            "https://www.planetemu.net/download/rom?token=42833f0fd22b5093d0e06a404c2d302c",
            "http://planetemu.net/roms/arcade/game",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://wowroms.com/en/roms",
            "https://romsfun.com/download/game-123",
            "https://example.com/file.zip",
            "",
            None,
        ]
        for url in invalid_urls:
            self.assertFalse(self.resolver.can_handle(url), f"Should not handle: {url}")

    def test_resolve_direct_token_url(self):
        token_url = "https://www.planetemu.net/download/rom?token=42833f0fd22b5093d0e06a404c2d302c"
        result = self.resolver.resolve(token_url)
        self.assertEqual(result["url"], token_url)
        self.assertEqual(result["source_service"], "PlanetEmu")
        self.assertIn("User-Agent", result["headers"])

    @patch("downloader.resolvers.planetemu.requests.Session")
    def test_resolve_standard_rom_page(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies = []

        page_html = """
        <html>
        <head><title>PlanetEmu - ROM</title></head>
        <body>
        <h1>Far East of Eden - Tengai Makyou - Ziria (Japan) (Rev 7)</h1>
        <form class="downloadForm" action="/download/rom" method="POST" data-download-quota="/download/quota">
            <input type="hidden" name="id" value="64853">
            <input type="hidden" name="type" value="rom">
            <input type="submit" name="download" value="Download Far East of Eden - Tengai Makyou - Ziria (Japan) (Rev 7).zip">
        </form>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.ok = True
        page_resp.status_code = 200
        page_resp.text = page_html

        quota_resp = MagicMock()
        quota_resp.ok = True
        quota_resp.status_code = 200
        quota_resp.json.return_value = {
            "blocked": False,
            "retryAfter": 0,
            "pass": "test_pass_token_xyz"
        }

        mock_session.get.side_effect = [page_resp, quota_resp]

        post_resp = MagicMock()
        post_resp.status_code = 302
        post_resp.headers = {"Location": "/download/rom?token=abc123xyz"}
        post_resp.text = ""
        mock_session.post.return_value = post_resp

        target_url = "https://www.planetemu.net/en/rom/nec-pc-engine-cd-turbografx-cd/far-east-of-eden-tengai-makyou-ziria-japan-rev-7"
        result = self.resolver.resolve(target_url)

        self.assertEqual(result["url"], "https://www.planetemu.net/download/rom?token=abc123xyz")
        self.assertEqual(result["filename"], "Far East of Eden - Tengai Makyou - Ziria (Japan) (Rev 7).zip")
        self.assertEqual(result["source_service"], "PlanetEmu")
        self.assertEqual(result["referer"], target_url)

    @patch("downloader.resolvers.planetemu.time.sleep")
    @patch("downloader.resolvers.planetemu.requests.Session")
    def test_resolve_rate_limit_retry(self, mock_session_cls, mock_sleep):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies = []

        page_html = """
        <html>
        <form class="downloadForm" action="/download/rom">
            <input type="hidden" name="id" value="12345">
            <input type="submit" name="download" value="Download TestGame.zip">
        </form>
        </html>
        """
        page_resp = MagicMock(ok=True, status_code=200, text=page_html)
        quota_resp = MagicMock(ok=True, status_code=200)
        quota_resp.json.return_value = {"blocked": False, "retryAfter": 0, "pass": "pass1"}

        mock_session.get.side_effect = [page_resp, quota_resp, quota_resp]

        rate_limit_resp = MagicMock(status_code=429, headers={}, text="429 Too Many Requests")
        success_post_resp = MagicMock(status_code=302, headers={"Location": "/download/rom?token=minted_token"}, text="")
        mock_session.post.side_effect = [rate_limit_resp, success_post_resp]

        result = self.resolver.resolve("https://planetemu.net/rom/test/game")
        self.assertEqual(result["url"], "https://planetemu.net/download/rom?token=minted_token")
        self.assertEqual(result["filename"], "TestGame.zip")
        self.assertTrue(mock_sleep.called)

    @patch("downloader.resolvers.planetemu.requests.Session")
    def test_resolve_quota_blocked_error(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies = []

        page_html = """
        <html>
        <form class="downloadForm" action="/download/rom">
            <input type="hidden" name="id" value="12345">
        </form>
        </html>
        """
        page_resp = MagicMock(ok=True, status_code=200, text=page_html)
        quota_resp = MagicMock(ok=True, status_code=200)
        quota_resp.json.return_value = {"blocked": True, "retryAfter": 3600}

        mock_session.get.side_effect = [page_resp, quota_resp]

        with self.assertRaises(ValueError) as ctx:
            self.resolver.resolve("https://planetemu.net/rom/test/game")
        self.assertIn("할당량이 초과되었습니다", str(ctx.exception))

    def test_addon_registration(self):
        addon = addon_registry.get_addon("planetemu")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.name, "PlanetEmu")
        self.assertIn("planetemu.net", addon.domains)
        self.assertIsInstance(addon.create_resolver(), PlanetEmuResolver)


if __name__ == "__main__":
    unittest.main()
