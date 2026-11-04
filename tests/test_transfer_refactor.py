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
