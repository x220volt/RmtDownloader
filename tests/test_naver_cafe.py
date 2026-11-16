"""네이버 카페 리졸버 및 첨부파일 추출 단위 테스트."""

import unittest
from unittest.mock import MagicMock, patch
from downloader.resolvers.naver_cafe import NaverCafeResolver, parse_naver_cafe_url


class TestNaverCafeResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = NaverCafeResolver()

    def test_parse_naver_cafe_url_various_formats(self):
        # 1. PC 쿼리스트링 형태
        u1 = "https://cafe.naver.com/ArticleRead.nhn?clubid=29771102&articleid=358137"
        p1 = parse_naver_cafe_url(u1)
        self.assertIsNotNone(p1)
        self.assertEqual(p1["club_id"], "29771102")
        self.assertEqual(p1["article_id"], "358137")

        # 2. 짧은 경로 형태
        u2 = "https://cafe.naver.com/moopung/358137"
        p2 = parse_naver_cafe_url(u2)
        self.assertIsNotNone(p2)
        self.assertEqual(p2["cafe_name"], "moopung")
        self.assertEqual(p2["article_id"], "358137")

        # 3. 신규 패스 형태
        u3 = "https://cafe.naver.com/ca-fe/cafes/29771102/articles/358137"
        p3 = parse_naver_cafe_url(u3)
        self.assertIsNotNone(p3)
        self.assertEqual(p3["club_id"], "29771102")
        self.assertEqual(p3["article_id"], "358137")

        # 4. 모바일 URL
        u4 = "https://m.cafe.naver.com/moopung/358137"
        p4 = parse_naver_cafe_url(u4)
        self.assertIsNotNone(p4)
        self.assertEqual(p4["cafe_name"], "moopung")
        self.assertEqual(p4["article_id"], "358137")

        # 5. downapi 직링크는 게시글 URL 파싱 대상이 아님
        u5 = "https://downapi.cafe.naver.com/v1.0/cafes/29771102/articles/358137/attachments/123/download"
        self.assertTrue(self.resolver.can_handle(u5))

    def test_downapi_direct_resolve(self):
        url = "https://downapi.cafe.naver.com/v1.0/cafes/29771102/articles/358137/attachments/123/download"
        res = self.resolver.resolve(url, options={"cookies": "NID_AUT=test_auth; NID_SES=test_ses"})
        self.assertEqual(res["url"], url)
        self.assertEqual(res["cookies"], "NID_AUT=test_auth; NID_SES=test_ses")
        self.assertEqual(res["headers"]["Cookie"], "NID_AUT=test_auth; NID_SES=test_ses")
        self.assertEqual(res["source_service"], "Naver Cafe")

    def test_unauthorized_member_only_article_raises_permission_error(self):
        # 비로그인 상태에서 401을 발생시키는 실제 네이버 카페 멤버 전용 글
        url = "https://cafe.naver.com/moopung/358137"
        with self.assertRaises(PermissionError) as ctx:
            self.resolver.resolve(url)
        self.assertIn("멤버 전용 글", str(ctx.exception))
        self.assertIn("크롬 확장 프로그램", str(ctx.exception))

    @patch("requests.Session")
    def test_resolve_with_mocked_api_response(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {
                "article": {
                    "subject": "테스트 네이버 카페 게시글 제목",
                    "contentHtml": "<p>내용</p>",
                },
                "attaches": [
                    {
                        "type": "F",
                        "fullName": "자료_파트1.zip",
                        "name": "자료_파트1.zip",
                        "url": "https://downapi.cafe.naver.com/v1.0/cafes/12345/articles/67890/attachments/1/download",
                        "size": 10485760,
                    },
                    {
                        "type": "F",
                        "fullName": "자료_파트2.zip",
                        "name": "자료_파트2.zip",
                        "url": "https://downapi.cafe.naver.com/v1.0/cafes/12345/articles/67890/attachments/2/download",
                        "size": 20971520,
                    },
                ],
            }
        }
        mock_session.get.return_value = mock_resp

        res = self.resolver.resolve(
            "https://cafe.naver.com/ca-fe/cafes/12345/articles/67890",
            options={"cookies": "NID_AUT=mock_cookie"},
        )

        self.assertEqual(res["url"], "https://downapi.cafe.naver.com/v1.0/cafes/12345/articles/67890/attachments/1/download")
        self.assertEqual(res["filename"], "자료_파트1.zip")
        self.assertEqual(res["article_title"], "테스트 네이버 카페 게시글 제목")
        self.assertEqual(res["subfolder"], "테스트 네이버 카페 게시글 제목")
        self.assertEqual(len(res["files"]), 2)
        self.assertEqual(res["files"][1]["name"], "자료_파트2.zip")
        self.assertEqual(res["cookies"], "NID_AUT=mock_cookie")
        self.assertEqual(res["headers"]["Cookie"], "NID_AUT=mock_cookie")


if __name__ == "__main__":
    unittest.main()
