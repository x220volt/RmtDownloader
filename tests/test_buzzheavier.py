import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.buzzheavier import BuzzheavierResolver


class BuzzheavierResolverTests(unittest.TestCase):
    """Buzzheavier 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = BuzzheavierResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://buzzheavier.com/n5v3itgq2iqt",
            "https://www.buzzheavier.com/n5v3itgq2iqt",
            "https://bzzhr.to/n5v3itgq2iqt",
            "https://www.bzzhr.to/n5v3itgq2iqt",
            "https://buzzheavier.com/n5v3itgq2iqt/download",
            "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=RLur4fD4ujVdPQW",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://datanodes.to/q7cltl21j7cl",
            "https://filekeeper.net/7lkwblvxjags",
            "https://mediafire.com/file/12345/sample.zip",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle: {url}")

    def test_resolve_direct_cdn_url(self):
        direct_url = "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=token123"
        with patch("downloader.resolvers.buzzheavier.extract_filename_from_headers", return_value="Metroid.part1.rar"):
            result = self.resolver.resolve(direct_url)
            self.assertEqual(result["url"], direct_url)
            self.assertEqual(result["filename"], "Metroid.part1.rar")
            self.assertEqual(result["source_service"], "Buzzheavier")
            self.assertEqual(result["subfolder"], "Metroid")
            self.assertEqual(result["article_title"], "Metroid.part1")

    @patch("downloader.resolvers.buzzheavier.requests.get")
    def test_resolve_fast_requests_flow(self, mock_get):
        # 1. Page GET returns 200 with download-btn and hx-get
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = """
        <html>
        <head><title>Test_Game_Archive.zip</title></head>
        <body>
            <a class="download-btn" hx-get="/n5v3itgq2iqt/download?t=token_123">Download</a>
        </body>
        </html>
        """

        # 2. HTMX GET returns 204 with hx-redirect
        hx_resp = MagicMock()
        hx_resp.status_code = 204
        hx_resp.headers = {"hx-redirect": "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=final_link"}

        mock_get.side_effect = [page_resp, hx_resp]

        result = self.resolver.resolve("https://buzzheavier.com/n5v3itgq2iqt")
        self.assertEqual(result["url"], "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=final_link")
        self.assertEqual(result["filename"], "Test_Game_Archive.zip")
        self.assertEqual(result["source_service"], "Buzzheavier")
        self.assertEqual(result["article_title"], "Test_Game_Archive")

    @patch("downloader.resolvers.buzzheavier.time.sleep", return_value=None)
    @patch("downloader.resolvers.buzzheavier.stealth_browser_session")
    @patch("downloader.resolvers.buzzheavier.requests.get")
    def test_resolve_browser_flow(self, mock_get, mock_browser_session, mock_sleep):
        # Initial requests.get gets blocked with Cloudflare 403
        cf_resp = MagicMock()
        cf_resp.status_code = 403
        cf_resp.text = "Just a moment..."
        mock_get.return_value = cf_resp

        # Mock browser page
        mock_page = MagicMock()
        mock_browser_session.return_value.__enter__.return_value = mock_page
        mock_page.title = "Metroid Prime 4 Beyond.nup"
        mock_page.user_agent = "Mozilla/5.0 Test Chrome"
        mock_page.cookies.return_value = [{"name": "cf_clearance", "value": "xyz"}]
        mock_page.run_js.return_value = {
            "status": 204,
            "hxRedirect": "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=browser_token",
        }

        result = self.resolver.resolve("https://buzzheavier.com/n5v3itgq2iqt")
        self.assertEqual(result["url"], "https://ts.buzzheavier.com/d/n5v3itgq2iqt?v=browser_token")
        self.assertEqual(result["filename"], "Metroid Prime 4 Beyond.nup")
        self.assertEqual(result["source_service"], "Buzzheavier")
        self.assertEqual(result["headers"]["User-Agent"], "Mozilla/5.0 Test Chrome")

    @patch("downloader.resolvers.buzzheavier.requests.get")
    def test_resolve_not_found(self, mock_get):
        resp_404 = MagicMock()
        resp_404.status_code = 404
        resp_404.text = "Not Found"
        mock_get.return_value = resp_404

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://buzzheavier.com/nonexistent")

    def test_addon_registration(self):
        addon = addon_registry.get_addon("buzzheavier")
        assert addon is not None
        self.assertEqual(addon.name, "Buzzheavier")
        self.assertIn("buzzheavier.com", addon.domains)
        self.assertIn("bzzhr.to", addon.domains)

