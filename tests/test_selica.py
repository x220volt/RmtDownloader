import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.dispatcher import default_dispatcher
from downloader.resolvers.selica import SelicaResolver
from downloader.resolvers.webpage import WebPageResolver


class SelicaResolverTests(unittest.TestCase):
    """셀리카 공방 (selica.kr) 리졸버 및 애드온 단위 테스트."""

    def setUp(self):
        self.resolver = SelicaResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://selica.kr/msx/msxtrain1",
            "https://selica.kr/api/dl/release/62",
            "https://selica.kr/api/dl/pick/66?v=both",
            "https://selica.kr/api/dl/postdocs/4",
            "https://selica.kr/tools/yama",
            "https://selica.kr/msx/msxtrain1/download?next=%2Fapi%2Fdl%2Frelease%2F62",
            "http://selica.kr/msx/socer",
            "https://www.selica.kr/msx/msxtrain1",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "",
            "https://example.com/msx/msxtrain1",
            "https://github.com/selica/repo",
            "https://dcinside.com/board/view/?id=selica",
        ]
        for url in invalid_urls:
            self.assertFalse(self.resolver.can_handle(url), f"Should NOT handle: {url}")

    @patch("downloader.resolvers.selica.requests.Session")
    def test_resolve_direct_api_link(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session_cls.return_value.__enter__.return_value = mock_session

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = "https://selica.kr/api/dl/release/62"
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="MSXTRAIN_KO_PATCH_v1.0.zip"; filename*=UTF-8\'\'MSXTRAIN_KO_PATCH_v1.0.zip',
            "Content-Length": "101288",
        }
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://selica.kr/api/dl/release/62")
        self.assertEqual(result["url"], "https://selica.kr/api/dl/release/62")
        self.assertEqual(result["filename"], "MSXTRAIN_KO_PATCH_v1.0.zip")
        self.assertEqual(result["size"], 101288)
        self.assertEqual(result["cookies"], "dl_ok=1")
        self.assertEqual(result["headers"]["Cookie"], "dl_ok=1")
        self.assertFalse(result["allow_parallel"])

    @patch("downloader.resolvers.selica.requests.Session")
    def test_resolve_page_single_link(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # 1. Page GET response
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.url = "https://selica.kr/msx/msxtrain1"
        page_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>MSX TraiN — 셀리카 공방</title></head>
        <body>
            <main>
                <h1>MSX TraiN</h1>
                <div class="prose"><p>본문 설명글</p></div>
                <aside>
                    <div class="DownloadBox">
                        <span class="relTitle">MSX TraiN 한글패치</span>
                        <a class="btn" href="/api/dl/release/62">↓ 전체 받기 (98.9 KB)</a>
                    </div>
                </aside>
            </main>
        </body>
        </html>
        """
        mock_session.get.return_value = page_resp

        # 2. HEAD response for /api/dl/release/62
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = "https://selica.kr/api/dl/release/62"
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="MSXTRAIN_KO_PATCH_v1.0.zip"',
            "Content-Length": "101288",
        }
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://selica.kr/msx/msxtrain1")
        self.assertEqual(result["url"], "https://selica.kr/api/dl/release/62")
        self.assertEqual(result["filename"], "MSXTRAIN_KO_PATCH_v1.0.zip")
        self.assertEqual(result["size"], 101288)
        self.assertEqual(result["article_title"], "MSX TraiN")
        self.assertEqual(result["subfolder"], "MSX TraiN")
        self.assertEqual(result["source_page_url"], "https://selica.kr/msx/msxtrain1")
        self.assertNotIn("files", result)

    @patch("downloader.resolvers.selica.requests.Session")
    def test_resolve_page_bundle_both_filters_normal_and_cheat(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.url = "https://selica.kr/msx/socer"
        page_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>소서리언 (Sorcerian) — 셀리카 공방</title></head>
        <body>
            <main>
                <h1>소서리언</h1>
                <div class="DownloadBox">
                    <a href="/api/dl/pick/66?v=normal">일반판</a>
                    <a href="/api/dl/pick/66?v=cheat">치트판</a>
                    <a href="/api/dl/pick/66?v=both">둘 다 받기</a>
                </div>
            </main>
        </body>
        </html>
        """
        mock_session.get.return_value = page_resp

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = "https://selica.kr/api/dl/pick/66?v=both"
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="socer_all.zip"',
            "Content-Length": "299128",
        }
        mock_session.head.return_value = head_resp

        result = self.resolver.resolve("https://selica.kr/msx/socer")
        self.assertEqual(result["url"], "https://selica.kr/api/dl/pick/66?v=both")
        self.assertEqual(result["filename"], "socer_all.zip")
        # normal과 cheat는 both가 있으므로 단일 결과로 유지됨
        self.assertNotIn("files", result)

    @patch("downloader.resolvers.selica.requests.Session")
    def test_resolve_page_multi_file_with_guide_doc(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.url = "https://selica.kr/msx/asteka"
        page_resp.text = """
        <!DOCTYPE html>
        <html>
        <head><title>태양의 신전 아스테카2 — 셀리카 공방</title></head>
        <body>
            <main>
                <h1>아스테카2</h1>
                <div class="DownloadBox">
                    <a href="/api/dl/release/101">전체 받기</a>
                    <a href="/api/dl/doc/30">공략집 받기</a>
                </div>
            </main>
        </body>
        </html>
        """
        mock_session.get.return_value = page_resp

        def mock_head(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.url = url
            if "release/101" in url:
                resp.headers = {
                    "Content-Disposition": 'attachment; filename="Asteka2_KR_Patch_v1.0.zip"',
                    "Content-Length": "23256",
                }
            else:
                resp.headers = {
                    "Content-Disposition": 'attachment; filename*=UTF-8\'\'%ED%83%9C%EC%96%91%EC%9D%98_%EC%8B%A0%EC%A0%84_%EC%95%84%EC%8A%A4%ED%85%8C%EC%B9%B42_%EC%99%84%EC%A0%84%EA%B3%B5%EB%9E%B5%EC%A7%91.pdf',
                    "Content-Length": "963280",
                }
            return resp

        mock_session.head.side_effect = mock_head

        result = self.resolver.resolve("https://selica.kr/msx/asteka")
        self.assertEqual(result["filename"], "Asteka2_KR_Patch_v1.0.zip")
        self.assertIn("files", result)
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(result["files"][0]["filename"], "Asteka2_KR_Patch_v1.0.zip")
        self.assertEqual(result["files"][1]["filename"], "태양의_신전_아스테카2_완전공략집.pdf")

    @patch("downloader.resolvers.selica.requests.Session")
    def test_resolve_download_notice_url(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session_cls.return_value.__enter__.return_value = mock_session

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.url = "https://selica.kr/api/dl/release/62"
        head_resp.headers = {
            "Content-Disposition": 'attachment; filename="MSXTRAIN_KO_PATCH_v1.0.zip"',
            "Content-Length": "101288",
        }
        mock_session.head.return_value = head_resp

        # notice page URL with ?next=%2Fapi%2Fdl%2Frelease%2F62
        notice_url = "https://selica.kr/msx/msxtrain1/download?next=%2Fapi%2Fdl%2Frelease%2F62"
        result = self.resolver.resolve(notice_url)
        self.assertEqual(result["url"], "https://selica.kr/api/dl/release/62")
        self.assertEqual(result["filename"], "MSXTRAIN_KO_PATCH_v1.0.zip")

    def test_addon_registration(self):
        addon = addon_registry.get_addon("selica")
        self.assertIsNotNone(addon)
        self.assertEqual(addon.name, "Selica")
        self.assertEqual(addon.badge_class, "badge-selica")
        self.assertTrue(addon.can_handle("https://selica.kr/msx/msxtrain1"))
        self.assertTrue(addon.can_handle("https://selica.kr/api/dl/release/62"))

    def test_dispatcher_finds_selica_resolver(self):
        resolver = default_dispatcher.get_resolver("https://selica.kr/msx/msxtrain1")
        self.assertIsNotNone(resolver)
        self.assertIsInstance(resolver, SelicaResolver)


if __name__ == "__main__":
    unittest.main()
