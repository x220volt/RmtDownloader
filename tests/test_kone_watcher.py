"""Unit and integration tests for KoneWatcher and Kone.gg board link extraction."""

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import config
from app import app
from downloader.kone_watcher import KoneWatcher, sanitize_folder_name
from downloader.url_helper import parse_download_candidates


class KoneWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.seen_file = Path(self.temp_dir.name) / "kone_seen_posts.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sanitize_folder_name(self):
        raw = '타임어택: [테스트] / "폴더명" * ? < > |'
        clean = sanitize_folder_name(raw)
        self.assertNotIn(":", clean)
        self.assertNotIn("/", clean)
        self.assertNotIn('"', clean)
        self.assertNotIn("?", clean)
        self.assertTrue("타임어택" in clean)

    def test_embedded_base64_url_extraction(self):
        # 1. Base64 URL 붙어있는 텍스트
        b64 = base64.b64encode(
            b"https://1024terabox.com/s/15yww7pCtsW_8JDTRelsMRw \xec\xb6\x94\xec\xb6\x9c \xec\xbd\x94\xeb\x93\x9c:dao5"
        ).decode("ascii")
        text = f"적용함.{b64}"
        cands = parse_download_candidates(raw_text=text)
        self.assertEqual(len(cands), 1)
        self.assertIn("1024terabox.com", cands[0]["url"])
        self.assertEqual(cands[0]["options"].get("password"), "dao5")

    def test_kone_watcher_seeding_on_first_run(self):
        mock_settings = {
            "kone_auto_check": True,
            "kone_board_url": "https://kone.gg/s/retrogame",
            "kone_check_interval_sec": 180,
            "kone_subfolder": True,
        }
        mock_dm = MagicMock()

        board_html = """
        <html>
            <body>
                <a href="/s/retrogame/post1234567890">
                    <div title="첫번째 게시글">첫번째</div>
                </a>
                <a href="/s/retrogame/post0987654321">
                    <div title="두번째 게시글">두번째</div>
                </a>
            </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = board_html

        with patch("downloader.kone_watcher.SEEN_POSTS_FILE", self.seen_file):
            with patch("requests.get", return_value=mock_resp):
                watcher = KoneWatcher(
                    settings_getter=lambda: mock_settings, download_manager=mock_dm
                )
                # 최초 실행 (is_manual=False)
                res = watcher.check_board(is_manual=False)
                self.assertTrue(res.get("initialized"))
                self.assertEqual(res.get("seeded_count"), 2)
                # 초기화 시점에는 기존 글을 즉시 다운로드 추가하지 않음
                mock_dm.add_urls.assert_not_called()

                # 시딩 파일이 저장되었는지 확인
                self.assertTrue(self.seen_file.exists())
                saved_seen = json.loads(self.seen_file.read_text(encoding="utf-8"))
                self.assertIn("post1234567890", saved_seen)
                self.assertIn("post0987654321", saved_seen)

    def test_kone_watcher_detects_new_post_and_enqueues(self):
        mock_settings = {
            "kone_auto_check": True,
            "kone_board_url": "https://kone.gg/s/retrogame",
            "kone_check_interval_sec": 180,
            "kone_subfolder": True,
        }
        mock_dm = MagicMock()
        mock_dm.add_urls.return_value = ["job-1"]

        # 이미 post1234567890은 본 상태
        self.seen_file.write_text(json.dumps(["post1234567890"]), encoding="utf-8")

        board_html = """
        <html>
            <body>
                <a href="/s/retrogame/newpost99999999">
                    <div title="새로운 게임 롬 공유">새로운 게임 롬 공유</div>
                </a>
                <a href="/s/retrogame/post1234567890">
                    <div title="첫번째 게시글">첫번째</div>
                </a>
            </body>
        </html>
        """

        # 상세 페이지 mock: SvelteKit SSR JSON 스크립트에 Base64 다운로드 링크 포함
        b64_url_pw = base64.b64encode(
            b"https://1024terabox.com/s/1-SAMPLE_LINK \xec\xb6\x94\xec\xb6\x9c \xec\xbd\x94\xeb\x93\x9c:pass"
        ).decode("ascii")
        ssr_payload = {
            "status": 200,
            "body": base64.b64encode(f"text1\\{b64_url_pw}1".encode("utf-8")).decode(
                "ascii"
            ),
        }
        post_html = f"""
        <html>
            <body>
                <script type="application/json" data-url="https://api.kone.gg/v1/article/get">
                {json.dumps(ssr_payload)}
                </script>
            </body>
        </html>
        """

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "newpost99999999" in url:
                resp.text = post_html
            else:
                resp.text = board_html
            return resp

        with patch("downloader.kone_watcher.SEEN_POSTS_FILE", self.seen_file):
            with patch("requests.get", side_effect=mock_get):
                watcher = KoneWatcher(
                    settings_getter=lambda: mock_settings, download_manager=mock_dm
                )
                res = watcher.check_board(is_manual=True)

                self.assertTrue(res.get("success"))
                self.assertEqual(res.get("new_posts_count"), 1)
                self.assertEqual(res.get("downloads_added"), 1)

                # add_urls가 새로운 post의 링크와 제목 폴더로 호출되었는지 검증
                mock_dm.add_urls.assert_called_once()
                args, kwargs = mock_dm.add_urls.call_args
                candidates = args[0]
                self.assertEqual(len(candidates), 1)
                self.assertIn("1-SAMPLE_LINK", candidates[0]["url"])
                self.assertEqual(kwargs.get("subfolder"), "새로운 게임 롬 공유")
                # 게시글 제목과 출처 URL이 옵션에 포함되어 전달되는지 검증
                self.assertEqual(
                    candidates[0]["options"].get("source_title"), "새로운 게임 롬 공유"
                )
                self.assertIn(
                    "newpost99999999", candidates[0]["options"].get("source_url")
                )

                # 워처 상태에 recent_logs가 기록되었는지 검증
                status = watcher.get_status()
                self.assertIn("recent_logs", status)
                self.assertTrue(len(status["recent_logs"]) > 0)
                self.assertEqual(
                    status["recent_logs"][-1]["title"], "새로운 게임 롬 공유"
                )

    def test_kone_settings_validation(self):
        settings = dict(config.DEFAULT_SETTINGS)
        settings["kone_auto_check"] = True
        settings["kone_check_interval_sec"] = 300
        settings["kone_subfolder"] = False
        settings["kone_board_url"] = "https://kone.gg/s/retrogame"
        config.validate_settings(settings)

        # 유효하지 않은 타입/범위 검증
        with self.assertRaises(ValueError):
            bad = dict(settings)
            bad["kone_auto_check"] = "not_a_bool"
            config.validate_settings(bad)

        with self.assertRaises(ValueError):
            bad = dict(settings)
            bad["kone_check_interval_sec"] = 5  # 너무 짧음 (< 30)
            config.validate_settings(bad)

    def test_kone_api_endpoints(self):
        client = app.test_client()

        # 로그인 세션 설정
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            from app import credential_signature

            sess["credential_signature"] = credential_signature(config.load_settings())

        # 상태 조회
        res = client.get("/api/kone/status")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn("enabled", data)
        self.assertIn("board_url", data)

        # 수동 확인 API
        with patch(
            "app.kone_watcher.check_board",
            return_value={"success": True, "new_posts_count": 0, "downloads_added": 0},
        ):
            res2 = client.post("/api/kone/check-now")
            self.assertEqual(res2.status_code, 200)
            self.assertTrue(res2.get_json().get("success"))


class DuplicateCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.history_file = Path(self.temp_dir.name) / "download_history.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_canonical_url_key(self):
        from downloader.url_helper import canonical_url_key

        # 1. TeraBox 도메인 별칭 및 쿼리 차이 정규화
        tb1 = "https://1024terabox.com/s/15yww7pCtsW_8JDTRelsMRw?pwd=dao5"
        tb2 = "https://www.terabox.app/s/15yww7pCtsW_8JDTRelsMRw/"
        tb3 = "https://terabox.com/s/15yww7pCtsW_8JDTRelsMRw"
        self.assertEqual(canonical_url_key(tb1), canonical_url_key(tb2))
        self.assertEqual(canonical_url_key(tb2), canonical_url_key(tb3))
        self.assertEqual(canonical_url_key(tb1), "terabox:15yww7pCtsW_8JDTRelsMRw")

        # 2. Google Drive 형태 정규화
        gd1 = "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view?usp=sharing"
        gd2 = "https://drive.google.com/uc?id=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs&export=download"
        self.assertEqual(canonical_url_key(gd1), canonical_url_key(gd2))
        self.assertEqual(
            canonical_url_key(gd1), "gdrive:1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs"
        )

        # 3. 마그넷 링크 해시 정규화
        mag1 = "magnet:?xt=urn:btih:d2474e86c95b19b8bcfdbb72427a243363023070&dn=game.iso&tr=http://tracker.com"
        mag2 = (
            "magnet:?xt=urn:btih:D2474E86C95B19B8BCFDBB72427A243363023070&dn=other_name"
        )
        self.assertEqual(canonical_url_key(mag1), canonical_url_key(mag2))
        self.assertEqual(
            canonical_url_key(mag1),
            "magnet:btih:d2474e86c95b19b8bcfdbb72427a243363023070",
        )

        # 4. 일반 URL 트래킹 파라미터 무시
        u1 = "https://example.com/file.zip?utm_source=test&utm_medium=banner"
        u2 = "https://example.com/file.zip"
        self.assertEqual(canonical_url_key(u1), canonical_url_key(u2))

    def test_manager_duplicate_skip_and_history_persistence(self):
        from downloader.manager import DownloadManager

        mock_settings = {
            "download_dir": self.temp_dir.name,
            "max_concurrent_downloads": 1,
            "check_duplicate_url": True,
            "terabox_cookie": "",
            "terabox_mode": "download",
            "terabox_password": "",
        }

        with (
            patch("downloader.manager.HISTORY_FILE", self.history_file),
            patch(
                "downloader.manager.default_dispatcher.resolve",
                return_value={
                    "url": "https://example.com/mock",
                    "filename": "game.zip",
                },
            ),
            patch("downloader.manager.DownloadTask"),
        ):
            dm = DownloadManager(settings_getter=lambda: mock_settings)

            # 첫 번째 추가
            url = "https://1024terabox.com/s/15yww7pCtsW_8JDTRelsMRw?pwd=dao5"
            job1 = dm.add_url(url)
            self.assertIsNotNone(job1)
            assert job1 is not None

            # 동일 URL 재추가 (현재 큐에 있으므로 skip_duplicate=True 시 중복 제외)
            dup_url = "https://www.terabox.app/s/15yww7pCtsW_8JDTRelsMRw"
            is_dup, reason, _ = dm.is_duplicate_url(dup_url)
            self.assertTrue(is_dup)
            job2 = dm.add_url(dup_url, skip_duplicate=True)
            self.assertIsNone(job2)  # 중복이므로 추가되지 않음

            # skip_duplicate=False로 강제 추가 테스트
            job_forced = dm.add_url(dup_url, skip_duplicate=False)
            self.assertIsNotNone(job_forced)

            # 첫 번째 작업 완료 처리 시뮬레이션
            job1.status = "completed"
            job1.file_path = "/path/to/game.zip"
            dm._record_completed_history(job1)

            # 히스토리 파일 생성 확인
            self.assertTrue(self.history_file.exists())

            # 메모리 목록 비우기 (UI에서 완료 목록 비우기 시뮬레이션)
            dm.jobs.clear()

            # 작업 큐가 완전히 비어있더라도, 영구 보관된 히스토리에서 중복 감지 확인
            is_dup3, reason3, info3 = dm.is_duplicate_url(dup_url)
            self.assertTrue(is_dup3)
            self.assertIn("이미 다운로드가 완료", reason3)

            # 새 DownloadManager 인스턴스 (서버 재시작 시뮬레이션)
            dm2 = DownloadManager(settings_getter=lambda: mock_settings)
            is_dup4, _, _ = dm2.is_duplicate_url(url)
            self.assertTrue(is_dup4)
            added_urls = dm2.add_urls([url, dup_url])
            self.assertEqual(len(added_urls), 0)

            # 히스토리 초기화 테스트
            cleared = dm2.clear_history()
            self.assertGreaterEqual(cleared, 1)
            is_dup5, _, _ = dm2.is_duplicate_url(url)
            self.assertFalse(is_dup5)


if __name__ == "__main__":
    unittest.main()
