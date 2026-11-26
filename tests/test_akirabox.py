import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.akirabox import AkiraBoxResolver, _clean_duplicated_extension, _extract_file_id, _format_size


class AkiraBoxResolverTests(unittest.TestCase):
    """AkiraBox 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = AkiraBoxResolver()

    def test_addon_registration(self):
        addon = addon_registry.get_addon("akirabox")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.name, "AkiraBox")
        self.assertEqual(addon.badge_class, "badge-akirabox")
        resolver = addon.create_resolver()
        self.assertIsInstance(resolver, AkiraBoxResolver)

    def test_can_handle(self):
        valid_urls = [
            "https://akirabox.to/LK0G1aakdG91/file",
            "https://akirabox.to/LK0G1aakdG91",
            "https://www.akirabox.to/LK0G1aakdG91/file",
            "https://akirabox.com/LK0G1aakdG91/file",
            "https://akirabox.net/LK0G1aakdG91",
            "https://akirabox.com/download/e1.testtoken/filename.zip",
            "https://download.akirabox.com/uploads/guests/coq5o3-game.zip?access=123",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://google.com",
            "https://vikingfile.com/f/12345",
            "https://buzzheavier.com/n5v3itgq2iqt",
            "https://akirabox.to/",
            "",
            None,
        ]
        for url in invalid_urls:
            target = url or ""
            self.assertFalse(self.resolver.can_handle(target), f"Should not handle: {url}")

    def test_helpers(self):
        self.assertEqual(_extract_file_id("https://akirabox.to/LK0G1aakdG91/file"), "LK0G1aakdG91")
        self.assertEqual(_extract_file_id("https://akirabox.com/LK0G1aakdG91"), "LK0G1aakdG91")
        self.assertEqual(_clean_duplicated_extension("game.zip.zip"), "game.zip")
        self.assertEqual(_clean_duplicated_extension("archive.7z.7z"), "archive.7z")
        self.assertEqual(_clean_duplicated_extension("normal.zip"), "normal.zip")
        self.assertIn("GB", _format_size(2963473185))

    @patch("downloader.resolvers.akirabox.requests.get")
    def test_metadata_extraction(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = """
        <html>
        <head>
            <title>[한] 완다와 거상.zip · AkiraBox</title>
            <meta property="og:title" content="[한] 완다와 거상.zip · AkiraBox" />
        </head>
        <body>
            <script>
            self.__next_f.push([1,"4:{\\"name\\":\\"[한] 완다와 거상.zip\\",\\"size\\":2963473185,\\"sha256\\":{\\"value\\":\\"b8ad33fef39d2ea8e0fec95d837c315655eea302fafe483ac57dc773684a3ccb\\",\\"pending\\":false}}"]);
            </script>
        </body>
        </html>
        """
        mock_get.return_value = resp

        meta = self.resolver._fetch_metadata_from_html("https://akirabox.to/LK0G1aakdG91/file")
        self.assertEqual(meta["filename"], "[한] 완다와 거상.zip")
        self.assertEqual(meta["size"], 2963473185)
        self.assertEqual(meta["sha256"], "b8ad33fef39d2ea8e0fec95d837c315655eea302fafe483ac57dc773684a3ccb")

    @patch("downloader.resolvers.akirabox.requests.head")
    @patch("downloader.resolvers.akirabox.requests.get")
    def test_resolve_with_options_direct_url(self, mock_get, mock_head):
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = '{"name":"Wanda.zip","size":1000}'
        mock_get.return_value = page_resp

        head_resp = MagicMock()
        head_resp.status_code = 302
        head_resp.headers = {"Location": "https://download.akirabox.com/uploads/final_cdn.zip"}
        mock_head.return_value = head_resp

        result = self.resolver.resolve(
            "https://akirabox.to/LK0G1aakdG91/file",
            options={"direct_url": "https://akirabox.com/download/token/Wanda.zip.zip"}
        )

        self.assertEqual(result["url"], "https://download.akirabox.com/uploads/final_cdn.zip")
        self.assertEqual(result["filename"], "Wanda.zip")
        self.assertEqual(result["source_service"], "AkiraBox")
        self.assertEqual(result["subfolder"], "Wanda")
        self.assertEqual(result["expected_size"], 1000)

    @patch("downloader.resolvers.akirabox.requests.head")
    @patch.object(AkiraBoxResolver, "_resolve_via_drission")
    @patch.object(AkiraBoxResolver, "_fetch_metadata_from_html")
    def test_resolve_via_drission_success(self, mock_meta, mock_drission, mock_head):
        mock_meta.return_value = {
            "filename": "[한] 완다와 거상.zip",
            "size": 2963473185,
            "sha256": "b8ad33",
            "title": "완다",
        }
        mock_drission.return_value = (
            "https://akirabox.com/download/token/[한]+완다와+거상.zip.zip",
            "[한] 완다와 거상.zip · AkiraBox",
            "cf_clearance=abc",
        )
        head_resp = MagicMock()
        head_resp.status_code = 302
        head_resp.headers = {"Location": "https://download.akirabox.com/uploads/guests/wanda.zip"}
        mock_head.return_value = head_resp

        result = self.resolver.resolve("https://akirabox.to/LK0G1aakdG91/file")

        self.assertEqual(result["url"], "https://download.akirabox.com/uploads/guests/wanda.zip")
        self.assertEqual(result["filename"], "[한] 완다와 거상.zip")
        self.assertEqual(result["source_service"], "AkiraBox")
        self.assertEqual(result["subfolder"], "[한] 완다와 거상")
        self.assertEqual(result["cookies"], "cf_clearance=abc")

    @patch("downloader.resolvers.akirabox.requests.get")
    def test_metadata_outlet_ignored(self, mock_get):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = """
        <html>
        <head>
            <title>[한] 완다와 거상.zip · AkiraBox</title>
            <meta property="og:title" content="[한] 완다와 거상.zip" />
        </head>
        <body>
            <script>
            // Next.js internal component with name Next.MetadataOutlet
            self.__next_f.push([1,"4:{\\"name\\":\\"Next.MetadataOutlet\\"}"]);
            self.__next_f.push([1,"5:{\\"name\\":\\"[한] 완다와 거상.zip\\",\\"extension\\":\\"zip\\",\\"size\\":2968834701}"]);
            </script>
        </body>
        </html>
        """
        mock_get.return_value = resp

        meta = self.resolver._fetch_metadata_from_html("https://akirabox.to/LK0G1aakdG91/file")
        self.assertEqual(meta["filename"], "[한] 완다와 거상.zip")
        self.assertEqual(meta["size"], 2968834701)

    @patch("downloader.resolvers.akirabox.requests.head")
    def test_fallback_filename_from_direct_and_cdn_url(self, mock_head):
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.headers = {}
        mock_head.return_value = head_resp

        # direct_url with '+' and '.zip.zip'
        res1 = self.resolver.resolve(
            "https://akirabox.com/download/token/%5B%ED%95%9C%5D+%EC%99%84%EB%8B%A4%EC%99%80+%EA%B1%B0%EC%83%81.zip.zip"
        )
        self.assertEqual(res1["filename"], "[한] 완다와 거상.zip")

        # cdn_url with random hash prefix
        res2 = self.resolver.resolve(
            "https://download.akirabox.com/uploads/guests/coq5o3d4iBkOxqjWEde-%5B%ED%95%9C%5D%20%EC%99%84%EB%8B%A4%EC%99%80%20%EA%B1%B0%EC%83%81.zip?access=123"
        )
        self.assertEqual(res2["filename"], "[한] 완다와 거상.zip")


if __name__ == "__main__":
    unittest.main()
