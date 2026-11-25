"""Unit tests for Hangulteo resolver."""

import unittest
from unittest.mock import MagicMock, patch

from downloader.resolvers.dispatcher import default_dispatcher
from downloader.resolvers.hangulteo import HangulteoResolver, extract_hangulteo_patch_id


class TestHangulteoResolver(unittest.TestCase):
    def setUp(self):
        self.resolver = HangulteoResolver()

    def test_extract_patch_id_and_can_handle(self):
        urls = [
            ("https://hangulteo.com/lhzero", "lhzero"),
            ("https://hangulteo.com/lhzero/", "lhzero"),
            ("https://hangulteo.com/#/lhzero", "lhzero"),
            ("https://www.hangulteo.com/ffx", "ffx"),
        ]
        for u, expected_id in urls:
            self.assertEqual(extract_hangulteo_patch_id(u), expected_id)
            self.assertTrue(self.resolver.can_handle(u))
            matched = default_dispatcher.get_resolver(u)
            self.assertIsNotNone(matched)
            self.assertEqual(matched.name, "Hangulteo")

        self.assertFalse(self.resolver.can_handle("https://example.com/lhzero"))

    @patch("downloader.resolvers.hangulteo.requests.Session")
    def test_resolve_hangulteo_patch(self, mock_session_cls):
        mock_html = """<!DOCTYPE html>
<html>
<head><title>영웅전설 : 제로의 궤적 한글 패치 v0.2.1 | 한글터</title></head>
<body>
<script>
window.__DATA={"PATCHES":[{
  "id": "lhzero",
  "title": "영웅전설 : 제로의 궤적",
  "platform": "PSP",
  "releases": [{
    "v": "v0.2.1",
    "file": "https://github.com/jaebogg/Korean-Patches/releases/download/lhzero0.2.1/KR.Patch.Zero.no.Kiseki_v0.2.1.zip",
    "size": "73.7 MB",
    "afile": "https://github.com/jaebogg/Korean-Patches/releases/download/lhzero0.2.1/KR.Patch.Zero.no.Kiseki_v0.2.1_Mobile.zip",
    "asize": "73.3 MB"
  }]
}]};
</script>
</body>
</html>"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html
        mock_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        mock_session_cls.return_value = mock_session

        res = self.resolver.resolve("https://hangulteo.com/lhzero")
        self.assertEqual(res["filename"], "KR.Patch.Zero.no.Kiseki_v0.2.1.zip")
        self.assertEqual(res["subfolder"], "[PSP] 영웅전설 _ 제로의 궤적 v0.2.1")
        self.assertEqual(res["source_service"], "Hangulteo")
        self.assertIn("KR.Patch.Zero.no.Kiseki_v0.2.1.zip", res["url"])

        # Check extra_jobs contains mobile version
        self.assertIn("extra_jobs", res)
        self.assertEqual(len(res["extra_jobs"]), 1)
        extra = res["extra_jobs"][0]
        self.assertEqual(extra["filename"], "KR.Patch.Zero.no.Kiseki_v0.2.1_Mobile.zip")
        self.assertEqual(extra["options"]["subfolder"], "[PSP] 영웅전설 _ 제로의 궤적 v0.2.1")


if __name__ == "__main__":
    unittest.main()
