import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.datanodes import DataNodesResolver


class DataNodesResolverTests(unittest.TestCase):
    """DataNodes 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = DataNodesResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://datanodes.to/q7cltl21j7cl/HYAOI_NUP.part1.rar",
            "https://datanodes.to/q7cltl21j7cl",
            "https://www.datanodes.to/abcdef123456/sample.zip",
            "https://datanodes.to/download",
            "https://tunnel5.dlproxy.uk/download/token123?sig=456",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://mediafire.com/file/12345/sample.zip",
            "https://filekeeper.net/7lkwblvxjags",
            "https://vimm.net/vault/1191",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle: {url}")

    def test_resolve_direct_download_url(self):
        direct_url = "https://tunnel5.dlproxy.uk/download/token123/HYAOI_NUP.part1.rar"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["url"], direct_url)
        self.assertEqual(result["filename"], "HYAOI_NUP.part1.rar")
        self.assertEqual(result["source_service"], "DataNodes")
        self.assertEqual(result["subfolder"], "HYAOI_NUP")
        self.assertEqual(result["article_title"], "HYAOI_NUP.part1")

    @patch("downloader.resolvers.datanodes.time.sleep", return_value=None)
    @patch("downloader.resolvers.datanodes.stealth_browser_session")
    @patch("downloader.resolvers.datanodes.requests.Session")
    def test_resolve_browser_flow(self, mock_session_cls, mock_browser_session, mock_sleep):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # Initial requests check returns 200 normal page
        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.text = "<html><body>DataNodes</body></html>"
        mock_session.get.return_value = init_resp

        # Mock browser page
        mock_page = MagicMock()
        mock_browser_session.return_value.__enter__.return_value = mock_page

        mock_page.ele.return_value = MagicMock()  # form exists
        mock_page.cookies.return_value = [{"name": "cf_clearance", "value": "test_cf"}]
        mock_page.user_agent = "Mozilla/5.0 Test"

        # Mock network packet for Step 2
        mock_packet = MagicMock()
        mock_packet.response.body = """
        <download-countdown :countdown="0"
            code="q7cltl21j7cl" rand="rand123" dl-token="dl_tok_456"
            name="HYAOI_NUP.part1.rar">
        </download-countdown>
        """
        mock_page.listen.wait.return_value = mock_packet

        # Mock run_js returns
        # 1) form submit js -> None
        # 2) turnstile token check -> 'turnstile_token_abc'
        # 3) post_js fetch -> {'url': 'https%3A%2F%2Ftunnel5.dlproxy.uk%2Fdownload%2Ffile123%2FHYAOI_NUP.part1.rar'}
        mock_page.run_js.side_effect = [
            None,
            "turnstile_token_abc",
            {"url": "https%3A%2F%2Ftunnel5.dlproxy.uk%2Fdownload%2Ffile123%2FHYAOI_NUP.part1.rar"},
        ]

        # HEAD request
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="HYAOI_NUP.part1.rar"',
            "Content-Length": "9663676416",
        }
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://datanodes.to/q7cltl21j7cl/HYAOI_NUP.part1.rar")
        self.assertEqual(
            result["url"],
            "https://tunnel5.dlproxy.uk/download/file123/HYAOI_NUP.part1.rar",
        )
        self.assertEqual(result["filename"], "HYAOI_NUP.part1.rar")
        self.assertEqual(result["source_service"], "DataNodes")
        self.assertEqual(result["subfolder"], "HYAOI_NUP")
        self.assertEqual(result["article_title"], "HYAOI_NUP.part1")

    @patch("downloader.resolvers.datanodes.requests.Session")
    def test_resolve_deleted_file_raises_filenotfound(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        init_resp = MagicMock()
        init_resp.status_code = 200
        init_resp.text = "<html><body>This file was deleted due to inactivity.</body></html>"
        mock_session.get.return_value = init_resp

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://datanodes.to/deleted123/file.rar")

    def test_addon_registration(self):
        addon = addon_registry.find_for_url("https://datanodes.to/q7cltl21j7cl/HYAOI_NUP.part1.rar")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.addon_id, "datanodes")
        self.assertEqual(addon.name, "DataNodes")


if __name__ == "__main__":
    unittest.main()
