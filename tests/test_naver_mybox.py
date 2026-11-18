"""네이버 MYBOX 리졸버 및 폴더 공유 처리 단위 테스트."""

import unittest
from unittest.mock import MagicMock, patch

from downloader.engine import _validate_file_response
from downloader.resolvers.direct import DirectResolver
from downloader.resolvers.naver_mybox import NaverMyBoxResolver


class TestNaverMyBoxResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = NaverMyBoxResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://mybox.naver.com/share/list?shareKey=GbILTHx392Mxcm0ZtmKfrZaCPtTjDAsDIYpUNTh8nHsC",
            "https://mybox.naver.com/share/detail?fileKey=AbCdEf123456",
            "https://mybox.naver.com/share/AbCdEf123456",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "https://mybox.naver.com/file/download.api?svcType=MYBOX-WEB&resourceKey=xyz",
            "https://cafe.naver.com/hansicgu/123",
            "https://example.com/test.zip",
        ]
        for url in invalid_urls:
            self.assertFalse(self.resolver.can_handle(url), f"Should not handle: {url}")

    @patch("requests.Session")
    def test_resolve_single_file(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies.get_dict.return_value = {"NDV_SHARE": "test_cookie_123"}

        # Mock property response
        prop_resp = MagicMock()
        prop_resp.ok = True
        prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "resourceType": "file",
                "resourceKey": "res_key_single_file",
                "resourceName": "patch.zip",
                "hasPassword": False,
                "blockDownload": False,
            },
        }

        # Mock file/property response
        file_prop_resp = MagicMock()
        file_prop_resp.ok = True
        file_prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "resourceSize": 1048576,
            },
        }

        mock_session.get.side_effect = [prop_resp, file_prop_resp]

        url = "https://mybox.naver.com/share/list?shareKey=singleShareKey"
        res = self.resolver.resolve(url)

        self.assertEqual(res["filename"], "patch.zip")
        self.assertIn("res_key_single_file", res["url"])
        self.assertEqual(res["file_size"], 1048576)
        self.assertEqual(res["cookies"], {"NDV_SHARE": "test_cookie_123"})
        self.assertEqual(res["source_service"], "Naver MYBOX")
        self.assertNotIn("files", res)

    @patch("requests.Session")
    def test_resolve_shared_folder(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies.get_dict.return_value = {"NDV_SHARE": "ndv_token_abc"}

        # Mock property response (folder)
        prop_resp = MagicMock()
        prop_resp.ok = True
        prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "resourceType": "folder",
                "resourceKey": "folder_res_key",
                "resourceName": "R6(0.9a)",
                "hasPassword": False,
                "blockDownload": False,
            },
        }

        # Mock folder list response (5 files)
        folder_resp = MagicMock()
        folder_resp.ok = True
        folder_resp.json.return_value = {
            "code": "0",
            "result": {
                "totalCount": 2,
                "list": [
                    {
                        "resourceType": "file",
                        "resourceKey": "k_track03",
                        "subPath": "/Track03.xdelta",
                        "resourceSize": 12829673,
                        "authToken": "tok1",
                    },
                    {
                        "resourceType": "file",
                        "resourceKey": "k_readme",
                        "subPath": "/README.txt",
                        "resourceSize": 1419,
                        "authToken": "tok2",
                    },
                ],
            },
        }

        mock_session.get.side_effect = [prop_resp, folder_resp]

        url = "https://mybox.naver.com/share/list?shareKey=folderShareKey"
        res = self.resolver.resolve(url)

        self.assertEqual(res["filename"], "Track03.xdelta")
        self.assertEqual(res["subfolder"], "R6(0.9a)")
        self.assertEqual(res["cookies"], {"NDV_SHARE": "ndv_token_abc"})
        self.assertIn("files", res)
        self.assertEqual(len(res["files"]), 2)
        self.assertEqual(res["files"][0]["name"], "Track03.xdelta")
        self.assertEqual(res["files"][1]["name"], "README.txt")
        self.assertIn("authToken=tok1", res["files"][0]["url"])

    @patch("requests.Session")
    def test_resolve_empty_folder_raises(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        mock_session.cookies.get_dict.return_value = {}

        prop_resp = MagicMock()
        prop_resp.ok = True
        prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "resourceType": "folder",
                "resourceKey": "empty_folder_key",
                "resourceName": "EmptyFolder",
                "hasPassword": False,
                "blockDownload": False,
            },
        }

        folder_resp = MagicMock()
        folder_resp.ok = True
        folder_resp.json.return_value = {
            "code": "0",
            "result": {"list": []},
        }

        mock_session.get.side_effect = [prop_resp, folder_resp]

        url = "https://mybox.naver.com/share/list?shareKey=emptyShare"
        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve(url)

    def test_validate_file_response_rejects_json_error(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Content-Length": "35",
        }
        resp.json.return_value = {"code": 1206, "message": "Auth Fail"}

        # Binary/patch file expecting download should reject json response
        with self.assertRaises(ValueError) as ctx:
            _validate_file_response(resp, "https://example.com/dl", "Track03.xdelta")
        self.assertIn("Auth Fail", str(ctx.exception))

    def test_validate_file_response_allows_explicit_json_download(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {
            "Content-Type": "application/json",
            "Content-Disposition": 'attachment; filename="data.json"',
        }

        ct, is_explicit_html = _validate_file_response(
            resp, "https://example.com/data.json", "data.json"
        )
        self.assertEqual(ct, "application/json")
        self.assertFalse(is_explicit_html)

    def test_direct_resolver_preserves_options(self):
        direct = DirectResolver()
        opts = {
            "filename": "custom_name.bin",
            "cookies": {"NDV_SHARE": "xyz"},
            "referer": "https://mybox.naver.com/",
            "headers": {"X-Custom": "val"},
        }
        res = direct.resolve("https://fs.mybox.naver.com/file/download.api?res=1", opts)
        self.assertEqual(res["filename"], "custom_name.bin")
        self.assertEqual(res["cookies"], {"NDV_SHARE": "xyz"})
        self.assertEqual(res["referer"], "https://mybox.naver.com/")
        self.assertEqual(res["headers"].get("X-Custom"), "val")


if __name__ == "__main__":
    unittest.main()
