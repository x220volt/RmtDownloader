"""Deterministic regression tests. No live services or user settings writes."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import config
from downloader.engine import DownloadTask, _parse_content_range
from downloader.manager import DownloadManager
from downloader.url_helper import clean_url, parse_download_candidates, with_password


class ConfigurationTests(unittest.TestCase):
    def test_atomic_save_and_invalid_values(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            target = Path(folder) / "settings.json"
            with patch.object(config, "CONFIG_FILE", target):
                config.save_settings(
                    {"download_dir": folder, "max_concurrent_downloads": 2}
                )
                original = target.read_bytes()
                for invalid in (0, -1, True, "3", None, 33):
                    with self.assertRaises(ValueError):
                        config.save_settings({"max_concurrent_downloads": invalid})
                    self.assertEqual(target.read_bytes(), original)
                self.assertEqual(config.load_settings()["max_concurrent_downloads"], 2)
                self.assertEqual(list(Path(folder).glob("*.tmp")), [])

    def test_invalid_file_and_read_without_directory_creation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            target = Path(folder) / "settings.json"
            target.write_text("[]")
            with (
                patch.object(config, "CONFIG_FILE", target),
                patch("config.os.makedirs") as mkdir,
            ):
                self.assertEqual(config.load_settings(), config.DEFAULT_SETTINGS)
                mkdir.assert_not_called()


class URLTests(unittest.TestCase):
    def test_reject_non_urls(self):
        for value in (None, 10, {}, "garbage", "file:///tmp/file"):
            self.assertEqual(clean_url(value), "")
        self.assertEqual(parse_download_candidates(raw_items=[{"url": "garbage"}]), [])

    def test_password_precedes_fragment(self):
        from urllib.parse import parse_qs, urlparse

        result = with_password("https://example.com/file#anchor", "a+b &")
        parsed = urlparse(result)
        self.assertEqual(parsed.fragment, "anchor")
        self.assertEqual(parse_qs(parsed.query)["pwd"], ["a+b &"])

    def test_service_hosts_are_exact(self):
        from downloader.resolvers.gdrive import GoogleDriveResolver
        from downloader.resolvers.gigafile import GigaFileResolver
        from downloader.resolvers.transferit import TransferitResolver

        for resolver, url in (
            (GoogleDriveResolver(), "https://drive.google.com.evil.test/file/d/abc"),
            (GigaFileResolver(), "https://evilgigafile.nu/file"),
            (TransferitResolver(), "https://transfer.it.evil.test/t/abc"),
        ):
            self.assertFalse(resolver.can_handle(url))


class EngineTests(unittest.TestCase):
    def response(self, status=200, headers=None, chunks=(b"abc",)):
        response = Mock(status_code=status, headers=headers or {})
        response.iter_content.return_value = iter(chunks)
        return response

    def test_incomplete_body_is_not_published(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            response = self.response(headers={"Content-Length": "10"})
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = response
                result = DownloadTask("https://example.com/a", folder, "a.bin").run()
                self.assertEqual(result["status"], "failed")
                self.assertFalse((Path(folder) / "a.bin").exists())
                factory.return_value.close.assert_called()

    def test_independent_partial_files_and_unique_final_names(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            (Path(folder) / "a.bin").write_bytes(b"original")
            for identifier in ("one", "two"):
                with patch("downloader.engine.requests.Session") as factory:
                    factory.return_value.get.return_value = self.response(
                        headers={"Content-Length": "3"}
                    )
                    result = DownloadTask(
                        "https://example.com/a", folder, "a.bin", partial_id=identifier
                    ).run()
                    self.assertEqual(result["status"], "completed")
            self.assertEqual((Path(folder) / "a.bin").read_bytes(), b"original")
            self.assertEqual((Path(folder) / "a (1).bin").read_bytes(), b"abc")
            self.assertEqual((Path(folder) / "a (2).bin").read_bytes(), b"abc")

    def test_resume_keeps_extensionless_partial(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            (Path(folder) / "file.id.part").write_bytes(b"ab")
            response = self.response(
                206,
                {"Content-Range": "bytes 2-2/3", "Content-Type": "application/zip"},
                (b"c",),
            )
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = response
                result = DownloadTask(
                    "https://example.com/a", folder, "file", partial_id="id"
                ).run()
            self.assertEqual(result["status"], "completed")
            self.assertEqual((Path(folder) / "file").read_bytes(), b"abc")

    def test_invalid_content_range(self):
        self.assertIsNone(_parse_content_range("bytes 10-5/20"))
        self.assertIsNone(_parse_content_range("bytes 0-20/20"))
        self.assertEqual(_parse_content_range("bytes 2-4/5"), (2, 4, 5))

    def test_html_content_type_fails_and_cleans_up(self):
        """Content-Type이 text/html인 경우 다운로드 실패로 처리되고 part 파일이 정리되어야 함."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            response = self.response(
                200,
                {"Content-Type": "text/html; charset=utf-8", "Content-Length": "100"},
                (b"<html>error</html>",),
            )
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = response
                result = DownloadTask(
                    "https://example.com/file.zip", folder, "file.zip"
                ).run()
            self.assertEqual(result["status"], "failed")
            self.assertIn("HTML", result["error"])
            # .part 및 최종 파일이 디스크에 남아있지 않아야 함
            self.assertFalse((Path(folder) / "file.zip").exists())
            self.assertFalse((Path(folder) / "file.zip.part").exists())

    def test_html_chunk_body_fails_and_cleans_up(self):
        """Content-Type이 일반 octet-stream이더라도 첫 청크가 HTML/Cloudflare 챌린지면 실패해야 함."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            html_body = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>challenges.cloudflare.com</body></html>"
            response = self.response(
                200,
                {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(len(html_body)),
                },
                (html_body,),
            )
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = response
                result = DownloadTask(
                    "https://example.com/data.bin", folder, "data.bin"
                ).run()
            self.assertEqual(result["status"], "failed")
            self.assertIn("Cloudflare", result["error"])
            self.assertFalse((Path(folder) / "data.bin").exists())
            self.assertFalse((Path(folder) / "data.bin.part").exists())


class ManagerTests(unittest.TestCase):
    def manager(self):
        settings = dict(
            config.DEFAULT_SETTINGS, download_dir="unused", max_concurrent_downloads=1
        )
        with patch.object(DownloadManager, "_start_workers"):
            manager = DownloadManager(lambda: settings)
        manager._max_workers = 1
        self.addCleanup(manager._stop_event.set)
        return manager

    def drain(self, manager):
        worker = threading.Thread(target=manager._worker_loop, daemon=True)
        worker.start()
        # Queue.join has no timeout: expose completion through an Event.
        finished = threading.Event()
        threading.Thread(
            target=lambda: (manager.queue.join(), finished.set()), daemon=True
        ).start()
        self.assertTrue(finished.wait(3), "worker failed to drain queue")
        manager._stop_event.set()
        worker.join(2)

    def test_cancel_during_resolve_stays_cancelled(self):
        manager = self.manager()
        job = manager.add_url("https://example.com/file")
        assert job is not None

        def resolve(*args, **kwargs):
            manager.cancel_job(job.id)
            self.assertFalse(manager.retry_job(job.id))
            return {"url": job.original_url}

        with (
            patch("downloader.manager.default_dispatcher.resolve", side_effect=resolve),
            patch("downloader.manager.DownloadTask") as task,
        ):
            self.drain(manager)
            task.assert_not_called()
        self.assertEqual(job.status, "cancelled")
        self.assertIsNone(job.thread)

    def test_task_exception_does_not_kill_worker(self):
        manager = self.manager()
        raw_jobs = [manager.add_url("https://example.com/file") for _ in range(2)]
        jobs = [j for j in raw_jobs if j is not None]
        self.assertEqual(len(jobs), 2)
        with (
            patch(
                "downloader.manager.default_dispatcher.resolve",
                return_value={"url": "https://example.com/a"},
            ),
            patch(
                "downloader.manager.DownloadTask",
                side_effect=RuntimeError("test failure"),
            ),
        ):
            self.drain(manager)
        self.assertEqual([j.status for j in jobs], ["failed", "failed"])

    def test_retry_rejects_running_job(self):
        manager = self.manager()
        job = manager.add_url("https://example.com/file")
        assert job is not None
        self.assertFalse(manager.retry_job(job.id))


class APITests(unittest.TestCase):
    def setUp(self):
        import app

        self.module = app
        self.client = app.app.test_client()
        self.settings = patch.object(
            app,
            "load_settings",
            return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
        )
        self.settings.start()
        self.addCleanup(self.settings.stop)

    def test_invalid_payload_returns_400(self):
        for data in ([], "text", {"url": 123}, {"urls": "text"}, {"url": None}):
            self.assertEqual(self.client.post("/api/jobs", json=data).status_code, 400)

    def test_bookmarklet_escapes_url(self):
        job = Mock(
            original_url="https://example.com/</script><script>alert(1)</script>"
        )
        with patch.object(self.module.download_manager, "add_url", return_value=job):
            response = self.client.get(
                "/add", query_string={"url": "https://example.com/file", "back": "1"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"<script>alert(1)", response.data)
        self.assertIn(b"\\u003c/script>", response.data)

    def test_sse_disconnect_unsubscribes(self):
        manager = self.module.download_manager
        count = len(manager._listeners)
        response = self.client.get("/api/events", buffered=False)
        self.assertEqual(len(manager._listeners), count + 1)
        response.close()
        self.assertEqual(len(manager._listeners), count)


class TransferTests(unittest.TestCase):
    def test_stop_interface(self):
        from downloader.resolvers.transferit import TransferitDownloadTask

        task = TransferitDownloadTask("https://transfer.it/t/example", ".")
        task.stop()
        self.assertTrue(task._is_stopped)
        self.assertFalse(task._is_paused)

    def test_ignored_range_restarts_cipher_and_keeps_original(self):
        from contextlib import contextmanager
        from types import SimpleNamespace

        from Cryptodome.Cipher import AES
        from Cryptodome.Util import Counter

        from downloader.resolvers.transferit import TransferitDownloadTask

        key = b"k" * 16
        nonce = b"n" * 8
        plain = b"complete plaintext body for range test"
        encrypted = AES.new(
            key, AES.MODE_CTR, counter=Counter.new(64, prefix=nonce, initial_value=0)
        ).encrypt(plain)
        api = Mock()
        api.get_download_url.return_value = {
            "g": "https://example.com/file",
            "s": len(plain),
        }
        modules = {
            "transferit": SimpleNamespace(MegaAPI=Mock(return_value=api)),
            "transferit._crypto": SimpleNamespace(
                a32_to_bytes=lambda _: nonce, attr_key=lambda _: key
            ),
            "transferit._models": SimpleNamespace(TransferNode=Mock()),
        }
        response = Mock(status_code=200, headers={})
        response.iter_bytes.return_value = iter([encrypted])

        @contextmanager
        def stream(*args, **kwargs):
            yield response

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            (Path(folder) / "file.bin").write_bytes(b"original")
            (Path(folder) / "file.bin.id.part").write_bytes(plain[:16])
            with (
                patch.dict("sys.modules", modules),
                patch("httpx.stream", side_effect=stream),
            ):
                result = TransferitDownloadTask(
                    "https://transfer.it/t/example",
                    folder,
                    "file.bin",
                    node_handle="node",
                    node_key=[0] * 8,
                    node_size=len(plain),
                    xh="xh",
                    partial_id="id",
                ).run()
            self.assertEqual(result["status"], "completed")
            self.assertEqual((Path(folder) / "file.bin").read_bytes(), b"original")
            self.assertEqual((Path(folder) / "file (1).bin").read_bytes(), plain)


class AuthenticationTests(unittest.TestCase):
    def test_password_change_invalidates_session(self):
        import app

        settings = dict(config.DEFAULT_SETTINGS)
        client = app.app.test_client()
        with patch.object(app, "load_settings", return_value=settings):
            response = client.post(
                "/login",
                data={
                    "username": settings["auth_username"],
                    "password": settings["auth_password"],
                },
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(client.get("/api/auth/test").status_code, 200)
            settings["auth_password"] = "changed-password"
            self.assertEqual(client.get("/api/auth/test").status_code, 401)

    def test_basic_auth_and_extension_headers(self):
        import app

        settings = dict(config.DEFAULT_SETTINGS)
        with patch.object(app, "load_settings", return_value=settings):
            client = app.app.test_client()
            self.assertEqual(client.get("/api/auth/test").status_code, 401)
            self.assertEqual(
                client.get(
                    "/api/auth/test",
                    headers={"X-Auth-User": "admin", "X-Auth-Password": "password1234"},
                ).status_code,
                200,
            )
            self.assertEqual(
                client.get(
                    "/api/auth/test", auth=("admin", "password1234")
                ).status_code,
                200,
            )


class ResolverLifecycleTests(unittest.TestCase):
    def test_drive_response_and_session_closed(self):
        from downloader.resolvers.gdrive import GoogleDriveResolver

        session = MagicMock()
        session.__enter__.return_value = session
        session.cookies.get_dict.return_value = {}
        view = Mock(status_code=200, text="<title>archive.zip - Google Drive</title>")
        response = MagicMock(
            status_code=200,
            headers={"Content-Length": "3", "Content-Type": "application/zip"},
            url="https://example.com/file",
        )
        response.__enter__.return_value = response
        session.get.side_effect = [view, response]
        with patch(
            "downloader.resolvers.gdrive.requests.Session", return_value=session
        ):
            result = GoogleDriveResolver().resolve(
                "https://drive.google.com/file/d/abc/view"
            )
        self.assertEqual(result["filename"], "archive.zip")
        self.assertEqual(result["size"], 3)
        response.__exit__.assert_called_once()
        session.__exit__.assert_called_once()

    def test_explicit_direct_filename(self):
        from downloader.resolvers.direct import DirectResolver

        result = DirectResolver().resolve(
            "https://example.com/a", {"filename": "custom.zip"}
        )
        self.assertEqual(result["filename"], "custom.zip")

    def test_xshare_resolver_bundle(self):
        from downloader.resolvers.xshare import XShareResolver

        bundle_resp = Mock(status_code=200, ok=True)
        bundle_resp.json.return_value = {
            "bundleId": "bundle123",
            "files": [
                {"id": "fileA", "name": "A.zip", "size": 100},
                {"id": "fileB", "name": "B.zip", "size": 200},
            ],
        }
        token_resp = Mock(status_code=200, ok=True)
        token_resp.json.return_value = {"key": "testkey123"}

        with (
            patch("downloader.resolvers.xshare.requests.get", return_value=bundle_resp),
            patch("downloader.resolvers.xshare.requests.post", return_value=token_resp),
        ):
            resolved = XShareResolver().resolve("https://x-share.net/b/bundle123")

        self.assertEqual(resolved["source_service"], "X-Share")
        self.assertEqual(resolved["filename"], "A.zip")
        self.assertIn("key=testkey123", resolved["url"])
        self.assertEqual(len(resolved["files"]), 2)
        self.assertEqual(resolved["files"][1]["name"], "B.zip")
        self.assertEqual(resolved["subfolder"], "xshare_bundle123")

    def test_xshare_resolver_single(self):
        from downloader.resolvers.xshare import XShareResolver

        file_resp = Mock(status_code=200, ok=True)
        file_resp.json.return_value = {"id": "fileX", "name": "single.rar", "size": 500}
        token_resp = Mock(status_code=200, ok=True)
        token_resp.json.return_value = {"key": "singlekey"}

        with (
            patch("downloader.resolvers.xshare.requests.get", return_value=file_resp),
            patch("downloader.resolvers.xshare.requests.post", return_value=token_resp),
        ):
            resolved = XShareResolver().resolve("https://x-share.net/s/fileX")

        self.assertEqual(resolved["source_service"], "X-Share")
        self.assertEqual(resolved["filename"], "single.rar")
        self.assertEqual(
            resolved["url"], "https://x-share.net/api/download/fileX?key=singlekey"
        )

    def test_xshare_turnstile_drission_multilingual(self):
        """영어 로케일(Docker/NAS 환경: 'Download') 환경에서도 Turnstile 완료 후 정상 다운로드 key를 추출해야 함."""
        from downloader.resolvers.xshare import _resolve_xshare_via_drission

        mock_page = MagicMock()
        mock_btn = MagicMock()
        mock_btn.attr.return_value = None
        mock_btn_text = MagicMock()
        mock_btn_text.text = "Download"

        def mock_ele(selector, timeout=None):
            if selector == "#dl-btn":
                return mock_btn
            if selector == "#dl-btn-text":
                return mock_btn_text
            return None

        mock_page.ele.side_effect = mock_ele
        mock_page.run_js.side_effect = [
            "mock_turnstile_token_123",
            {
                "ok": True,
                "status": 200,
                "data": {"key": "bypass_key_abc"},
                "filename": "english_locale.zip",
            },
        ]

        with patch("downloader.resolvers.xshare.stealth_browser_session") as mock_sess:
            mock_sess.return_value.__enter__.return_value = mock_page
            with patch("time.sleep", return_value=None):
                key, filename = _resolve_xshare_via_drission(
                    "https://x-share.net/s/file123", timeout=3
                )

        self.assertEqual(key, "bypass_key_abc")
        self.assertEqual(filename, "english_locale.zip")

    def test_xshare_turnstile_flaresolverr(self):
        """FlareSolverr 설정 시 x-share Turnstile 우회 및 다운로드 key 발급이 동작해야 함."""
        from downloader.resolvers.xshare import XShareResolver

        file_resp = Mock(status_code=200, ok=True)
        file_resp.json.return_value = {
            "id": "fileFS",
            "name": "flaresolverr.zip",
            "size": 100,
        }

        fail_token_resp = Mock(status_code=403, ok=False)
        fs_resp = Mock(status_code=200, ok=True)
        fs_resp.json.return_value = {
            "status": "ok",
            "solution": {
                "response": '<html><body><input name="cf-turnstile-response" value="fs_cf_token_xyz" /></body></html>',
                "cookies": [{"name": "cf_clearance", "value": "xyz"}],
                "userAgent": "FlareSolverr-UA",
            },
        }

        success_token_resp = Mock(status_code=200, ok=True)
        success_token_resp.json.return_value = {"key": "flaresolverr_key_456"}

        def mock_post(url, *args, **kwargs):
            if "localhost:8191" in url:
                return fs_resp
            if "api/download-token" in url:
                payload = kwargs.get("json", {})
                if payload.get("turnstileToken") == "fs_cf_token_xyz":
                    return success_token_resp
                return fail_token_resp
            return fail_token_resp

        with (
            patch("downloader.resolvers.xshare.requests.get", return_value=file_resp),
            patch("downloader.resolvers.xshare.requests.post", side_effect=mock_post),
        ):
            resolver = XShareResolver()
            res = resolver.resolve(
                "https://x-share.net/s/fileFS",
                options={"flaresolverr_url": "http://localhost:8191"},
            )

        self.assertEqual(
            res["url"],
            "https://x-share.net/api/download/fileFS?key=flaresolverr_key_456",
        )

    def test_torrent_engine_helpers(self):
        import os
        import tempfile

        from downloader.torrent_engine import (
            parse_aria2_summary_line,
            parse_bytes_string,
            parse_torrent_file,
        )

        # 1. parse_bytes_string
        self.assertEqual(parse_bytes_string("100B"), 100)
        self.assertEqual(parse_bytes_string("1KiB"), 1024)
        self.assertEqual(parse_bytes_string("2MiB"), 2 * 1024 * 1024)
        self.assertEqual(parse_bytes_string("1.5GiB"), int(1.5 * 1024 * 1024 * 1024))

        # 2. parse_aria2_summary_line
        line = "[#77e77b 50MiB/200MiB(25%) CN:8 DL:2.5MiB ETA:1m0s]"
        res = parse_aria2_summary_line(line)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res["progress"], 25.0)
        self.assertEqual(res["dl_bytes"], 50 * 1024 * 1024)
        self.assertEqual(res["total_bytes"], 200 * 1024 * 1024)
        self.assertEqual(res["speed_bps"], int(2.5 * 1024 * 1024))
        self.assertEqual(res["eta_seconds"], 60)

        # 3. bdecode & parse_torrent_file
        torrent_bytes = b"d4:infod4:name9:test.file6:lengthi1048576eee"
        with tempfile.NamedTemporaryFile(suffix=".torrent", delete=False) as tf:
            tf.write(torrent_bytes)
            tf_path = tf.name
        try:
            info = parse_torrent_file(tf_path)
            self.assertEqual(info["name"], "test.file")
            self.assertEqual(info["total_size"], 1048576)
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

    def test_torrent_resolver_magnet(self):
        from downloader.resolvers.torrent import TorrentResolver

        resolver = TorrentResolver()

        # Magnet with dn
        magnet_url = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=ArchLinux_2026.iso"
        self.assertTrue(resolver.can_handle(magnet_url))
        res = resolver.resolve(magnet_url)
        self.assertEqual(res["source_service"], "BitTorrent (마그넷)")
        self.assertEqual(res["filename"], "ArchLinux_2026.iso")
        self.assertTrue(callable(res["task_factory"]))

        # Magnet without dn (hash fallback)
        magnet_raw = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"
        res_raw = resolver.resolve(magnet_raw)
        self.assertEqual(res_raw["filename"], "Torrent_fedcba9876")

    def test_upload_torrent_endpoint(self):
        import io
        from unittest.mock import Mock, patch

        import app as app_module
        from app import app

        client = app.test_client()

        import config

        with patch.object(
            app_module,
            "load_settings",
            return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
        ):
            settings = app_module.load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        client.post("/login", data={"username": curr_user, "password": curr_pw})

        torrent_data = b"d4:infod4:name16:uploaded_pkg.zip6:lengthi2048eeee"
        data = {
            "file": (io.BytesIO(torrent_data), "uploaded_pkg.torrent"),
            "subfolder": "torrents_test",
        }

        mock_job = Mock()
        mock_job.to_dict.return_value = {"filename": "uploaded_pkg.zip"}

        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch.object(app_module, "TORRENTS_DIR", Path(tmp_dir)),
                patch.object(
                    app_module,
                    "load_settings",
                    return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
                ),
                patch.object(
                    app_module.download_manager, "add_url", return_value=mock_job
                ) as mock_add,
            ):
                res = client.post(
                    "/api/upload-torrent", data=data, content_type="multipart/form-data"
                )
                self.assertEqual(res.status_code, 200)
                json_data = res.get_json()
                self.assertTrue(json_data["success"])
                self.assertIn("job", json_data)
                self.assertEqual(json_data["job"]["filename"], "uploaded_pkg.zip")
                mock_add.assert_called_once()
                self.assertEqual(
                    mock_add.call_args[1].get("subfolder"), "torrents_test"
                )

    def test_change_username_and_password_hashed(self):
        import config
        from app import app
        from config import load_settings

        client = app.test_client()

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            temp_config = Path(folder) / "settings.json"
            temp_config.write_text(
                json.dumps(config.DEFAULT_SETTINGS, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", temp_config):
                # 1. 초기 계정으로 로그인
                login_res = client.post(
                    "/login", data={"username": "admin", "password": "password1234"}
                )
                self.assertEqual(login_res.status_code, 302)

                # 2. 아이디 및 비밀번호 변경
                change_payload = {
                    "auth_username": "custom_manager",
                    "auth_password": "super_secret_pw_777",
                }
                update_res = client.post("/api/settings", json=change_payload)
                self.assertEqual(update_res.status_code, 200)

                # 3. 임시 settings.json 저장 상태 검증 (단방향 해시 확인 및 평문 노출 방지)
                new_settings = load_settings()
                self.assertEqual(new_settings["auth_username"], "custom_manager")
                self.assertTrue(
                    str(new_settings["auth_password"]).startswith(
                        ("scrypt:", "pbkdf2:")
                    )
                )
                self.assertNotEqual(
                    new_settings["auth_password"], "super_secret_pw_777"
                )

                # 4. 설정 변경 직후 현재 세션이 끊기지 않고 유지되는지 확인
                test_res = client.get("/api/auth/test")
                self.assertEqual(test_res.status_code, 200)
                self.assertEqual(test_res.get_json()["username"], "custom_manager")

                # 5. 새 클라이언트가 변경된 아이디/비밀번호로 정상 로그인되는지 확인
                new_client = app.test_client()
                bad_login = new_client.post(
                    "/login", data={"username": "admin", "password": "password1234"}
                )
                self.assertEqual(bad_login.status_code, 200)  # 이전 정보는 실패

                good_login = new_client.post(
                    "/login",
                    data={
                        "username": "custom_manager",
                        "password": "super_secret_pw_777",
                    },
                )
                self.assertEqual(good_login.status_code, 302)

    def test_change_password_confirmation_validation(self):
        import config
        from app import app

        client = app.test_client()

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            temp_config = Path(folder) / "settings.json"
            temp_config.write_text(
                json.dumps(config.DEFAULT_SETTINGS, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", temp_config):
                client.post(
                    "/login", data={"username": "admin", "password": "password1234"}
                )

                # 불일치 시 400 반환
                mismatch_payload = {
                    "auth_password": "new_password_123",
                    "auth_password_confirm": "different_password_456",
                }
                res_mismatch = client.post("/api/settings", json=mismatch_payload)
                self.assertEqual(res_mismatch.status_code, 400)
                self.assertIn("일치하지 않습니다", res_mismatch.get_json()["error"])

                # 일치 시 200 성공
                match_payload = {
                    "auth_password": "new_password_123",
                    "auth_password_confirm": "new_password_123",
                }
                res_match = client.post("/api/settings", json=match_payload)
                self.assertEqual(res_match.status_code, 200)
                self.assertTrue(res_match.get_json()["success"])

    def test_port_setting_and_default(self):
        import config
        from app import app
        from config import load_settings

        client = app.test_client()

        # 1. 기본 포트 검증
        self.assertEqual(config.DEFAULT_SETTINGS["port"], 7971)

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            temp_config = Path(folder) / "settings.json"
            temp_config.write_text(
                json.dumps(config.DEFAULT_SETTINGS, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", temp_config):
                client.post(
                    "/login", data={"username": "admin", "password": "password1234"}
                )

                # 2. 기본 info 조회 시 7971 반환
                info_res = client.get("/api/info")
                self.assertEqual(info_res.status_code, 200)
                self.assertEqual(info_res.get_json()["port"], 7971)

                # 3. 잘못된 포트(범위 초과, 0, 음수, 문자열) 검증 실패 -> 400
                for bad_port in [0, -1, 65536, 99999, "invalid_port"]:
                    bad_res = client.post("/api/settings", json={"port": bad_port})
                    self.assertEqual(
                        bad_res.status_code, 400, f"Expected 400 for port: {bad_port}"
                    )

                # 4. 올바른 포트 변경 -> 200 성공
                good_res = client.post("/api/settings", json={"port": 8888})
                self.assertEqual(good_res.status_code, 200)
                self.assertEqual(good_res.get_json()["settings"]["port"], 8888)

                # 5. 저장된 settings.json 검증
                saved = load_settings()
                self.assertEqual(saved["port"], 8888)

    def test_chrome_extension_integrity_and_download(self):
        import io
        import re
        import zipfile

        from bs4 import BeautifulSoup

        from app import BASE_DIR, app

        ext_dir = Path(BASE_DIR) / "extension"
        manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertIn("clipboardRead", manifest.get("permissions", []))
        self.assertIn("offscreen", manifest.get("permissions", []))
        self.assertTrue(
            any(
                "content.js" in cs.get("js", [])
                for cs in manifest.get("content_scripts", [])
            )
        )
        self.assertTrue((ext_dir / manifest["background"]["service_worker"]).is_file())
        self.assertTrue((ext_dir / manifest["action"]["default_popup"]).is_file())
        self.assertTrue((ext_dir / "content.js").is_file())
        self.assertTrue((ext_dir / "offscreen.html").is_file())
        self.assertTrue((ext_dir / "offscreen.js").is_file())

        popup_html = (ext_dir / "popup.html").read_text(encoding="utf-8")
        soup = BeautifulSoup(popup_html, "html.parser")
        html_ids = {str(el["id"]) for el in soup.find_all(id=True)}
        self.assertIn("clipboardAutoFillCheck", html_ids)
        self.assertIn("clipboardCopyPromptCheck", html_ids)
        self.assertEqual(len(soup.find_all(style=True)), 0)

        popup_js = (ext_dir / "popup.js").read_text(encoding="utf-8")
        js_ids = set(re.findall(r'getElementById\(\s*"([^"]+)"\s*\)', popup_js))
        self.assertEqual(js_ids - html_ids, set())

        for rel_js in (
            "extension/popup.js",
            "extension/background.js",
            "extension/content.js",
            "extension/offscreen.js",
            "static/js/app.js",
        ):
            code = (Path(BASE_DIR) / rel_js).read_text(encoding="utf-8")
            self.assertEqual(
                code.count("{"), code.count("}"), f"Unbalanced braces in {rel_js}"
            )

        # Verify web dashboard settings modal & button DOM structure
        index_html = (Path(BASE_DIR) / "templates" / "index.html").read_text(
            encoding="utf-8"
        )
        idx_soup = BeautifulSoup(index_html, "html.parser")
        for req_id in (
            "settingsBtn",
            "settingsModal",
            "closeSettingsModal",
            "cancelSettingsBtn",
            "settingsForm",
            "settingCaptureSourcePage",
        ):
            self.assertIsNotNone(
                idx_soup.find(id=req_id), f"Missing #{req_id} in templates/index.html"
            )
        self.assertIsNone(
            idx_soup.find(id="capturePageCheck"),
            "capturePageCheck should only be in settings",
        )

        client = app.test_client()
        resp = client.get("/api/extension/download")
        self.assertEqual(resp.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(resp.data), "r") as zf:
            names = set(zf.namelist())
            for expected in (
                "manifest.json",
                "popup.html",
                "popup.css",
                "popup.js",
                "background.js",
                "content.js",
                "offscreen.html",
                "offscreen.js",
            ):
                self.assertIn(expected, names)
            self.assertEqual(
                zf.read("popup.html").decode("utf-8").replace("\r\n", "\n"),
                popup_html.replace("\r\n", "\n"),
            )

    def test_smartphone_quick_add_and_pwa_manifest(self):
        import base64
        from unittest.mock import Mock, patch

        import app as app_module
        import config
        from app import app

        client = app.test_client()
        mock_settings = dict(
            config.DEFAULT_SETTINGS,
            auth_enabled=True,
            api_token="mobile_test_token_123",
        )

        with patch.object(app_module, "load_settings", return_value=mock_settings):
            # 1. PWA manifest 엔드포인트 검증
            manifest_res = client.get("/manifest.webmanifest")
            self.assertEqual(manifest_res.status_code, 200)
            self.assertEqual(manifest_res.get_json()["display"], "standalone")

            # 2. 아이폰 단축어 / 안드로이드 HTTP Shortcuts: URL 인코딩 없이 내부 쿼리(&export=download)가 포함된 URL 전송 검증
            fake_job = Mock(
                original_url="https://drive.google.com/uc?id=abc123&export=download"
            )
            fake_job.to_dict.return_value = {"url": fake_job.original_url}
            with patch.object(
                app_module.download_manager, "add_url", return_value=fake_job
            ) as mock_add:
                res = client.get(
                    "/add?token=mobile_test_token_123&url=https://drive.google.com/uc?id=abc123&export=download"
                )
                self.assertEqual(res.status_code, 200)
                called_url = mock_add.call_args[0][0]
                self.assertIn("id=abc123", called_url)
                self.assertIn("export=download", called_url)

            # 3. 모바일 스마트 북마클릿: 드래그 선택한 Base64+비밀번호 텍스트 및 Referer 전달 검증
            raw_b64 = base64.b64encode(
                "https://1024terabox.com/s/1abcDEF 비번:9876".encode("utf-8")
            ).decode("ascii")
            with patch.object(
                app_module.download_manager, "add_url", return_value=fake_job
            ) as mock_add_b64:
                res_b64 = client.get(
                    "/add",
                    query_string={
                        "token": "mobile_test_token_123",
                        "referer": "https://kone.gg/s/retrogame/123",
                        "url": f"공유링크: {raw_b64}",
                    },
                )
                self.assertEqual(res_b64.status_code, 200)
                opts = mock_add_b64.call_args[1].get("options", {})
                self.assertEqual(opts.get("password"), "9876")
                self.assertEqual(opts.get("referer"), "https://kone.gg/s/retrogame/123")

    def test_terabox_passwordless_link_with_default_password(self):
        from unittest.mock import Mock, patch

        from downloader.resolvers.terabox import TeraBoxResolver

        fake_session = Mock()
        fake_session.__enter__ = Mock(return_value=fake_session)
        fake_session.__exit__ = Mock(return_value=False)
        fake_session.headers = {}
        fake_session.cookies = Mock()
        fake_session.cookies.get_dict.return_value = {}

        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://www.1024tera.com/sharing/link?surl=9e9HdA3oSkqVs4YyLHl0Cg",
            headers={"Content-Type": "text/html; charset=utf-8"},
            text='<script>var i18n={"msg":"Please input the extraction code"}; fn%28%22ABCDEF123456%22%29</script>',
        )
        list_resp = Mock(
            status_code=200,
            ok=True,
        )
        list_resp.json.return_value = {
            "errno": 0,
            "share_id": 12345,
            "uk": 67890,
            "list": [
                {
                    "fs_id": "999",
                    "isdir": "0",
                    "server_filename": "STEINS;GATE REBOOT 1.0.zip",
                    "size": "2146615188",
                    "dlink": "https://d.terabox.app/file/mock_direct_link",
                }
            ],
        }
        fake_session.get.side_effect = [page_resp, list_resp]

        with patch(
            "downloader.resolvers.terabox.requests.Session", return_value=fake_session
        ):
            res = TeraBoxResolver().resolve(
                "https://1024terabox.com/s/19e9HdA3oSkqVs4YyLHl0Cg",
                options={"password": "dao5", "terabox_mode": "download"},
            )
            self.assertEqual(res["filename"], "STEINS;GATE REBOOT 1.0.zip")
            self.assertEqual(res["url"], "https://d.terabox.app/file/mock_direct_link")
            fake_session.post.assert_not_called()

    def test_webpage_post_extractor_dcinside_and_general(self):
        import base64
        from unittest.mock import patch

        from downloader.resolvers.dispatcher import default_dispatcher
        from downloader.resolvers.webpage import WebPageResolver

        dc_url = "https://gall.dcinside.com/mgallery/board/view/?id=retrogame&no=243625&exception_mode=recommend&page=1"
        self.assertIsInstance(default_dispatcher.get_resolver(dc_url), WebPageResolver)

        b64_kio = base64.b64encode("https://kio.ac/c/mock123".encode("utf-8")).decode(
            "ascii"
        )
        mock_html = f"""
        <html>
        <head><title>(nsw) 대해적 스토리 원피스 모드 업뎃 +apk(한정배포 8시까지) - 레트로게임 마이너 갤러리</title></head>
        <body>
          <span class="title_subject">(nsw) 대해적 스토리 원피스 모드 업뎃 +apk(한정배포 8시까지)</span>
          <div class="write_div">
            <p>주요 코드 검증을 진행했으며 다시 빌드하였습니다.</p>
            <p>스위치</p>
            <p><a href="https://52.gigafile.nu/0205-cca8ad736180d8807726292cfd439825d">https://52.gigafile.nu/0205-cca8ad736180d8807726292cfd439825d</a></p>
            <p>안드로이드 미러: {b64_kio}</p>
            <p>비번: 4321</p>
          </div>
          <ul class="appending_file">
            <li><a href="https://image.dcinside.com/download.php?no=1&f_no=screen.png">screen.png</a></li>
          </ul>
        </body>
        </html>
        """
        wp = WebPageResolver()
        title, cands = wp.extract_candidates_from_html(mock_html, dc_url)
        self.assertEqual(
            title, "(nsw) 대해적 스토리 원피스 모드 업뎃 +apk(한정배포 8시까지)"
        )
        self.assertEqual(len(cands), 2)
        self.assertIn(
            "52.gigafile.nu/0205-cca8ad736180d8807726292cfd439825d", cands[0]["url"]
        )
        self.assertIn("kio.ac/c/mock123", cands[1]["url"])
        self.assertEqual(cands[1]["options"].get("password"), "4321")

        with patch(
            "downloader.resolvers.gigafile.GigaFileResolver.resolve",
            return_value={
                "url": "https://52.gigafile.nu/download.php?file=0205-cca8ad736180d8807726292cfd439825d",
                "filename": "010077801A942000.zip",
                "source_service": "GigaFile",
            },
        ):
            resolved = wp.resolve_from_html(mock_html, dc_url)
            self.assertEqual(resolved["filename"], "010077801A942000.zip")
            self.assertEqual(
                resolved["subfolder"],
                "(nsw) 대해적 스토리 원피스 모드 업뎃 +apk(한정배포 8시까지)",
            )
            self.assertEqual(len(resolved.get("extra_jobs", [])), 1)
            self.assertIn("kio.ac/c/mock123", resolved["extra_jobs"][0]["url"])
            self.assertEqual(resolved.get("source_page_url"), dc_url)

    def test_capture_source_page_option_and_screenshot(self):
        from unittest.mock import MagicMock, Mock, patch

        import app as app_module
        from app import app
        from downloader.browser_solver import capture_full_page_screenshot

        client = app.test_client()
        mock_settings = dict(
            config.DEFAULT_SETTINGS, auth_enabled=False, capture_source_page=False
        )
        fake_job = Mock(
            original_url="https://gall.dcinside.com/mgallery/board/view/?id=retrogame&no=243625"
        )
        fake_job.to_dict.return_value = {"url": fake_job.original_url}

        with (
            patch.object(app_module, "load_settings", return_value=mock_settings),
            patch.object(
                app_module.download_manager, "add_urls", return_value=[fake_job]
            ) as mock_add_urls,
        ):
            res = client.post(
                "/api/jobs",
                json={
                    "url": "https://gall.dcinside.com/mgallery/board/view/?id=retrogame&no=243625",
                    "capture_page": True,
                },
            )
            self.assertEqual(res.status_code, 200)
            passed_cands = mock_add_urls.call_args[0][0]
            self.assertTrue(passed_cands[0]["options"].get("capture_page"))

        import os

        with tempfile.TemporaryDirectory(
            dir=Path(__file__).resolve().parent
        ) as tmp_dir:
            temp_cfg = Path(tmp_dir) / "settings.json"
            with patch.object(config, "CONFIG_FILE", temp_cfg):
                config.save_settings(
                    {
                        "download_dir": tmp_dir,
                        "auth_enabled": False,
                        "capture_source_page": False,
                    }
                )
                post_res = client.post(
                    "/api/settings", json={"capture_source_page": True}
                )
                self.assertEqual(post_res.status_code, 200)
                self.assertTrue(post_res.get_json()["settings"]["capture_source_page"])

                get_res = client.get("/api/settings")
                self.assertEqual(get_res.status_code, 200)
                self.assertTrue(get_res.get_json()["capture_source_page"])

                info_res = client.get("/api/info")
                self.assertTrue(info_res.get_json()["capture_source_page"])

                auth_res = client.post("/api/auth/test")
                self.assertTrue(auth_res.get_json()["capture_source_page"])
            fake_page = MagicMock()
            fake_page.title = "대해적 스토리 원피스 모드"

            def fake_shot(path=None, name=None, full_page=False):
                out_f = Path(str(path or "")) / str(name or "")
                out_f.write_bytes(b"\x89PNG\r\n\x1a\nmock")
                return str(out_f)

            fake_page.get_screenshot.side_effect = fake_shot

            from contextlib import contextmanager

            @contextmanager
            def fake_browser_ctx(*args, **kwargs):
                yield fake_page

            with (
                patch(
                    "downloader.browser_solver.stealth_browser_session",
                    side_effect=fake_browser_ctx,
                ),
                patch("downloader.browser_solver.time.sleep"),
            ):
                saved_path = capture_full_page_screenshot(
                    "https://gall.dcinside.com/mgallery/board/view/?id=retrogame&no=243625",
                    tmp_dir,
                    title_hint="(nsw) 대해적 스토리 원피스 모드",
                )
                self.assertIsNotNone(saved_path)
                assert saved_path is not None
                self.assertTrue(os.path.exists(saved_path))
                self.assertIn("[페이지캡처]", os.path.basename(saved_path))

    def test_kone_ssr_binary_string_extraction_and_gofile_fallback(self):
        import base64

        from downloader.kone_watcher import KoneWatcher, extract_kone_ssr_strings
        from downloader.resolvers.gofile import GofileResolver

        # 1. Kone.gg TLV 바이너리 포맷 (1 + length + UTF-8 bytes + 1 + \x04type) 파싱 검증
        raw_tlv = (
            b"1\x04text1\x1chttps://gofile.io/d/Y0akDCRY1\x04type"
            b"1\x04text1\x1chttps://youtu.be/G0q59G_KsQs1\x04type"
        )
        extracted = extract_kone_ssr_strings(raw_tlv)
        self.assertIn("https://gofile.io/d/Y0akDCRY", extracted)
        self.assertNotIn("https://gofile.io/d/Y0akDCRY1", extracted)

        # 2. KoneWatcher._extract_download_links_from_post에서 유튜브는 제외하고 GoFile만 추출하는지 검증
        b64_body = base64.b64encode(raw_tlv).decode("ascii")
        fake_html = f'<html><body><script data-url="https://api.kone.gg/v1/article/get">{json.dumps({"body": b64_body})}</script></body></html>'
        mock_resp = Mock(status_code=200, text=fake_html)
        kw = KoneWatcher(lambda: dict(config.DEFAULT_SETTINGS), MagicMock())
        with patch("downloader.kone_watcher.requests.get", return_value=mock_resp):
            links = kw._extract_download_links_from_post(
                "https://kone.gg/s/retrogame/a6Oecb0oPNImf5z01HGQeb", {}
            )
            urls = [c["url"] for c in links]
            self.assertEqual(urls, ["https://gofile.io/d/Y0akDCRY"])

        # 3. GofileResolver가 끝자리 '1'이 붙은 ID(Y0akDCRY1)에 대해 자동으로 Y0akDCRY로 보정 재시도하는지 검증
        not_found_resp = Mock(status_code=200, ok=True)
        not_found_resp.json.return_value = {"status": "error-notFound", "data": {}}
        ok_resp = Mock(status_code=200, ok=True)
        ok_resp.json.return_value = {
            "status": "ok",
            "data": {
                "children": {
                    "f1": {
                        "type": "file",
                        "name": "mgr.zip",
                        "link": "https://store.gofile.io/download/web/f1/mgr.zip",
                    }
                }
            },
        }
        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.side_effect = [not_found_resp, ok_resp]
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
            resolved = GofileResolver().resolve("https://gofile.io/d/Y0akDCRY1")
            self.assertEqual(resolved["filename"], "mgr.zip")
            self.assertEqual(resolved["referer"], "https://gofile.io/d/Y0akDCRY")

    def test_mojibake_jamo_recovery_and_kone_watermark(self):
        import tempfile

        from bs4 import BeautifulSoup

        from downloader.engine import fix_mojibake, sanitize_folder_name
        from downloader.kone_watcher import KoneWatcher
        from downloader.resolvers.webpage import _extract_post_title

        # 1. 한글 자모(ㅌ, ㅇ, ㅈ)의 UTF-8 두 번째 바이트(0x85 NEL)가 공백으로 치환된 뒤 깨진 폴더명 복원 검증
        garbled = "ë©\x94ã \x8c ê¸°ã \x87 ë\x9d¼ã \x87ã \x88 - retrogame"
        self.assertEqual(fix_mojibake(garbled), "메ㅌ 기ㅇ 라ㅇㅈ - retrogame")
        self.assertEqual(sanitize_folder_name(garbled), "메ㅌ 기ㅇ 라ㅇㅈ - retrogame")

        # 2. Kone.gg 게시글 <title>에서 '- retrogame | Kone' 접미사 제거 및 깨진 인코딩 복원 검증
        html_bytes = "<title>메ㅌ 기ㅇ 라ㅇㅈ - retrogame | Kone</title>".encode(
            "utf-8"
        )
        latin1_decoded = html_bytes.decode("latin1")
        soup = BeautifulSoup(latin1_decoded, "html.parser")
        self.assertEqual(
            _extract_post_title(
                soup, "https://kone.gg/s/retrogame/a6Oecb0oPNImf5z01HGQeb"
            ),
            "메ㅌ 기ㅇ 라ㅇㅈ",
        )

        # 3. KoneWatcher 감시 기준점(last_checked_post)이 상단 공지글을 제외하고 최신 일반 게시글로 기록되는지 검증
        with tempfile.TemporaryDirectory() as tmp:
            seen_path = Path(tmp) / "kone_seen_posts.json"
            board_html = """
            <html><body>
              <a href="/s/retrogame/notice11111111"><span>공지</span><div title="[필독] 이용규칙">[필독] 이용규칙</div><span>1 year ago</span></a>
              <a href="/s/retrogame/a6Oecb0oPNImf5z01HGQeb"><span>정보</span><div title="메ㅌ 기ㅇ 라ㅇㅈ">메ㅌ 기ㅇ 라ㅇㅈ</div><span>28 minutes ago</span></a>
              <a href="/s/retrogame/older222222222"><span>일반</span><div title="이전 게시글">이전 게시글</div><span>1 hour ago</span></a>
            </body></html>
            """
            mock_resp = Mock(
                status_code=200, content=board_html.encode("utf-8"), text=board_html
            )
            with (
                patch("downloader.kone_watcher.SEEN_POSTS_FILE", seen_path),
                patch("downloader.kone_watcher.requests.get", return_value=mock_resp),
            ):
                watcher = KoneWatcher(
                    lambda: dict(config.DEFAULT_SETTINGS), MagicMock()
                )
                res = watcher.check_board(is_manual=False)
                self.assertTrue(res.get("initialized"))
                cp = watcher.get_status().get("last_checked_post")
                self.assertIsNotNone(cp)
                assert cp is not None
                self.assertEqual(cp["id"], "a6Oecb0oPNImf5z01HGQeb")
                self.assertEqual(cp["title"], "메ㅌ 기ㅇ 라ㅇㅈ")
                self.assertEqual(cp["category"], "정보")

            # 4. download_post_by_url('⬇️ 받기' 버튼) 호출 시 force=True로 정상 추가되는지 검증
            fake_dm = MagicMock()
            fake_dm.add_urls.return_value = [MagicMock()]
            post_html = "<html><body>https://1024terabox.com/s/1hDHRjw1ZRnfbdpOgHmlwig?pwd=dao5</body></html>"
            post_resp = Mock(
                status_code=200, content=post_html.encode("utf-8"), text=post_html
            )
            with (
                patch("downloader.kone_watcher.SEEN_POSTS_FILE", seen_path),
                patch("downloader.kone_watcher.requests.get", return_value=post_resp),
            ):
                watcher2 = KoneWatcher(lambda: dict(config.DEFAULT_SETTINGS), fake_dm)
                dl_res = watcher2.download_post_by_url(
                    post_url="https://kone.gg/s/retrogame/awhXmsiTh8KJvWkL5NGQeb",
                    post_title="ㄱㄹㅈㅇㅇ 누가 이거 필요 하다고 봤는뎅",
                    post_id="awhXmsiTh8KJvWkL5NGQeb",
                )
                self.assertTrue(dl_res.get("success"))
                self.assertEqual(dl_res.get("downloads_added"), 1)
                fake_dm.add_urls.assert_called_once()
                _, call_kwargs = fake_dm.add_urls.call_args
                self.assertTrue(call_kwargs.get("force"))

    def test_pixeldrain_resolver_and_job_origin_metadata(self):
        """Pixeldrain 리졸버 동작 및 작업 출처(source_title, source_url, referer) 메타데이터 검증."""
        from downloader.manager import DownloadJob
        from downloader.resolvers.pixeldrain import PixeldrainResolver
        from downloader.url_helper import parse_download_candidates

        # 1. PixeldrainResolver can_handle 검증
        resolver = PixeldrainResolver()
        self.assertTrue(resolver.can_handle("https://pixeldrain.com/u/eXJ4G"))
        self.assertTrue(resolver.can_handle("https://pixeldrain.net/u/abc12345"))
        self.assertFalse(resolver.can_handle("https://example.com/u/eXJ4G"))

        # 2. Pixeldrain resolve 성공 모의 테스트
        mock_info = Mock(status_code=200)
        mock_info.json.return_value = {
            "id": "eXJ4G",
            "name": "RetroGame_Rom.zip",
            "size": 1048576,
        }
        with patch(
            "downloader.resolvers.pixeldrain.requests.get", return_value=mock_info
        ):
            res = resolver.resolve("https://pixeldrain.com/u/eXJ4G")
            self.assertEqual(
                res["url"], "https://pixeldrain.com/api/file/eXJ4G?download"
            )
            self.assertEqual(res["filename"], "RetroGame_Rom.zip")
            self.assertEqual(res["service_name"], "Pixeldrain")

        # 3. Pixeldrain 404 시 의미 있는 에러 메시지 발생 검증
        mock_404 = Mock(status_code=404)
        with patch(
            "downloader.resolvers.pixeldrain.requests.get", return_value=mock_404
        ):
            with self.assertRaises(ValueError) as ctx:
                resolver.resolve("https://pixeldrain.com/u/missing123")
            self.assertIn("Pixeldrain 파일", str(ctx.exception))
            self.assertIn("404", str(ctx.exception))

        # 4. parse_download_candidates에서 source_title, source_url, referer 보존 검증
        cands = parse_download_candidates(
            raw_text="https://pixeldrain.com/u/eXJ4G",
            single_referer="https://kone.gg/s/retrogame/samplepost",
            single_source_title="테스트 레트로 게임 게시글",
            single_source_url="https://kone.gg/s/retrogame/samplepost",
        )
        self.assertEqual(len(cands), 1)
        opts = cands[0]["options"]
        self.assertEqual(opts.get("referer"), "https://kone.gg/s/retrogame/samplepost")
        self.assertEqual(opts.get("source_title"), "테스트 레트로 게임 게시글")
        self.assertEqual(
            opts.get("source_url"), "https://kone.gg/s/retrogame/samplepost"
        )

        # 5. DownloadJob 생성 및 to_dict 직렬화 시 출처 정보 포함 검증
        job = DownloadJob(
            url="https://pixeldrain.com/u/eXJ4G",
            download_dir="./downloads",
            options=opts,
        )
        job_dict = job.to_dict()
        self.assertEqual(job_dict["original_url"], "https://pixeldrain.com/u/eXJ4G")
        self.assertEqual(job_dict["source_title"], "테스트 레트로 게임 게시글")
        self.assertEqual(
            job_dict["source_url"], "https://kone.gg/s/retrogame/samplepost"
        )
        self.assertEqual(job_dict["referer"], "https://kone.gg/s/retrogame/samplepost")


class SplitArchiveAndNaverBlogTests(unittest.TestCase):
    def test_split_archive_extensions(self):
        from downloader.resolvers.webpage import _has_downloadable_extension

        # 다양한 분할 압축 확장자
        for ext in (
            ".z01",
            ".z05",
            ".z99",
            ".001",
            ".002",
            ".010",
            ".r00",
            ".r01",
            ".a00",
            ".a01",
        ):
            url = f"https://example.com/files/archive{ext}"
            self.assertTrue(_has_downloadable_extension(url), f"Failed for {ext}")

        # 쿼리 파라미터 내 분할 압축 파일명
        self.assertTrue(
            _has_downloadable_extension(
                "https://example.com/download?file=game.part01.rar"
            )
        )
        self.assertTrue(
            _has_downloadable_extension(
                "https://example.com/download?filename=patch.z01"
            )
        )

        # 비다운로드 확장자
        self.assertFalse(_has_downloadable_extension("https://example.com/page.html"))
        self.assertFalse(
            _has_downloadable_extension("https://example.com/article/12345")
        )

    def test_naver_blog_attachment_extraction(self):
        from downloader.resolvers.webpage import WebPageResolver

        sample_html = """
        <div class="se-main-container">
            <div class="se-component se-file">
                <div class="se-module se-module-file">
                    <div class="se-file-title">
                        <span class="se-file-name">Saikano_KO.z01</span>
                        <span class="se-file-extension">.z01</span>
                    </div>
                    <a class="se-file-save-button" href="https://download.blog.naver.com/open/abc1234/Saikano_KO.z01">내 PC 저장</a>
                </div>
            </div>
            <div class="se-component se-file">
                <div class="se-module se-module-file">
                    <div class="se-file-title">
                        <span class="se-file-name">Saikano_KO</span>
                        <span class="se-file-extension">.zip</span>
                    </div>
                    <a class="se-file-save-button" href="https://download.blog.naver.com/open/def5678/Saikano_KO.zip">내 PC 저장</a>
                </div>
            </div>
        </div>
        """
        resolver = WebPageResolver()
        title, candidates = resolver.extract_candidates_from_html(
            sample_html, "https://blog.naver.com/sample/12345"
        )

        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0]["name"], "Saikano_KO.z01")
        self.assertEqual(
            candidates[0]["url"],
            "https://download.blog.naver.com/open/abc1234/Saikano_KO.z01",
        )
        self.assertEqual(candidates[1]["name"], "Saikano_KO.zip")
        self.assertEqual(
            candidates[1]["url"],
            "https://download.blog.naver.com/open/def5678/Saikano_KO.zip",
        )


class RomsfunResolverTests(unittest.TestCase):
    def test_can_handle(self):
        from downloader.resolvers.romsfun import RomsfunResolver

        resolver = RomsfunResolver()

        self.assertTrue(
            resolver.can_handle(
                "https://romsfun.com/download/saishuu-heiki-kanojo-166134/1"
            )
        )
        self.assertTrue(
            resolver.can_handle(
                "https://romsfun.com/download/saishuu-heiki-kanojo-166134"
            )
        )
        self.assertTrue(
            resolver.can_handle(
                "https://romsfun.com/roms/playstation-2/saishuu-heiki-kanojo.html"
            )
        )
        self.assertTrue(
            resolver.can_handle("https://www.romsfun.com/download/pokemon-1234/1")
        )
        self.assertFalse(resolver.can_handle("https://google.com/download/test"))
        self.assertFalse(
            resolver.can_handle("https://example.com/saishuu-heiki-kanojo-166134/1")
        )
        self.assertFalse(resolver.can_handle(""))

    def test_resolve_direct_download(self):
        from downloader.resolvers.romsfun import RomsfunResolver

        resolver = RomsfunResolver()

        mock_page_html = """
        <html>
            <head><title>Download Saishuu Heiki Kanojo</title></head>
            <body>
                <div id="download-container" data-ajax-url="https://romsfun.com/wp-admin/admin-ajax.php">
                    <div id="download-button" class="hidden">
                        <a id="download-link" href="https://sto.romsfast.com/old/Saishuu%20Heiki%20Kanojo.zip">Download</a>
                    </div>
                </div>
            </body>
        </html>
        """
        mock_ajax_json = {
            "success": True,
            "data": {
                "html": '<a id="download-link" href="https://sto.romsfast.com/PS2-Redump/Saishuu%20Heiki%20Kanojo%20(Japan).zip?e=179999&s=abc">Download Now</a>'
            },
        }

        mock_page_resp = Mock(status_code=200, ok=True, text=mock_page_html)
        mock_ajax_resp = Mock(status_code=200, ok=True)
        mock_ajax_resp.json.return_value = mock_ajax_json

        with patch("downloader.resolvers.romsfun.requests.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.get.return_value = mock_page_resp
            mock_session.post.return_value = mock_ajax_resp

            res = resolver.resolve(
                "https://romsfun.com/download/saishuu-heiki-kanojo-166134/1"
            )
            self.assertEqual(res["source_service"], "Romsfun")
            self.assertEqual(res["filename"], "Saishuu Heiki Kanojo (Japan).zip")
            self.assertEqual(
                res["url"],
                "https://sto.romsfast.com/PS2-Redump/Saishuu%20Heiki%20Kanojo%20(Japan).zip?e=179999&s=abc",
            )
            self.assertEqual(
                res["headers"]["Referer"],
                "https://romsfun.com/download/saishuu-heiki-kanojo-166134/1",
            )
            self.assertEqual(res["subfolder"], "Saishuu Heiki Kanojo")

    def test_resolve_404_raises_error(self):
        from downloader.resolvers.romsfun import RomsfunResolver

        resolver = RomsfunResolver()

        mock_404_resp = Mock(status_code=404, ok=False)
        with patch("downloader.resolvers.romsfun.requests.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.get.return_value = mock_404_resp

            with self.assertRaises(FileNotFoundError):
                resolver.resolve(
                    "https://romsfun.com/download/non-existent-rom-999999/1"
                )


class DuplicateExclusionAndRetryTests(unittest.TestCase):
    def test_duplicate_registered_as_failed_and_retryable(self):
        from downloader.manager import DownloadManager, canonical_url_key

        mock_settings = {
            "download_dir": "./downloads",
            "max_concurrent_downloads": 2,
            "check_duplicate_url": True,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            hist_path = Path(tmp_dir) / "completed_downloads.json"
            with patch("downloader.manager.HISTORY_FILE", hist_path):
                dm = DownloadManager(settings_getter=lambda: mock_settings)
                test_url = "https://blog.naver.com/sample/12345"

                # 1. 첫 번째 작업 등록 및 완료 시뮬레이션
                job1 = dm.add_url(test_url)
                self.assertIsNotNone(job1)
                assert job1 is not None
                job1.status = "completed"
                dm._record_completed_history(job1)

                key = canonical_url_key(test_url)
                self.assertIn(key, dm._completed_history)

                # 2. 동일 링크 재추가 (register_duplicate_as_failed=True)
                job2 = dm.add_url(
                    test_url, skip_duplicate=True, register_duplicate_as_failed=True
                )
                self.assertIsNotNone(job2)
                assert job2 is not None
                self.assertEqual(job2.status, "failed")
                self.assertIn("중복 다운로드 링크 자동 제외", job2.error_message)
                self.assertIn(job2.id, dm.jobs)

                # 3. 실패한 중복 작업 재시도 (retry_job)
                retry_ok = dm.retry_job(job2.id)
                self.assertTrue(retry_ok)
                self.assertEqual(job2.status, "queued")
                self.assertEqual(job2.error_message, "")
                # 완료 히스토리에서 해당 URL 제거 확인 (재다운로드 허용)
                self.assertNotIn(key, dm._completed_history)

                # 4. register_duplicate_as_failed=False인 경우 None 반환 확인
                job3 = dm.add_url(
                    test_url, skip_duplicate=True, register_duplicate_as_failed=False
                )
                # 현재 job2가 큐에 있으므로 중복 감지되어 None 반환
                self.assertIsNone(job3)

    def test_base64_detection_and_service_format_analysis(self):
        """Base64 인코딩 링크 검출 루틴 강화 및 단축 URL/다운로드 형식 분석 루틴 검증."""
        from downloader.resolvers.gigafile import GigaFileResolver
        from downloader.url_helper import (
            clean_url,
            decode_base64_string,
            detect_url_service_type,
            is_shortened_url,
            parse_download_candidates,
            unshorten_url,
        )

        # 1. GigaFileResolver can_handle 검증 (xgf.nu 지원)
        giga = GigaFileResolver()
        self.assertTrue(giga.can_handle("https://xgf.nu/3cM8o"))
        self.assertTrue(giga.can_handle("https://sub.xgf.nu/abcd"))
        self.assertTrue(
            giga.can_handle(
                "https://23.gigafile.nu/0206-b503f8d9b4200b001d63e95d91483137e"
            )
        )
        self.assertFalse(giga.can_handle("https://google.com/test"))

        # 2. 단축 URL 판별 (is_shortened_url)
        self.assertTrue(is_shortened_url("https://xgf.nu/3cM8o"))
        self.assertTrue(is_shortened_url("https://bit.ly/3xyz123"))
        self.assertTrue(is_shortened_url("https://url.kr/abc987"))
        self.assertFalse(
            is_shortened_url(
                "https://23.gigafile.nu/0206-b503f8d9b4200b001d63e95d91483137e"
            )
        )
        self.assertFalse(
            is_shortened_url("https://kone.gg/s/retrogame/c_eLH0X_KETbj9iVMIH6eb")
        )

        # 3. Base64 단일 문자열 및 주변 텍스트/암호 포함 검출
        # (a) 단일 Base64 문자열 (https://xgf.nu/3cM8o)
        b64_pure = "aHR0cHM6Ly94Z2YubnUvM2NNOG8="
        dec_pure = decode_base64_string(b64_pure)
        self.assertEqual(dec_pure, "https://xgf.nu/3cM8o")

        # (b) 한국어 문맥 + Base64 + 암호
        mixed_text = "다운로드 링크입니다: aHR0cHM6Ly94Z2YubnUvM2NNOG8= 비번: 5678"
        dec_mixed = decode_base64_string(mixed_text)
        self.assertIn("https://xgf.nu/3cM8o", dec_mixed)

        # (c) clean_url로 추출 및 암호 병합 (GigaFile dlkey)
        cleaned = clean_url(mixed_text)
        self.assertTrue(
            cleaned.startswith("https://xgf.nu/3cM8o?dlkey=5678")
            or "dlkey=5678" in cleaned
        )

        # 4. parse_download_candidates에서 본문 텍스트 내 Base64 링크 추출
        article_text = """
        안녕하세요. 파일 공유합니다.
        링크: aHR0cHM6Ly94Z2YubnUvM2NNOG8=
        비밀번호: 4321
        즐거운 하루 되세요!
        """
        cands = parse_download_candidates(raw_text=article_text)
        self.assertEqual(len(cands), 1)
        self.assertIn("xgf.nu/3cM8o", cands[0]["url"])
        self.assertEqual(cands[0]["options"].get("password"), "4321")

        # 5. unshorten_url 및 detect_url_service_type 검증 (Mock)
        mock_resp = Mock()
        mock_resp.url = "https://23.gigafile.nu/0206-b503f8d9b4200b001d63e95d91483137e"
        mock_resp.__enter__ = Mock(return_value=mock_resp)
        mock_resp.__exit__ = Mock(return_value=False)

        with patch("downloader.url_helper.requests.get", return_value=mock_resp):
            unshortened = unshorten_url("https://xgf.nu/3cM8o")
            self.assertEqual(
                unshortened,
                "https://23.gigafile.nu/0206-b503f8d9b4200b001d63e95d91483137e",
            )

            # 서비스 형식 분석
            svc_type, final_u = detect_url_service_type("https://xgf.nu/3cM8o")
            self.assertEqual(svc_type, "GigaFile")
            self.assertEqual(
                final_u, "https://23.gigafile.nu/0206-b503f8d9b4200b001d63e95d91483137e"
            )

        # 6. 다양한 URL 형식 분석 (detect_url_service_type)
        self.assertEqual(
            detect_url_service_type("magnet:?xt=urn:btih:abcdef")[0], "Magnet"
        )
        self.assertEqual(
            detect_url_service_type(
                "https://1024terabox.com/s/15yww7pCtsW_8JDTRelsMRw"
            )[0],
            "TeraBox",
        )
        self.assertEqual(
            detect_url_service_type("https://example.com/files/game.zip")[0],
            "Direct File",
        )
        self.assertEqual(
            detect_url_service_type("https://kone.gg/s/retrogame")[0], "WebPage"
        )

    def test_vikingfile_resolver_turnstile_bypass_and_s3_direct_link(self):
        """VikingFile 리졸버의 Cloudflare Turnstile 우회 및 S3 직링크 해석 검증."""
        from downloader.resolvers.vikingfile import VikingFileResolver

        resolver = VikingFileResolver()

        # 1. can_handle 검증
        self.assertTrue(resolver.can_handle("https://vikingfile.com/f/vAXzgTjLOu"))
        self.assertTrue(resolver.can_handle("https://vik1ngfile.site/f/vAXzgTjLOu"))
        self.assertTrue(
            resolver.can_handle("https://vikingfile.com/d/57Zu11aaT3/test.zip")
        )
        self.assertFalse(resolver.can_handle("https://example.com/f/vAXzgTjLOu"))

        # 2. 파일 삭제/404 시 FileNotFoundError 발생 검증
        mock_404_resp = Mock(status_code=404)
        with patch(
            "downloader.resolvers.vikingfile.requests.Session.get",
            return_value=mock_404_resp,
        ):
            with self.assertRaises(FileNotFoundError):
                resolver.resolve("https://vik1ngfile.site/f/nonexistent")

        # 3. FlareSolverr 우회 경로 검증
        mock_flare_resp = Mock(status_code=200)
        mock_flare_resp.json.return_value = {
            "solution": {
                "response": '<html><head><title>test.zip</title></head><body><a id="download-link" href="https://vikingfile.com/d/abc/test.zip">Download</a></body></html>',
                "cookies": [{"name": "cf_clearance", "value": "xyz123"}],
            }
        }
        mock_s3_resp = Mock()
        mock_s3_resp.status_code = 200
        mock_s3_resp.url = "https://s3.ovh.net/bucket/test.zip?signature=123"
        mock_s3_resp.headers = {
            "content-length": "1048576",
            "content-disposition": 'attachment; filename="test.zip"',
        }
        mock_s3_resp.__enter__ = Mock(return_value=mock_s3_resp)
        mock_s3_resp.__exit__ = Mock(return_value=False)

        with (
            patch(
                "downloader.resolvers.vikingfile.requests.post",
                return_value=mock_flare_resp,
            ),
            patch(
                "downloader.resolvers.vikingfile.requests.Session.get",
                return_value=mock_s3_resp,
            ),
        ):
            res = resolver.resolve(
                "https://vik1ngfile.site/f/vAXzgTjLOu",
                options={"flaresolverr_url": "http://localhost:8191"},
            )
            self.assertEqual(res["source_service"], "VikingFile")
            self.assertEqual(
                res["url"], "https://s3.ovh.net/bucket/test.zip?signature=123"
            )
            self.assertEqual(res["filename"], "test.zip")
            self.assertEqual(res["size"], 1048576)
            self.assertIn("cf_clearance=xyz123", res["cookies"])

        # 4. DrissionPage 스텔스 브라우저 우회 경로 검증 (Turnstile 감지 시)
        mock_turnstile_html = """
        <html>
            <head><title>코나츠.zip - VikingFile</title></head>
            <body>
                <div id="captcha-download">
                    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
                </div>
            </body>
        </html>
        """
        mock_init_resp = Mock(
            status_code=200,
            text=mock_turnstile_html,
            url="https://vik1ngfile.site/f/vAXzgTjLOu",
        )
        with (
            patch("downloader.resolvers.vikingfile.requests.Session.get") as mock_get,
            patch(
                "downloader.resolvers.vikingfile._resolve_via_drission"
            ) as mock_drission,
        ):
            mock_get.side_effect = [mock_init_resp, mock_s3_resp]
            mock_drission.return_value = (
                "https://vikingfile.com/d/57Zu11aaT3/konatsu.zip",
                "코나츠.zip",
                "theme=light",
            )
            res = resolver.resolve("https://vik1ngfile.site/f/vAXzgTjLOu")
            self.assertEqual(res["source_service"], "VikingFile")
            self.assertEqual(
                res["url"], "https://s3.ovh.net/bucket/test.zip?signature=123"
            )
            self.assertEqual(res["filename"], "코나츠.zip")
            self.assertEqual(res["size"], 1048576)
            self.assertIn("theme=light", res["cookies"])
            mock_drission.assert_called_once()

    def test_auto_manual_download_distinction(self):
        """자동/수동 다운로드 구분 플래그 및 API/매니저 연동 검증."""
        import tempfile

        from downloader.manager import DownloadManager
        from downloader.url_helper import parse_download_candidates

        with tempfile.TemporaryDirectory() as tmp_dir:
            dm = DownloadManager(
                settings_getter=lambda: {
                    "download_dir": tmp_dir,
                    "check_duplicate_url": False,
                }
            )

            # 1. 수동 작업 (기본값)
            job_manual = dm.add_url("https://example.com/manual.zip", is_auto=False)
            self.assertIsNotNone(job_manual)
            assert job_manual is not None
            self.assertFalse(job_manual.is_auto)
            self.assertEqual(job_manual.download_type, "manual")
            d_manual = job_manual.to_dict()
            self.assertFalse(d_manual["is_auto"])
            self.assertEqual(d_manual["download_type"], "manual")

            # 2. 자동 작업 (is_auto=True)
            job_auto = dm.add_url("https://example.com/auto.zip", is_auto=True)
            self.assertIsNotNone(job_auto)
            assert job_auto is not None
            self.assertTrue(job_auto.is_auto)
            self.assertEqual(job_auto.download_type, "auto")
            d_auto = job_auto.to_dict()
            self.assertTrue(d_auto["is_auto"])
            self.assertEqual(d_auto["download_type"], "auto")

            # 3. parse_download_candidates 옵션 연동
            cands = parse_download_candidates(
                raw_text="https://example.com/item.zip",
                single_is_auto=True,
            )
            self.assertEqual(len(cands), 1)
            self.assertTrue(cands[0]["options"]["is_auto"])

            # 4. add_urls 일괄 추가 시 상속
            jobs = dm.add_urls(cands, is_auto=True)
            self.assertEqual(len(jobs), 1)
            self.assertTrue(jobs[0].is_auto)
            self.assertEqual(jobs[0].download_type, "auto")

            # 5. 히스토리 기록 시 보존
            job_auto.status = "completed"
            job_auto.filename = "auto.zip"
            dm._record_completed_history(job_auto)
            hist = dm.get_history()
            auto_hist = [h for h in hist if h["url"] == "https://example.com/auto.zip"]
            self.assertTrue(len(auto_hist) > 0)
            self.assertTrue(auto_hist[0]["is_auto"])
            self.assertEqual(auto_hist[0]["download_type"], "auto")

    def test_resume_download_part_file_preservation_and_range_request(self):
        """다운로드 중단/오류 발생 시 .part 임시 파일 보존 및 HTTP Range 이어받기 검증."""
        import os
        import tempfile
        from unittest.mock import Mock, patch

        from downloader.engine import DownloadTask

        with tempfile.TemporaryDirectory() as tmp_dir:
            task = DownloadTask(
                url="https://example.com/largefile.iso",
                dest_dir=tmp_dir,
                filename="largefile.iso",
                partial_id="task123",
            )
            part_path = os.path.join(tmp_dir, "largefile.iso.task123.part")

            # 1. 1차 다운로드 중 오류 발생 시뮬레이션:
            # 100바이트 스트리밍 도중 네트워크 에러 발생
            mock_resp_fail = Mock()
            mock_resp_fail.status_code = 200
            mock_resp_fail.headers = {
                "Content-Length": "1000",
                "Content-Type": "application/octet-stream",
            }
            mock_resp_fail.url = "https://example.com/largefile.iso"
            mock_resp_fail.raise_for_status = Mock()

            def fail_iter(chunk_size=1024):
                yield b"A" * 100
                raise ConnectionResetError("네트워크 연결 끊김 시뮬레이션")

            mock_resp_fail.iter_content = fail_iter

            with patch(
                "downloader.engine.requests.Session.get", return_value=mock_resp_fail
            ):
                result1 = task.run()

            # 오류 반환 확인
            self.assertFalse(result1["success"])
            self.assertEqual(result1["status"], "failed")
            self.assertEqual(result1["downloaded_bytes"], 100)

            # .part 파일이 삭제되지 않고 디스크에 보존되어 있어야 함!
            self.assertTrue(os.path.exists(part_path))
            self.assertEqual(os.path.getsize(part_path), 100)

            # 2. 2차 다운로드 (재시도) 시뮬레이션:
            # 보존된 100바이트 .part 파일이 있으므로 Range: bytes=100- 요청 전송 확인
            captured_headers = {}

            mock_resp_resume = Mock()
            mock_resp_resume.status_code = 206  # Partial Content
            mock_resp_resume.headers = {
                "Content-Range": "bytes 100-999/1000",
                "Content-Length": "900",
                "Content-Type": "application/octet-stream",
            }
            mock_resp_resume.url = "https://example.com/largefile.iso"
            mock_resp_resume.raise_for_status = Mock()

            def success_iter(chunk_size=1024):
                yield b"B" * 900

            mock_resp_resume.iter_content = success_iter

            def mock_get(url, headers=None, **kwargs):
                nonlocal captured_headers
                captured_headers = dict(headers or {})
                return mock_resp_resume

            with patch("downloader.engine.requests.Session.get", side_effect=mock_get):
                result2 = task.run()

            # Range 요청 헤더 확인
            self.assertEqual(captured_headers.get("Range"), "bytes=100-")
            # 다운로드 완료 확인
            self.assertTrue(result2["success"])
            self.assertEqual(result2["status"], "completed")
            final_file = os.path.join(tmp_dir, "largefile.iso")
            self.assertTrue(os.path.exists(final_file))
            self.assertEqual(os.path.getsize(final_file), 1000)
            with open(final_file, "rb") as f:
                content = f.read()
                self.assertEqual(content, b"A" * 100 + b"B" * 900)

    def test_retry_job_part_file_handling_and_cleanup(self):
        """매니저에서 retry_job 시 .part 크기 반영 및 remove_job/clear_jobs 시 .part 정리 검증."""
        import os
        import tempfile
        from unittest.mock import patch

        from downloader.manager import DownloadJob, DownloadManager

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(DownloadManager, "_start_workers"):
                dm = DownloadManager(
                    settings_getter=lambda: {
                        "download_dir": tmp_dir,
                        "check_duplicate_url": False,
                    }
                )
            job = DownloadJob(
                url="https://example.com/test_resume.bin", download_dir=tmp_dir
            )
            job.filename = "test_resume.bin"
            job.status = "failed"
            job.total_bytes = 1024
            dm.jobs[job.id] = job

            # 임의로 임시 .part 파일 생성 (512바이트)
            part_path = os.path.join(tmp_dir, f"test_resume.bin.{job.id}.part")
            with open(part_path, "wb") as f:
                f.write(b"X" * 512)

            # 1. retry_job 호출 시 기존 .part 크기 감지 및 진행도 유지
            ok = dm.retry_job(job.id)
            self.assertTrue(ok)
            self.assertEqual(job.downloaded_bytes, 512)
            self.assertEqual(job.progress, 50.0)
            self.assertEqual(job.status, "queued")

            # 2. remove_job 호출 시 사용자의 명시적 삭제이므로 .part 파일 정리
            self.assertTrue(os.path.exists(part_path))
            rm_ok = dm.remove_job(job.id)
            self.assertTrue(rm_ok)
            self.assertFalse(os.path.exists(part_path))

    def test_ignore_ssl_certs_configuration(self):
        """SSL 인증서 검증 무시 설정 활성화/비활성화 및 requests/ssl 동작 검증."""
        import ssl

        import requests

        import config

        # 1. 설정 기본값 검증 (기본: False)
        self.assertIn("ignore_ssl_certs", config.DEFAULT_SETTINGS)
        self.assertFalse(config.DEFAULT_SETTINGS["ignore_ssl_certs"])

        # 2. 비활성화 상태 확인
        config.configure_ssl_verification(False)
        s_verify = requests.Session()
        self.assertTrue(s_verify.verify)
        ctx_verify = ssl.create_default_context()
        self.assertEqual(ctx_verify.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx_verify.check_hostname)

        # 3. 활성화 상태 확인 (SSL 검증 무시)
        config.configure_ssl_verification(True)
        s_noverify = requests.Session()
        self.assertFalse(s_noverify.verify)
        ctx_noverify = ssl.create_default_context()
        self.assertEqual(ctx_noverify.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx_noverify.check_hostname)

        # 복구
        config.configure_ssl_verification(False)


class RootzResolverTests(unittest.TestCase):
    def test_can_handle(self):
        from downloader.resolvers.rootz import RootzResolver

        resolver = RootzResolver()
        self.assertTrue(resolver.can_handle("https://www.rootz.so/d/1wBzrE"))
        self.assertTrue(resolver.can_handle("http://rootz.so/d/test-file_123"))
        self.assertFalse(resolver.can_handle("https://rootz.so/"))
        self.assertFalse(resolver.can_handle("https://rootz.so/login"))
        self.assertFalse(resolver.can_handle("https://example.com/d/1wBzrE"))
        self.assertFalse(resolver.can_handle(""))

    def test_dispatcher_integration(self):
        from downloader.resolvers.dispatcher import default_dispatcher
        from downloader.resolvers.rootz import RootzResolver

        resolver = default_dispatcher.get_resolver("https://www.rootz.so/d/1wBzrE")
        self.assertIsInstance(resolver, RootzResolver)

    def test_resolve_success(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.rootz import RootzResolver

        resolver = RootzResolver()
        sample_url = "https://www.rootz.so/d/1wBzrE"

        mock_page_resp = MagicMock()
        mock_page_resp.status_code = 200
        mock_page_resp.text = "<html><head><title>My_Archive_File.part1.rar - Rootz</title></head><body></body></html>"

        mock_proxy_resp = MagicMock()
        mock_proxy_resp.status_code = 302
        mock_proxy_resp.headers = {
            "Location": "https://cdn-files.alcyone.so/users/123/sample.rar?filename=My_Archive_File.part1.rar"
        }

        mock_head_resp = MagicMock()
        mock_head_resp.status_code = 200
        mock_head_resp.headers = {"Content-Length": "1048576"}

        with patch("downloader.resolvers.rootz.requests.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.cookies.get_dict.return_value = {"cookie_key": "cookie_val"}
            mock_session.get.side_effect = [mock_page_resp, mock_proxy_resp]
            mock_session.head.return_value = mock_head_resp

            result = resolver.resolve(sample_url)

            self.assertEqual(
                result["url"],
                "https://cdn-files.alcyone.so/users/123/sample.rar?filename=My_Archive_File.part1.rar",
            )
            self.assertEqual(result["filename"], "My_Archive_File.part1.rar")
            self.assertEqual(result["file_size"], 1048576)
            self.assertEqual(result["source_service"], "Rootz")
            self.assertEqual(result["cookies"], {"cookie_key": "cookie_val"})
            self.assertIn("User-Agent", result["headers"])
            self.assertEqual(result["referer"], "https://www.rootz.so/")

    def test_resolve_404(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.rootz import RootzResolver

        resolver = RootzResolver()
        sample_url = "https://www.rootz.so/d/nonexistent"

        mock_page_resp = MagicMock()
        mock_page_resp.status_code = 404

        with patch("downloader.resolvers.rootz.requests.Session") as mock_session_cls:
            mock_session = mock_session_cls.return_value
            mock_session.get.return_value = mock_page_resp

            with self.assertRaises(ValueError) as cm:
                resolver.resolve(sample_url)
            self.assertIn("404", str(cm.exception))


class GofilePasswordTests(unittest.TestCase):
    def test_password_required_fallback_to_terabox_password_success(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        # 수동 비밀번호가 잘못 입력된 경우, terabox_password로 자동 재시도하여 성공하는 시나리오
        url = "https://gofile.io/d/WOP99VgW?pwd=wrong_manual"

        # 1차 시도 (수동 비밀번호 wrong_manual): passwordWrong 반환
        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": False,
                "password": True,
                "passwordStatus": "passwordWrong",
            },
        }

        # 2차 시도 (기본 패스워드 terabox_password 'dao5' 자동 재시도): 성공 반환
        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": True,
                "children": {
                    "f1": {
                        "type": "file",
                        "name": "RomForge.zip",
                        "link": "https://store.gofile.io/dl/RomForge.zip",
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
            res = resolver.resolve(url, options={"terabox_password": "dao5"})
            self.assertEqual(res["filename"], "RomForge.zip")
            self.assertEqual(res["url"], "https://store.gofile.io/dl/RomForge.zip")
            self.assertEqual(fake_session.get.call_count, 2)

    def test_password_in_url_query_success(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/WOP99VgW?pwd=mypassword"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": True,
                "children": {
                    "f1": {
                        "type": "file",
                        "name": "RomForge.zip",
                        "link": "https://store.gofile.io/dl/RomForge.zip",
                    }
                },
            },
        }

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.return_value = resp

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
            res = resolver.resolve(url)
            self.assertEqual(res["filename"], "RomForge.zip")
            self.assertEqual(fake_session.get.call_count, 1)

    def test_password_required_no_password_error_message(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/WOP99VgW"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": False,
                "password": True,
                "passwordStatus": "passwordRequired",
            },
        }

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.return_value = resp

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
            patch("config.load_settings", return_value={"terabox_password": ""}),
        ):
            with self.assertRaises(ValueError) as cm:
                resolver.resolve(url, options={})
            self.assertIn("비밀번호로 보호되어 있는 링크입니다", str(cm.exception))

    def test_password_wrong_error_message(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/WOP99VgW"

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "status": "ok",
            "data": {
                "canAccess": False,
                "password": True,
                "passwordStatus": "passwordWrong",
            },
        }

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.return_value = resp

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
            patch("config.load_settings", return_value={"terabox_password": ""}),
        ):
            with self.assertRaises(ValueError) as cm:
                resolver.resolve(url, options={"password": "wrong_password"})
            self.assertIn("비밀번호가 일치하지 않습니다", str(cm.exception))
            self.assertIn("wrong_password", str(cm.exception))

    def test_gofile_wrong_token_auto_recovery(self):
        """GoFile API가 error-wrongToken을 반환하면 토큰을 재발급받아 자동으로 재시도하는지 검증."""
        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/TESTRETRY"

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        # 1차 시도: error-wrongToken, 2차 시도: 정상 파일 응답
        resp_fail = MagicMock()
        resp_fail.status_code = 401
        resp_fail.json.return_value = {"status": "error-wrongToken", "data": {}}

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {
            "status": "ok",
            "data": {
                "type": "file",
                "name": "Recovered.zip",
                "link": "https://store.gofile.io/dl/Recovered.zip",
            },
        }
        fake_session.get.side_effect = [resp_fail, resp_ok]

        with (
            patch("downloader.resolvers.gofile.requests.Session", return_value=fake_session),
            patch("downloader.resolvers.gofile.get_guest_token", side_effect=["old_token", "new_token"]) as mock_tok,
            patch("downloader.resolvers.gofile.get_gofile_secret", return_value="sec_123"),
            patch("downloader.resolvers.gofile._clear_guest_token") as mock_clear,
        ):
            res = resolver.resolve(url)
            self.assertEqual(res["url"], "https://store.gofile.io/dl/Recovered.zip")
            mock_clear.assert_called_once()
            self.assertEqual(mock_tok.call_count, 2)

    def test_gofile_rate_limit_friendly_error(self):
        """GoFile API가 error-rateLimit / HTTP 429를 반환하면 사용자 친화적인 메시지를 전달하는지 검증."""
        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/TESTRATE"

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.json.return_value = {"status": "error-rateLimit", "data": {}}
        fake_session.get.return_value = resp_429

        with (
            patch("downloader.resolvers.gofile.requests.Session", return_value=fake_session),
            patch("downloader.resolvers.gofile.get_guest_token", return_value="token123"),
            patch("downloader.resolvers.gofile.get_gofile_secret", return_value="sec_123"),
        ):
            with self.assertRaises(RuntimeError) as cm:
                resolver.resolve(url)
            self.assertIn("요청 한도 초과", str(cm.exception))
            self.assertIn("Rate Limit", str(cm.exception))

    def test_gofile_token_persistence(self):
        """GoFile 게스트 토큰의 디스크 저장, 로드, 삭제가 정상 작동하는지 검증."""
        import tempfile
        from pathlib import Path
        from downloader.resolvers import gofile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "data" / "gofile_session.json"
            with patch("downloader.resolvers.gofile._get_token_file_path", return_value=tmp_path):
                # 1. 초기 상태는 None
                self.assertIsNone(gofile._load_persisted_token())

                # 2. 저장 후 로드 성공
                gofile._save_persisted_token("persisted_test_token")
                self.assertTrue(tmp_path.exists())
                self.assertEqual(gofile._load_persisted_token(), "persisted_test_token")

                # 3. 토큰 삭제
                gofile._clear_guest_token()
                self.assertFalse(tmp_path.exists())
                self.assertIsNone(gofile._load_persisted_token())

    def test_gofile_fallback_secret_on_timeout(self):
        """GoFile 웹페이지 연결 시간 초과 시 기본 백업 secret으로 안전하게 폴백되는지 검증."""
        import requests
        from downloader.resolvers.gofile import get_gofile_secret, DEFAULT_WT_SECRET

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.side_effect = requests.exceptions.ConnectTimeout("Connection timed out")

        with patch("downloader.resolvers.gofile._cached_secret", None):
            secret = get_gofile_secret(fake_session, force_refresh=True)
            self.assertEqual(secret, DEFAULT_WT_SECRET)

    def test_gofile_proxy_applied(self):
        """GoFile 옵션에 프록시가 주어지면 requests.Session.proxies에 정상 설정되는지 검증."""
        from downloader.resolvers.gofile import GofileResolver

        resolver = GofileResolver()
        url = "https://gofile.io/d/TESTPROXY"

        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "status": "ok",
            "data": {"type": "file", "name": "f.zip", "link": "https://store.gofile.io/f.zip"},
        }
        fake_session.get.return_value = resp

        with (
            patch("downloader.resolvers.gofile.requests.Session", return_value=fake_session),
            patch("downloader.resolvers.gofile.get_guest_token", return_value="tok"),
            patch("downloader.resolvers.gofile.get_gofile_secret", return_value="sec"),
        ):
            resolver.resolve(url, options={"gofile_proxy": "http://127.0.0.1:8080"})
            self.assertEqual(fake_session.proxies, {"http": "http://127.0.0.1:8080", "https": "http://127.0.0.1:8080"})


class MegaResolverTests(unittest.TestCase):
    def test_can_handle(self):
        from downloader.resolvers.dispatcher import default_dispatcher
        from downloader.resolvers.mega import MegaResolver

        resolver = MegaResolver()
        self.assertTrue(
            resolver.can_handle(
                "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
            )
        )
        self.assertTrue(
            resolver.can_handle(
                "https://mega.co.nz/#!KV0U3RbY!LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
            )
        )
        self.assertTrue(
            resolver.can_handle(
                "https://mega.io/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
            )
        )
        self.assertFalse(
            resolver.can_handle(
                "https://example.com/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
            )
        )
        self.assertFalse(resolver.can_handle(""))

        # dispatcher 매칭 검증
        disp_res = default_dispatcher.get_resolver(
            "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
        )
        self.assertIsInstance(disp_res, MegaResolver)

    def test_resolve_success(self):
        from unittest.mock import MagicMock, patch

        from transferit._crypto import b64_to_a32, encrypt_attr

        from downloader.resolvers.mega import MegaResolver

        resolver = MegaResolver()
        url = (
            "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
        )

        key_a32 = b64_to_a32("LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM")
        from transferit._crypto import b64url_encode

        enc_attr_b64 = b64url_encode(
            encrypt_attr({"n": "Tokyo_Bus_Guide.xdelta"}, key_a32)
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "s": 16408976,
                "at": enc_attr_b64,
                "g": "https://gfs.mega.co.nz/dl/test_download_token",
            }
        ]

        with patch("downloader.resolvers.mega.requests.post", return_value=mock_resp):
            res = resolver.resolve(url)
            self.assertEqual(res["filename"], "Tokyo_Bus_Guide.xdelta")
            self.assertEqual(res["file_size"], 16408976)
            self.assertEqual(
                res["url"], "https://gfs.mega.co.nz/dl/test_download_token"
            )
            self.assertEqual(res["source_service"], "MEGA")
            self.assertTrue(callable(res.get("task_factory")))

    def test_resolve_error_code(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.mega import MegaResolver

        resolver = MegaResolver()
        url = (
            "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [-9]  # ENOENT: 삭제되거나 없음

        with patch("downloader.resolvers.mega.requests.post", return_value=mock_resp):
            with self.assertRaises(ValueError) as cm:
                resolver.resolve(url)
            self.assertIn("ENOENT", str(cm.exception))


class NaverMyBoxResolverTests(unittest.TestCase):
    def test_can_handle(self):
        from downloader.resolvers.dispatcher import default_dispatcher
        from downloader.resolvers.naver_mybox import NaverMyBoxResolver

        resolver = NaverMyBoxResolver()
        self.assertTrue(
            resolver.can_handle(
                "https://mybox.naver.com/share/list?shareKey=_djQmZqy77-Jis-J1RabCXeAg7aU1TlgCf9F2q3RXRV9X7R-ZtBDHsMvLT0uu9kD1sWcmkHydfdoWuZxx3xaVgU%3D"
            )
        )
        self.assertTrue(
            resolver.can_handle("https://mybox.naver.com/share/file?fileKey=abc12345")
        )
        self.assertFalse(resolver.can_handle("https://cafe.naver.com/sample/123"))
        self.assertFalse(
            resolver.can_handle("https://example.com/share/list?shareKey=123")
        )
        self.assertFalse(resolver.can_handle(""))

        # Dispatcher integration
        disp = default_dispatcher.get_resolver(
            "https://mybox.naver.com/share/list?shareKey=_djQmZqy77-Jis-J1RabCXeAg7aU1TlgCf9F2q3RXRV9X7R-ZtBDHsMvLT0uu9kD1sWcmkHydfdoWuZxx3xaVgU%3D"
        )
        self.assertIsInstance(disp, NaverMyBoxResolver)

    def test_resolve_success(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.naver_mybox import NaverMyBoxResolver

        resolver = NaverMyBoxResolver()
        url = "https://mybox.naver.com/share/list?shareKey=testShareKey123"

        mock_prop_resp = MagicMock()
        mock_prop_resp.ok = True
        mock_prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "resourceKey": "sampleResKeyXYZ",
                "resourceName": "SamplePatch.zip",
                "hasPassword": False,
            },
        }

        mock_file_prop_resp = MagicMock()
        mock_file_prop_resp.ok = True
        mock_file_prop_resp.json.return_value = {
            "code": "0",
            "result": {
                "fileName": "SamplePatch.zip",
                "resourceSize": 144660763,
                "resourceKey": "sampleResKeyXYZ",
            },
        }

        fake_session = MagicMock()
        fake_session.get.side_effect = [mock_prop_resp, mock_file_prop_resp]

        with patch(
            "downloader.resolvers.naver_mybox.requests.Session",
            return_value=fake_session,
        ):
            res = resolver.resolve(url)
            self.assertEqual(res["filename"], "SamplePatch.zip")
            self.assertEqual(res["file_size"], 144660763)
            self.assertEqual(res["source_service"], "Naver MYBOX")
            self.assertIn("fs.mybox.naver.com", res["url"])
            self.assertIn("sampleResKeyXYZ", res["url"])
            self.assertEqual(res["referer"], "https://mybox.naver.com/")

    def test_resolve_no_resource_key_error(self):
        from unittest.mock import MagicMock, patch

        from downloader.resolvers.naver_mybox import NaverMyBoxResolver

        resolver = NaverMyBoxResolver()
        url = "https://mybox.naver.com/share/list?shareKey=expiredKey"

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"code": "2001", "message": "Expired"}

        fake_session = MagicMock()
        fake_session.get.return_value = mock_resp

        with patch(
            "downloader.resolvers.naver_mybox.requests.Session",
            return_value=fake_session,
        ):
            with self.assertRaises(ValueError) as cm:
                resolver.resolve(url)
            self.assertIn("리소스 키", str(cm.exception))


class ServerStorageEndpointTests(unittest.TestCase):
    """서버 다운로드 폴더 저장용량 및 디스크 정보 API 단위 테스트."""

    def test_get_storage_info_structure(self):
        import os
        import tempfile
        from app import get_storage_info

        with tempfile.TemporaryDirectory() as tmpdir:
            # 테스트 파일 2개 생성 (100바이트, 200바이트)
            f1 = os.path.join(tmpdir, "test1.bin")
            f2 = os.path.join(tmpdir, "test2.bin")
            with open(f1, "wb") as f:
                f.write(b"A" * 100)
            with open(f2, "wb") as f:
                f.write(b"B" * 200)

            info = get_storage_info(tmpdir, force_refresh=True)
            self.assertEqual(info["download_dir"], os.path.abspath(tmpdir))
            self.assertGreater(info["disk_total"], 0)
            self.assertGreater(info["disk_free"], 0)
            self.assertGreaterEqual(info["disk_percent"], 0.0)
            self.assertEqual(info["folder_size"], 300)
            self.assertEqual(info["file_count"], 2)

    def test_server_storage_api_endpoint(self):
        from app import app
        from config import get_or_create_api_token

        token = get_or_create_api_token()
        with app.test_client() as client:
            resp = client.get("/api/server/storage", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIn("disk_total", data)
            self.assertIn("disk_free", data)
            self.assertIn("disk_percent", data)
            self.assertIn("folder_size", data)
            self.assertIn("file_count", data)

            info_resp = client.get("/api/info", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(info_resp.status_code, 200)
            info_data = info_resp.get_json()
            self.assertIn("storage", info_data)
            self.assertEqual(info_data["storage"]["download_dir"], data["download_dir"])


class HtmlAttachmentAndKonePageTests(unittest.TestCase):
    """동적 다운로드 엔드포인트의 .html 첨부파일 허용 및 Kone.gg 직접 링크 해석 테스트."""

    def test_explicit_html_attachment_download_succeeds(self):
        html_bytes = b"<!doctype html><html lang='ko'><head><title>Skill Guide</title></head><body>OK</body></html>"
        resp = Mock(
            status_code=200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="guide.html"',
                "Content-Length": str(len(html_bytes)),
            },
        )
        resp.iter_content.return_value = [html_bytes]
        resp.close = Mock()

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = resp
                result = DownloadTask(
                    "https://65.gigafile.nu/download.php?file=1234",
                    folder,
                    "guide.html",
                ).run()
            self.assertEqual(result["status"], "completed")
            self.assertEqual((Path(folder) / "guide.html").read_bytes(), html_bytes)

    def test_html_filename_with_text_html_without_attachment_still_fails(self):
        resp = Mock(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8", "Content-Length": "50"},
        )
        resp.iter_content.return_value = [b"<html><body>File expired</body></html>"]
        resp.close = Mock()

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = resp
                result = DownloadTask(
                    "https://65.gigafile.nu/download.php?file=1234",
                    folder,
                    "guide.html",
                ).run()
            self.assertEqual(result["status"], "failed")
            self.assertIn("HTML", result["error"])

    def test_html_attachment_with_cloudflare_challenge_still_fails(self):
        cf_bytes = b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>challenges.cloudflare.com</body></html>"
        resp = Mock(
            status_code=200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="guide.html"',
                "Content-Length": str(len(cf_bytes)),
            },
        )
        resp.iter_content.return_value = [cf_bytes]
        resp.close = Mock()

        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = resp
                result = DownloadTask(
                    "https://65.gigafile.nu/download.php?file=1234",
                    folder,
                    "guide.html",
                ).run()
            self.assertEqual(result["status"], "failed")
            self.assertIn("Cloudflare", result["error"])

    def test_webpage_resolver_kone_chrome_ua_fallback(self):
        from downloader.resolvers.webpage import WebPageResolver

        empty_bot_resp = Mock(
            status_code=200,
            ok=True,
            url="https://kone.gg/s/retrogame/post123",
            text="<html><body><div id='app'></div></body></html>",
            content=b"<html><body><div id='app'></div></body></html>",
            encoding="utf-8",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        rich_chrome_html = (
            '<html><head><title>Sample Post - retrogame | 코네</title></head>'
            '<body><div id="post-article"><p>https://gofile.io/d/AbCdEf</p></div></body></html>'
        )
        chrome_resp = Mock(
            status_code=200,
            ok=True,
            url="https://kone.gg/s/retrogame/post123",
            text=rich_chrome_html,
            content=rich_chrome_html.encode("utf-8"),
            encoding="utf-8",
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.get.side_effect = [empty_bot_resp, chrome_resp]

        resolver = WebPageResolver()
        with patch("downloader.resolvers.webpage.requests.Session", return_value=fake_session):
            html_text, final_url = resolver._fetch_html("https://kone.gg/s/retrogame/post123")
        self.assertEqual(final_url, "https://kone.gg/s/retrogame/post123")
        self.assertIn("gofile.io/d/AbCdEf", html_text)
        title, cands = resolver.extract_candidates_from_html(html_text, final_url)
        self.assertEqual(title, "Sample Post")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["url"], "https://gofile.io/d/AbCdEf")


class GigaFileBundleResolverTests(unittest.TestCase):
    """GigaFile 다중 파일 묶음(matomete) 및 자식 파일 재해석 검증."""

    @staticmethod
    def _make_fake_session():
        fake_session = MagicMock()
        fake_session.__enter__.return_value = fake_session
        fake_session.__exit__.return_value = False
        fake_session.headers = {}
        fake_session.cookies.get_dict.return_value = {"gfsid": "sess_token"}
        return fake_session

    def test_resolve_matomete_bundle_extracts_all_files(self):
        from downloader.resolvers.gigafile import GigaFileResolver

        bundle_html = """
        <html><body>
        <script>
        var server = "65.gigafile.nu";
        var dl_ajax_server = "65x.gigafile.nu";
        var files = [{"file": "0219-aaa", "size": 100, "bypasses": []}, {"file": "0219-bbb", "size": 200, "bypasses": []}];
        </script>
        <div id="contents_matomete">
            <div id="matomete_zip"><span id="matomete_zip_filename">bundle_pack.zip</span></div>
            <div class="matomete_file">
                <div class="matomete_file_info"><span>1</span><span class="matomete_file_name">A***.zip</span></div>
                <button class="download_panel_btn_dl" onclick="download(0, '0219-aaa', false)">DL</button>
            </div>
            <div class="matomete_file">
                <div class="matomete_file_info"><span>2</span><span class="matomete_file_name">B***.zip</span></div>
                <button class="download_panel_btn_dl" onclick="download(1, '0219-bbb', false)">DL</button>
            </div>
        </div>
        </body></html>
        """
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://65.gigafile.nu/0219-bundle",
            text=bundle_html,
        )
        name_resp_1 = Mock(status_code=200, ok=True)
        name_resp_1.json.return_value = {"status": 0, "filename": "A.zip"}
        name_resp_2 = Mock(status_code=200, ok=True)
        name_resp_2.json.return_value = {"status": 0, "filename": "B.zip"}

        fake_session = self._make_fake_session()
        fake_session.get.side_effect = [page_resp, name_resp_1, name_resp_2]

        with patch(
            "downloader.resolvers.gigafile.requests.Session", return_value=fake_session
        ):
            resolved = GigaFileResolver().resolve("https://65.gigafile.nu/0219-bundle")

        self.assertEqual(resolved["source_service"], "GigaFile")
        self.assertEqual(
            resolved["url"], "https://65.gigafile.nu/download.php?file=0219-aaa"
        )
        self.assertEqual(resolved["filename"], "A.zip")
        self.assertEqual(resolved["size"], 100)
        self.assertEqual(resolved["subfolder"], "bundle_pack")
        self.assertIn("prog_key=", resolved["cookies"])
        self.assertEqual(resolved["headers"]["Referer"], "https://65.gigafile.nu/0219-bundle")
        self.assertEqual(len(resolved["files"]), 2)
        self.assertEqual(
            resolved["files"][0]["url"],
            "https://65.gigafile.nu/download.php?file=0219-aaa",
        )
        self.assertEqual(resolved["files"][0]["name"], "A.zip")
        self.assertEqual(
            resolved["files"][1]["url"],
            "https://65.gigafile.nu/download.php?file=0219-bbb",
        )
        self.assertEqual(resolved["files"][1]["name"], "B.zip")
        self.assertEqual(resolved["files"][1]["size"], 200)

    def test_re_resolve_child_download_php_url_without_recursive_files(self):
        from downloader.resolvers.gigafile import GigaFileResolver

        bundle_html = """
        <script>
        var server = "65.gigafile.nu";
        var dl_ajax_server = "65x.gigafile.nu";
        var files = [{"file": "0219-aaa", "size": 100}, {"file": "0219-bbb", "size": 200}];
        </script>
        <div id="contents_matomete">
            <div class="matomete_file"><button class="download_panel_btn_dl" onclick="download(0, '0219-aaa', false)">DL</button></div>
            <div class="matomete_file"><button class="download_panel_btn_dl" onclick="download(1, '0219-bbb', false)">DL</button></div>
        </div>
        """
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://65.gigafile.nu/0219-bundle",
            text=bundle_html,
        )
        name_api_fail = Mock(status_code=500, ok=False)

        fake_session = self._make_fake_session()
        fake_session.get.side_effect = [page_resp, name_api_fail]

        with patch(
            "downloader.resolvers.gigafile.requests.Session", return_value=fake_session
        ):
            resolved = GigaFileResolver().resolve(
                "https://65.gigafile.nu/download.php?file=0219-bbb",
                options={
                    "referer": "https://65.gigafile.nu/0219-bundle",
                    "filename": "B.zip",
                },
            )

        self.assertEqual(
            fake_session.get.call_args_list[0].args[0],
            "https://65.gigafile.nu/0219-bundle",
        )
        self.assertEqual(
            resolved["url"], "https://65.gigafile.nu/download.php?file=0219-bbb"
        )
        self.assertEqual(resolved["filename"], "B.zip")
        self.assertEqual(resolved["size"], 200)
        self.assertNotIn("files", resolved)

    def test_resolve_password_protected_matomete_bundle(self):
        from downloader.resolvers.gigafile import GigaFileResolver

        bundle_html = """
        <script>
        var server = "65.gigafile.nu";
        var dl_ajax_server = "65x.gigafile.nu";
        var files = [{"file": "0219-aaa", "size": 100}, {"file": "0219-bbb", "size": 200}];
        </script>
        <div id="contents_matomete">
            <div class="matomete_file">
                <input type="text" class="matomete_dlkey">
                <button class="download_panel_btn_dl" onclick="download(0, '0219-aaa', true)">DL</button>
            </div>
            <div class="matomete_file">
                <input type="text" class="matomete_dlkey">
                <button class="download_panel_btn_dl" onclick="download(1, '0219-bbb', true)">DL</button>
            </div>
        </div>
        """
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://65.gigafile.nu/0219-bundle",
            text=bundle_html,
        )
        check_wrong = Mock(status_code=200, ok=True)
        check_wrong.json.return_value = {"status": 1}
        check_ok_1 = Mock(status_code=200, ok=True)
        check_ok_1.json.return_value = {"status": 0}
        name_resp_1 = Mock(status_code=200, ok=True)
        name_resp_1.json.return_value = {"status": 0, "filename": "A.zip"}
        check_ok_2 = Mock(status_code=200, ok=True)
        check_ok_2.json.return_value = {"status": 0}
        name_resp_2 = Mock(status_code=200, ok=True)
        name_resp_2.json.return_value = {"status": 0, "filename": "B.zip"}

        fake_session = self._make_fake_session()
        fake_session.get.side_effect = [
            page_resp,
            check_wrong,
            check_ok_1,
            name_resp_1,
            check_ok_2,
            name_resp_2,
        ]

        with patch(
            "downloader.resolvers.gigafile.requests.Session", return_value=fake_session
        ):
            resolved = GigaFileResolver().resolve(
                "https://65.gigafile.nu/0219-bundle",
                options={"candidate_passwords": "wrong, 1234"},
            )

        self.assertIn("dlkey=1234", resolved["url"])
        self.assertEqual(len(resolved["files"]), 2)
        self.assertIn("dlkey=1234", resolved["files"][0]["url"])
        self.assertIn("dlkey=1234", resolved["files"][1]["url"])

    def test_resolve_matomete_zip_fallback(self):
        from downloader.resolvers.gigafile import GigaFileResolver

        zip_only_html = """
        <script>
        var server = "65.gigafile.nu";
        var dl_ajax_server = "65x.gigafile.nu";
        </script>
        <div id="contents_matomete">
            <div id="matomete_zip">
                <span id="matomete_zip_filename">archive_bundle</span>
                <input type="password" id="matomete_zip_dlkey">
                <button onclick="download_zip('0219-bundle', true)">ZIP DL</button>
            </div>
        </div>
        """
        page_resp = Mock(
            status_code=200,
            ok=True,
            url="https://65.gigafile.nu/0219-bundle",
            text=zip_only_html,
        )
        check_ok = Mock(status_code=200, ok=True)
        check_ok.json.return_value = {"status": 0}

        fake_session = self._make_fake_session()
        fake_session.get.side_effect = [page_resp, check_ok]

        with patch(
            "downloader.resolvers.gigafile.requests.Session", return_value=fake_session
        ):
            resolved = GigaFileResolver().resolve(
                "https://65.gigafile.nu/0219-bundle",
                options={"password": "4321"},
            )

        self.assertEqual(
            resolved["url"],
            "https://65.gigafile.nu/dl_zip.php?file=0219-bundle&dlkey=4321",
        )
        self.assertEqual(resolved["filename"], "archive_bundle.zip")
        self.assertEqual(check_ok_call := fake_session.get.call_args_list[1].kwargs["params"], {"file": "0219-bundle", "dlkey": "4321", "is_zip": 1})


if __name__ == "__main__":
    unittest.main()
