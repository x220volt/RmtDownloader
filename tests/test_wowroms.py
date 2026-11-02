import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from downloader.engine import DownloadTask
from downloader.resolvers.wowroms import WowromsResolver


class WowromsResolverTests(unittest.TestCase):
    """WoWroms 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = WowromsResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://wowroms.com/download/roms/nintendo-gameboy/download-nada-asatarou-no-powerful-mahjong-tsugi-no-itte-100-dai-japan/9742.html",
            "https://wowroms.com/en/isos/playstation-portable/god-of-war-chains-of-olympus/33588.html",
            "https://wowroms.com/en/roms/nintendo-gameboy-advance/pokemon-firered-version-usa/13828.html",
            "https://www.wowroms.com/emulators/list/1",
            "https://wowroms.com/en/roms/list",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle {url}")

        invalid_urls = [
            "https://google.com",
            "https://romsfun.com/download/game-123",
            "https://example.com/file.zip",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle {url}")

    @patch("downloader.resolvers.wowroms.requests.Session")
    def test_resolve_direct_download_page(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        html_content = """
        <html>
        <head><title>Test Game - Gameboy rom download</title></head>
        <body>
        <form id="submitForm" method="post">
            <input type="hidden" name="emuid" value="26">
            <input type="hidden" name="id" value="9742">
            <input type="hidden" name="file" value="Test Game (Japan).zip">
        </form>
        <script>
            var ajaxLinkUrl = "/download/emulators-roms/download/9742/Test%2BGame";
        </script>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.ok = True
        page_resp.status_code = 200
        page_resp.text = html_content
        mock_session.get.return_value = page_resp

        ajax_resp = MagicMock()
        ajax_resp.ok = True
        ajax_resp.status_code = 200
        ajax_resp.json.return_value = {
            "s": True,
            "link": "https://filesdw.wowroms.com/d/roms/g/token123/k1/k2",
        }
        mock_session.post.return_value = ajax_resp

        target_url = "https://wowroms.com/download/roms/nintendo-gameboy/download-test-game/9742.html"
        result = self.resolver.resolve(target_url)

        self.assertEqual(result["url"], "https://filesdw.wowroms.com/d/roms/g/token123/k1/k2")
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["filename"], "Test Game (Japan).zip")
        self.assertEqual(result["source_service"], "WoWroms")
        self.assertEqual(result["post_data"], {"emuid": "26", "id": "9742", "file": "Test Game (Japan).zip"})
        self.assertEqual(result["referer"], target_url)

    @patch("downloader.resolvers.wowroms.requests.Session")
    def test_resolve_detail_page_to_download_page(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        detail_html = """
        <html>
        <head><title>Test Game Detail</title></head>
        <body>
            <a href="/en/roms/nintendo-gameboy/download-test-game/9742.html">Download rom</a>
        </body>
        </html>
        """
        download_html = """
        <html>
        <head><title>Test Game - Download</title></head>
        <body>
        <form id="submitForm" method="post">
            <input type="hidden" name="emuid" value="26">
            <input type="hidden" name="id" value="9742">
            <input type="hidden" name="file" value="Test Game (Japan).zip">
        </form>
        <script>
            var ajaxLinkUrl = "/download/emulators-roms/download/9742/Test%2BGame";
        </script>
        </body>
        </html>
        """

        resp1 = MagicMock(ok=True, status_code=200, text=detail_html)
        resp2 = MagicMock(ok=True, status_code=200, text=download_html)
        mock_session.get.side_effect = [resp1, resp2]

        ajax_resp = MagicMock(ok=True, status_code=200)
        ajax_resp.json.return_value = {
            "s": True,
            "link": "https://slave5t.wowroms.com/d/roms/g/action456",
        }
        mock_session.post.return_value = ajax_resp

        target_url = "https://wowroms.com/en/roms/nintendo-gameboy/test-game/9742.html"
        result = self.resolver.resolve(target_url)

        self.assertEqual(result["url"], "https://slave5t.wowroms.com/d/roms/g/action456")
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["filename"], "Test Game (Japan).zip")
        self.assertEqual(result["source_service"], "WoWroms")

    @patch("downloader.resolvers.wowroms.requests.Session")
    def test_resolve_404_error(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        resp = MagicMock(ok=False, status_code=404)
        mock_session.get.return_value = resp

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://wowroms.com/download/roms/not-found/99999.html")


class DownloadTaskPostMethodTests(unittest.TestCase):
    """DownloadTask의 POST 전송 기능 테스트."""

    @patch("downloader.engine.requests.Session")
    def test_download_task_post_method(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": "12",
            "Content-Disposition": 'attachment; filename="test.zip"',
        }
        mock_resp.iter_content.return_value = [b"hello ", b"world!"]
        mock_session.post.return_value = mock_resp

        with tempfile.TemporaryDirectory() as tmpdir:
            task = DownloadTask(
                url="https://filesdw.wowroms.com/d/test",
                dest_dir=tmpdir,
                filename="test.zip",
                method="POST",
                post_data={"key": "val"},
            )
            res = task.run()
            self.assertTrue(res.get("success"))
            out_file = os.path.join(tmpdir, "test.zip")
            self.assertTrue(os.path.exists(out_file))
            with open(out_file, "rb") as f:
                self.assertEqual(f.read(), b"hello world!")

            mock_session.post.assert_called_once()
            args, kwargs = mock_session.post.call_args
            self.assertEqual(args[0], "https://filesdw.wowroms.com/d/test")
            self.assertEqual(kwargs.get("data"), {"key": "val"})


if __name__ == "__main__":
    unittest.main()

