import unittest
from unittest.mock import MagicMock, patch


class HostingAddonArchitectureTests(unittest.TestCase):
    """호스팅/클라우드 애드온 모듈화 아키텍처 단위 테스트."""

    def test_addon_registry_defaults(self):
        from downloader.addons import addon_registry

        addons = addon_registry.get_addons()
        self.assertGreaterEqual(len(addons), 20)

        addon_ids = [a.addon_id for a in addons]
        expected_ids = [
            "torrent",
            "gofile",
            "vikingfile",
            "megaup",
            "terabox",
            "gigafile",
            "transferit",
            "mega",
            "onefichier",
            "kio",
            "pixeldrain",
            "gdrive",
            "xshare",
            "romsfun",
            "rootz",
            "naver_mybox",
            "naver_cafe",
            "tistory",
            "webpage",
            "direct",
        ]
        for eid in expected_ids:
            self.assertIn(eid, addon_ids, f"Addon {eid} should be registered")

    def test_addon_registry_find_for_url(self):
        from downloader.addons import addon_registry

        test_cases = [
            ("magnet:?xt=urn:btih:1234567890abcdef", "torrent"),
            ("https://gofile.io/d/abc123", "gofile"),
            (
                "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM",
                "mega",
            ),
            ("https://mybox.naver.com/share/list?shareKey=testKey", "naver_mybox"),
            ("https://www.rootz.so/d/1wBzrE", "rootz"),
            ("https://romsfun.com/download/game-123", "romsfun"),
            ("https://transfer.it/t/xyz", "transferit"),
            ("https://cafe.naver.com/hansicgu/36206", "naver_cafe"),
            ("https://unknown-domain.example.com/file.iso", "direct"),
        ]
        for url, expected_id in test_cases:
            addon = addon_registry.find_for_url(url)
            self.assertIsNotNone(addon, f"Should find addon for {url}")
            assert addon is not None
            self.assertEqual(addon.addon_id, expected_id)

    def test_addon_registry_find_for_service(self):
        from downloader.addons import addon_registry

        addon = addon_registry.find_for_service("MEGA")
        self.assertIsNotNone(addon)
        assert addon is not None
        self.assertEqual(addon.addon_id, "mega")

        addon2 = addon_registry.find_for_service("Naver MYBOX")
        self.assertIsNotNone(addon2)
        assert addon2 is not None
        self.assertEqual(addon2.addon_id, "naver_mybox")

    def test_dispatcher_integration(self):
        from downloader.resolvers.dispatcher import ResolverDispatcher

        dispatcher = ResolverDispatcher()
        res = dispatcher.get_resolver(
            "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
        )
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.name, "MEGA")

        res_box = dispatcher.get_resolver(
            "https://mybox.naver.com/share/list?shareKey=abc"
        )
        self.assertIsNotNone(res_box)
        assert res_box is not None
        self.assertEqual(res_box.name, "Naver MYBOX")

    def test_custom_task_restoration(self):
        from downloader.addons import addon_registry

        mega_addon = addon_registry.get_addon("mega")
        self.assertIsNotNone(mega_addon)
        assert mega_addon is not None

        mock_job = MagicMock()
        mock_job.options = {"mega_file_id": "test_id", "mega_key_a32": [1, 2, 3, 4]}
        mock_job.resolved_url = "https://gfs.mega.nz/test"
        mock_job.original_url = (
            "https://mega.nz/file/KV0U3RbY#LzC0KbY97I3lzPVmH7cnPbm9UWDBX8c3qzLOHx2m_XM"
        )
        mock_job.download_dir = "/tmp/downloads"
        mock_job.filename = "test.zip"
        mock_job.id = "job_123"

        self.assertTrue(mega_addon.can_restore_task(mock_job))
        task = mega_addon.restore_task(mock_job)
        from downloader.resolvers.mega import MegaDownloadTask

        self.assertIsInstance(task, MegaDownloadTask)
        self.assertEqual(task.file_id, "test_id")

    def test_ui_manifest(self):
        from downloader.addons import addon_registry

        manifest = addon_registry.get_ui_manifest()
        self.assertIsInstance(manifest, dict)
        self.assertIn("addons", manifest)
        self.assertIn("categories", manifest)
        self.assertGreaterEqual(len(manifest["addons"]), 20)
        first = manifest["addons"][0]
        self.assertIn("addon_id", first)
        self.assertIn("name", first)
        self.assertIn("badge_class", first)

    def test_app_api_addons_endpoint(self):
        from app import app

        with app.test_client() as client:
            with patch("auth.session_authenticated", return_value=True):
                resp = client.get("/api/addons")
                self.assertEqual(resp.status_code, 200)
                data = resp.get_json()
                self.assertTrue(data.get("success"))
                self.assertIn("addons", data)
                self.assertGreaterEqual(len(data["addons"].get("addons", [])), 20)


if __name__ == "__main__":
    unittest.main()
