import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from downloader.aria2_http_engine import Aria2HttpDownloadTask
from downloader.manager import DownloadJob, DownloadManager


class Aria2HttpEngineTests(unittest.TestCase):
    """Aria2HttpDownloadTask 단위 테스트."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_and_split_clamping(self):
        # 1. Normal split
        task = Aria2HttpDownloadTask(
            url="https://example.com/file.zip",
            dest_dir=self.temp_dir,
            filename="file.zip",
            split=8,
        )
        self.assertEqual(task.split, 8)
        self.assertEqual(task.filename, "file.zip")

        # 2. Clamping under 1
        task_low = Aria2HttpDownloadTask(
            url="https://example.com/file.zip",
            dest_dir=self.temp_dir,
            split=0,
        )
        self.assertEqual(task_low.split, 1)

        # 3. Clamping over 16
        task_high = Aria2HttpDownloadTask(
            url="https://example.com/file.zip",
            dest_dir=self.temp_dir,
            split=32,
        )
        self.assertEqual(task_high.split, 16)

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    @patch("downloader.aria2_http_engine.subprocess.Popen")
    def test_run_success(self, mock_popen, mock_get_path):
        progress_events = []

        def on_progress(**kwargs):
            progress_events.append(kwargs)

        output_lines = [
            "FILE: /downloads/test.zip\n",
            "[#123456 50MiB/100MiB(50%) CN:8 DL:10.5MiB ETA:5s]\n",
            "[#123456 100MiB/100MiB(100%) CN:8 DL:12.0MiB ETA:0s]\n",
        ]
        mock_proc = MagicMock()
        mock_proc.stdout = iter(output_lines)
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        task = Aria2HttpDownloadTask(
            url="https://example.com/test.zip",
            dest_dir=self.temp_dir,
            filename="test.zip",
            headers={"User-Agent": "CustomUA/1.0", "Authorization": "Bearer 123"},
            cookies="sid=abc; uid=456",
            referer="https://example.com/page",
            split=8,
            progress_callback=on_progress,
        )

        os.makedirs(task._work_dir, exist_ok=True)
        with open(os.path.join(task._work_dir, "test.zip"), "wb") as output:
            output.write(b"0" * 1024)

        res = task.run()

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]
        self.assertIn("--split=8", cmd)
        self.assertIn("--max-connection-per-server=8", cmd)
        self.assertIn("--out=test.zip", cmd)
        self.assertIn("--referer=https://example.com/page", cmd)
        self.assertIn("--header=Cookie: sid=abc; uid=456", cmd)
        self.assertIn("--header=User-Agent: CustomUA/1.0", cmd)
        self.assertIn("--header=Authorization: Bearer 123", cmd)
        self.assertEqual(cmd[-1], "https://example.com/test.zip")

        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["filename"], "test.zip")
        self.assertTrue(len(progress_events) > 0)

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    @patch("downloader.aria2_http_engine.subprocess.Popen")
    def test_run_failure(self, mock_popen, mock_get_path):
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["Error occurred\n"])
        mock_proc.poll.return_value = 1
        mock_proc.wait.return_value = 1
        mock_popen.return_value = mock_proc

        task = Aria2HttpDownloadTask(
            url="https://example.com/test.zip",
            dest_dir=self.temp_dir,
            filename="test.zip",
        )

        res = task.run()
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "failed")
        self.assertIn("aria2c 프로세스 종료 오류", res["error"])

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    @patch("downloader.aria2_http_engine.subprocess.Popen")
    def test_pause_and_stop(self, mock_popen, mock_get_path):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        task = Aria2HttpDownloadTask(
            url="https://example.com/test.zip",
            dest_dir=self.temp_dir,
            filename="test.zip",
        )
        task._process = mock_proc

        task.pause()
        self.assertTrue(task._is_paused)
        self.assertTrue(task._is_stopped)
        mock_proc.terminate.assert_called()

        task.stop()
        self.assertFalse(task._is_paused)
        self.assertTrue(task._is_stopped)

    @patch("downloader.aria2_http_engine.Aria2HttpDownloadTask")
    @patch("downloader.manager.DownloadTask")
    def test_manager_fallback_on_aria2_failure(
        self, mock_download_task_cls, mock_aria2_task_cls
    ):
        settings_data = {
            "aria2_http_enabled": True,
            "aria2_http_split": 8,
            "chunk_size_kb": 256,
            "download_dir": self.temp_dir,
        }

        # 1. aria2 task fails
        mock_aria2_instance = MagicMock()
        mock_aria2_instance._is_stopped = False
        mock_aria2_instance.run.return_value = {
            "success": False,
            "status": "failed",
            "error": "HTTP 403 Forbidden on split connection",
        }
        mock_aria2_task_cls.return_value = mock_aria2_instance

        # 2. fallback DownloadTask succeeds
        mock_fallback_instance = MagicMock()
        def on_fallback_run():
            manager._stop_event.set()
            return {
                "success": True,
                "status": "completed",
                "filename": "fallback.zip",
                "file_path": "/path/fallback.zip",
                "total_bytes": 1024,
            }
        mock_fallback_instance.run.side_effect = on_fallback_run
        mock_download_task_cls.return_value = mock_fallback_instance

        with patch.object(DownloadManager, "_start_workers"):
            manager = DownloadManager(settings_getter=lambda: settings_data, jobs_file=None)
            manager._max_workers = 1

        job = DownloadJob(
            url="https://example.com/fallback.zip",
            download_dir=self.temp_dir,
            options={
                "_pre_resolved": {
                    "url": "https://example.com/fallback.zip",
                    "filename": "fallback.zip",
                    "source_service": "Direct",
                }
            },
        )
        job.filename = "fallback.zip"
        job.status = "queued"
        manager.jobs[job.id] = job
        manager.queue.put(job.id)

        with patch.object(manager, "_save_jobs"):
            manager._worker_loop(0)

        # Verify that both were called (aria2 attempted first, then fallback to DownloadTask)
        mock_aria2_instance.run.assert_called_once()
        mock_fallback_instance.run.assert_called_once()
        self.assertEqual(job.status, "completed")


if __name__ == "__main__":
    unittest.main()
