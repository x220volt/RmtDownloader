import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.romspure import RomspureResolver


class RomspureResolverTests(unittest.TestCase):
    """Romspure 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = RomspureResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://romspure.cc/download/metal-gear-solid-hd-collection-3-25052/2",
            "https://romspure.cc/download/metal-gear-solid-hd-collection-3-25052/1",
            "https://romspure.cc/download/puyo-puyo-2-ketteiban-10468",
            "https://romspure.cc/roms/sony-playstation-vita/metal-gear-solid-hd-collection/",
            "https://www.romspure.cc/roms/nintendo-switch/pokemon-sword/",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle {url}")

        invalid_urls = [
            "https://google.com",
            "https://romsfun.com/download/game-123",
            "https://planetemu.net/rom/arcade/pac-man",
            "https://romspure.cc/about-us",
            "",
            None,
        ]
        for url in invalid_urls:
            self.assertFalse(self.resolver.can_handle(url), f"Should not handle {url}")

    @patch("downloader.resolvers.romspure.requests.Session")
    def test_resolve_individual_download_page(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        html_page = """
        <html>
        <head><title>Download Metal Gear Solid HD Collection - PSV ROM</title></head>
        <body>
        <div data-download-ajax data-post-id="25052" data-index="2" data-delay="3000" data-total="7000">
            <a data-download-link href="#">Download</a>
        </div>
        </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.ok = True
        page_resp.status_code = 200
        page_resp.text = html_page

        nonce_resp = MagicMock()
        nonce_resp.ok = True
        nonce_resp.status_code = 200
        nonce_resp.json.return_value = {"success": True, "data": {"nonce": "test_nonce_123"}}

        link_resp = MagicMock()
        link_resp.ok = True
        link_resp.status_code = 200
        link_resp.json.return_value = {
            "success": True,
            "data": {
                "url": "https://sto.romsfast.com/PSV/MetalGearSolidHD.7z?e=123&s=abc",
                "name": "Metal Gear Solid HD (PCSE00020)",
                "size": "3.34 G",
            },
        }

        check_resp = MagicMock()
        check_resp.status_code = 206
        check_resp.headers = {}

        mock_session.get.side_effect = [page_resp, check_resp]
        mock_session.post.side_effect = [nonce_resp, link_resp]

        result = self.resolver.resolve(
            "https://romspure.cc/download/metal-gear-solid-hd-collection-3-25052/2"
        )
        self.assertEqual(
            result["url"],
            "https://sto.romsfast.com/PSV/MetalGearSolidHD.7z?e=123&s=abc",
        )
        self.assertEqual(result["filename"], "Metal Gear Solid HD (PCSE00020).7z")
        self.assertEqual(result["source_service"], "Romspure")
        self.assertEqual(result["article_title"], "Metal Gear Solid HD Collection")
        self.assertIsNone(result["cookies"])

    @patch("downloader.resolvers.romspure.stealth_browser_session")
    @patch("downloader.resolvers.romspure.requests.Session")
    def test_resolve_cloudflare_challenge(self, mock_session_cls, mock_browser_session):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        html_page = """
        <html>
        <head><title>Download Protected Game - PSV</title></head>
        <body>
        <div data-download-ajax data-post-id="9999" data-index="1"></div>
        </body>
        </html>
        """
        page_resp = MagicMock(ok=True, status_code=200, text=html_page)
        nonce_resp = MagicMock(ok=True, json=lambda: {"success": True, "data": {"nonce": "nonce_val"}})
        link_resp = MagicMock(
            ok=True,
            json=lambda: {
                "success": True,
                "data": {
                    "url": "https://sto2.romsforever.co/game.zip?e=123",
                    "name": "Protected Game",
                },
            },
        )
        # Check returns 403 with Cloudflare challenge
        check_resp = MagicMock(
            status_code=403,
            headers={"cf-mitigated": "challenge"},
        )

        mock_session.get.side_effect = [page_resp, check_resp]
        mock_session.post.side_effect = [nonce_resp, link_resp]

        # Mock browser session
        mock_page = MagicMock()
        mock_page.user_agent = "CustomBrowserUA/1.0"
        mock_page.cookies.return_value = [{"name": "cf_clearance", "value": "solved_clearance_token"}]
        mock_browser_session.return_value.__enter__.return_value = mock_page

        result = self.resolver.resolve("https://romspure.cc/download/protected-game-9999/1")
        self.assertEqual(result["url"], "https://sto2.romsforever.co/game.zip?e=123")
        self.assertEqual(result["cookies"], "cf_clearance=solved_clearance_token")
        self.assertEqual(result["headers"]["User-Agent"], "CustomBrowserUA/1.0")

    @patch("downloader.resolvers.romspure.requests.Session")
    def test_resolve_multi_part_page(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session

        # 1. Selection page with 2 sub-links
        multi_page = """
        <html>
        <head><title>Download Multi Disc Game</title></head>
        <body>
        <a href="https://romspure.cc/download/multi-disc-1234/1">Disc 1</a>
        <a href="https://romspure.cc/download/multi-disc-1234/2">Disc 2</a>
        </body>
        </html>
        """
        resp_multi = MagicMock(ok=True, status_code=200, text=multi_page)

        # 2. Disc 1 individual page
        disc1_page = """
        <html>
        <head><title>Download Multi Disc Game - Disc 1</title></head>
        <body>
        <div data-download-ajax data-post-id="1234" data-index="1"></div>
        </body>
        </html>
        """
        resp_disc1 = MagicMock(ok=True, status_code=200, text=disc1_page)
        check_resp = MagicMock(status_code=200, headers={})

        nonce_resp = MagicMock(ok=True, json=lambda: {"success": True, "data": {"nonce": "nonce_val"}})
        link_resp = MagicMock(
            ok=True,
            json=lambda: {
                "success": True,
                "data": {
                    "url": "https://sto.romsfast.com/Disc1.zip",
                    "name": "Multi Disc Game (Disc 1)",
                },
            },
        )

        mock_session.get.side_effect = [resp_multi, resp_disc1, check_resp]
        mock_session.post.side_effect = [nonce_resp, link_resp]

        result = self.resolver.resolve("https://romspure.cc/download/multi-disc-1234")
        self.assertEqual(result["filename"], "Multi Disc Game (Disc 1).zip")
        self.assertIn("extra_jobs", result)
        self.assertEqual(len(result["extra_jobs"]), 1)
        self.assertEqual(
            result["extra_jobs"][0]["url"],
            "https://romspure.cc/download/multi-disc-1234/2",
        )

    @patch("downloader.resolvers.romspure.requests.Session")
    def test_resolve_404_raises_error(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.get.return_value = MagicMock(ok=False, status_code=404)

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://romspure.cc/download/non-existent-9999/1")

    def test_addon_registration(self):
        addon = addon_registry.get_addon("romspure")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.name, "Romspure")
        self.assertIn("romspure.cc", addon.domains)
        self.assertIsInstance(addon.create_resolver(), RomspureResolver)


if __name__ == "__main__":
    unittest.main()

