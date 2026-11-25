"""Unit tests for TeeTeeTee resolver."""

import unittest
from unittest.mock import MagicMock, patch

from downloader.resolvers.dispatcher import default_dispatcher
from downloader.resolvers.teeteetee import TeeTeeTeeResolver


class TestTeeTeeTeeResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = TeeTeeTeeResolver()

    def test_can_handle(self):
        urls = [
            "https://teeteetee-korean.com/projects/tales-of-rebirth",
            "https://teeteetee-korean.com/projects/sengoku-basara-2/",
            "https://teeteetee-korean.com/api/patch-releases?project=tales-of-rebirth&mobile=1",
            "https://teeteetee-korean.com/api/rebirth-xdelta",
            "https://teeteetee-korean.com/downloads/cvhd-v1.0.zip",
        ]
        for u in urls:
            self.assertTrue(self.resolver.can_handle(u), f"Should handle: {u}")
            matched = default_dispatcher.get_resolver(u)
            self.assertIsNotNone(matched)
            self.assertEqual(matched.name, "TeeTeeTee")

        self.assertFalse(self.resolver.can_handle("https://example.com/projects/test"))

    @patch("downloader.resolvers.teeteetee.requests.Session")
    def test_resolve_mobile_xdelta(self, mock_session_cls):
        mock_page_html = "<html><head><title>테일즈 오브 리버스 한국어 패치 | 한글패치 아카이브</title></head></html>"
        mock_page_resp = MagicMock()
        mock_page_resp.status_code = 200
        mock_page_resp.text = mock_page_html

        mock_mobile_resp = MagicMock()
        mock_mobile_resp.status_code = 200
        mock_mobile_resp.headers = {
            "Content-Disposition": 'attachment; filename="tales-of-rebirth-v1-41.xdelta"',
            "Content-Length": "103598382",
        }

        mock_session = MagicMock()
        def mock_get(url, *args, **kwargs):
            if "/projects/" in url:
                return mock_page_resp
            if "mobile=1" in url:
                return mock_mobile_resp
            return MagicMock(status_code=404)

        mock_session.get.side_effect = mock_get
        mock_session_cls.return_value = mock_session

        res = self.resolver.resolve("https://teeteetee-korean.com/projects/tales-of-rebirth")
        self.assertEqual(res["filename"], "tales-of-rebirth-v1-41.xdelta")
        self.assertEqual(res["subfolder"], "테일즈 오브 리버스")
        self.assertEqual(res["size"], 103598382)
        self.assertEqual(res["source_service"], "TeeTeeTee")
        self.assertIn("mobile=1", res["url"])

    @patch("downloader.resolvers.teeteetee.requests.Session")
    def test_resolve_legacy_xdelta(self, mock_session_cls):
        mock_page_resp = MagicMock()
        mock_page_resp.status_code = 200
        mock_page_resp.text = "<html><head><title>전국 BASARA2 영웅외전 한글패치</title></head></html>"

        mock_legacy_resp = MagicMock()
        mock_legacy_resp.status_code = 200
        mock_legacy_resp.headers = {
            "Content-Disposition": 'attachment; filename="Sengoku-BASARA2-Heroes-Korean-v0.99-hotfix.xdelta"',
            "Content-Length": "5432100",
        }

        mock_session = MagicMock()
        def mock_get(url, *args, **kwargs):
            if "/projects/" in url:
                return mock_page_resp
            if "mobile=1" in url:
                # Returns 200 but not an attachment (e.g. current: null)
                return MagicMock(status_code=200, headers={"Content-Type": "application/json"})
            if "basara-xdelta" in url:
                return mock_legacy_resp
            return MagicMock(status_code=404)

        mock_session.get.side_effect = mock_get
        mock_session_cls.return_value = mock_session

        res = self.resolver.resolve("https://teeteetee-korean.com/projects/sengoku-basara-2")
        self.assertEqual(res["filename"], "Sengoku-BASARA2-Heroes-Korean-v0.99-hotfix.xdelta")
        self.assertEqual(res["subfolder"], "전국 BASARA2 영웅외전")
        self.assertEqual(res["size"], 5432100)
        self.assertEqual(res["source_service"], "TeeTeeTee")

    @patch("downloader.resolvers.teeteetee.requests.Session")
    def test_resolve_web_only_exclusion(self, mock_session_cls):
        mock_page_resp = MagicMock()
        mock_page_resp.status_code = 200
        mock_page_resp.text = "<html><head><title>유희왕! 스고로쿠의 스고로쿠 한글패치</title></head></html>"

        mock_mobile_resp = MagicMock()
        mock_mobile_resp.status_code = 503
        mock_mobile_resp.text = "패치 점검을 위해 배포를 잠시 중단했습니다. 재개 후 이용해 주세요."

        mock_meta_resp = MagicMock()
        mock_meta_resp.status_code = 200
        mock_meta_resp.json.return_value = {
            "manifest": {
                "delivery": {
                    "mobile": None,
                    "mode": "web-both",
                    "mobileExclusionReason": "관리자가 PC와 모바일 공용 웹패치를 선택했습니다.",
                }
            }
        }

        mock_session = MagicMock()
        def mock_get(url, *args, **kwargs):
            if "/projects/" in url:
                return mock_page_resp
            if "mobile=1" in url:
                return mock_mobile_resp
            if "/api/patch-releases?project=" in url:
                return mock_meta_resp
            return MagicMock(status_code=404)

        mock_session.get.side_effect = mock_get
        mock_session_cls.return_value = mock_session

        with self.assertRaises(FileNotFoundError) as ctx:
            self.resolver.resolve("https://teeteetee-korean.com/projects/current-gba-sugoroku")
        self.assertIn("관리자가 PC와 모바일 공용 웹패치를 선택했습니다", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
