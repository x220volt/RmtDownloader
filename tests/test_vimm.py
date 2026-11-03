import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.vimm import VimmResolver


class VimmResolverTests(unittest.TestCase):
    """Vimm's Lair 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = VimmResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://vimm.net/vault/1191",
            "https://www.vimm.net/vault/1191",
            "https://vimm.net/vault/?p=details&id=1191",
            "http://vimm.net/vault/25000",
            "https://dl3.vimm.net/?mediaId=1165&token=abc123xyz",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://planetemu.net/rom/sega/game",
            "https://romspure.cc/download/game-123",
            "https://wowroms.com/en/roms",
            "https://vimm.net/manual/?id=1",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle: {url}")

    def test_resolve_direct_dl_url(self):
        direct_url = "https://dl3.vimm.net/?mediaId=1165&token=abc123xyz"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["url"], direct_url)
        self.assertEqual(result["source_service"], "Vimm's Lair")
        self.assertIn("User-Agent", result["headers"])

    @patch("downloader.resolvers.vimm.time.sleep")
    @patch("downloader.resolvers.vimm.requests.head")
    @patch("downloader.resolvers.vimm.stealth_browser_session")
    def test_resolve_mocked_vault_page(self, mock_browser_session, mock_requests_head, mock_sleep):
        # 모의 브라우저 설정
        mock_page = MagicMock()
        mock_page.title = "The Vault: Dragon Quest V: Tenkuu no Hanayome (SNES)"
        mock_page.user_agent = "Mozilla/5.0 TestBrowser"
        mock_page.cookies.return_value = [
            {"name": "PHPSESSID", "value": "testsession123"},
            {"name": "counted", "value": "1"},
        ]
        mock_page.html = """
        <html>
        <head><title>The Vault: Dragon Quest V: Tenkuu no Hanayome (SNES)</title></head>
        <body>
        <h2>Super Nintendo</h2>
        <form id="dl-form" action="//dl3.vimm.net/" method="POST">
            <input type="hidden" name="mediaId" value="1165">
            <input type="hidden" name="alt" value="0" disabled="">
            <input type="hidden" name="token" value="test_token_val==">
            <button type="submit">Download</button>
        </form>
        </body>
        </html>
        """
        mock_page.ele.return_value = True

        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_page
        mock_ctx.__exit__.return_value = None
        mock_browser_session.return_value = mock_ctx

        # 모의 HEAD 응답
        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200
        mock_head_resp.headers = {
            "Content-Disposition": 'attachment; filename="Dragon Quest V - Tenkuu no Hanayome (Japan).zip"',
            "Content-Length": "1136674",
        }
        mock_requests_head.return_value = mock_head_resp

        result = self.resolver.resolve("https://vimm.net/vault/1191")

        self.assertIn("https://dl3.vimm.net/?", result["url"])
        self.assertIn("mediaId=1165", result["url"])
        self.assertIn("token=test_token_val%3D%3D", result["url"])
        self.assertNotIn("alt=", result["url"])  # disabled 태그는 제외되어야 함
        self.assertEqual(result["filename"], "Dragon Quest V - Tenkuu no Hanayome (Japan).zip")
        self.assertEqual(result["source_service"], "Vimm's Lair")
        self.assertEqual(result["article_title"], "Dragon Quest V: Tenkuu no Hanayome (SNES)")
        self.assertEqual(result["headers"]["User-Agent"], "Mozilla/5.0 TestBrowser")
        self.assertEqual(result["headers"]["Referer"], "https://vimm.net/vault/1191")
        self.assertIn("PHPSESSID=testsession123", result["cookies"])

    @patch("downloader.resolvers.vimm.time.sleep")
    @patch("downloader.resolvers.vimm.stealth_browser_session")
    def test_resolve_unavailable_raises_filenotfound(self, mock_browser_session, mock_sleep):
        mock_page = MagicMock()
        mock_page.title = "The Vault: Unavailable Game"
        mock_page.ele.return_value = False
        mock_page.html = "<html><body><p>This game has been removed or is unavailable due to an ESA request.</p></body></html>"
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_page
        mock_ctx.__exit__.return_value = None
        mock_browser_session.return_value = mock_ctx

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://vimm.net/vault/99999")

    def test_addon_registration(self):
        addon = addon_registry.find_for_url("https://vimm.net/vault/1191")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.addon_id, "vimm")
        self.assertEqual(addon.name, "Vimm's Lair")


if __name__ == "__main__":
    unittest.main()
