"""GoFile (gofile.io) 링크 리졸버."""

import concurrent.futures
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
from pathlib import Path
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
WT_FETCH_TIMEOUT = 5
WT_PROBE_TOKEN = "RemoteDownloaderProbeToken"
DEFAULT_WT_SECRET = "12af056dacea0b"
GUEST_TOKEN_LIFETIME = 24 * 3600  # 24시간 토큰 보존
AUTO_PROXY_CACHE_SECONDS = 3600  # 1시간 프록시 캐시

_cached_secret = None
_cached_at = 0.0
_cache_lock = threading.Lock()
_guest_token_cache = None
_guest_lock = threading.Lock()
_qcore_app = None
_auto_proxy_cache: str | None = None
_auto_proxy_at: float = 0.0
_auto_proxy_lock = threading.Lock()


def get_auto_fallback_proxy(force_refresh: bool = False) -> str | None:
    """GoFile API 접속 차단 또는 Rate Limit 발생 시 자동으로 사용 가능한 무료 우회 프록시를 탐색합니다."""
    global _auto_proxy_cache, _auto_proxy_at
    now = time.time()
    with _auto_proxy_lock:
        if (
            not force_refresh
            and _auto_proxy_cache
            and (now - _auto_proxy_at < AUTO_PROXY_CACHE_SECONDS)
        ):
            return _auto_proxy_cache

        logger.info("GoFile 자동 우회용 프록시 목록 탐색 중...")
        proxy_sources = [
            "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=3000&country=all&ssl=yes&anonymity=elite",
            "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
        ]
        candidates = []
        for src in proxy_sources:
            try:
                resp = requests.get(src, timeout=4)
                if resp.status_code == 200 and resp.text:
                    lines = [
                        line.strip()
                        for line in resp.text.splitlines()
                        if line.strip() and ":" in line and not line.startswith("#")
                    ]
                    candidates.extend(lines)
                    if len(candidates) >= 30:
                        break
            except Exception:
                continue

        if not candidates:
            logger.warning("GoFile 자동 우회 프록시 소스에서 목록을 가져오지 못했습니다.")
            return None

        def _test_proxy(addr: str) -> str | None:
            px_dict = {"http": f"http://{addr}", "https": f"http://{addr}"}
            try:
                test_resp = requests.get(
                    f"{GOFILE_API_ROOT}/accounts",
                    proxies=px_dict,
                    timeout=3.0,
                )
                if test_resp.status_code in (200, 400, 404):
                    return addr
            except Exception:
                return None
            return None

        tested_pool = candidates[:25]
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_test_proxy, p) for p in tested_pool]
            for f in concurrent.futures.as_completed(futures):
                try:
                    res = f.result()
                    if res:
                        selected = f"http://{res}"
                        _auto_proxy_cache = selected
                        _auto_proxy_at = time.time()
                        logger.info(f"GoFile 자동 우회 프록시 선택 완료: {selected}")
                        return selected
                except Exception:
                    pass

        logger.warning("유효한 GoFile 우회 프록시를 찾지 못했습니다.")
        return None


def clear_auto_fallback_proxy() -> None:
    global _auto_proxy_cache, _auto_proxy_at
    with _auto_proxy_lock:
        _auto_proxy_cache = None
        _auto_proxy_at = 0.0


def _get_token_file_path() -> Path:
    try:
        from config import CONFIG_DIR

        return CONFIG_DIR / "data" / "gofile_session.json"
    except Exception:
        return Path.cwd() / "gofile_session.json"


def _load_persisted_token() -> str | None:
    try:
        p = _get_token_file_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            tok = data.get("token")
            created_at = data.get("created_at", 0)
            if (
                tok
                and isinstance(tok, str)
                and (time.time() - created_at < GUEST_TOKEN_LIFETIME)
            ):
                return tok.strip()
    except Exception as e:
        logger.debug(f"GoFile 저장된 토큰 로드 실패: {e}")
    return None


def _save_persisted_token(token: str) -> None:
    try:
        p = _get_token_file_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"token": token, "created_at": time.time()}
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"GoFile 토큰 저장 실패: {e}")


def _clear_guest_token() -> None:
    global _guest_token_cache
    with _guest_lock:
        _guest_token_cache = None
    try:
        p = _get_token_file_path()
        if p.exists():
            p.unlink()
    except Exception:
        pass


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
    # 1. PyQt6 QJSEngine 시도 (Windows 및 Qt6 그래픽 라이브러리가 갖춰진 환경)
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
        logger.info(
            f"PyQt6 JS 엔진 사용 불가({qjs_err}). 대체 실행 엔진(Node/Browser)으로 자동 전환합니다."
        )

    # 2. Node.js CLI 시도 (Linux / Docker 가벼운 런타임 환경)
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
                [node_exe, "-"],
                input=js_code,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception as node_err:
            logger.debug(f"Node.js 대체 평가 실패: {node_err}")

    # 3. 로컬 브라우저 세션(DrissionPage / Chromium) 시도 (NAS 도커 등 브라우저 환경)
    try:
        from ..browser_solver import stealth_browser_session

        with stealth_browser_session(prefix="gofile_eval_") as page:
            page.get("about:blank")
            page.run_js("window.eval(arguments[0]);", script)
            page.run_js(
                "_sha256 = function(v) { window.__probe = String(v); return '0'.repeat(64); };"
            )
            raw = page.run_js(
                f"generateWT({json.dumps(WT_PROBE_TOKEN)}); return window.__probe;"
            )
            if raw and WT_PROBE_TOKEN in str(raw):
                return str(raw)
    except Exception as browser_err:
        logger.debug(f"DrissionPage GoFile JS 평가 실패: {browser_err}")

    raise RuntimeError(
        "GoFile 웹 토큰 생성 실패: 사용 가능한 JS 실행 환경(PyQt6, Node.js, Chromium)이 없습니다."
    )


def _extract_secret_from_script(script: str) -> str:
    raw = _evaluate_hash_input(script)
    probe_needle = f"::{WT_PROBE_TOKEN}::"
    if probe_needle in raw:
        _, _, remainder = raw.partition(probe_needle)
        window, sep, secret = remainder.partition("::")
        secret = secret.strip()
        if sep and window.isdigit() and re.fullmatch(r"[A-Za-z0-9._-]{6,128}", secret):
            return secret

    prefix = f"{GOFILE_USER_AGENT}::{GOFILE_LANGUAGE}::{WT_PROBE_TOKEN}::"
    if raw.startswith(prefix):
        remainder = raw[len(prefix) :]
        window, sep, secret = remainder.partition("::")
        secret = secret.strip()
        if sep and window.isdigit() and re.fullmatch(r"[A-Za-z0-9._-]{6,128}", secret):
            return secret

    raise ValueError("GoFile 인증 토큰 형식이 변경되었습니다.")


def get_gofile_secret(
    session: requests.Session, force_refresh: bool = False
) -> str:
    global _cached_secret, _cached_at
    now = time.time()
    with _cache_lock:
        if (
            not force_refresh
            and _cached_secret
            and (now - _cached_at < WT_CACHE_SECONDS)
        ):
            return _cached_secret

        logger.info("GoFile 웹 토큰 secret을 갱신합니다...")
        try:
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

            if secret:
                _cached_secret = secret
                _cached_at = now
                logger.info(f"GoFile 웹 토큰 secret 획득 성공: {secret}")
                return secret
        except Exception as e:
            logger.warning(
                f"GoFile 웹페이지 secret 추출 실패({e}). 기본/캐시된 secret을 사용합니다."
            )

        if _cached_secret:
            return _cached_secret
        return DEFAULT_WT_SECRET


def generate_website_token(account_token: str, secret: str) -> str:
    now = time.time()
    window = int(now // GOFILE_WT_WINDOW_SECONDS)
    raw = f"{GOFILE_USER_AGENT}::{GOFILE_LANGUAGE}::{account_token}::{window}::{secret}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_guest_token(
    session: requests.Session, force_refresh: bool = False
) -> str:
    global _guest_token_cache
    with _guest_lock:
        if not force_refresh:
            if _guest_token_cache:
                return _guest_token_cache
            persisted = _load_persisted_token()
            if persisted:
                _guest_token_cache = persisted
                return persisted

        logger.info("GoFile 게스트 계정 토큰 발급 요청 중...")
        try:
            resp = session.post(
                f"{GOFILE_API_ROOT}/accounts",
                headers={
                    "Accept": "application/json",
                    "Origin": GOFILE_ROOT,
                    "Referer": f"{GOFILE_ROOT}/",
                    "User-Agent": GOFILE_USER_AGENT,
                },
                timeout=(5, 10),
            )
        except requests.RequestException as e:
            err_resp = getattr(e, "response", None)
            if err_resp is not None and err_resp.status_code == 429:
                raise RuntimeError(
                    "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                    "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
                ) from e
            err_str = str(e).lower()
            if (
                isinstance(e, (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError))
                or "timed out" in err_str
                or "timeout" in err_str
                or "connection refused" in err_str
            ):
                raise RuntimeError(
                    "GoFile 서버 연결 시간 초과 (IP 차단 또는 네트워크 장애): GoFile 방화벽에 의해 현재 공인 IP가 일시 차단되었거나 서버 응답이 없습니다. "
                    "공유기/모뎀을 재부팅하여 새 IP를 할당받거나, VPN 사용 또는 설정에서 프록시(gofile_proxy)를 지정해 주세요."
                ) from e
            raise RuntimeError(f"GoFile 게스트 계정 생성 실패: {e}") from e

        if resp.status_code == 429:
            raise RuntimeError(
                "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
            )

        try:
            data = resp.json()
        except Exception:
            data = {}

        if data.get("status") == "error-rateLimit":
            raise RuntimeError(
                "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
            )

        if data.get("status") == "ok":
            token = data.get("data", {}).get("token")
            if token:
                _guest_token_cache = token
                _save_persisted_token(token)
                logger.info("GoFile 게스트 계정 토큰 발급 완료")
                return token

        raise RuntimeError(
            f"GoFile 게스트 토큰을 발급받지 못했습니다: {data.get('status') or resp.status_code}"
        )


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
    if status in (
        "error-passwordRequired",
        "error-passwordWrong",
        "error-wrongPassword",
        "error-invalidPassword",
    ) or "password" in status.lower():
        clean_code = status.replace("error-", "")
        return True, clean_code or "passwordRequired"
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
        if can_access is False and is_password:
            return True, "passwordRequired"
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
        raw_content_id = parse_gofile_content_id(clean_u) or parse_gofile_content_id(url)
        if not raw_content_id:
            raise ValueError(f"유효한 GoFile 공유 링크가 아닙니다: {url}")
        content_id: str = raw_content_id

        options = options or {}
        configured_token = options.get("gofile_token", "").strip()
        configured_proxy = options.get("gofile_proxy", "").strip()
        if not configured_proxy:
            try:
                from config import load_settings

                configured_proxy = (
                    load_settings().get("gofile_proxy") or ""
                ).strip()
            except Exception:
                pass

        from ..url_helper import parse_password_list

        raw_manual = (
            extracted_pwd
            or str(
                options.get("password")
                or options.get("pwd")
                or options.get("code")
                or ""
            ).strip()
        )
        parsed_manual = parse_password_list(raw_manual)
        manual_pwd = parsed_manual[0] if parsed_manual else ""

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

        def _execute_resolve(active_proxy: str) -> dict:
            nonlocal content_id, configured_token
            with requests.Session() as session:
                if active_proxy:
                    session.proxies = {
                        "http": active_proxy,
                        "https": active_proxy,
                    }

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
                    try:
                        resp = session.get(
                            f"{GOFILE_API_ROOT}/contents/{target_id}",
                            headers=headers,
                            params=req_params,
                            timeout=(5, 15),
                        )
                    except requests.RequestException as e:
                        err_resp = getattr(e, "response", None)
                        if err_resp is not None and err_resp.status_code == 429:
                            raise RuntimeError(
                                "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                                "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
                            ) from e
                        err_str = str(e).lower()
                        if (
                            isinstance(e, (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError))
                            or "timed out" in err_str
                            or "timeout" in err_str
                            or "connection refused" in err_str
                        ):
                            raise RuntimeError(
                                "GoFile 서버 연결 시간 초과 (IP 차단 또는 네트워크 장애): GoFile 방화벽에 의해 현재 공인 IP가 일시 차단되었거나 서버 응답이 없습니다. "
                                "공유기/모뎀을 재부팅하여 새 IP를 할당받거나, VPN 사용 또는 설정에서 프록시(gofile_proxy)를 지정해 주세요."
                            ) from e
                        raise
                    if resp.status_code == 429:
                        raise RuntimeError(
                            "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                            "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
                        )
                    if resp.status_code in (400, 401, 403):
                        try:
                            err_payload = resp.json()
                            if (
                                isinstance(err_payload, dict)
                                and "status" in err_payload
                            ):
                                return err_payload
                        except Exception:
                            pass
                    resp.raise_for_status()
                    return resp.json()

                # 첫 번째 시도 (비밀번호 후보가 있으면 1순위 사용, 없으면 패스워드 없이 조회)
                if pwd_candidates:
                    first_desc, current_pwd = pwd_candidates[0]
                    logger.info(
                        f"GoFile 콘텐츠 조회 중 ({first_desc} 적용): {content_id}"
                    )
                    payload = _fetch_contents(content_id, current_pwd)
                    tried_pwds = [current_pwd]
                else:
                    current_pwd = None
                    logger.info(f"GoFile 콘텐츠 조회 중 (비밀번호 없음): {content_id}")
                    payload = _fetch_contents(content_id, None)
                    tried_pwds = []

                # 인증 토큰 오류 자동 복구 (error-wrongToken, error-token, error-accountId)
                if payload.get("status") in (
                    "error-wrongToken",
                    "error-token",
                    "error-accountId",
                ):
                    logger.warning(
                        f"GoFile 토큰 거부됨({payload.get('status')}). 토큰을 초기화하고 재시도합니다."
                    )
                    if configured_token:
                        logger.warning(
                            "설정된 gofile_token이 유효하지 않아 게스트 토큰으로 전환합니다."
                        )
                        configured_token = ""
                    _clear_guest_token()
                    token = get_guest_token(session, force_refresh=True)
                    secret = get_gofile_secret(session, force_refresh=True)
                    wt = generate_website_token(token, secret)
                    headers["Authorization"] = f"Bearer {token}"
                    headers["X-Website-Token"] = wt
                    payload = _fetch_contents(content_id, current_pwd)

                # 웹 토큰(WT) secret 불일치 자동 복구 (error-notPremium)
                if payload.get("status") == "error-notPremium":
                    logger.warning(
                        "GoFile 웹 토큰 인증 실패(error-notPremium). secret을 갱신하고 재시도합니다."
                    )
                    secret = get_gofile_secret(session, force_refresh=True)
                    wt = generate_website_token(token, secret)
                    headers["X-Website-Token"] = wt
                    payload = _fetch_contents(content_id, current_pwd)

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
                        is_pwd_protected, _pwd_status = _check_password_status(payload)
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
                    if status == "error-rateLimit":
                        raise RuntimeError(
                            "GoFile API 요청 한도 초과 (Rate Limit / HTTP 429): GoFile 일시적 요청 제한이 적용 중입니다. "
                            "약 5~15분 후 다시 시도하거나, 설정에서 본인의 GoFile API 토큰(gofile_token) 또는 프록시(gofile_proxy)를 설정해 주세요."
                        )
                    if status in (
                        "error-passwordRequired",
                        "error-passwordWrong",
                        "error-wrongPassword",
                        "error-invalidPassword",
                    ) or "password" in str(status).lower():
                        tried_str = (
                            f" (시도한 비밀번호: {', '.join(repr(p) for p in tried_pwds)})"
                            if tried_pwds
                            else ""
                        )
                        raise ValueError(
                            f"GoFile 비밀번호로 보호되어 있는 링크이며, 입력/기본 비밀번호가 일치하지 않습니다.{tried_str}"
                        )
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

        initial_proxy = configured_proxy or _auto_proxy_cache or ""
        try:
            return _execute_resolve(initial_proxy)
        except Exception as first_err:
            err_str = str(first_err).lower()
            is_blocked = (
                isinstance(first_err, (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError))
                or "시간 초과" in str(first_err)
                or "차단" in str(first_err)
                or "rate limit" in err_str
                or "429" in err_str
                or "timed out" in err_str
                or "timeout" in err_str
                or "connection refused" in err_str
            )
            if not configured_proxy and is_blocked:
                logger.info("GoFile 직접 연결 실패(IP 차단 또는 Rate Limit). 자동 우회 프록시를 탐색합니다...")
                clear_auto_fallback_proxy()
                auto_px = get_auto_fallback_proxy(force_refresh=True)
                if auto_px:
                    logger.info(f"GoFile 자동 우회 프록시 적용 중: {auto_px}")
                    _clear_guest_token()
                    try:
                        return _execute_resolve(auto_px)
                    except Exception as retry_err:
                        logger.warning(
                            f"자동 우회 프록시({auto_px}) 1차 실패({retry_err}). 2차 대체 프록시 재탐색..."
                        )
                        clear_auto_fallback_proxy()
                        auto_px2 = get_auto_fallback_proxy(force_refresh=True)
                        if auto_px2:
                            logger.info(f"GoFile 자동 우회 2차 프록시 적용 중: {auto_px2}")
                            return _execute_resolve(auto_px2)
            raise first_err
