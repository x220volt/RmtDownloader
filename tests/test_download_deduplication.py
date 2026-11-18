"""한건의 다운로드 내 중복 링크/파일 방지 단위 테스트."""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from downloader.manager import DownloadJob, DownloadManager
from downloader.resolvers.webpage import WebPageResolver
from downloader.url_helper import is_github_release_asset_of_tag


class TestDownloadDeduplication(unittest.TestCase):
    def test_is_github_release_asset_of_tag(self):
        tag_url = "https://github.com/Simon-nhelix/nethelix/releases/tag/sonicrush-v1.0"
        asset_url = "https://github.com/Simon-nhelix/nethelix/releases/download/sonicrush-v1.0/sonicrush-ko-v1.0.zip"
        other_asset = "https://github.com/Simon-nhelix/nethelix/releases/download/v2.0/sonicrush-ko-v1.0.zip"
        other_repo = "https://github.com/other/nethelix/releases/download/sonicrush-v1.0/file.zip"

        self.assertTrue(is_github_release_asset_of_tag(asset_url, tag_url))
        self.assertFalse(is_github_release_asset_of_tag(other_asset, tag_url))
        self.assertFalse(is_github_release_asset_of_tag(other_repo, tag_url))
        self.assertFalse(is_github_release_asset_of_tag("https://example.com/a.zip", tag_url))

    def test_webpage_resolver_consolidates_github_release_and_asset(self):
        html = """
        <html>
        <body>
            <h1>소닉 러시 한글 패치</h1>
            <p>
                <a href="https://github.com/Simon-nhelix/nethelix/releases/download/sonicrush-v1.0/sonicrush-ko-v1.0.zip">직접 다운로드</a>
            </p>
            <p>
                <a href="https://github.com/Simon-nhelix/nethelix/releases/tag/sonicrush-v1.0">릴리스 페이지</a>
            </p>
        </body>
        </html>
        """
        resolver = WebPageResolver()
        title, candidates = resolver.extract_candidates_from_html(html, "https://cafe.naver.com/aihangeul/79")

        # 릴리스 태그 페이지가 모든 에셋을 포함하므로 중복 에셋 직링크는 제외되어 1건만 남아야 함
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["url"], "https://github.com/Simon-nhelix/nethelix/releases/tag/sonicrush-v1.0")

    def test_manager_group_deduplication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = {
                "download_dir": tmpdir,
                "auto_subfolder": False,
                "subfolder_rule": "none",
                "check_duplicate_url": False,
            }
            manager = DownloadManager(lambda: settings)

            # 1. Root Job 생성
            job1 = manager.add_url("https://example.com/post/1")
            self.assertIsNotNone(job1)
            group_id = job1.options["group_id"]
            self.assertEqual(group_id, job1.id)

            job1.status = "downloading"
            job1.resolved_url = "https://example.com/file1.zip"
            job1.filename = "file1.zip"
            job1.options.setdefault("_registered_urls", []).append("https://example.com/file1.zip")

            # 2. _enqueue_extra_items 호출 시 이미 존재하는 file1.zip은 제외되어야 함
            resolved = {
                "url": "https://example.com/file1.zip",
                "filename": "file1.zip",
                "files": [
                    {"name": "file1.zip", "url": "https://example.com/file1.zip"},
                    {"name": "file2.zip", "url": "https://example.com/file2.zip"},
                    {"name": "file1.zip", "url": "https://example.com/file1_dup.zip"},  # 파일명 중복
                ],
                "extra_jobs": [
                    {"url": "https://example.com/file1.zip"},  # 이미 등록된 URL
                    {"url": "https://example.com/file3.zip"},  # 신규
                ],
            }

            manager._enqueue_extra_items(job1.id, job1, resolved)

            # 3. 추가 등록된 작업 확인 (file2.zip과 file3.zip만 등록되고 file1 관련 중복은 등록되지 않아야 함)
            jobs_in_group = [j for j in manager.jobs.values() if j.options.get("group_id") == group_id]
            # job1 + file2.zip + file3.zip = 3개
            self.assertEqual(len(jobs_in_group), 3)

            registered_urls = set()
            for j in jobs_in_group:
                registered_urls.add(j.original_url)
                if j.resolved_url:
                    registered_urls.add(j.resolved_url)

            self.assertIn("https://example.com/file2.zip", registered_urls)
            self.assertIn("https://example.com/file3.zip", registered_urls)
            self.assertNotIn("https://example.com/file1_dup.zip", registered_urls)

    def test_worker_skips_job_if_resolved_url_already_in_group(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = {
                "download_dir": tmpdir,
                "auto_subfolder": False,
                "check_duplicate_url": False,
            }
            manager = DownloadManager(lambda: settings)

            # Job1 이미 file.zip 다운로드 완료 상태
            job1 = DownloadJob("https://example.com/page1", tmpdir)
            job1.status = "completed"
            job1.resolved_url = "https://cdn.example.com/game.zip"
            job1.filename = "game.zip"
            job1.options["group_id"] = "group_123"
            manager.jobs[job1.id] = job1

            # Job2 같은 그룹 내에서 다른 URL로 시작했으나 해석 결과 동일한 game.zip
            job2 = DownloadJob("https://example.com/page2", tmpdir)
            job2.options["group_id"] = "group_123"
            manager.jobs[job2.id] = job2

            is_dup = manager._is_duplicate_in_group(
                "group_123",
                url="https://cdn.example.com/game.zip",
                filename="game.zip",
                dest_dir=tmpdir,
                exclude_job_id=job2.id,
            )
            self.assertTrue(is_dup)


if __name__ == "__main__":
    unittest.main()
