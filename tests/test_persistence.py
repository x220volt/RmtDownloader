import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader.manager import DownloadJob, DownloadManager


class TestJobPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.jobs_file = Path(self.temp_dir) / "test_jobs.json"
        self.history_file = Path(self.temp_dir) / "test_history.json"
        self.download_dir = os.path.join(self.temp_dir, "downloads")
        os.makedirs(self.download_dir, exist_ok=True)

        self.mock_settings = {
            "download_dir": self.download_dir,
            "max_concurrent_downloads": 1,
            "check_duplicate_url": True,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_job_serialization_roundtrip(self):
        """DownloadJob의 to_storage_dict 및 from_storage_dict가 모든 필드를 온전하게 보존하는지 검증."""
        job = DownloadJob(
            url="https://example.com/archive.zip",
            download_dir=self.download_dir,
            options={"subfolder": "test_folder", "is_auto": True, "password": "secret"},
        )
        job.status = "downloading"
        job.filename = "archive.zip"
        job.file_path = os.path.join(self.download_dir, "archive.zip")
        job.downloaded_bytes = 1048576
        job.total_bytes = 2097152
        job.progress = 50.0
        job.error_message = "Temporary disconnect"
        job.source_title = "게시글 제목"
        job.source_url = "https://kone.gg/s/retrogame/123"
        job.referer = "https://kone.gg/s/retrogame/123"
        job.retry_count = 1

        data = job.to_storage_dict()
        self.assertEqual(data["id"], job.id)
        self.assertEqual(data["original_url"], job.original_url)
        self.assertEqual(data["status"], "downloading")
        self.assertEqual(data["filename"], "archive.zip")
        self.assertEqual(data["downloaded_bytes"], 1048576)
        self.assertEqual(data["total_bytes"], 2097152)
        self.assertEqual(data["options"]["password"], "secret")

        restored = DownloadJob.from_storage_dict(data)
        self.assertEqual(restored.id, job.id)
        self.assertEqual(restored.original_url, job.original_url)
        self.assertEqual(restored.filename, job.filename)
        self.assertEqual(restored.file_path, job.file_path)
        self.assertEqual(restored.downloaded_bytes, 1048576)
        self.assertEqual(restored.total_bytes, 2097152)
        self.assertEqual(restored.progress, 50.0)
        self.assertEqual(restored.error_message, "Temporary disconnect")
        self.assertEqual(restored.source_title, "게시글 제목")
        self.assertEqual(restored.source_url, "https://kone.gg/s/retrogame/123")
        self.assertEqual(restored.retry_count, 1)

    def test_persistence_across_server_restart(self):
        """서버가 재시작되더라도 완료/실패/진행 중이던 작업들이 온전하게 기억되고 미완료 작업은 이어받기로 자동 재개되는지 검증."""
        with patch("downloader.manager.HISTORY_FILE", self.history_file), patch(
            "downloader.manager.JOBS_FILE", self.jobs_file
        ):
            # 1. 1차 매니저 기동 및 작업들 등록
            dm1 = DownloadManager(
                settings_getter=lambda: self.mock_settings,
                jobs_file=self.jobs_file,
            )

            # 완료된 작업 시뮬레이션
            j_completed = dm1.add_url("https://example.com/done.zip", filename="done.zip")
            assert j_completed is not None
            j_completed.status = "completed"
            j_completed.file_path = os.path.join(self.download_dir, "done.zip")
            j_completed.downloaded_bytes = 1000
            j_completed.total_bytes = 1000
            j_completed.progress = 100.0
            dm1._record_completed_history(j_completed)

            # 실패한 작업 시뮬레이션
            j_failed = dm1.add_url("https://example.com/failed.zip", filename="failed.zip")
            assert j_failed is not None
            j_failed.status = "failed"
            j_failed.error_message = "서버 404 응답"

            # 다운로드 중이던 작업 시뮬레이션 (.part 파일 500바이트 생성)
            j_downloading = dm1.add_url(
                "https://example.com/partial.zip", filename="partial.zip"
            )
            assert j_downloading is not None
            j_downloading.status = "downloading"
            j_downloading.total_bytes = 1000

            # .part 파일 생성
            part_path = os.path.join(
                self.download_dir, f"partial.zip.{j_downloading.id}.part"
            )
            with open(part_path, "wb") as pf:
                pf.write(b"x" * 500)
            j_downloading.downloaded_bytes = 500
            j_downloading.progress = 50.0

            # 상태 저장 및 매니저 종료 (서버 셧다운)
            dm1._save_jobs()
            dm1.stop()
            self.assertTrue(self.jobs_file.exists())

            # 2. 2차 매니저 기동 (서버 재시작 시뮬레이션 - 워커를 시작하기 전 상태 확인을 위해 _start_workers 목킹 가능)
            with patch.object(DownloadManager, "_start_workers"):
                dm2 = DownloadManager(
                    settings_getter=lambda: self.mock_settings,
                    jobs_file=self.jobs_file,
                )

                all_jobs = dm2.get_all_jobs()
                self.assertEqual(len(all_jobs), 3)

                # ID로 각 작업 조회
                job_map = {j["id"]: j for j in all_jobs}

                # 완료 작업 검증
                c_job = job_map[j_completed.id]
                self.assertEqual(c_job["status"], "completed")
                self.assertEqual(c_job["filename"], "done.zip")
                self.assertEqual(c_job["progress"], 100.0)

                # 실패 작업 검증
                f_job = job_map[j_failed.id]
                self.assertEqual(f_job["status"], "failed")
                self.assertEqual(f_job["error_message"], "서버 404 응답")

                # 다운로드 중이던 작업 검증: 이어받기 대기열(queued)로 자동 전환되고 수신 바이트 및 진행률 보정
                d_job = job_map[j_downloading.id]
                self.assertEqual(d_job["status"], "queued")
                self.assertEqual(d_job["downloaded_bytes"], 500)
                self.assertEqual(d_job["total_bytes"], 1000)
                self.assertEqual(d_job["progress"], 50.0)

                # 큐에 자동으로 재진입되었는지 검증
                self.assertEqual(dm2.queue.qsize(), 1)
                queued_id = dm2.queue.get_nowait()
                self.assertEqual(queued_id, j_downloading.id)

                dm2.stop()

    def test_removal_and_clear_persisted(self):
        """삭제 및 일괄 비우기 시 영구 저장 파일(jobs.json)에도 즉시 반영되는지 검증."""
        with patch("downloader.manager.HISTORY_FILE", self.history_file), patch(
            "downloader.manager.JOBS_FILE", self.jobs_file
        ):
            dm = DownloadManager(
                settings_getter=lambda: self.mock_settings,
                jobs_file=self.jobs_file,
            )
            j1 = dm.add_url("https://example.com/file1.zip")
            j2 = dm.add_url("https://example.com/file2.zip")
            assert j1 and j2

            self.assertEqual(len(dm.get_all_jobs()), 2)

            # j1 개별 삭제
            dm.remove_job(j1.id)
            self.assertEqual(len(dm.get_all_jobs()), 1)

            # 서버 재시작 시뮬레이션
            dm.stop()
            dm_restarted = DownloadManager(
                settings_getter=lambda: self.mock_settings,
                jobs_file=self.jobs_file,
            )
            self.assertEqual(len(dm_restarted.get_all_jobs()), 1)
            self.assertEqual(dm_restarted.get_all_jobs()[0]["id"], j2.id)

            # 전체 삭제 테스트
            dm_restarted.clear_jobs(status_filter="all")
            self.assertEqual(len(dm_restarted.get_all_jobs()), 0)

            dm_restarted.stop()
            dm_restarted2 = DownloadManager(
                settings_getter=lambda: self.mock_settings,
                jobs_file=self.jobs_file,
            )
            self.assertEqual(len(dm_restarted2.get_all_jobs()), 0)
            dm_restarted2.stop()


if __name__ == "__main__":
    unittest.main()
