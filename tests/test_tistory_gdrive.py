"""Unit tests for Tistory resolver external Google Drive links and GoogleDriveResolver enhancements."""

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from downloader.resolvers.gdrive import GoogleDriveResolver, extract_gdrive_id
from downloader.resolvers.tistory import TistoryResolver


class TestTistoryGDriveResolution(unittest.TestCase):
    def setUp(self):
        self.tistory_resolver = TistoryResolver()
        self.gdrive_resolver = GoogleDriveResolver()

    def test_gdrive_id_extraction_and_can_handle(self):
        urls = [
            (
                "https://drive.google.com/file/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view?usp=sharing",
                "181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            ),
            (
                "https://drive.google.com/file/u/0/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view",
                "181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            ),
            (
                "https://drive.google.com/open?id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
                "181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            ),
            (
                "https://drive.google.com/uc?export=download&id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
                "181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            ),
            (
                "https://drive.usercontent.google.com/download?id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh&export=download",
                "181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            ),
        ]
        for url, expected_id in urls:
            extracted = extract_gdrive_id(url)
            self.assertEqual(
                extracted,
                expected_id,
                f"Failed to extract ID from {url}: got {extracted}",
            )
            self.assertTrue(
                self.gdrive_resolver.can_handle(url),
                f"GoogleDriveResolver should handle {url}",
            )

    @patch("downloader.resolvers.tistory.requests.Session")
    def test_tistory_post_with_gdrive_link(self, mock_session_cls):
        mock_html = """<!DOCTYPE html>
<html>
<head>
    <meta property="og:title" content="[패치] sfc 드래곤볼Z 초사이어인 전설 한글패치 v1.7">
    <title>드래곤볼Z 초사이어인 전설 한글패치 배포</title>
</head>
<body>
    <div class="entry-content">
        <p>드래곤볼Z 초사이어인 전설 한글패치 v1.7 배포 페이지입니다.</p>
        <div class="patch-box">
            <a class="patch-btn-dl" href="https://drive.google.com/file/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view?usp=sharing" target="_blank">
                ▼ 구글 드라이브 (v1.7)
            </a>
        </div>
    </div>
</body>
</html>"""
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value.__enter__.return_value = mock_session

        fake_gdrive_result = {
            "url": "https://drive.usercontent.google.com/download?id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh&export=download",
            "filename": "dbz-ssd-ko-v1.7.zip",
            "source_service": "Google Drive",
            "headers": {"User-Agent": "test"},
            "cookies": "NID=123",
            "referer": "https://drive.google.com/file/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view",
        }

        with patch.object(
            GoogleDriveResolver, "resolve", return_value=fake_gdrive_result
        ) as mock_gdrive_resolve:
            res = self.tistory_resolver.resolve("https://7cloud.tistory.com/39")
            self.assertEqual(res["source_service"], "Google Drive")
            self.assertEqual(res["filename"], "dbz-ssd-ko-v1.7.zip")
            self.assertEqual(
                res["article_title"],
                "[패치] sfc 드래곤볼Z 초사이어인 전설 한글패치 v1.7",
            )
            self.assertEqual(
                res["subfolder"],
                "[패치] sfc 드래곤볼Z 초사이어인 전설 한글패치 v1.7",
            )
            self.assertEqual(
                res["source_page_url"], "https://7cloud.tistory.com/39"
            )
            mock_gdrive_resolve.assert_called_once()

    @patch("downloader.resolvers.tistory.requests.Session")
    def test_tistory_multiple_external_cloud_links_creates_extra_jobs(
        self, mock_session_cls
    ):
        mock_html = """<!DOCTYPE html>
<html>
<head>
    <meta property="og:title" content="파이널 판타지 7 한글판 디스크 세트">
</head>
<body>
    <div class="entry-content">
        <a href="https://drive.google.com/file/d/DISC1_AAA/view">구글 드라이브 (Disc 1)</a>
        <a href="https://drive.google.com/file/d/DISC2_BBB/view">구글 드라이브 (Disc 2)</a>
    </div>
</body>
</html>"""
        mock_resp = MagicMock()
        mock_resp.text = mock_html
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value.__enter__.return_value = mock_session

        fake_res1 = {
            "url": "https://drive.usercontent.google.com/download?id=DISC1_AAA",
            "filename": "ff7_disc1.iso",
            "source_service": "Google Drive",
        }

        with patch.object(
            GoogleDriveResolver, "resolve", return_value=fake_res1
        ):
            res = self.tistory_resolver.resolve("https://sample.tistory.com/100")
            self.assertEqual(res["filename"], "ff7_disc1.iso")
            self.assertIn("extra_jobs", res)
            self.assertEqual(len(res["extra_jobs"]), 1)
            extra = res["extra_jobs"][0]
            self.assertIn("DISC2_BBB", extra["url"])
            self.assertEqual(extra["filename"], "구글 드라이브 (Disc 2)")
            self.assertEqual(
                extra["options"]["subfolder"], "파이널 판타지 7 한글판 디스크 세트"
            )

    @patch("downloader.resolvers.gdrive.requests.Session")
    def test_gdrive_resolver_view_metadata_failure_resilience(
        self, mock_session_cls
    ):
        """View 페이지 메타데이터 접근 시 403 Forbidden이 발생해도 실제 직링크로 계속 진행하여 정상 다운로드되는지 검증."""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        # 1. view 페이지 요청 응답: 403 Forbidden (봇 차단 등)
        mock_view_resp = MagicMock()
        mock_view_resp.status_code = 403

        # 2. 직링크 다운로드 응답: 200 OK 바이너리 스트림 (with session.get(...) as resp)
        mock_dl_resp = MagicMock()
        mock_dl_resp.status_code = 200
        mock_dl_resp.headers = {
            "Content-Type": "application/zip",
            "Content-Disposition": 'attachment; filename="dbz-ssd-ko-v1.7.zip"',
        }
        mock_dl_resp.url = (
            "https://drive.usercontent.google.com/download?id=TEST_ID"
        )

        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_dl_resp

        mock_session.get.side_effect = [mock_view_resp, mock_cm]
        mock_session.cookies.get_dict.return_value = {"NID": "test"}

        result = self.gdrive_resolver.resolve(
            "https://drive.google.com/file/d/TEST_ID/view"
        )
        self.assertEqual(result["filename"], "dbz-ssd-ko-v1.7.zip")
        self.assertEqual(result["source_service"], "Google Drive")
        self.assertIn("TEST_ID", result["url"])

    def test_popup_extension_tistory_and_gdrive_patterns(self):
        popup_path = Path(__file__).resolve().parent.parent / "extension" / "popup.js"
        popup_js = popup_path.read_text(encoding="utf-8")

        # 1. Tistory scanner logic should be present
        self.assertIn("isTistory", popup_js)
        self.assertIn("[티스토리 첨부/링크]", popup_js)

        # 2. Google Drive regex should match multi-account and usercontent URLs
        gdrive_regex_match = re.search(
            r'\{ service: "Google Drive", regex: (/[^\n\r]+?/)i \}', popup_js
        )
        self.assertIsNotNone(gdrive_regex_match)

        test_urls = [
            "https://drive.google.com/file/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view?usp=sharing",
            "https://drive.google.com/file/u/0/d/181rXTkYGUye45FpYWVTsCMr7RcGNNpmh/view",
            "https://drive.google.com/open?id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            "https://drive.usercontent.google.com/download?id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
            "https://drive.google.com/uc?export=download&id=181rXTkYGUye45FpYWVTsCMr7RcGNNpmh",
        ]
        assert gdrive_regex_match is not None
        raw_pat = gdrive_regex_match.group(1)[1:-1]
        compiled_pat = re.compile(raw_pat, re.I)
        for u in test_urls:
            self.assertTrue(compiled_pat.search(u), f"Regex failed to match: {u}")


if __name__ == "__main__":
    unittest.main()
