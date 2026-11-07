"""Offline regressions for shared transfer policy and concurrent output isolation."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from downloader.addons import addon_registry
from downloader.aria2_http_engine import Aria2HttpDownloadTask
from downloader.engine import DownloadTask
from downloader.manager import DownloadJob, DownloadManager
from downloader.transfer_policy import HttpTransferPolicy


class TransferPolicyTests(unittest.TestCase):
    def policy(self, resolved=None, options=None):
        job = SimpleNamespace(
            resolved_url="https://example.com/file",
            download_dir="downloads",
            filename="file.zip",
            id="one",
            options=options or {},
        )
        return HttpTransferPolicy.from_job(
            job, resolved or {}, {"aria2_http_enabled": True}
        )

    def test_parallel_limits_and_unsafe_methods(self):
        self.assertEqual(self.policy({"max_connections": 2}).split, 2)
        self.assertTrue(self.policy().use_aria2)
        for resolved in (
            {"allow_parallel": False},
            {"method": "POST"},
            {"post_data": {"token": "one-use"}},
        ):
            self.assertFalse(self.policy(resolved).use_aria2)

    def test_explicit_empty_options_override_stale_credentials(self):
        policy = self.policy(
            {"headers": {}, "cookies": "", "referer": ""},
            {"headers": {"Authorization": "old"}, "cookies": "old", "referer": "old"},
        )
        for key, value in policy.request_kwargs.items():
            self.assertEqual(policy.standard_kwargs[key], value)
        self.assertEqual(policy.standard_kwargs["cookies"], "")
        self.assertEqual(policy.standard_kwargs["headers"], {})

    def test_legacy_special_tasks_are_found_without_service_name(self):
        job = SimpleNamespace(
            service_name="unknown",
            options={"mega_file_id": "id", "mega_key_a32": [1, 2, 3, 4]},
        )
        self.assertEqual(addon_registry.find_task_addon(job).addon_id, "mega")


class TransferIOTests(unittest.TestCase):
    def test_workers_share_one_progress_checkpoint_interval(self):
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(DownloadManager, "_start_workers"),
                patch.object(DownloadManager, "_load_history"),
                patch.object(DownloadManager, "_load_jobs"),
            ):
                manager = DownloadManager(lambda: {"download_dir": folder})
            manager._max_workers = 1
            for index in range(2):
                url = f"https://example.com/{index}.bin"
                job = DownloadJob(url, folder, options={"_pre_resolved": {"url": url}})
                manager.jobs[job.id] = job
                manager.queue.put(job.id)
            checkpoint_writes = []

            def create_task(**kwargs):
                def run():
                    before = save.call_count
                    kwargs["progress_callback"](1, 2, 1, 50, 1)
                    checkpoint_writes.append(save.call_count - before)
                    if len(checkpoint_writes) == 2:
                        manager._stop_event.set()
                    return {"success": True, "status": "completed", "filename": "a.bin"}

                return Mock(run=run)

            with (
                patch.object(manager, "_save_jobs") as save,
                patch.object(manager, "_record_completed_history"),
                patch("downloader.manager.DownloadTask", side_effect=create_task),
                patch("downloader.manager.time.monotonic", return_value=100),
            ):
                manager._worker_loop(0)
            self.assertEqual(checkpoint_writes, [1, 0])

    def test_fresh_unnamed_download_needs_only_one_request(self):
        with tempfile.TemporaryDirectory() as folder:
            response = Mock(
                status_code=200,
                headers={
                    "Content-Disposition": 'attachment; filename="real.zip"',
                    "Content-Length": "3",
                },
            )
            response.iter_content.return_value = iter([b"abc"])
            with patch("downloader.engine.requests.Session") as factory:
                factory.return_value.get.return_value = response
                result = DownloadTask("https://example.com/get", folder).run()
                factory.return_value.head.assert_not_called()
                factory.return_value.get.assert_called_once()
            self.assertTrue(result["success"])
            self.assertEqual((Path(folder) / "real.zip").read_bytes(), b"abc")

    def process(self):
        proc = MagicMock()
        proc.stdout = iter([])
        proc.poll.return_value = 0
        proc.wait.return_value = 0
        return proc

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_parallel_jobs_with_same_filename_preserve_existing_file(self, _):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "a.zip").write_bytes(b"existing")
            for identifier in ("one", "two"):
                staging = Path(folder) / f".aria2-{identifier}"
                staging.mkdir()
                (staging / "a.zip").write_bytes(identifier.encode())
                task = Aria2HttpDownloadTask(
                    "https://example.com/a", folder, "a.zip", partial_id=identifier
                )
                with patch(
                    "downloader.aria2_http_engine.subprocess.Popen",
                    return_value=self.process(),
                ):
                    result = task.run()
                self.assertTrue(result["success"])
                task.stop()
                self.assertTrue(Path(result["file_path"]).exists())
            self.assertEqual((Path(folder) / "a.zip").read_bytes(), b"existing")
            self.assertEqual((Path(folder) / "a (1).zip").read_bytes(), b"one")
            self.assertEqual((Path(folder) / "a (2).zip").read_bytes(), b"two")

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_missing_output_never_uses_another_jobs_file(self, _):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / "other.zip"
            other.write_bytes(b"other")
            task = Aria2HttpDownloadTask("https://example.com/a", folder, "a.zip")
            with patch(
                "downloader.aria2_http_engine.subprocess.Popen",
                return_value=self.process(),
            ):
                self.assertEqual(task.run()["status"], "failed")
            self.assertEqual(other.read_bytes(), b"other")

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_header_only_cookies_and_referer_are_forwarded(self, _):
        with tempfile.TemporaryDirectory() as folder:
            task = Aria2HttpDownloadTask(
                "https://example.com/a",
                folder,
                headers={"Cookie": "sid=1", "Referer": "https://example.com"},
            )
            with patch(
                "downloader.aria2_http_engine.subprocess.Popen",
                return_value=self.process(),
            ) as popen:
                task.run()
            command = popen.call_args.args[0]
            self.assertIn("--header=Cookie: sid=1", command)
            self.assertIn("--header=Referer: https://example.com", command)

    def test_cleanup_does_not_remove_other_jobs_or_final_files(self):
        with tempfile.TemporaryDirectory() as folder:
            staging = Path(folder) / ".aria2-one"
            staging.mkdir()
            (staging / "a.zip").write_bytes(b"partial")
            (staging / "a.zip.aria2").write_bytes(b"control")
            final = Path(folder) / "a.zip"
            final.write_bytes(b"complete")
            Aria2HttpDownloadTask.cleanup_partial(folder, "one")
            Aria2HttpDownloadTask.cleanup_partial(folder, "../two")
            self.assertFalse(staging.exists())
            self.assertEqual(final.read_bytes(), b"complete")

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_aria2_command_includes_disable_ipv6(self, _):
        with tempfile.TemporaryDirectory() as folder:
            task = Aria2HttpDownloadTask("https://example.com/a", folder)
            with patch(
                "downloader.aria2_http_engine.subprocess.Popen",
                return_value=self.process(),
            ) as popen:
                task.run()
            command = popen.call_args.args[0]
            self.assertIn("--disable-ipv6=true", command)

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_aria2_matches_output_when_filename_was_empty(self, _):
        with tempfile.TemporaryDirectory() as folder:
            staging = Path(folder) / ".aria2-job123"
            staging.mkdir()
            (staging / "downloaded_file.zip").write_bytes(b"content")
            task = Aria2HttpDownloadTask(
                "https://example.com/a", folder, filename="", partial_id="job123"
            )
            with patch(
                "downloader.aria2_http_engine.subprocess.Popen",
                return_value=self.process(),
            ):
                result = task.run()
            self.assertTrue(result["success"])
            self.assertEqual(result["filename"], "downloaded_file.zip")
            self.assertTrue((Path(folder) / "downloaded_file.zip").exists())

    def test_auto_download_always_checks_duplicates_even_if_setting_false(self):
        with tempfile.TemporaryDirectory() as folder:
            dm = DownloadManager(
                settings_getter=lambda: {
                    "download_dir": folder,
                    "check_duplicate_url": False,
                }
            )
            url = "https://example.com/item.zip"
            # 1. 수동 추가 (check_duplicate_url=False이므로 중복 허용)
            job1 = dm.add_url(url, is_auto=False)
            self.assertIsNotNone(job1)
            # 2. 자동 추가 (is_auto=True이므로 check_duplicate_url=False여도 중복 감지 및 차단)
            job2 = dm.add_url(url, is_auto=True)
            self.assertIsNone(job2)

    def test_server_restart_does_not_requeue_completed_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            dm = DownloadManager(
                settings_getter=lambda: {
                    "download_dir": folder,
                    "check_duplicate_url": True,
                }
            )
            job_url = "https://example.com/completed.zip"
            # 완료 히스토리에 수동 등록
            from downloader.url_helper import canonical_url_key
            dm._completed_history[canonical_url_key(job_url)] = {"url": job_url}

            # jobs_file에 downloading 상태인 더미 job 저장
            jobs_file = Path(folder) / "test_jobs.json"
            dm.jobs_file = jobs_file
            dm._should_persist = True
            job = DownloadJob(url=job_url, download_dir=folder)
            job.status = "downloading"
            dm.jobs[job.id] = job
            dm._save_jobs()

            # 매니저의 메모리 큐/작업 초기화 후 _load_jobs 실행
            dm.jobs.clear()
            while not dm.queue.empty():
                dm.queue.get_nowait()

            dm._load_jobs()
            # 이미 완료된 작업이므로 큐에 다시 들어가지 않고 completed로 보정되어야 함
            self.assertEqual(dm.queue.qsize(), 0)
            self.assertEqual(dm.jobs[job.id].status, "completed")

