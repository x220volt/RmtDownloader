"""원격 다운로더(Remote Downloader) 인증 및 보안 모듈.

- Werkzeug 기반 scrypt 단방향 암호화 해싱 및 검증
- 평문/레거시 비밀번호 검증 하위 호환
- HMAC SHA-256 세션 무결성 서명 (비밀번호 변경 시 구 세션 자동 만료)
- Flask Session, HTTP Basic Auth, 커스텀 X-Auth 헤더, API Token 통합 인증 데코레이터
"""

import hashlib
import hmac
import json
import logging
import sys
from functools import wraps
from typing import Any, Callable, Optional

from flask import current_app, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)


def _constant_time_equal(left: str, right: str) -> bool:
    """Compare UTF-8 credentials without ASCII-only compare_digest failures."""
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
    )


def hash_password(raw_password: str) -> str:
    """비밀번호를 안전한 scrypt 단방향 해시로 생성합니다."""
    return generate_password_hash(raw_password, method="scrypt")


def verify_password(stored_hash_or_pw: str, candidate_pw: str) -> bool:
    """비밀번호 검증 (scrypt/pbkdf2 해시 및 레거시 평문 완벽 호환)."""
    if not stored_hash_or_pw or not candidate_pw:
        return False
    if stored_hash_or_pw.startswith(("scrypt:", "pbkdf2:")):
        try:
            return check_password_hash(stored_hash_or_pw, candidate_pw)
        except Exception:
            return False
    return _constant_time_equal(stored_hash_or_pw, candidate_pw)


def _get_app_module():
    return sys.modules.get("app")


def _get_settings() -> dict:
    app_mod = _get_app_module()
    if app_mod and hasattr(app_mod, "load_settings"):
        return app_mod.load_settings()
    from config import load_settings

    return load_settings()


def _get_secret_key(secret_key: Optional[str | bytes] = None) -> bytes:
    if secret_key is not None:
        if isinstance(secret_key, str):
            return secret_key.encode("utf-8")
        return secret_key
    app_mod = _get_app_module()
    if app_mod and hasattr(app_mod, "app") and getattr(app_mod.app, "secret_key", None):
        sk = app_mod.app.secret_key
    else:
        try:
            sk = current_app.secret_key or b""
        except Exception:
            sk = b""
    if isinstance(sk, str):
        return sk.encode("utf-8")
    return sk or b""


def credential_signature(
    settings: dict, secret_key: Optional[str | bytes] = None
) -> str:
    """현재 설정된 아이디/비밀번호에 대한 HMAC 서명을 생성합니다.

    아이디나 비밀번호가 변경되면 서명이 바뀌므로 이전 세션이 자동으로 무효화됩니다.
    """
    credentials = json.dumps(
        [
            str(settings.get("auth_username", "admin")),
            str(settings.get("auth_password", "password1234")),
        ]
    )
    secret = _get_secret_key(secret_key)
    return hmac.new(secret, credentials.encode("utf-8"), hashlib.sha256).hexdigest()


def session_authenticated(
    settings: dict,
    secret_key: Optional[str | bytes] = None,
    session_obj: Optional[Any] = None,
) -> bool:
    """브라우저 세션의 유효성 및 서명 일치 여부를 검사합니다."""
    target_session: Any = session_obj
    if target_session is None:
        try:
            target_session = session
        except Exception:
            return False
    if target_session is None or not hasattr(target_session, "get"):
        return False
    return target_session.get("authenticated") is True and _constant_time_equal(
        str(target_session.get("credential_signature", "")),
        credential_signature(settings, secret_key=secret_key),
    )


def validate_credentials(cand_user: str, cand_pw: str, settings: dict) -> bool:
    """아이디와 비밀번호가 현재 설정과 일치하는지 안전하게 검증합니다."""
    if not cand_user or not cand_pw:
        return False
    cfg_user = settings.get("auth_username", "admin")
    cfg_pw = settings.get("auth_password", "password1234")
    return _constant_time_equal(cand_user, cfg_user) and verify_password(
        cfg_pw, cand_pw
    )


def is_token_authorized(token: Optional[str], settings: dict) -> bool:
    """API Token 또는 인증 비밀번호가 올바른지 검증합니다."""
    if not token:
        return False
    api_token = settings.get("api_token")
    if api_token and _constant_time_equal(token, api_token):
        return True
    auth_pw = settings.get("auth_password")
    if auth_pw and verify_password(auth_pw, token):
        return True
    return False


def login_required(f: Callable) -> Callable:
    """로그인 인증 확인 데코레이터.

    세션 쿠키, HTTP Basic Auth, 커스텀 X-Auth 헤더(X-Auth-User, X-Auth-Password)를 지원합니다.
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        settings = _get_settings()

        if not settings.get("auth_enabled", True):
            return f(*args, **kwargs)

        if request.method == "OPTIONS":
            return f(*args, **kwargs)

        # 1. 세션 쿠키 인증 확인
        if session_authenticated(settings):
            return f(*args, **kwargs)

        # 2. HTTP Basic Auth 또는 커스텀 헤더 인증 (확장 프로그램 및 외부 API 연동용)
        auth = request.authorization
        header_user = request.headers.get("X-Auth-User")
        header_pw = request.headers.get("X-Auth-Password") or request.headers.get(
            "X-Auth-Pass"
        )

        cand_user = auth.username if auth else header_user
        cand_pw = auth.password if auth else header_pw

        if cand_user and cand_pw and validate_credentials(cand_user, cand_pw, settings):
            return f(*args, **kwargs)

        # 3. 미인증 처리 (API 요청은 401 JSON, 일반 웹 브라우징은 /login 리다이렉트)
        if request.path.startswith("/api/"):
            return jsonify(
                {"error": "Unauthorized", "message": "로그인이 필요합니다."}
            ), 401
        return redirect(url_for("login"))

    return decorated_function
