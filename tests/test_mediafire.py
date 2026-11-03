import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.mediafire import MediaFireResolver


class MediaFireResolverTests(unittest.TestCase):
    """MediaFire 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = MediaFireResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://www.mediafire.com/file/fytn4diqt1d7tuq/UP1004-BLUS30557_00-MP3KORPATCHV0100-A0110-V0101-PE.pkg/file",
            "https://mediafire.com/file/abcdef123456/sample.zip",
            "https://www.mediafire.com/download/abcdef123456/game.rar",
            "https://www.mediafire.com/?fytn4diqt1d7tuq",
            "https://www.mediafire.com/view/fytn4diqt1d7tuq/sample.txt",
            "https://download2297.mediafire.com/hash123/key/file.pkg",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
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
        direct_url = "https://download2297.mediafire.com/hash123/key/sample.pkg"
        result = self.resolver.resolve(direct_url)
        self.assertEqual(result["url"], direct_url)
        self.assertEqual(result["filename"], "sample.pkg")
        self.assertEqual(result["source_service"], "MediaFire")

    @patch("downloader.resolvers.mediafire.requests.Session")
    def test_resolve_standard_html(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        page_html = """
        <html>
        <head><title>Test Game Patch</title></head>
        <body>
        <div class="filename">TestGamePatch.pkg</div>
        <a id="downloadButton" href="https://download100.mediafire.com/token123/file/TestGamePatch.pkg">Download</a>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = page_html

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="TestGamePatch.pkg"',
            "Content-Length": "10485760",
        }

        mock_session.get.return_value = page_resp
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://www.mediafire.com/file/fytn4diqt1d7tuq/TestGamePatch.pkg/file")
        self.assertEqual(
            result["url"],
            "https://download100.mediafire.com/token123/file/TestGamePatch.pkg",
        )
        self.assertEqual(result["filename"], "TestGamePatch.pkg")
        self.assertEqual(result["source_service"], "MediaFire")
        self.assertEqual(result["article_title"], "TestGamePatch")
        self.assertEqual(result["subfolder"], "TestGamePatch")

    @patch("downloader.resolvers.mediafire.requests.Session")
    def test_resolve_file_removed_raises_filenotfound(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        page_html = """
        <html>
        <body>
        <div class="error">The file you attempted to download has been removed or is unavailable. File Removed.</div>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = page_html
        mock_session.get.return_value = page_resp

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://www.mediafire.com/file/deleted123/file")

    def test_addon_registration(self):
        addon = addon_registry.find_for_url("https://www.mediafire.com/file/fytn4diqt1d7tuq/sample.pkg/file")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.addon_id, "mediafire")
        self.assertEqual(addon.name, "MediaFire")


if __name__ == "__main__":
    unittest.main()
