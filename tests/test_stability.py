"""Deterministic failure and ownership tests; no external services."""

import json
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from downloader.aria2_http_engine import Aria2HttpDownloadTask
from downloader.engine import DownloadTask
from downloader.manager import DownloadJob, DownloadManager
from downloader.process_utils import reap_process
from downloader.torrent_engine import TorrentDownloadTask, bdecode


def response(status=200, headers=None, chunks=(b"abc",)):
    result = Mock(status_code=status, headers=headers or {})
    result.iter_content.return_value = iter(chunks)
    return result


class HttpStabilityTests(unittest.TestCase):
    def test_stopped_before_start_never_publishes_empty_file(self):
        for action in ("stop", "pause"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                task = DownloadTask("https://example.com/a", folder, "a.bin")
                getattr(task, action)()
                with patch("downloader.engine.requests.Session") as session:
                    result = task.run()
                self.assertEqual(
                    result["status"], "paused" if action == "pause" else "cancelled"
                )
                session.return_value.get.assert_not_called()
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_foreign_partial_files_are_neither_moved_nor_appended(self):
        with tempfile.TemporaryDirectory() as folder:
            foreign = Path(folder) / "a.bin.other.part"
            prefix = Path(folder) / "a.bin.extra.zip.other.part"
            foreign.write_bytes(b"foreign")
            prefix.write_bytes(b"unrelated")
            with patch("downloader.engine.requests.Session") as session:
                session.return_value.get.return_value = response(
                    headers={"Content-Length": "3"}
                )
                result = DownloadTask(
                    "https://example.com/a", folder, "a.bin", partial_id="mine"
                ).run()
            self.assertTrue(result["success"])
            self.assertEqual(foreign.read_bytes(), b"foreign")
            self.assertEqual(prefix.read_bytes(), b"unrelated")
            self.assertEqual(Path(result["file_path"]).read_bytes(), b"abc")
            self.assertNotIn(
                "Range", session.return_value.get.call_args.kwargs["headers"]
            )

    def test_invalid_responses_are_never_published(self):
        cases = [
            (204, {}, ()),
            (200, {"Content-Length": "-1"}, (b"abc",)),
            (200, {"Content-Length": "2"}, (b"abc",)),
            (206, {"Content-Range": "bytes 0-2/*"}, (b"abc",)),
            (206, {"Content-Range": "bytes 0-2/3", "Content-Length": "2"}, (b"abc",)),
            (206, {"Content-Range": "bytes 0-1/3"}, (b"abc",)),
        ]
        for status, headers, chunks in cases:
            with (
                self.subTest(status=status, headers=headers),
                tempfile.TemporaryDirectory() as folder,
            ):
                with patch("downloader.engine.requests.Session") as session:
                    session.return_value.get.return_value = response(
                        status, headers, chunks
                    )
                    result = DownloadTask(
                        "https://example.com/a", folder, "a.bin", max_retries=0
                    ).run()
                self.assertEqual(result["status"], "failed")
                self.assertFalse((Path(folder) / "a.bin").exists())

    def test_range_restart_checks_html_headers_again(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "a.bin.mine.part").write_bytes(b"a")
            with patch("downloader.engine.requests.Session") as session:
                session.return_value.get.side_effect = [
                    response(206, {"Content-Range": "bytes 0-2/3"}),
                    response(200, {"Content-Type": "text/html"}, (b"Access denied",)),
                ]
                result = DownloadTask(
                    "https://example.com/a",
                    folder,
                    "a.bin",
                    partial_id="mine",
                    max_retries=0,
                ).run()
            self.assertEqual(result["status"], "failed")
            self.assertIn("HTML", result["error"])
            self.assertFalse((Path(folder) / "a.bin").exists())

    def test_final_observer_exception_does_not_fail_completed_transfer(self):
        with tempfile.TemporaryDirectory() as folder:
            observer = Mock(side_effect=RuntimeError("UI disconnected"))
            with patch("downloader.engine.requests.Session") as session:
                session.return_value.get.return_value = response(
                    headers={"Content-Length": "3"}
                )
                result = DownloadTask(
                    "https://example.com/a", folder, "a.bin", progress_callback=observer
                ).run()
            self.assertTrue(result["success"])
            self.assertEqual(Path(result["file_path"]).read_bytes(), b"abc")
            observer.assert_called_once()

    def test_restart_does_not_reuse_range_from_previous_session(self):
        def interrupted(*args, **kwargs):
            yield b"a"
            raise ConnectionError("disconnected")

        first = response(200, {"Content-Length": "3"})
        first.iter_content.side_effect = interrupted
        requests_seen = []
        responses = iter([first, response(416), response(200, {"Content-Length": "3"})])

        def send(prepared, **kwargs):
            requests_seen.append(dict(prepared.headers))
            return next(responses)

        with tempfile.TemporaryDirectory() as folder:
            with patch("requests.Session.send", side_effect=send):
                result = DownloadTask(
                    "https://example.com/a",
                    folder,
                    "a.bin",
                    max_retries=1,
                    retry_delay_base=0,
                ).run()
            self.assertTrue(result["success"])
            self.assertEqual(requests_seen[1]["Range"], "bytes=1-")
            self.assertNotIn("Range", requests_seen[2])


class ProcessStabilityTests(unittest.TestCase):
    def process(self):
        process = MagicMock()
        process.stdout = iter([])
        process.poll.return_value = 0
        process.wait.return_value = 0
        return process

    def test_process_is_reaped_after_forced_kill(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("aria2c", 3), 0]
        reap_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_count, 2)

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_unnamed_task_cannot_adopt_existing_destination_file(self, _):
        with tempfile.TemporaryDirectory() as folder:
            unrelated = Path(folder) / "unrelated.zip"
            unrelated.write_bytes(b"keep")
            task = Aria2HttpDownloadTask("https://example.com/a", folder)
            self.assertNotEqual(Path(task._work_dir), Path(folder))
            with patch(
                "downloader.aria2_http_engine.subprocess.Popen",
                return_value=self.process(),
            ):
                self.assertEqual(task.run()["status"], "failed")
            task.stop()
            self.assertEqual(unrelated.read_bytes(), b"keep")

    @patch("downloader.aria2_http_engine.get_aria2_path", return_value="aria2c")
    def test_ambiguous_or_incomplete_aria_outputs_fail(self, _):
        for files in (("one.zip", "two.zip"), ("one.zip", "one.zip.aria2")):
            with self.subTest(files=files), tempfile.TemporaryDirectory() as folder:
                task = Aria2HttpDownloadTask(
                    "https://example.com/a", folder, partial_id="one"
                )
                Path(task._work_dir).mkdir()
                for name in files:
                    (Path(task._work_dir) / name).write_bytes(b"partial")
                with patch(
                    "downloader.aria2_http_engine.subprocess.Popen",
                    return_value=self.process(),
                ):
                    self.assertEqual(task.run()["status"], "failed")
                self.assertFalse((Path(folder) / "one.zip").exists())

    @patch("downloader.torrent_engine.get_aria2_path", return_value="aria2c")
    def test_torrent_does_not_select_an_unrelated_latest_file(self, _):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "other.bin").write_bytes(b"keep")
            process = self.process()
            process.stdout = Mock()
            process.stdout.readline.return_value = ""
            with patch(
                "downloader.torrent_engine.subprocess.Popen", return_value=process
            ):
                result = TorrentDownloadTask(
                    "magnet:?xt=urn:btih:abc", folder, "missing.bin"
                ).run()
            self.assertEqual(result["status"], "failed")


class JobStabilityTests(unittest.TestCase):
    def manager(self, folder):
        with patch.object(DownloadManager, "_start_workers"):
            manager = DownloadManager(lambda: {"download_dir": folder})
        manager._max_workers = 1
        return manager

    def test_shutdown_pauses_instead_of_deleting_partial_state(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(folder)
            job = DownloadJob("https://example.com/a", folder)
            job.status = "downloading"
            job.task = Mock()
            manager.jobs[job.id] = job
            manager.stop()
            job.task.pause.assert_called_once()
            job.task.stop.assert_not_called()

    def test_late_transfer_failure_preserves_explicit_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(folder)
            job = DownloadJob(
                "https://example.com/a",
                folder,
                options={"_pre_resolved": {"url": "https://example.com/a"}},
            )
            manager.jobs[job.id] = job
            manager.queue.put(job.id)

            def run():
                manager.pause_job(job.id)
                manager._stop_event.set()
                return {"status": "failed", "error": "socket closed during pause"}

            with patch("downloader.manager.DownloadTask", return_value=Mock(run=run)):
                manager._worker_loop(0)
            self.assertEqual(job.status, "paused")
            self.assertIsNone(job.task)
            self.assertEqual(manager.queue.unfinished_tasks, 0)

    def test_invalid_stored_job_does_not_prevent_other_jobs_from_loading(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(folder)
            good = DownloadJob("https://example.com/good", folder)
            good.status = "paused"
            bad = good.to_storage_dict() | {"id": "bad", "total_bytes": "corrupted"}
            manager.jobs_file = Path(folder) / "jobs.json"
            manager.jobs_file.write_text(
                json.dumps([bad, good.to_storage_dict()]), encoding="utf-8"
            )
            manager._should_persist = True
            manager._load_jobs()
            self.assertEqual(list(manager.jobs), [good.id])


class TorrentDecodeTests(unittest.TestCase):
    def test_rejects_truncated_trailing_and_unbounded_input(self):
        for data in (
            b"5:abc",
            b"i1ee",
            b"l",
            b"d",
            b"i-0e",
            b"01:a",
            b"di1e1:ae",
            b"l" * 130 + b"e" * 130,
        ):
            with self.subTest(data=data[:30]), self.assertRaises(ValueError):
                bdecode(data)

    def test_accepts_complete_nested_metadata(self):
        self.assertEqual(
            bdecode(b"d4:infod4:name9:test.file6:lengthi1048576eee"),
            {b"info": {b"name": b"test.file", b"length": 1048576}},
        )


class UploadStabilityTests(unittest.TestCase):
    def test_same_name_uploads_use_distinct_metadata_files(self):
        import app as app_module
        import config

        with tempfile.TemporaryDirectory() as folder:
            job = Mock()
            job.to_dict.return_value = {}
            with (
                patch.object(app_module, "TORRENTS_DIR", Path(folder)),
                patch.object(
                    app_module,
                    "load_settings",
                    return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
                ),
                patch.object(
                    app_module.download_manager, "add_url", return_value=job
                ) as add,
                patch.object(app_module.time, "time", return_value=1000),
            ):
                client = app_module.app.test_client()
                for content in (b"first", b"second"):
                    result = client.post(
                        "/api/upload-torrent",
                        data={"file": (io.BytesIO(content), "same.torrent")},
                        content_type="multipart/form-data",
                    )
                    self.assertEqual(result.status_code, 200)
            paths = [Path(call.args[0]) for call in add.call_args_list]
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual(
                [path.read_bytes() for path in paths], [b"first", b"second"]
            )

    def test_rejected_upload_does_not_leave_an_unused_cache_file(self):
        import app as app_module
        import config

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(app_module, "TORRENTS_DIR", Path(folder)),
                patch.object(
                    app_module,
                    "load_settings",
                    return_value=dict(config.DEFAULT_SETTINGS, auth_enabled=False),
                ),
                patch.object(app_module.download_manager, "add_url", return_value=None),
            ):
                result = app_module.app.test_client().post(
                    "/api/upload-torrent",
                    data={"file": (io.BytesIO(b"data"), "file.torrent")},
                    content_type="multipart/form-data",
                )
                self.assertEqual(result.status_code, 400)
            self.assertEqual(list(Path(folder).iterdir()), [])
