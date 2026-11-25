"""Unit tests for Tistory resolver external Google Drive links and GoogleDriveResolver enhancements."""

import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from downloader.resolvers.gdrive import (
    GoogleDriveResolver,
    extract_gdrive_folder_id,
    extract_gdrive_id,
)
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

    @patch("downloader.resolvers.tistory.requests.Session")
    def test_tistory_fileblock_kakaocdn_dna_split_archive_filenames(
        self, mock_session_cls
    ):
        dna_url_1 = (
            "https://blog.kakaocdn.net/dna/KLq5Z/btsDAvIlpFB/"
            "AAAAAAAAAAAAAAAAAAAAAGCPvB3xxG4CkxaUCdHR8gKaN_jSCCXrLQnh3sQg7FW5/"
            "Sly%20Cooper%20and%20the%20Thievius%20Raccoonus.7z.001"
            "?credential=abc&expires=1796050799&signature=sig1&attach=1&knm=tfile.001"
        )
        dna_url_65 = (
            "https://blog.kakaocdn.net/dna/XYZ99/btsDB999999/"
            "AAAAAAAAAAAAAAAAAAAAAGCPvB3xxG4CkxaUCdHR8gKaN_jSCCXrLQnh3sQg7FW5/"
            "Sly%20Cooper%20and%20the%20Thievius%20Raccoonus.7z.065"
            "?credential=abc&expires=1796050799&signature=sig65&attach=1&knm=tfile.065"
        )
        mock_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta property="og:title" content="슬라이 쿠퍼 1 (Sly Cooper)">
</head>
<body>
    <div class="contents_style">
        <figure class="fileblock" data-ke-align="alignCenter">
            <a class="" href="{dna_url_1}">
                <div class="image"></div>
                <div class="desc">
                    <div class="filename"><span class="name">Sly Cooper and the Thievius Raccoonus.7z.001</span></div>
                    <div class="size">19.53MB</div>
                </div>
            </a>
        </figure>
        <figure class="fileblock" data-ke-align="alignCenter">
            <a class="" href="{dna_url_65}">
                <div class="image"></div>
                <div class="desc">
                    <div class="filename"><span class="name">Sly Cooper and the Thievius Raccoonus.7z.065</span></div>
                    <div class="size">11.86MB</div>
                </div>
            </a>
        </figure>
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

        self.assertTrue(self.tistory_resolver.can_handle(dna_url_1))
        self.assertTrue(self.tistory_resolver.is_direct_attachment_url(dna_url_1))

        res = self.tistory_resolver.resolve("https://fpwjemkula.tistory.com/125")
        self.assertEqual(res["source_service"], "Tistory")
        self.assertEqual(
            res["filename"], "Sly Cooper and the Thievius Raccoonus.7z.001"
        )
        self.assertEqual(len(res["files"]), 2)
        self.assertEqual(
            res["files"][0]["name"],
            "Sly Cooper and the Thievius Raccoonus.7z.001",
        )
        self.assertEqual(
            res["files"][1]["name"],
            "Sly Cooper and the Thievius Raccoonus.7z.065",
        )

        # Direct resolution of a Kakao CDN /dna/ URL should keep the real path filename, not knm=tfile.001
        direct_res = self.tistory_resolver.resolve(dna_url_1)
        self.assertEqual(direct_res["source_service"], "Tistory")
        self.assertEqual(
            direct_res["filename"],
            "Sly Cooper and the Thievius Raccoonus.7z.001",
        )

    def test_gdrive_folder_id_extraction_and_can_handle(self):
        urls = [
            (
                "https://drive.google.com/drive/folders/1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6",
                "1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6",
            ),
            (
                "https://drive.google.com/drive/u/0/folders/1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6?usp=sharing",
                "1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6",
            ),
            (
                "https://drive.google.com/folders/1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6",
                "1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6",
            ),
        ]
        for url, expected_id in urls:
            self.assertEqual(extract_gdrive_folder_id(url), expected_id)
            self.assertTrue(self.gdrive_resolver.can_handle(url))

    @patch("downloader.resolvers.gdrive.requests.Session")
    def test_gdrive_folder_resolution_multiple_files(self, mock_session_cls):
        mock_folder_html = """<!DOCTYPE html>
<html>
<head>
    <title>[PS2] 블러디 로어 3 [Kor-patch ver 1.0] - Google Drive</title>
</head>
<body>
    <script>
    window['_DRIVE_ivd'] = '\\x5b\\x5b\\x5b\\x22file1_id_12345678901234567890\\x22,\\x5b\\x22folder_id_xyz\\x22\\x5d,\\x22BloodyRoar3_KR_1.0.7z\\x22,\\x22application\\/x-compressed\\x22,0,null,0,0,0,0,0,null,null,12460958\\x5d,\\x5b\\x22file2_id_12345678901234567890\\x22,\\x5b\\x22folder_id_xyz\\x22\\x5d,\\x22BloodyRoar3_manual.pdf\\x22,\\x22application\\/pdf\\x22,0,null,0,0,0,0,0,null,null,54321\\x5d\\x5d\\x5d';
    </script>
</body>
</html>"""
        def mock_get(url, *args, **kwargs):
            resp = MagicMock()
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            if "folders" in url:
                resp.status_code = 200
                resp.url = url
                resp.text = mock_folder_html
                resp.headers = {"Content-Type": "text/html"}
            elif "file/d/" in url:
                resp.status_code = 200
                resp.url = url
                resp.text = "<html><title>BloodyRoar3_KR_1.0.7z - Google Drive</title></html>"
                resp.headers = {"Content-Type": "text/html"}
            else:
                resp.status_code = 200
                resp.url = "https://drive.usercontent.google.com/download?id=file1_id&export=download"
                resp.headers = {
                    "Content-Type": "application/x-compressed",
                    "Content-Length": "12460958",
                    "Content-Disposition": 'attachment; filename="BloodyRoar3_KR_1.0.7z"',
                }
            return resp

        mock_session = MagicMock()
        mock_session.get.side_effect = mock_get
        mock_session_cls.return_value.__enter__.return_value = mock_session

        res = self.gdrive_resolver.resolve(
            "https://drive.google.com/drive/folders/1ZgK-oXFNRswlQ7Om7YipCH2bKa981sJ6"
        )

        self.assertEqual(res["filename"], "BloodyRoar3_KR_1.0.7z")
        self.assertEqual(
            res["subfolder"], "[PS2] 블러디 로어 3 [Kor-patch ver 1.0]"
        )
        self.assertEqual(
            res["article_title"], "[PS2] 블러디 로어 3 [Kor-patch ver 1.0]"
        )
        self.assertIn("extra_jobs", res)
        self.assertEqual(len(res["extra_jobs"]), 1)
        extra = res["extra_jobs"][0]
        self.assertIn("file2_id_12345678901234567890", extra["url"])
        self.assertEqual(extra["filename"], "BloodyRoar3_manual.pdf")
        self.assertEqual(
            extra["options"]["subfolder"],
            "[PS2] 블러디 로어 3 [Kor-patch ver 1.0]",
        )


if __name__ == "__main__":
    unittest.main()
