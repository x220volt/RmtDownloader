"""블로그스팟 및 HTML 다운로드 방지/웹페이지 리졸버 단위 테스트."""

import unittest
from unittest.mock import MagicMock
from urllib.parse import urlparse

from downloader.resolvers import default_dispatcher
from downloader.resolvers.webpage import WebPageResolver
from downloader.engine import _validate_file_response


class TestWebPageAndHtmlDownloadRejection(unittest.TestCase):
    def setUp(self):
        self.wp_resolver = WebPageResolver()

    def test_blogspot_url_routed_to_webpage_resolver(self):
        url = "https://rom-kor-patch.blogspot.com/2026/10/sfc-cyber-knight-korean-patch.html"
        self.assertTrue(self.wp_resolver.can_handle(url))

        resolver = default_dispatcher.get_resolver(url)
        self.assertIsInstance(resolver, WebPageResolver)

    def test_githubio_project_routed_to_webpage_resolver(self):
        url = "https://ievy3.github.io/projects/dunamis15/"
        self.assertTrue(self.wp_resolver.can_handle(url))

        resolver = default_dispatcher.get_resolver(url)
        self.assertIsInstance(resolver, WebPageResolver)

    def test_general_html_url_routed_to_webpage_resolver(self):
        # 일반 웹페이지 .html URL도 DirectResolver가 아닌 WebPageResolver로 라우팅됨
        url = "https://example.com/games/retro/patch-v1.html"
        self.assertTrue(self.wp_resolver.can_handle(url))
        resolver = default_dispatcher.get_resolver(url)
        self.assertIsInstance(resolver, WebPageResolver)

    def test_direct_zip_file_not_routed_to_webpage_resolver(self):
        # 실제 바이너리 다운로드 파일은 WebPageResolver가 아니라 DirectResolver 등으로 처리됨
        zip_url = "https://example.com/downloads/game_patch.zip"
        self.assertFalse(self.wp_resolver.can_handle(zip_url))

    def test_html_response_rejected_even_if_url_ends_with_html(self):
        # 사용자가 다운로드 파일로 html을 받는 경우는 없으므로, Content-Type: text/html이면 예외 발생
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "Content-Type": "text/html; charset=UTF-8",
            "Content-Length": "35000",
        }
        url = "https://rom-kor-patch.blogspot.com/2026/10/sfc-cyber-knight-korean-patch.html"

        with self.assertRaises(ValueError) as ctx:
            _validate_file_response(mock_resp, url, filename="sfc-cyber-knight-korean-patch.html")

        self.assertIn("웹페이지(HTML)가 수신되었습니다", str(ctx.exception))

    def test_resolve_blogspot_mock_html_finds_google_drive_link(self):
        mock_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>SFC 사이버 나이트 한글 패치</title>
            <meta property="og:title" content="SFC 사이버 나이트 한글 패치">
        </head>
        <body>
            <div class="post-title">SFC 사이버 나이트 한글 패치</div>
            <div class="post-body">
                <p>사이버 나이트 한글 패치 배포합니다.</p>
                <p><a href="https://drive.google.com/uc?export=download&id=1pGVDFmQ_-qY7A5BFahanUb524cFsUIk3">구글 드라이브 다운로드</a></p>
            </div>
        </body>
        </html>
        """
        page_url = "https://rom-kor-patch.blogspot.com/2026/10/sfc-cyber-knight-korean-patch.html"
        _, candidates = self.wp_resolver.extract_candidates_from_html(mock_html, page_url)

        self.assertTrue(len(candidates) >= 1)
        target_url = candidates[0]["url"]
        self.assertIn("drive.google.com", target_url)
        self.assertIn("1pGVDFmQ_-qY7A5BFahanUb524cFsUIk3", target_url)

    def test_resolve_from_html_preserves_post_title_and_page_url(self):
        mock_html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>[XBOX360] 어메이징 스파이더 맨 2 한글패치 배포 : 네이버 블로그</title>
            <meta property="og:title" content="[XBOX360] 어메이징 스파이더 맨 2 한글패치 배포">
        </head>
        <body>
            <div class="se-main-container">
                <a href="https://www.mediafire.com/file/3mq9ze7ivi6lw1h/file.zip">MediaFire 다운로드</a>
            </div>
        </body>
        </html>
        """
        page_url = "https://blog.naver.com/jsyhmju/224446546866"
        with unittest.mock.patch("downloader.resolvers.mediafire.MediaFireResolver.resolve") as mock_mf_resolve:
            mock_mf_resolve.return_value = {
                "url": "https://download1593.mediafire.com/token/file.zip",
                "filename": "어메이징_스파이더맨2_한국어_패쳐_1.0.zip",
                "source_service": "MediaFire",
                "article_title": "어메이징_스파이더맨2_한국어_패쳐_1.0",
                "subfolder": "어메이징_스파이더맨2_한국어_패쳐_1.0",
            }
            resolved = self.wp_resolver.resolve_from_html(mock_html, page_url)
            self.assertEqual(resolved["article_title"], "[XBOX360] 어메이징 스파이더 맨 2 한글패치 배포")
            self.assertEqual(resolved["subfolder"], "[XBOX360] 어메이징 스파이더 맨 2 한글패치 배포")
            self.assertEqual(resolved["source_page_url"], page_url)
            self.assertEqual(resolved["filename"], "어메이징_스파이더맨2_한국어_패쳐_1.0.zip")


if __name__ == "__main__":
    unittest.main()
