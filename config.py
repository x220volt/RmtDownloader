"""원격 다운로더 설정 및 로컬 네트워크 환경 감지 모듈."""

import json
import logging
import os
import secrets
import socket
import ssl
import sys
import tempfile
import threading
from pathlib import Path

import requests
import urllib3

APP_VERSION = "0.1.36"
APP_VERSION_TAG = f"v{APP_VERSION}"

CONFIG_DIR = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).parent
)
CONFIG_FILE = CONFIG_DIR / "settings.json"
TORRENTS_DIR = CONFIG_DIR / "data" / "torrents"

DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Downloads" / "RemoteDownloads")

DEFAULT_SETTINGS = {
    "host": "0.0.0.0",
    "port": 7971,
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "max_concurrent_downloads": 3,
    "chunk_size_kb": 256,
    "gofile_token": "",
    "gofile_proxy": "",
    "auth_enabled": True,
    "auth_username": "admin",
    "auth_password": "password1234",
    "api_token": "",
    "auto_subfolder": True,
    "subfolder_rule": "package_or_title",
    "terabox_cookie": "",
    "terabox_password": "",
    "terabox_mode": "download",
    "terabox_save_path": "/",
    "flaresolverr_url": "",
    "kone_auto_check": False,
    "kone_board_url": "https://kone.gg/s/retrogame",
    "kone_check_interval_sec": 180,
    "kone_subfolder": True,
    "check_duplicate_url": True,
    "capture_source_page": False,
    "ignore_ssl_certs": False,
    "session_lifetime_days": 30,
    "secret_key": "",
    "extension_dir": "",
}


_SETTINGS_LOCK = threading.RLock()


def generate_api_token():
    """안전한 32자리 무작위 16진수 API 전용 전송 토큰을 생성합니다."""
    return secrets.token_hex(16)


def validate_settings(settings):
    for key, low, high in (
        ("port", 1, 65535),
        ("max_concurrent_downloads", 1, 32),
        ("chunk_size_kb", 1, 16384),
        ("kone_check_interval_sec", 30, 86400),
        ("session_lifetime_days", 1, 365),
    ):
        if key in settings:
            value = settings[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key}: expected integer between {low} and {high}")
    for key in (
        "auth_enabled",
        "auto_subfolder",
        "kone_auto_check",
        "kone_subfolder",
        "check_duplicate_url",
        "capture_source_page",
        "ignore_ssl_certs",
    ):
        if key in settings and type(settings[key]) is not bool:
            raise ValueError(f"{key}: expected boolean")
    for key, default in DEFAULT_SETTINGS.items():
        if isinstance(default, str) and not isinstance(settings[key], str):
            raise ValueError(f"{key}: expected string")
    if not settings["download_dir"].strip():
        raise ValueError("download_dir must not be empty")
    if "auth_username" in settings and not str(settings["auth_username"]).strip():
        raise ValueError("auth_username must not be empty")
    if settings["terabox_mode"] not in ("download", "save"):
        raise ValueError("terabox_mode: expected download or save")
    if settings["subfolder_rule"] not in (
        "package_or_title",
        "date",
        "service",
        "none",
    ):
        raise ValueError("Invalid subfolder_rule")


_SSL_HOOKED = False
_ORIGINAL_SESSION_INIT = requests.Session.__init__
_ORIGINAL_CREATE_DEFAULT_CONTEXT = ssl.create_default_context
_IGNORE_SSL_CERTS = False


def configure_ssl_verification(ignore_ssl: bool):
    """SSL 인증서 검증 무시 설정을 프로세스 전역(requests/ssl)에 적용합니다."""
    global _SSL_HOOKED, _IGNORE_SSL_CERTS
    _IGNORE_SSL_CERTS = bool(ignore_ssl)

    if not _SSL_HOOKED:

        def _patched_session_init(self, *args, **kwargs):
            _ORIGINAL_SESSION_INIT(self, *args, **kwargs)
            if _IGNORE_SSL_CERTS:
                self.verify = False

        requests.Session.__init__ = _patched_session_init

        def _patched_create_default_context(*args, **kwargs):
            ctx = _ORIGINAL_CREATE_DEFAULT_CONTEXT(*args, **kwargs)
            if _IGNORE_SSL_CERTS:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            return ctx

        ssl.create_default_context = _patched_create_default_context
        _SSL_HOOKED = True

    if _IGNORE_SSL_CERTS:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        urllib3.disable_warnings()


def load_settings():
    """Read a consistent settings snapshot without creating directories."""
    with _SETTINGS_LOCK:
        settings = dict(DEFAULT_SETTINGS)
        try:
            with open(CONFIG_FILE, encoding="utf-8") as stream:
                saved = json.load(stream)
            if not isinstance(saved, dict):
                raise ValueError("Settings must be a JSON object")
            settings.update(saved)
            validate_settings(settings)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).warning(
                "Invalid settings file; using defaults", exc_info=True
            )
            settings = dict(DEFAULT_SETTINGS)
        configure_ssl_verification(settings.get("ignore_ssl_certs", False))
        return settings


def _get_or_create_secret(setting_name, factory):
    """Serialize first-use creation and return only a persisted credential."""
    with _SETTINGS_LOCK:
        value = load_settings().get(setting_name)
        if not value:
            value = factory()
            save_settings({setting_name: value})
        return value


def get_or_create_api_token():
    """Return the persisted API token, creating it once if necessary."""
    return _get_or_create_secret("api_token", generate_api_token)


def get_or_create_secret_key() -> str:
    """Return the environment override or a persisted session signing key."""
    return os.environ.get("REMOTE_DOWNLOADER_SECRET_KEY") or _get_or_create_secret(
        "secret_key", lambda: secrets.token_hex(32)
    )


def save_settings(new_settings):
    """Validate and atomically replace settings under the shared lock."""
    with _SETTINGS_LOCK:
        if not isinstance(new_settings, dict):
            raise ValueError("Settings must be a JSON object")
        settings = load_settings()
        settings.update(new_settings)
        validate_settings(settings)
        os.makedirs(str(settings["download_dir"]), exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=CONFIG_FILE.parent,
                suffix=".tmp",
                delete=False,
            ) as stream:
                temp_path = stream.name
                json.dump(settings, stream, indent=2, ensure_ascii=False)
            os.replace(temp_path, CONFIG_FILE)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        configure_ssl_verification(settings.get("ignore_ssl_certs", False))
        return settings


def get_local_ip_addresses():
    """스마트폰이나 다른 PC에서 접속할 수 있는 현재 PC의 로컬 IPv4 주소 목록을 반환합니다."""
    ip_list = []
    try:
        # 외부 통신용 기본 라우트 IP 감지 시도
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # 8.8.8.8에 실제 패킷을 보내지 않고 라우팅 테이블 기준 로컬 IP 결정
        s.connect(("8.8.8.8", 80))
        primary_ip = s.getsockname()[0]
        s.close()
        if primary_ip and not primary_ip.startswith("127."):
            ip_list.append(primary_ip)
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = str(info[4][0])
            if ip and not ip.startswith("127.") and ip not in ip_list:
                ip_list.append(ip)
    except Exception:
        pass

    if not ip_list:
        ip_list.append("127.0.0.1")

    return ip_list
