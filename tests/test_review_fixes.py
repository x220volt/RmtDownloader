"""Offline regressions for the 2026-10-29 review."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import auth
import config
from downloader.kone_watcher import KoneWatcher
from downloader.manager import DownloadManager


class CredentialTests(unittest.TestCase):
    def test_unicode_credentials_and_invalid_tokens(self):
        settings = dict(
            config.DEFAULT_SETTINGS,
            auth_username="관리자",
            auth_password="비밀번호",
            api_token="abc",
        )
        self.assertTrue(auth.validate_credentials("관리자", "비밀번호", settings))
        self.assertFalse(auth.validate_credentials("다른사용자", "비밀번호", settings))
        self.assertTrue(auth.is_token_authorized("비밀번호", settings))
        self.assertFalse(auth.is_token_authorized("잘못된토큰", settings))
        self.assertFalse(
            auth.session_authenticated(
                settings,
                secret_key="key",
                session_obj={
                    "authenticated": True,
                    "credential_signature": "잘못된서명",
                },
            )
        )

    def test_concurrent_token_creation_returns_persisted_value(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            with patch.object(config, "CONFIG_FILE", path):
                config.save_settings({"download_dir": folder})
                with ThreadPoolExecutor(max_workers=8) as pool:
                    tokens = list(
                        pool.map(lambda _: config.get_or_create_api_token(), range(16))
                    )
                self.assertEqual(set(tokens), {config.load_settings()["api_token"]})

    def test_token_persistence_failure_is_not_silenced(self):
        with (
            patch.object(config, "load_settings", return_value=config.DEFAULT_SETTINGS),
            patch.object(config, "save_settings", side_effect=OSError("disk full")),
        ):
            with self.assertRaises(OSError):
                config.get_or_create_api_token()


class QueueLifecycleTests(unittest.TestCase):
    def setUp(self):
        with (
            patch.object(DownloadManager, "_start_workers"),
            patch.object(DownloadManager, "_load_history"),
        ):
            self.manager = DownloadManager(
                lambda: dict(config.DEFAULT_SETTINGS, download_dir="unused")
            )
        self.manager._max_workers = 1
        self.addCleanup(self.manager._stop_event.set)

    def drain(self):
        finished = threading.Event()
        worker = threading.Thread(target=self.manager._worker_loop, daemon=True)
        worker.start()
        threading.Thread(
            target=lambda: (self.manager.queue.join(), finished.set()), daemon=True
        ).start()
        self.assertTrue(finished.wait(3), "Queue did not drain")
        self.manager._stop_event.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_concurrent_duplicate_submission(self):
        barrier = threading.Barrier(8)

        def submit(_):
            barrier.wait(timeout=3)
            return self.manager.add_url("https://example.com/file", skip_duplicate=True)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertEqual(sum(job is not None for job in results), 1)
        self.assertEqual(len(self.manager.jobs), 1)
        self.assertEqual(self.manager.queue.qsize(), 1)

    def run_html_fallback(self, action=None):
        job = self.manager.add_url("https://example.com/article")
        assert job is not None
        first = Mock()
        first.run.return_value = {"status": "failed", "error": "웹페이지(HTML) 응답"}
        second = Mock(filename="file.bin")
        second.run.return_value = {"status": "completed", "filename": "file.bin"}

        def resolve_page(*args, **kwargs):
            if action:
                getattr(self.manager, action)(job.id)
            return {"url": "https://example.com/file.bin", "filename": "file.bin"}

        with (
            patch(
                "downloader.manager.default_dispatcher.resolve",
                return_value={"url": job.original_url, "source_service": "Direct"},
            ),
            patch(
                "downloader.manager.DownloadTask", side_effect=[first, second]
            ) as factory,
            patch(
                "downloader.resolvers.webpage.WebPageResolver.resolve",
                side_effect=resolve_page,
            ) as fallback,
            patch.object(self.manager, "_record_completed_history"),
        ):
            self.drain()
            fallback.assert_called_once()
        return job, factory

    def test_html_failure_result_retries_extracted_link(self):
        job, factory = self.run_html_fallback()
        self.assertEqual(job.status, "completed")
        self.assertEqual(factory.call_count, 2)
        self.assertIsNone(job.thread)

    def test_cancel_during_html_fallback_stays_cancelled(self):
        job, factory = self.run_html_fallback("cancel_job")
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(factory.call_count, 1)
        self.assertIsNone(job.task)
        self.assertIsNone(job.thread)

    def test_remove_during_html_fallback_does_not_requeue(self):
        job, factory = self.run_html_fallback("remove_job")
        self.assertNotIn(job.id, self.manager.jobs)
        self.assertEqual(factory.call_count, 1)


class WatcherLifecycleTests(unittest.TestCase):
    def test_old_generation_cannot_stop_new_generation(self):
        with patch.object(KoneWatcher, "_load_seen_posts"):
            watcher = KoneWatcher(lambda: dict(config.DEFAULT_SETTINGS), Mock())
        old_event = watcher._stop_event
        old_event.set()
        with patch("downloader.kone_watcher.threading.Thread"):
            watcher.start()
        self.assertIsNot(old_event, watcher._stop_event)
        self.assertTrue(old_event.is_set())
        watcher._run_loop(old_event)
        self.assertTrue(watcher._running)
        watcher._run_loop(
            watcher._stop_event
        )  # Disabled configuration exits this generation.
        self.assertFalse(watcher._running)


class SettingsTransactionTests(unittest.TestCase):
    def test_failed_save_does_not_change_session_lifetime(self):
        import app

        original = app.app.permanent_session_lifetime
        with (
            patch.object(
                app,
                "load_settings",
                return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
            ),
            patch.object(
                app, "save_settings", side_effect=ValueError("invalid settings")
            ),
        ):
            response = app.app.test_client().post(
                "/api/settings", json={"session_lifetime_days": 45}
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(app.app.permanent_session_lifetime, original)


class ServiceCookieTests(unittest.TestCase):
    def test_lookalike_hosts_cannot_receive_account_cookies(self):
        from downloader.resolvers.terabox import TeraBoxResolver, _apply_terabox_cookies

        for host in ("terabox.com.evil.test", "evil-terabox.com", "terabox.evil.test"):
            with self.subTest(host=host):
                resolver = TeraBoxResolver()
                self.assertFalse(resolver.can_handle("https://" + host + "/s/abc"))
                session = Mock()
                with self.assertRaises(ValueError):
                    _apply_terabox_cookies(session, "ndus=01234567890123456789", host)
                session.cookies.set.assert_not_called()
                with patch("downloader.resolvers.terabox.requests.Session") as factory:
                    with self.assertRaises(ValueError):
                        resolver.resolve("https://" + host + "/s/abc")
                    factory.assert_not_called()

    def test_known_domains_and_subdomains_are_supported(self):
        from downloader.resolvers.terabox import TeraBoxResolver

        for host in (
            "terabox.com",
            "www.terabox.app",
            "1024terabox.com",
            "www.4funbox.com",
        ):
            self.assertTrue(TeraBoxResolver().can_handle("https://" + host + "/s/abc"))


class ExtensionUpdateSecurityTests(unittest.TestCase):
    def test_update_requires_authentication(self):
        from app import app
        client = app.test_client()
        resp = client.post("/api/extension/update", json={})
        self.assertIn(resp.status_code, (401, 302))

    def test_zip_slip_and_whitelist_security(self):
        import io
        import os
        from pathlib import Path
        import zipfile
        import tempfile
        from app import _safe_extract_extension_zip

        # 1. Zip Slip 시도 zip 생성
        malicious_buf = io.BytesIO()
        with zipfile.ZipFile(malicious_buf, "w") as zf:
            zf.writestr("../evil.bat", "calc.exe")
        malicious_buf.seek(0)

        with tempfile.TemporaryDirectory() as target_dir:
            with self.assertRaises(ValueError) as ctx:
                _safe_extract_extension_zip(malicious_buf.getvalue(), Path(target_dir))
            self.assertIn("허용되지 않는", str(ctx.exception))

        # 2. 실행 파일(.exe) 차단
        exe_buf = io.BytesIO()
        with zipfile.ZipFile(exe_buf, "w") as zf:
            zf.writestr("malware.exe", "fake binary")
        exe_buf.seek(0)

        with tempfile.TemporaryDirectory() as target_dir:
            with self.assertRaises(ValueError) as ctx:
                _safe_extract_extension_zip(exe_buf.getvalue(), Path(target_dir))
            self.assertIn("허용되지 않는 파일 확장자", str(ctx.exception))

        # 3. 안전한 확장 파일은 정상 추출
        safe_buf = io.BytesIO()
        with zipfile.ZipFile(safe_buf, "w") as zf:
            zf.writestr("manifest.json", '{"name": "test", "version": "1.0.0"}')
            zf.writestr("popup.js", 'console.log("hello");')
        safe_buf.seek(0)

        with tempfile.TemporaryDirectory() as target_dir:
            extracted_count = _safe_extract_extension_zip(safe_buf.getvalue(), Path(target_dir))
            self.assertEqual(extracted_count, 2)
            self.assertTrue(os.path.exists(os.path.join(target_dir, "manifest.json")))
            self.assertTrue(os.path.exists(os.path.join(target_dir, "popup.js")))


class AppUpdateSecurityTests(unittest.TestCase):
    def test_is_newer_version(self):
        from app import is_newer_version

        self.assertTrue(is_newer_version("v0.1.34", "v0.1.35"))
        self.assertTrue(is_newer_version("0.1.34", "v0.1.35"))
        self.assertTrue(is_newer_version("v0.1.34", "0.2.0"))
        self.assertTrue(is_newer_version("v0.1.34", "v1.0.0"))
        self.assertFalse(is_newer_version("v0.1.34", "v0.1.34"))
        self.assertFalse(is_newer_version("v0.1.35", "v0.1.34"))
        self.assertFalse(is_newer_version("v0.1.34", ""))
        self.assertFalse(is_newer_version("v0.1.34", None))

    def test_app_update_requires_auth(self):
        from app import app

        client = app.test_client()
        resp = client.post("/api/app/update", json={})
        self.assertIn(resp.status_code, (401, 302))

    def test_get_extension_dir_excludes_mei_and_detects_properly(self):
        from app import _is_mei_dir, _get_extension_dir
        from pathlib import Path

        # 1. _is_mei_dir 체크
        mei_path = Path("C:/Users/sharp/AppData/Local/Temp/_MEI123456/extension")
        self.assertTrue(_is_mei_dir(mei_path))
        normal_path = Path("C:/Users/sharp/Documents/RemoteDownloader-Extension")
        self.assertFalse(_is_mei_dir(normal_path))

        # 2. _get_extension_dir() 결과가 non-MEI 경로인지 확인
        detected = _get_extension_dir()
        self.assertFalse(_is_mei_dir(detected))
        self.assertTrue(str(detected).endswith("extension") or str(detected).endswith("RemoteDownloader-Extension"))

    def test_app_update_check_api(self):
        from app import app
        from unittest.mock import patch, MagicMock

        client = app.test_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "tag_name": "v9.9.9",
            "body": "테스트 릴리즈 노트",
            "html_url": "https://github.com/x220volt/RmtDownloader/releases/tag/v9.9.9",
            "assets": [
                {
                    "name": "RemoteDownloader.exe",
                    "browser_download_url": "https://github.com/x220volt/RmtDownloader/releases/download/v9.9.9/RemoteDownloader.exe",
                }
            ],
        }

        with patch("requests.get", return_value=mock_resp):
            resp = client.get("/api/app/update/check?force=true")
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["latest_version"], "v9.9.9")
            self.assertTrue(data["has_update"])
            self.assertTrue(data["has_exe_asset"])




class AutoResumeAndRetryTests(unittest.TestCase):
    """대용량 다운로드 중단 시 자동 이어받기 및 재시도 검증 테스트."""

    def test_download_task_in_stream_retry_and_resume(self):
        """다운로드 도중 네트워크 끊김이 발생해도 바로 취소되지 않고 Range 요청으로 이어받아 완료하는지 검증."""
        from downloader.engine import DownloadTask

        with tempfile.TemporaryDirectory() as tmp_dir:
            status_msgs = []

            def on_status(msg):
                status_msgs.append(msg)

            task = DownloadTask(
                url="https://example.com/huge.bin",
                dest_dir=tmp_dir,
                filename="huge.bin",
                partial_id="test_part_1",
                max_retries=3,
                retry_delay_base=0.01,  # 테스트용 초고속 재시도
                status_callback=on_status,
            )

            # 1차 요청 응답: 100바이트 수신 후 ConnectionResetError 발생
            resp1 = Mock()
            resp1.status_code = 200
            resp1.headers = {
                "Content-Length": "1000",
                "Content-Type": "application/octet-stream",
            }
            resp1.url = "https://example.com/huge.bin"
            resp1.raise_for_status = Mock()

            def fail_iter(chunk_size=1024):
                yield b"A" * 100
                raise ConnectionResetError("일시적 네트워크 단절 시뮬레이션")

            resp1.iter_content = fail_iter

            # 2차 요청 응답 (이어받기): Range 100- 요청에 206 응답 및 나머지 900바이트 전송
            resp2 = Mock()
            resp2.status_code = 206
            resp2.headers = {
                "Content-Range": "bytes 100-999/1000",
                "Content-Length": "900",
                "Content-Type": "application/octet-stream",
            }
            resp2.url = "https://example.com/huge.bin"
            resp2.raise_for_status = Mock()

            def success_iter(chunk_size=1024):
                yield b"B" * 900

            resp2.iter_content = success_iter

            calls = []

            def mock_request(url, headers=None, **kwargs):
                h = dict(headers or {})
                calls.append(h)
                if len(calls) == 1:
                    return resp1
                return resp2

            with (
                patch("downloader.engine.requests.Session.get", side_effect=mock_request),
                patch("downloader.engine.requests.Session.head", return_value=Mock(status_code=405)),
            ):
                result = task.run()

            # 단 1회의 task.run() 호출 내에서 자동 재시도되어 다운로드가 성공해야 함!
            self.assertTrue(result["success"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["total_bytes"], 1000)

            # 2차 요청 시 Range 헤더 검증
            self.assertEqual(len(calls), 2)
            self.assertIn("Range", calls[1])
            self.assertEqual(calls[1]["Range"], "bytes=100-")

            # 상태 콜백 확인
            self.assertTrue(any("연결 끊김 감지" in msg for msg in status_msgs))

            # 최종 파일 내용 검증 (100바이트 A + 900바이트 B)
            final_file = Path(tmp_dir) / "huge.bin"
            self.assertTrue(final_file.exists())
            self.assertEqual(final_file.read_bytes(), b"A" * 100 + b"B" * 900)

    def test_manager_worker_requeues_and_resumes_on_download_failure(self):
        """직링크 만료나 태스크 오류 시 바로 실패하지 않고 워커 레벨에서 재시도 큐에 진입하는지 검증."""
        from downloader.manager import DownloadManager, DownloadJob
        import requests

        with tempfile.TemporaryDirectory() as tmp_dir:
            dm = DownloadManager(settings_getter=lambda: {"download_dir": tmp_dir})
            dm._stop_event.set()  # 워커 스레드 중지 상태로 단위 검사

            job = DownloadJob(
                url="https://example.com/test.zip",
                download_dir=tmp_dir,
            )
            job.id = "retry_test_job"
            job.status = "downloading"
            dm.jobs[job.id] = job

            # 영구적이지 않은 네트워크 오류 검사
            self.assertTrue(dm._is_retryable_error(requests.exceptions.ChunkedEncodingError("broken stream")))
            self.assertTrue(dm._is_retryable_error(ConnectionResetError("reset by peer")))

            # 영구 오류는 재시도 불가 검사
            self.assertFalse(dm._is_retryable_error("404 Not Found"))
            self.assertFalse(dm._is_retryable_error("비밀번호가 올바르지 않습니다"))
            self.assertFalse(dm._is_retryable_error(RuntimeError("internal syntax bug")))

            # 수동 재시도 시 retry_count 리셋 검증
            job.retry_count = 2
            job.status = "failed"
            self.assertTrue(dm.retry_job(job.id))
            self.assertEqual(job.retry_count, 0)

if __name__ == "__main__":
    unittest.main()
