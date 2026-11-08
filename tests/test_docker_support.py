"""도커 및 리눅스 환경 동작 검증 단위 테스트 모듈."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import app, shutdown_server, _sigterm_handler, ensure_single_instance
import config
from downloader.aria2_manager import get_aria2_path
from downloader.browser_solver import stealth_browser_session


class TestDockerSupport(unittest.TestCase):
    """Docker / Linux 환경 설정, 경로 보정, 헬스체크 및 프로세스 수명주기 검증."""

    def setUp(self):
        self.client = app.test_client()

    def test_healthcheck_endpoint(self):
        """비인증 헬스체크 /api/health 엔드포인트 동작 검증."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("version", data)
        self.assertIn("version_tag", data)

    def test_windows_path_auto_correction_on_linux(self):
        """Linux 환경에서 Windows 절대 경로(C:\\...)가 지정되어 있을 때 DOWNLOAD_DIR로 자동 보정되는지 검증."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_config = Path(tmp_dir) / "settings.json"
            tmp_config.write_text(
                '{"download_dir": "C:\\\\Users\\\\Admin\\\\Downloads\\\\Remote"}',
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", tmp_config):
                with patch("os.name", "posix"):
                    with patch.dict(os.environ, {"DOWNLOAD_DIR": "/downloads"}):
                        settings = config.load_settings()
                        self.assertEqual(settings["download_dir"], "/downloads")

    def test_linux_native_path_preserved_on_linux(self):
        """Linux 환경에서 리눅스 네이티브 경로(/custom/path)가 설정되어 있을 때는 변경 없이 유지되는지 검증."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_config = Path(tmp_dir) / "settings.json"
            tmp_config.write_text(
                '{"download_dir": "/custom/path/downloads"}',
                encoding="utf-8",
            )
            with patch.object(config, "CONFIG_FILE", tmp_config):
                with patch("os.name", "posix"):
                    with patch.dict(os.environ, {"DOWNLOAD_DIR": "/downloads"}):
                        settings = config.load_settings()
                        self.assertEqual(
                            settings["download_dir"], "/custom/path/downloads"
                        )

    def test_single_instance_linux_success(self):
        """Linux 환경에서 fcntl 기반 락 획득 성공 검증."""
        mock_fcntl = MagicMock()
        mock_file = MagicMock()

        with patch("sys.platform", "linux"):
            with patch.dict(sys.modules, {"fcntl": mock_fcntl}):
                with patch("builtins.open", return_value=mock_file):
                    with patch("pathlib.Path.mkdir"):
                        ensure_single_instance()
                        mock_fcntl.flock.assert_called_once()

    def test_single_instance_linux_already_running(self):
        """Linux 환경에서 중복 실행 시 BlockingIOError 발생하고 sys.exit(1) 호출되는지 검증."""
        mock_fcntl = MagicMock()
        mock_fcntl.flock.side_effect = BlockingIOError("Resource temporarily unavailable")
        mock_file = MagicMock()

        with patch("sys.platform", "linux"):
            with patch.dict(sys.modules, {"fcntl": mock_fcntl}):
                with patch("builtins.open", return_value=mock_file):
                    with patch("pathlib.Path.mkdir"):
                        with self.assertRaises(SystemExit) as cm:
                            ensure_single_instance()
                        self.assertEqual(cm.exception.code, 1)

    def test_aria2_path_linux_resolution(self):
        """Linux 환경에서 aria2c 실행 파일 경로 감지 검증."""
        # 1. ARIA2C_PATH 환경 변수 지정 시
        with patch.dict(os.environ, {"ARIA2C_PATH": "/usr/local/bin/aria2c"}):
            with patch("os.path.isfile", return_value=True):
                self.assertEqual(get_aria2_path(), "/usr/local/bin/aria2c")

        # 2. 로컬 bin에 없고 시스템 PATH에서 감지 시 (/usr/bin/aria2c)
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(Path, "is_file", return_value=False):
                with patch("shutil.which", return_value="/usr/bin/aria2c"):
                    self.assertEqual(get_aria2_path(), "/usr/bin/aria2c")

    def test_browser_solver_xvfb_display_detection(self):
        """Linux 환경에서 DISPLAY 존재 유무에 따른 headless 설정 분기 검증."""
        mock_page = MagicMock()
        mock_options = MagicMock()

        # 1. DISPLAY 없음 -> headless(True)
        with patch("sys.platform", "linux"):
            with patch.dict(os.environ, {}, clear=True):
                with patch("DrissionPage.ChromiumOptions", return_value=mock_options):
                    with patch("DrissionPage.ChromiumPage", return_value=mock_page):
                        with stealth_browser_session():
                            mock_options.headless.assert_called_with(True)

        mock_options.reset_mock()

        # 2. DISPLAY=:99 (Xvfb) -> headless(False)
        with patch("sys.platform", "linux"):
            with patch.dict(os.environ, {"DISPLAY": ":99"}):
                with patch("DrissionPage.ChromiumOptions", return_value=mock_options):
                    with patch("DrissionPage.ChromiumPage", return_value=mock_page):
                        with stealth_browser_session():
                            mock_options.headless.assert_called_with(False)

    def test_graceful_shutdown(self):
        """SIGTERM 수신 및 shutdown_server 호출 시 백그라운드 매니저 정상 중지 검증."""
        with patch("app.download_manager.stop") as mock_dm_stop:
            with patch("app.kone_watcher.stop") as mock_kw_stop:
                shutdown_server()
                mock_dm_stop.assert_called_once()
                mock_kw_stop.assert_called_once()

    def test_sigterm_handler(self):
        """SIGTERM 핸들러가 shutdown_server를 부르고 sys.exit(0)을 수행하는지 검증."""
        with patch("app.shutdown_server") as mock_shutdown:
            with self.assertRaises(SystemExit) as cm:
                _sigterm_handler(15, None)
            mock_shutdown.assert_called_once()
            self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
