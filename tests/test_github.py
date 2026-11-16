import unittest
from unittest.mock import MagicMock, patch

from downloader.addons import addon_registry
from downloader.resolvers.dispatcher import ResolverDispatcher
from downloader.resolvers.github import GitHubResolver


class GitHubResolverTests(unittest.TestCase):
    """GitHub 릴리즈, 아카이브 및 Raw 파일 리졸버 단위 테스트."""

    def setUp(self):
        self.resolver = GitHubResolver()

    def test_can_handle(self):
        valid_urls = [
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/latest",
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0",
            "https://github.com/snake759494/opoona-wii-korean-patch/releases",
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/expanded_assets/v1.0",
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
            "https://github.com/snake759494/opoona-wii-korean-patch/archive/refs/tags/v1.0.zip",
            "https://github.com/owner/repo/zipball/main",
            "https://github.com/owner/repo/tarball/v1.0",
            "https://github.com/owner/repo/blob/main/patch.xdelta",
            "https://github.com/owner/repo/raw/main/patch.xdelta",
            "https://github.com/owner/repo/tree/main",
            "https://github.com/owner/repo",
            "https://raw.githubusercontent.com/owner/repo/main/patch.xdelta",
            "https://codeload.github.com/owner/repo/zip/refs/tags/v1.0",
            "https://objects.githubusercontent.com/github-production-release-asset-123/456",
            "https://release-assets.githubusercontent.com/github-production-release-asset-123/456",
            "https://gist.githubusercontent.com/user/gistid/raw/file.txt",
            "https://api.github.com/repos/owner/repo/releases/latest",
        ]
        for url in valid_urls:
            self.assertTrue(self.resolver.can_handle(url), f"Should handle: {url}")

        invalid_urls = [
            "",
            "https://example.com/file.zip",
            "https://github.com/settings/profile",
            "https://github.com/explore",
            "https://github.com/topics/python",
            "https://github.com/owner",
            "https://github.com/owner/repo/issues/12",
            "https://github.com/owner/repo/pull/34",
            "https://github.com/owner/repo/wiki",
            "https://github.com/owner/repo/actions",
            "https://github.com/owner/repo/commits/main",
            "https://github.com/owner/repo/blob/main",
            "https://github.com/owner/repo/releases/download",
        ]
        for url in invalid_urls:
            self.assertFalse(self.resolver.can_handle(url), f"Should NOT handle: {url}")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_latest_release_via_api(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "tag_name": "v1.0",
            "name": "v1.0",
            "html_url": "https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0",
            "assets": [
                {
                    "name": "Opoona_Wii_KO_v1.0.xdelta",
                    "size": 365927722,
                    "browser_download_url": (
                        "https://github.com/snake759494/opoona-wii-korean-patch/"
                        "releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta"
                    ),
                }
            ],
        }
        mock_session.get.return_value = api_resp

        result = self.resolver.resolve(
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/latest"
        )
        self.assertEqual(
            result["url"],
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
        )
        self.assertEqual(result["filename"], "Opoona_Wii_KO_v1.0.xdelta")
        self.assertEqual(result["size"], 365927722)
        self.assertEqual(result["source_service"], "GitHub")
        self.assertEqual(result["subfolder"], "opoona-wii-korean-patch v1.0")
        self.assertNotIn("files", result)

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_latest_release_expanded_assets_html_fallback(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_rate_limited = MagicMock()
        api_rate_limited.status_code = 403

        latest_html_resp = MagicMock()
        latest_html_resp.status_code = 200
        latest_html_resp.url = (
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0"
        )
        latest_html_resp.text = """
        <html>
          <body>
            <h1 class="d-inline">v1.0</h1>
            <include-fragment src="https://github.com/snake759494/opoona-wii-korean-patch/releases/expanded_assets/v1.0"></include-fragment>
          </body>
        </html>
        """

        expanded_resp = MagicMock()
        expanded_resp.status_code = 200
        expanded_resp.text = """
        <ul>
          <li>
            <a href="/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta">
              <span>Opoona_Wii_KO_v1.0.xdelta</span>
            </a>
          </li>
          <li>
            <a href="/snake759494/opoona-wii-korean-patch/archive/refs/tags/v1.0.zip">
              <span>Source code</span> (zip)
            </a>
          </li>
        </ul>
        """

        mock_session.get.side_effect = [api_rate_limited, latest_html_resp, expanded_resp]

        result = self.resolver.resolve(
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/latest"
        )
        self.assertEqual(
            result["url"],
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
        )
        self.assertEqual(result["filename"], "Opoona_Wii_KO_v1.0.xdelta")
        self.assertEqual(result["source_service"], "GitHub")
        self.assertEqual(result["subfolder"], "opoona-wii-korean-patch v1.0")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_multi_asset_release(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "tag_name": "v0.1.62",
            "name": "Remote Downloader v0.1.62",
            "html_url": "https://github.com/x220volt/RmtDownloader/releases/tag/v0.1.62",
            "assets": [
                {
                    "name": "RemoteDownloader.exe",
                    "size": 78022212,
                    "browser_download_url": "https://github.com/x220volt/RmtDownloader/releases/download/v0.1.62/RemoteDownloader.exe",
                },
                {
                    "name": "RemoteDownloader-Extension.zip",
                    "size": 47226,
                    "browser_download_url": "https://github.com/x220volt/RmtDownloader/releases/download/v0.1.62/RemoteDownloader-Extension.zip",
                },
            ],
        }
        mock_session.get.return_value = api_resp

        result = self.resolver.resolve(
            "https://github.com/x220volt/RmtDownloader/releases/tag/v0.1.62"
        )
        self.assertEqual(result["filename"], "RemoteDownloader.exe")
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(result["files"][0]["name"], "RemoteDownloader.exe")
        self.assertEqual(result["files"][1]["name"], "RemoteDownloader-Extension.zip")
        self.assertEqual(result["subfolder"], "RmtDownloader Remote Downloader v0.1.62")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_release_without_assets_falls_back_to_source_zip(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "tag_name": "v2.0",
            "name": "v2.0",
            "assets": [],
        }
        mock_session.get.return_value = api_resp

        result = self.resolver.resolve(
            "https://github.com/owner/cool-tool/releases/tag/v2.0"
        )
        self.assertEqual(
            result["url"],
            "https://github.com/owner/cool-tool/archive/refs/tags/v2.0.zip",
        )
        self.assertEqual(result["filename"], "cool-tool-v2.0.zip")
        self.assertEqual(result["source_service"], "GitHub")

    def test_resolve_direct_asset_blob_and_archive(self):
        direct_res = self.resolver.resolve(
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta"
        )
        self.assertEqual(
            direct_res["url"],
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
        )
        self.assertEqual(direct_res["filename"], "Opoona_Wii_KO_v1.0.xdelta")
        self.assertEqual(direct_res["source_service"], "GitHub")

        blob_res = self.resolver.resolve(
            "https://github.com/owner/my-repo/blob/main/patches/game.bps"
        )
        self.assertEqual(
            blob_res["url"],
            "https://raw.githubusercontent.com/owner/my-repo/main/patches/game.bps",
        )
        self.assertEqual(blob_res["filename"], "game.bps")

        arch_res = self.resolver.resolve(
            "https://github.com/snake759494/opoona-wii-korean-patch/archive/refs/tags/v1.0.zip"
        )
        self.assertEqual(arch_res["filename"], "opoona-wii-korean-patch-v1.0.zip")

        obj_res = self.resolver.resolve(
            "https://objects.githubusercontent.com/github-production-release-asset/1?response-content-disposition=attachment%3B%20filename%3DOpoona_Wii_KO_v1.0.xdelta"
        )
        self.assertEqual(obj_res["filename"], "Opoona_Wii_KO_v1.0.xdelta")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_repo_root_falls_back_to_head_zip_when_no_releases(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        resp_latest_404 = MagicMock()
        resp_latest_404.status_code = 404
        resp_list_empty = MagicMock()
        resp_list_empty.status_code = 200
        resp_list_empty.json.return_value = []
        mock_session.get.side_effect = [resp_latest_404, resp_list_empty]

        result = self.resolver.resolve("https://github.com/owner/sample-repo")
        self.assertEqual(result["url"], "https://github.com/owner/sample-repo/archive/HEAD.zip")
        self.assertEqual(result["filename"], "sample-repo-HEAD.zip")
        self.assertEqual(result["source_service"], "GitHub")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_not_found_raises_file_not_found(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        resp_404 = MagicMock()
        resp_404.status_code = 404
        mock_session.get.return_value = resp_404

        with self.assertRaises(FileNotFoundError):
            self.resolver.resolve("https://github.com/owner/repo/releases/tag/v99.9")

    @patch("downloader.resolvers.github.requests.Session")
    def test_resolve_repo_root_and_readme_resolve_latest_release(self, mock_session_cls):
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "tag_name": "v1.0",
            "name": "v1.0",
            "html_url": "https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0",
            "assets": [
                {
                    "name": "Opoona_Wii_KO_v1.0.xdelta",
                    "size": 365927722,
                    "browser_download_url": (
                        "https://github.com/snake759494/opoona-wii-korean-patch/"
                        "releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta"
                    ),
                }
            ],
        }
        mock_session.get.return_value = api_resp

        for target_url in (
            "https://github.com/snake759494/opoona-wii-korean-patch",
            "https://github.com/snake759494/opoona-wii-korean-patch/",
            "https://github.com/snake759494/opoona-wii-korean-patch/blob/main/README.md",
        ):
            result = self.resolver.resolve(target_url)
            self.assertEqual(
                result["url"],
                "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
            )
            self.assertEqual(result["filename"], "Opoona_Wii_KO_v1.0.xdelta")
            self.assertEqual(result["source_service"], "GitHub")
            self.assertEqual(result["subfolder"], "opoona-wii-korean-patch v1.0")

    def test_canonical_url_key_unifies_repo_root_and_latest_release(self):
        from downloader.url_helper import canonical_url_key

        expected_key = "github:snake759494/opoona-wii-korean-patch:latest"
        self.assertEqual(
            canonical_url_key("https://github.com/snake759494/opoona-wii-korean-patch"),
            expected_key,
        )
        self.assertEqual(
            canonical_url_key("https://github.com/snake759494/opoona-wii-korean-patch/"),
            expected_key,
        )
        self.assertEqual(
            canonical_url_key("https://github.com/snake759494/opoona-wii-korean-patch/releases"),
            expected_key,
        )
        self.assertEqual(
            canonical_url_key("https://github.com/snake759494/opoona-wii-korean-patch/releases/latest"),
            expected_key,
        )
        self.assertNotEqual(
            canonical_url_key("https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0"),
            expected_key,
        )
        self.assertEqual(
            canonical_url_key("https://github.com/settings/profile"),
            "github.com/settings/profile",
        )

    @patch("downloader.resolvers.github.requests.Session")
    def test_webpage_resolver_extracts_and_dedups_github_repo_links(self, mock_session_cls):
        from downloader.resolvers.webpage import WebPageResolver

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__.return_value = mock_session

        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.json.return_value = {
            "tag_name": "v1.0",
            "name": "v1.0",
            "html_url": "https://github.com/snake759494/opoona-wii-korean-patch/releases/tag/v1.0",
            "assets": [
                {
                    "name": "Opoona_Wii_KO_v1.0.xdelta",
                    "size": 365927722,
                    "browser_download_url": (
                        "https://github.com/snake759494/opoona-wii-korean-patch/"
                        "releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta"
                    ),
                }
            ],
        }
        mock_session.get.return_value = api_resp

        post_html = """
        <html>
          <head><title>오푸나 Wii 한글 패치 배포</title></head>
          <body>
            <article>
              <h1>오푸나 Wii 한글 패치</h1>
              <p>패치 저장소: <a href="https://github.com/snake759494/opoona-wii-korean-patch">깃허브 링크</a></p>
              <p>릴리즈 페이지: <a href="https://github.com/snake759494/opoona-wii-korean-patch/releases/latest">최신 릴리즈</a></p>
            </article>
          </body>
        </html>
        """
        wp = WebPageResolver()
        title, candidates = wp.extract_candidates_from_html(
            post_html, "https://kone.gg/s/retrogame/post12345"
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0]["url"],
            "https://github.com/snake759494/opoona-wii-korean-patch",
        )

        resolved = wp.resolve_from_html(
            post_html, "https://kone.gg/s/retrogame/post12345"
        )
        self.assertEqual(resolved["filename"], "Opoona_Wii_KO_v1.0.xdelta")
        self.assertEqual(
            resolved["url"],
            "https://github.com/snake759494/opoona-wii-korean-patch/releases/download/v1.0/Opoona_Wii_KO_v1.0.xdelta",
        )
        self.assertNotIn("extra_jobs", resolved)

    def test_addon_and_dispatcher_registration(self):
        addon = addon_registry.get_addon("github")
        self.assertIsNotNone(addon)
        assert addon is not None
        self.assertEqual(addon.name, "GitHub")
        self.assertIn("github.com", addon.domains)

        url = "https://github.com/snake759494/opoona-wii-korean-patch/releases/latest"
        found_addon = addon_registry.find_for_url(url)
        self.assertIsNotNone(found_addon)
        assert found_addon is not None
        self.assertEqual(found_addon.addon_id, "github")

        dispatcher = ResolverDispatcher()
        resolved_handler = dispatcher.get_resolver(url)
        self.assertIsNotNone(resolved_handler)
        assert resolved_handler is not None
        self.assertEqual(resolved_handler.name, "GitHub")


if __name__ == "__main__":
    unittest.main()
