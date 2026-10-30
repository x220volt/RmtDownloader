"""인증 모듈(auth.py) 및 브라우저 솔버(browser_solver.py) 단위 테스트."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import (
    credential_signature,
    hash_password,
    is_token_authorized,
    session_authenticated,
    validate_credentials,
    verify_password,
)
from downloader.browser_solver import find_browser_path, stealth_browser_session


class AuthModuleTests(unittest.TestCase):
    def test_hash_and_verify_scrypt(self):
        raw = "my_secret_1234!"
        hashed = hash_password(raw)
        self.assertTrue(hashed.startswith("scrypt:"))
        self.assertTrue(verify_password(hashed, raw))
        self.assertFalse(verify_password(hashed, "wrong_pw"))
        # Pass-the-hash 방어: 저장된 scrypt 해시 문자열 자체를 비밀번호로 입력할 경우 거부되어야 함
        self.assertFalse(verify_password(hashed, hashed))

    def test_verify_legacy_plaintext(self):
        self.assertTrue(verify_password("legacy_plain", "legacy_plain"))
        self.assertFalse(verify_password("legacy_plain", "other"))
        self.assertFalse(verify_password("", "other"))
        self.assertFalse(verify_password("stored", ""))

    def test_validate_credentials(self):
        settings = {
            "auth_username": "manager",
            "auth_password": hash_password("pass123"),
        }
        self.assertTrue(validate_credentials("manager", "pass123", settings))
        self.assertFalse(validate_credentials("manager", "wrong", settings))
        self.assertFalse(validate_credentials("admin", "pass123", settings))
        self.assertFalse(validate_credentials("", "", settings))

    def test_is_token_authorized(self):
        settings = {
            "api_token": "token_abc_xyz",
            "auth_password": hash_password("pw_999"),
        }
        self.assertTrue(is_token_authorized("token_abc_xyz", settings))
        self.assertTrue(is_token_authorized("pw_999", settings))
        self.assertFalse(is_token_authorized("wrong_token", settings))
        self.assertFalse(is_token_authorized("", settings))

    def test_credential_signature_changes_on_password_update(self):
        settings = {
            "auth_username": "admin",
            "auth_password": "pw1",
        }
        sig1 = credential_signature(settings, secret_key="test_secret")
        self.assertTrue(isinstance(sig1, str) and len(sig1) == 64)

        settings["auth_password"] = "pw2"
        sig2 = credential_signature(settings, secret_key="test_secret")
        self.assertNotEqual(sig1, sig2)

    def test_session_lifetime_validation_and_secret_key(self):
        from config import DEFAULT_SETTINGS, get_or_create_secret_key, validate_settings

        self.assertIn("session_lifetime_days", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["session_lifetime_days"], 30)

        # 유효 범위 테스트 (1 ~ 365)
        valid_cfg = dict(DEFAULT_SETTINGS)
        valid_cfg["session_lifetime_days"] = 90
        validate_settings(valid_cfg)  # no error

        with self.assertRaises(ValueError):
            invalid_cfg = dict(DEFAULT_SETTINGS)
            invalid_cfg["session_lifetime_days"] = 0
            validate_settings(invalid_cfg)

        with self.assertRaises(ValueError):
            invalid_cfg = dict(DEFAULT_SETTINGS)
            invalid_cfg["session_lifetime_days"] = 366
            validate_settings(invalid_cfg)

        # secret key 생성 및 일관성
        sk1 = get_or_create_secret_key()
        self.assertTrue(bool(sk1))
        sk2 = get_or_create_secret_key()
        self.assertEqual(sk1, sk2)


class BrowserSolverTests(unittest.TestCase):
    def test_find_browser_path(self):
        # find_browser_path should return string path or None without exceptions
        path = find_browser_path()
        if path:
            self.assertTrue(isinstance(path, str))
            self.assertTrue(os.path.exists(path))

    @patch("DrissionPage.ChromiumPage")
    @patch("DrissionPage.ChromiumOptions")
    def test_stealth_browser_session_lifecycle(self, mock_options, mock_page_cls):
        mock_page = MagicMock()
        mock_page_cls.return_value = mock_page

        with stealth_browser_session(prefix="test_sess_") as page:
            self.assertEqual(page, mock_page)

        mock_page.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
