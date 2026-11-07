import unittest
from unittest.mock import MagicMock, Mock, patch

from downloader.url_helper import (
    extract_service_password,
    get_candidate_passwords,
    parse_password_list,
)


class TestAutoPasswords(unittest.TestCase):
    def test_parse_password_list_formats(self):
        # 쉼표 구분
        self.assertEqual(
            parse_password_list("1234, abcd, 2026, 7777"),
            ["1234", "abcd", "2026", "7777"],
        )
        # 공백 구분
        self.assertEqual(
            parse_password_list("1234 abcd 2026 7777"),
            ["1234", "abcd", "2026", "7777"],
        )
        # 세미콜론 및 줄바꿈 구분
        self.assertEqual(
            parse_password_list("1234; abcd\n2026\r\n7777"),
            ["1234", "abcd", "2026", "7777"],
        )
        # 중복 제거 및 빈 항목 무시 (순서 보존)
        self.assertEqual(
            parse_password_list("1234, abcd, 1234, 2026, abcd, "),
            ["1234", "abcd", "2026"],
        )
        # 빈 값
        self.assertEqual(parse_password_list(""), [])
        self.assertEqual(parse_password_list(None), [])
        # 리스트 입력
        self.assertEqual(
            parse_password_list(["pass1", "pass2", "pass1"]), ["pass1", "pass2"]
        )

    def test_get_candidate_passwords_priority(self):
        # 1. URL 자체에 지정된 암호가 최우선
        with patch(
            "config.load_settings",
            return_value={"auto_passwords": "auto1, auto2", "terabox_password": "tb1"},
        ):
            cands = get_candidate_passwords(
                url="https://terabox.com/s/xyz?pwd=urlpwd",
                options={"password": "manual1"},
            )
            self.assertEqual(cands, ["urlpwd", "manual1", "auto1", "auto2", "tb1"])

        # 2. URL에 암호 없고 options에만 있는 경우
        with patch(
            "config.load_settings",
            return_value={"auto_passwords": "auto1, auto2", "terabox_password": "tb1"},
        ):
            cands = get_candidate_passwords(
                url="https://terabox.com/s/xyz",
                options={"password": "manual1"},
            )
            self.assertEqual(cands, ["manual1", "auto1", "auto2", "tb1"])

        # 3. URL과 options 모두 암호 없는 경우 -> 사전 등록된 설정만 순서대로
        with patch(
            "config.load_settings",
            return_value={"auto_passwords": "auto1, auto2", "terabox_password": "tb1"},
        ):
            cands = get_candidate_passwords(
                url="https://terabox.com/s/xyz",
                options={},
            )
            self.assertEqual(cands, ["auto1", "auto2", "tb1"])

    def test_extract_service_password_backwards_compatible(self):
        with patch(
            "config.load_settings",
            return_value={"auto_passwords": "auto1, auto2", "terabox_password": "tb1"},
        ):
            clean_u, pwd = extract_service_password("https://terabox.com/s/xyz")
            self.assertEqual(clean_u, "https://terabox.com/s/xyz")
            self.assertEqual(pwd, "auto1")

    def test_terabox_multiple_candidate_passwords(self):
        from downloader.resolvers.terabox import TeraBoxResolver

        resolver = TeraBoxResolver()
        fake_session = Mock()
        fake_session.__enter__ = Mock(return_value=fake_session)
        fake_session.__exit__ = Mock(return_value=False)
        fake_session.headers = {}
        fake_session.cookies = Mock()
        fake_session.cookies.get_dict.return_value = {}

        # 1. 초기 페이지 요청 -> 암호 필요 페이지 (/sharing/init)
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://www.1024tera.com/sharing/init?surl=test123",
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<script>var i18n={"msg":"Please input the extraction code"};</script>',
        )

        # 2. 암호 검증 요청 1차(wrong1) -> 실패 (errno 400135)
        verify_fail = Mock(status_code=200, ok=True)
        verify_fail.json.return_value = {"errno": 400135}

        # 3. 암호 검증 요청 2차(correct_pass) -> 성공 (errno 0)
        verify_ok = Mock(status_code=200, ok=True)
        verify_ok.json.return_value = {"errno": 0, "randsk": "test_randsk_val"}

        # 4. 파일 목록 API 요청 -> 성공
        list_resp = Mock(status_code=200, ok=True)
        list_resp.json.return_value = {
            "errno": 0,
            "share_id": 12345,
            "uk": 67890,
            "list": [
                {
                    "fs_id": "111",
                    "isdir": "0",
                    "server_filename": "GameArchive.rar",
                    "size": "500000000",
                    "dlink": "https://d.terabox.app/file/mock_game_link",
                }
            ],
        }

        init_resp = Mock(status_code=200, ok=True, text="<html></html>", headers={})
        fake_session.get.side_effect = [page_resp, init_resp, list_resp]
        fake_session.post.side_effect = [verify_fail, verify_ok]

        with (
            patch(
                "downloader.resolvers.terabox.requests.Session",
                return_value=fake_session,
            ),
            patch("config.load_settings", return_value={"terabox_cookie": ""}),
        ):
            res = resolver.resolve(
                "https://1024terabox.com/s/1test123",
                options={
                    "candidate_passwords": "wrong1, correct_pass",
                    "terabox_mode": "download",
                    "terabox_cookie": "",
                },
            )
            self.assertEqual(res["filename"], "GameArchive.rar")
            self.assertEqual(res["url"], "https://d.terabox.app/file/mock_game_link")
            self.assertEqual(fake_session.post.call_count, 2)

    def test_gigafile_multiple_candidate_dlkeys(self):
        from downloader.resolvers.gigafile import GigaFileResolver

        resolver = GigaFileResolver()
        fake_session = Mock()
        fake_session.__enter__ = Mock(return_value=fake_session)
        fake_session.__exit__ = Mock(return_value=False)
        fake_session.headers = {}
        fake_session.cookies = Mock()
        fake_session.cookies.get_dict.return_value = {}

        # 페이지 응답
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://1.gigafile.nu/0203-testfile",
            text='''
            <input type="text" id="dlkey" placeholder="다운로드 키">
            var file = "0203-testfile";
            var server = "1.gigafile.nu";
            var size = 123456;
            ''',
        )

        # 1차 dlkey 검사 (key1) -> 불일치 (status 1)
        check_fail = Mock(status_code=200, ok=True)
        check_fail.json.return_value = {"status": 1}

        # 2차 dlkey 검사 (key2) -> 일치 (status 0)
        check_ok = Mock(status_code=200, ok=True)
        check_ok.json.return_value = {"status": 0}

        # 파일명 API
        name_resp = Mock(status_code=200, ok=True)
        name_resp.json.return_value = {"status": 0, "filename": "backup.zip"}

        fake_session.get.side_effect = [page_resp, check_fail, check_ok, name_resp]

        with patch(
            "downloader.resolvers.gigafile.requests.Session", return_value=fake_session
        ):
            res = resolver.resolve(
                "https://1.gigafile.nu/0203-testfile",
                options={"candidate_passwords": "key1, key2"},
            )
            self.assertEqual(res["filename"], "backup.zip")
            self.assertIn("dlkey=key2", res["url"])

    def test_transferit_candidate_passwords(self):
        from downloader.resolvers.transferit import TransferitResolver

        resolver = TransferitResolver()
        mock_api = Mock()
        mock_node = Mock()
        mock_node.is_file = True
        mock_node.handle = "h1"
        mock_node.name = "package.zip"
        mock_node.size = 1000
        mock_node.key = [1, 2, 3]

        # 1차 시도 (암호 없음 None) -> 실패 (password required)
        # 2차 시도 (사전 등록 암호 secret123) -> 성공
        mock_api.fetch_transfer.side_effect = [
            Exception("password required to decrypt transfer"),
            ([{"handle": "h1"}], "token_val"),
        ]

        with (
            patch(
                "transferit.MegaAPI",
                return_value=mock_api,
            ),
            patch(
                "transferit.TransferNode.from_dict",
                return_value=mock_node,
            ),
        ):
            res = resolver.resolve(
                "https://transfer.it/t/mock_transfer_id",
                options={"auto_passwords": "secret123"},
            )
            self.assertEqual(res["filename"], "package.zip")
            self.assertEqual(mock_api.fetch_transfer.call_count, 2)
            self.assertEqual(mock_api.fetch_transfer.call_count, 2)


    def test_terabox_all_candidate_passwords_fail(self):
        from downloader.resolvers.terabox import TeraBoxResolver

        resolver = TeraBoxResolver()
        fake_session = Mock()
        fake_session.__enter__ = Mock(return_value=fake_session)
        fake_session.__exit__ = Mock(return_value=False)
        fake_session.headers = {}
        fake_session.cookies = Mock()
        fake_session.cookies.get_dict.return_value = {}

        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://www.1024tera.com/sharing/init?surl=test123",
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<script>var i18n={"msg":"Please input the extraction code"};</script>',
        )
        verify_fail1 = Mock(status_code=200, ok=True)
        verify_fail1.json.return_value = {"errno": 400135}
        verify_fail2 = Mock(status_code=200, ok=True)
        verify_fail2.json.return_value = {"errno": 400135}

        fake_session.get.side_effect = [page_resp]
        fake_session.post.side_effect = [verify_fail1, verify_fail2]

        with (
            patch(
                "downloader.resolvers.terabox.requests.Session",
                return_value=fake_session,
            ),
            patch(
                "config.load_settings",
                return_value={"terabox_cookie": "", "terabox_password": ""},
            ),
        ):
            with self.assertRaises(ValueError) as ctx:
                resolver.resolve(
                    "https://1024terabox.com/s/1test123",
                    options={
                        "candidate_passwords": "wrong1, wrong2",
                        "terabox_mode": "download",
                        "terabox_cookie": "",
                    },
                )
            self.assertIn("'wrong1', 'wrong2'", str(ctx.exception))
            self.assertEqual(fake_session.post.call_count, 2)

    def test_gofile_multiple_passwords(self):
        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/TESTID"

        # 1차 시도 (pw1): passwordWrong
        resp1 = Mock(status_code=200, ok=True)
        resp1.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": False,
                "password": True,
                "passwordStatus": "passwordWrong",
            },
        }

        # 2차 시도 (pw2): 성공
        resp2 = Mock(status_code=200, ok=True)
        resp2.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": True,
                "children": {
                    "f1": {
                        "type": "file",
                        "name": "GameFile.zip",
                        "link": "https://store.gofile.io/dl/GameFile.zip",
                    }
                },
            },
        }

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.side_effect = [resp1, resp2]

        with (
            patch(
                "downloader.resolvers.gofile.requests.Session",
                return_value=fake_session,
            ),
            patch(
                "downloader.resolvers.gofile.get_guest_token", return_value="guest_tok"
            ),
            patch(
                "downloader.resolvers.gofile.get_gofile_secret", return_value="sec_123"
            ),
        ):
            res = resolver.resolve(url, options={"auto_passwords": "pw1, pw2"})
            self.assertEqual(res["filename"], "GameFile.zip")
            self.assertEqual(fake_session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
