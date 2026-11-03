import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.filekeeper import FileKeeperResolver


class FileKeeperResolverTests(unittest.TestCase):
    """FileKeeper 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = FileKeeperResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://filekeeper.net/7lkwblvxjags/HYAOI_NUP.part1.rar",
            "https://filekeeper.net/7lkwblvxjags",
            "https://www.filekeeper.net/abcdef123456/sample.zip",
            "https://filekeeper.net/download",
            "https://edge01.fetchio.cc/f/some_token_hash?sig=12345",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://mediafire.com/file/12345/sample.zip",
            "https://vimm.net/vault/1191",
            "https://planetemu.net/rom/sega/game",
            "https://example.com/file.zip",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle: {url}")

    def test_resolve_direct_download_url(self):
        direct_url = "https://edge01.fetchio.cc/f/token123/HYAOI_NUP.part1.rar"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["url"], direct_url)
        self.assertEqual(result["filename"], "HYAOI_NUP.part1.rar")
        self.assertEqual(result["source_service"], "FileKeeper")
        self.assertEqual(result["subfolder"], "HYAOI_NUP")
        self.assertEqual(result["article_title"], "HYAOI_NUP.part1")

    @patch("downloader.resolvers.filekeeper.requests.Session")
    def test_resolve_standard_html(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        page_html = """
        <html>
        <head><title>Download HYAOI NUP part1 rar</title></head>
        <body>
        <h2>HYAOI_NUP.part1.rar</h2>
        <div id="download-countdown" data-countdown="5" data-code="7lkwblvxjags"
             data-referer="" data-rand="r123" data-method=""
             data-has-password="false" data-has-captcha="false"></div>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = page_html

        post_resp = MagicMock()
        post_resp.status_code = 302
        post_resp.headers = {
            "Location": "https://edge01.fetchio.cc/f/token999/HYAOI_NUP.part1.rar?sig=abc"
        }

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="HYAOI_NUP.part1.rar"',
            "Content-Length": "9663676416",
        }

        mock_session.get.return_value = page_resp
        mock_session.post.return_value = post_resp
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://filekeeper.net/7lkwblvxjags/HYAOI_NUP.part1.rar")
        self.assertEqual(
            result["url"],
            "https://edge01.fetchio.cc/f/token999/HYAOI_NUP.part1.rar?sig=abc",
        )
        self.assertEqual(result["filename"], "HYAOI_NUP.part1.rar")
        self.assertEqual(result["source_service"], "FileKeeper")
        self.assertEqual(result["subfolder"], "HYAOI_NUP")
        self.assertEqual(result["article_title"], "HYAOI_NUP.part1")

    @patch("downloader.resolvers.filekeeper.requests.Session")
    def test_resolve_file_removed_raises_filenotfound(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        page_html = """
        <html>
        <body>
        <h1>FileKeeper</h1>
        <div>File not found or deleted.</div>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = page_html
        mock_session.get.return_value = page_resp

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://filekeeper.net/deleted123/file.rar")

    def test_addon_registration(self):
        addon = addon_registry.find_for_url("https://filekeeper.net/7lkwblvxjags/HYAOI_NUP.part1.rar")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.addon_id, "filekeeper")
        self.assertEqual(addon.name, "FileKeeper")


if __name__ == "__main__":
    unittest.main()

