"""GoFile (gofile.io) 링크 리졸버."""

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from .base import BaseResolver

logger = logging.getLogger(__name__)

GOFILE_ROOT = "https://gofile.io"
GOFILE_API_ROOT = "https://api.gofile.io"
GOFILE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
GOFILE_LANGUAGE = "en-US"
GOFILE_WT_WINDOW_SECONDS = 14400
WT_CACHE_SECONDS = 4 * 60 * 60
WT_FETCH_TIMEOUT = 15
WT_PROBE_TOKEN = "RemoteDownloaderProbeToken"

_cached_secret = None
_cached_at = 0.0
_cache_lock = threading.Lock()
_guest_token_cache = None
_guest_lock = threading.Lock()
_qcore_app = None


def _ensure_qcore_app():
    """QJSEngine 실행을 위해 QCoreApplication 인스턴스를 보장합니다."""
    global _qcore_app
    try:
        from PyQt6.QtCore import QCoreApplication

        inst = QCoreApplication.instance()
        if inst is None:
            _qcore_app = QCoreApplication(sys.argv)
        else:
            _qcore_app = inst
    except Exception as e:
        logger.warning(f"QCoreApplication 초기화 경고: {e}")


class _ScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "script":
            return
        src = dict(attrs).get("src")
        if src and "wt" in src.lower() and src.lower().endswith(".js"):
            self.sources.append(src)


def _find_wt_script_urls(html: str):
    parser = _ScriptParser()
    parser.feed(html)
    urls = [urljoin(f"{GOFILE_ROOT}/", src) for src in parser.sources]
    urls.append(f"{GOFILE_ROOT}/js/wt.obf.js")

    trusted = []
    for u in dict.fromkeys(urls):
        p = urlparse(u)
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme == "https" and host in ("gofile.io", "www.gofile.io"):
            trusted.append(u)
    return trusted


def _evaluate_hash_input(script: str) -> str:
    try:
        _ensure_qcore_app()
        from PyQt6.QtQml import QJSEngine

        engine = QJSEngine()
        bootstrap = (
            "var navigator = {userAgent: "
            + json.dumps(GOFILE_USER_AGENT)
            + ", language: "
            + json.dumps(GOFILE_LANGUAGE)
            + "}; var window = this; window.navigator = navigator; "
            "window.location = {hostname: 'gofile.io', search: ''};"
        )
        val = engine.evaluate(bootstrap, "bootstrap.js")
        if val.isError():
            raise RuntimeError(f"GoFile JS 부트스트랩 실패: {val.toString()}")

        val = engine.evaluate(script, "wt.obf.js")
        if val.isError():
            raise RuntimeError(f"GoFile WT 스크립트 실행 실패: {val.toString()}")

        probe = (
            "var __gofileHashInput = ''; "
            "_sha256 = function(value) { "
            "__gofileHashInput = String(value); return '0'.repeat(64); }; "
            f"generateWT({json.dumps(WT_PROBE_TOKEN)}); __gofileHashInput;"
        )
        val = engine.evaluate(probe, "probe.js")
        if val.isError():
            raise RuntimeError(f"GoFile probe 실행 실패: {val.toString()}")

        return val.toString()
    except Exception as qjs_err:
        node_exe = shutil.which("node") or shutil.which("nodejs")
        if node_exe:
            try:
                js_code = (
                    f"var navigator = {{userAgent: {json.dumps(GOFILE_USER_AGENT)}, language: {json.dumps(GOFILE_LANGUAGE)}}};\n"
                    f"var window = globalThis; window.navigator = navigator;\n"
                    f"window.location = {{hostname: 'gofile.io', search: ''}};\n"
                    f"{script}\n"
                    f"var __gofileHashInput = '';\n"
                    f"_sha256 = function(v) {{ __gofileHashInput = String(v); return '0'.repeat(64); }};\n"
                    f"generateWT({json.dumps(WT_PROBE_TOKEN)});\n"
                    f"process.stdout.write(__gofileHashInput);\n"
                )
                proc = subprocess.run(
                    [node_exe, "-e", js_code],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.strip()
            except Exception as node_err:
                logger.warning(f"Node.js 대체 평가 실패: {node_err}")
        raise qjs_err


def _extract_secret_from_script(script: str) -> str:
    raw = _evaluate_hash_input(script)
    prefix = f"{GOFILE_USER_AGENT}::{GOFILE_LANGUAGE}::{WT_PROBE_TOKEN}::"
    if not raw.startswith(prefix):
        raise ValueError("GoFile 인증 토큰 형식이 변경되었습니다.")
    remainder = raw[len(prefix) :]
    window, sep, secret = remainder.partition("::")
    if not sep or not window.isdigit():
        raise ValueError("GoFile 인증 토큰 시간 파싱 실패")
    if not re.fullmatch(r"[A-Za-z0-9._-]{6,128}", secret):
        raise ValueError("GoFile secret 형식이 비정상입니다.")
    return secret


def get_gofile_secret(session: requests.Session) -> str:
    global _cached_secret, _cached_at
    now = time.time()
    with _cache_lock:
        if _cached_secret and (now - _cached_at < WT_CACHE_SECONDS):
            return _cached_secret

        logger.info("GoFile 웹 토큰 secret을 갱신합니다...")
        resp = session.get(
            GOFILE_ROOT,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": GOFILE_USER_AGENT,
            },
            timeout=WT_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
        html = resp.text

        script_urls = _find_wt_script_urls(html)
        secret = None
        for s_url in script_urls:
            try:
                s_resp = session.get(
                    s_url,
                    headers={
                        "Referer": f"{GOFILE_ROOT}/",
                        "User-Agent": GOFILE_USER_AGENT,
                    },
                    timeout=WT_FETCH_TIMEOUT,
                )
                s_resp.raise_for_status()
                script_text = s_resp.text
                if "generateWT" in script_text:
                    secret = _extract_secret_from_script(script_text)
                    break
            except Exception as e:
                logger.warning(f"스크립트 {s_url} 처리 실패: {e}")

        if not secret:
            raise RuntimeError("GoFile 웹 토큰 secret을 추출할 수 없습니다.")

        _cached_secret = secret
        _cached_at = now
        logger.info(f"GoFile 웹 토큰 secret 획득 성공: {secret}")
        return secret


def generate_website_token(account_token: str, secret: str) -> str:
    now = time.time()
    window = int(now // GOFILE_WT_WINDOW_SECONDS)
    raw = f"{GOFILE_USER_AGENT}::{GOFILE_LANGUAGE}::{account_token}::{window}::{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_guest_token(session: requests.Session) -> str:
    global _guest_token_cache
    with _guest_lock:
        if _guest_token_cache:
            return _guest_token_cache

        logger.info("GoFile 게스트 계정 토큰 발급 요청 중...")
        resp = session.post(
            f"{GOFILE_API_ROOT}/accounts",
            headers={
                "Accept": "application/json",
                "Origin": GOFILE_ROOT,
                "Referer": f"{GOFILE_ROOT}/",
                "User-Agent": GOFILE_USER_AGENT,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            token = data.get("data", {}).get("token")
            if token:
                _guest_token_cache = token
                logger.info("GoFile 게스트 계정 토큰 발급 완료")
                return token
        raise RuntimeError("GoFile 게스트 토큰을 발급받지 못했습니다.")


def parse_gofile_content_id(url: str):
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in ("http", "https") or host not in (
        "gofile.io",
        "www.gofile.io",
    ):
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "d":
        return parts[1]
    return None


def _check_password_status(payload: dict) -> tuple[bool, str]:
    """
    GoFile API 응답이 비밀번호로 보호되어 있는지 검사합니다.
    (is_protected, status_code) 튜플 반환.
    status_code: 'passwordRequired' | 'passwordWrong' | ''
    """
    if not isinstance(payload, dict):
        return False, ""
    status = str(payload.get("status") or "")
    if status == "error-passwordRequired":
        return True, "passwordRequired"
    data = payload.get("data")
    if isinstance(data, dict):
        can_access = data.get("canAccess")
        is_password = data.get("password")
        pwd_status = str(data.get("passwordStatus") or "")
        if can_access is False and (
            is_password or pwd_status in ("passwordRequired", "passwordWrong")
        ):
            return True, pwd_status or "passwordRequired"
        if pwd_status in ("passwordRequired", "passwordWrong"):
            return True, pwd_status
    return False, ""


class GofileResolver(BaseResolver):
    """GoFile (gofile.io) 다운로드 링크 리졸버."""

    name = "GoFile"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        from ..url_helper import extract_service_password

        clean_u, _ = extract_service_password(url)
        return (parse_gofile_content_id(clean_u) is not None) or (
            parse_gofile_content_id(url) is not None
        )

    def resolve(self, url: str, options: dict | None = None) -> dict:
        from ..url_helper import extract_service_password, get_candidate_passwords

        clean_u, extracted_pwd = extract_service_password(url, options)
        content_id = parse_gofile_content_id(clean_u) or parse_gofile_content_id(url)
        if not content_id:
            raise ValueError(f"유효한 GoFile 공유 링크가 아닙니다: {url}")

        options = options or {}
        configured_token = options.get("gofile_token", "").strip()

        # 1. 수동 지정 비밀번호 (URL 쿼리, 파이프 구분자, 또는 options 매개변수)
        manual_pwd = (
            extracted_pwd
            or str(
                options.get("password")
                or options.get("pwd")
                or options.get("code")
                or ""
            ).strip()
        )

        # 2. 비밀번호 후보군 전체 구성 (수동 비밀번호 -> 사전 등록 비밀번호 목록)
        all_candidates = get_candidate_passwords(url, options)
        pwd_candidates: list[tuple[str, str]] = []
        for idx, cand in enumerate(all_candidates):
            label = (
                "수동 입력 비밀번호"
                if (manual_pwd and cand == manual_pwd)
                else f"사전 등록 비밀번호 ({idx + 1})"
            )
            pwd_candidates.append((label, cand))

        with requests.Session() as session:
            token = configured_token or get_guest_token(session)
            secret = get_gofile_secret(session)
            wt = generate_website_token(token, secret)

            headers = {
                "Accept": "application/json",
                "Origin": GOFILE_ROOT,
                "Referer": f"{GOFILE_ROOT}/",
                "User-Agent": GOFILE_USER_AGENT,
                "Authorization": f"Bearer {token}",
                "X-BL": GOFILE_LANGUAGE,
                "X-Website-Token": wt,
            }

            def _fetch_contents(target_id: str, pwd_value: str | None) -> dict:
                req_params: dict = {"page": 1, "pageSize": 100}
                if pwd_value:
                    req_params["password"] = hashlib.sha256(
                        pwd_value.encode("utf-8")
                    ).hexdigest()
                resp = session.get(
                    f"{GOFILE_API_ROOT}/contents/{target_id}",
                    headers=headers,
                    params=req_params,
                    timeout=20,
                )
                resp.raise_for_status()
                return resp.json()

            # 첫 번째 시도 (비밀번호 후보가 있으면 1순위 사용, 없으면 패스워드 없이 조회)
            if pwd_candidates:
                first_desc, current_pwd = pwd_candidates[0]
                logger.info(f"GoFile 콘텐츠 조회 중 ({first_desc} 적용): {content_id}")
                payload = _fetch_contents(content_id, current_pwd)
                tried_pwds = [current_pwd]
            else:
                current_pwd = None
                logger.info(f"GoFile 콘텐츠 조회 중 (비밀번호 없음): {content_id}")
                payload = _fetch_contents(content_id, None)
                tried_pwds = []

            # 비밀번호 보호 확인 및 추가 비밀번호 후보 재시도
            is_pwd_protected, pwd_status = _check_password_status(payload)
            if is_pwd_protected:
                # 다음 후보군 순차 시도
                next_candidates = pwd_candidates[1:] if current_pwd else pwd_candidates
                for next_desc, next_pwd in next_candidates:
                    logger.info(f"GoFile {next_desc}로 재시도 중: {content_id}")
                    current_pwd = next_pwd
                    tried_pwds.append(current_pwd)
                    payload = _fetch_contents(content_id, current_pwd)
                    is_pwd_protected, pwd_status = _check_password_status(payload)
                    if not is_pwd_protected:
                        logger.info(f"GoFile {next_desc} 인증 성공: {content_id}")
                        break

            # 모든 비밀번호를 시도했으나 여전히 비밀번호로 보호되어 있는 경우
            if is_pwd_protected:
                if tried_pwds:
                    tried_str = ", ".join(repr(p) for p in tried_pwds)
                    raise ValueError(
                        f"GoFile 비밀번호로 보호되어 있는 링크이며, 입력/기본 비밀번호가 일치하지 않습니다. "
                        f"(시도한 비밀번호: {tried_str})"
                    )
                else:
                    raise ValueError(
                        "GoFile 비밀번호로 보호되어 있는 링크입니다. "
                        "링크 뒤에 '?pwd=비밀번호'를 붙이거나 설정에서 기본 비밀번호를 확인해 주세요."
                    )

            # Kone.gg 바이너리 태그('1')가 끝에 붙어 잘못 추출된 ID(예: Y0akDCRY1 -> Y0akDCRY) 자동 보정 재시도
            if (
                payload.get("status") == "error-notFound"
                and content_id.endswith("1")
                and len(content_id) >= 7
            ):
                trimmed_id = content_id[:-1]
                logger.info(
                    f"GoFile ID 끝자리 '1' 제거 후 재시도 중: {content_id} -> {trimmed_id}"
                )
                retry_payload = _fetch_contents(trimmed_id, current_pwd)
                if retry_payload.get("status") == "ok":
                    content_id = trimmed_id
                    payload = retry_payload

            if payload.get("status") != "ok":
                status = payload.get("status")
                if status == "error-passwordRequired":
                    raise ValueError("GoFile 비밀번호로 보호되어 있는 링크입니다.")
                if status == "error-notFound":
                    raise FileNotFoundError(
                        "GoFile 콘텐츠를 찾을 수 없습니다(삭제되었거나 잘못된 링크)."
                    )
                raise RuntimeError(f"GoFile API 오류: {status}")

            data = payload.get("data", {})
            children = data.get("children") or data.get("contents")
            if isinstance(children, dict):
                items = list(children.values())
            elif isinstance(children, list):
                items = children
            else:
                items = []

            if data.get("type") == "file":
                items.insert(0, data)

            selected_file = None
            for item in items:
                if not isinstance(item, dict):
                    continue
                if (
                    item.get("type") == "file"
                    or item.get("link")
                    or item.get("directLink")
                ):
                    selected_file = item
                    break

            if not selected_file:
                raise FileNotFoundError(
                    "GoFile 공유 폴더에 다운로드 가능한 파일이 없습니다."
                )

            download_url = selected_file.get("link") or selected_file.get("directLink")
            filename = selected_file.get("name", "")

            return {
                "url": download_url,
                "filename": filename,
                "headers": {
                    "User-Agent": GOFILE_USER_AGENT,
                    "Referer": f"{GOFILE_ROOT}/",
                },
                "cookies": f"accountToken={token}",
                "referer": f"{GOFILE_ROOT}/d/{content_id}",
                "source_service": "GoFile",
            }
