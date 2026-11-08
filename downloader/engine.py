"""HTTP 스트리밍 및 이어받기 지원 내장 다운로드 엔진."""

import glob
import logging
import mimetypes
import os
import re
import threading
import time
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

import requests

from .callbacks import safe_callback

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_CHUNK_SIZE = 256 * 1024  # 256 KB
REQUEST_TIMEOUT = (15, 60)

_FINALIZE_LOCK = threading.Lock()

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_MIME_MAP = {
    "application/vnd.rar": ".rar",
    "application/x-rar": ".rar",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/x-gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-bzip2": ".bz2",
    "application/x-xz": ".xz",
    "application/pdf": ".pdf",
}


def fix_mojibake(text: str) -> str:
    """ISO-8859-1(latin-1)로 잘못 디코딩되어 깨진 UTF-8 한글 문자열을 원본 한글로 복원합니다."""
    if not text:
        return ""
    try:
        if any(
            c in text for c in ("ê", "ë", "ì", "í", "î", "ï", "ð", "ñ", "ò", "ó", "ã")
        ):
            restored = re.sub(r"ã ([\x80-\xbf])", "ã\x85\\1", text)
            return restored.encode("latin1").decode("utf-8")
    except Exception:
        pass
    return text


def sanitize_filename(name: str) -> str:
    """원격 파일명을 안전한 로컬 파일명으로 정제합니다."""
    name = fix_mojibake((name or "")).replace("\\", "/").rsplit("/", 1)[-1]
    raw = re.sub(r"\s+", " ", (name or ""))
    cleaned = _INVALID_FILENAME_CHARS.sub("_", raw).strip().rstrip(". ")
    if not cleaned or cleaned in (".", ".."):
        cleaned = "download.dat"
    root, ext = os.path.splitext(cleaned)
    if root.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 220:
        cleaned = f"{root[:200]}{ext[:19]}"
    return cleaned


def sanitize_folder_name(name: str) -> str:
    """원격 게시글 제목이나 패키지명을 안전한 폴더명으로 정제합니다."""
    raw = re.sub(r"\s+", " ", fix_mojibake(name or ""))
    cleaned = _INVALID_FILENAME_CHARS.sub("_", raw).strip().rstrip(". ")
    if not cleaned or cleaned in (".", ".."):
        cleaned = "Package"
    root = cleaned.split(".")[0]
    if root.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 70:
        cleaned = cleaned[:70].strip().rstrip(". ")
    return cleaned


def extract_filename_from_headers(
    headers: Mapping[str, Any] | None = None,
    fallback_url: str | None = "",
) -> str:
    """Content-Disposition 헤더 또는 URL 경로에서 파일명을 추출합니다."""
    if headers:
        cd = headers.get("Content-Disposition") or headers.get(
            "content-disposition", ""
        )
        if cd:
            # RFC 5987/6266: filename*=UTF-8''...
            star_match = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd)
            if star_match:
                try:
                    return unquote(star_match.group(1).strip().strip("\"'"))
                except Exception:
                    pass

            # filename="..." or filename=...
            name_match = re.search(
                r'filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;\s]+)', cd
            )
            if name_match:
                raw = name_match.group(1) or name_match.group(2)
                if raw:
                    try:
                        return unquote(raw.strip().strip("\"'"))
                    except Exception:
                        return raw.strip().strip("\"'")

    if fallback_url:
        parsed = urlparse(fallback_url)
        path = unquote(parsed.path or "")
        base = os.path.basename(path)
        if base and "." in base:
            return base

    return ""


def get_unique_filename(dest_dir: str, filename: str) -> str:
    """대상 디렉토리에 같은 이름의 파일이 이미 있을 경우 (1), (2) 형태로 유니크한 이름을 반환합니다."""
    base_name, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(dest_dir, candidate)):
        candidate = f"{base_name} ({counter}){ext}"
        counter += 1
    return candidate


def format_bytes(num_bytes: int | float | None) -> str:
    """바이트 수를 사람이 읽기 쉬운 단위(B, KB, MB, GB, TB)로 변환합니다."""
    if num_bytes is None or num_bytes < 0:
        return "알 수 없음"
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(num_bytes)
    for unit in units:
        if val < 1024.0 or unit == units[-1]:
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} PB"


def format_speed(bytes_per_sec: float | None) -> str:
    """초당 바이트 속도를 문자열로 변환합니다."""
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "0 KB/s"
    return f"{format_bytes(bytes_per_sec)}/s"


def format_eta(seconds: float | None) -> str:
    """남은 시간(초)을 HH:MM:SS 또는 MM:SS로 변환합니다."""
    if seconds is None or seconds < 0 or seconds > 86400 * 30:
        return "--:--"
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _parse_content_range(header_val: str):
    """Content-Range 헤더를 파싱합니다. bytes start-end/total"""
    match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", header_val or "")
    if not match:
        return None
    start, end, total = match.groups()
    start_val = int(start)
    end_val = int(end)
    total_val = None if total == "*" else int(total)
    if end_val < start_val or (total_val is not None and end_val >= total_val):
        return None
    return start_val, end_val, total_val


def _validate_file_response(response, url):
    """Validate every response, including replacements after a range failure."""
    if response.status_code not in (200, 206):
        raise ValueError(f"Unexpected download response: HTTP {response.status_code}")
    length = response.headers.get("Content-Length")
    if length is not None and not str(length).isdigit():
        raise ValueError("Invalid download Content-Length")
    content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    explicit_html = urlparse(url).path.lower().endswith((".html", ".htm"))
    if content_type in ("text/html", "application/xhtml+xml") and not explicit_html:
        raise ValueError("다운로드 링크가 아닌 웹페이지(HTML)가 수신되었습니다.")
    return content_type, explicit_html


class DownloadTask:
    """단일 다운로드를 수행하고 실시간 이벤트를 콜백으로 전달하는 태스크."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: str = "",
        headers: dict | None = None,
        cookies: str | dict | None = None,
        referer: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_callback=None,
        status_callback=None,
        partial_id: str = "",
        method: str = "GET",
        post_data: dict | None = None,
        max_retries: int = 5,
        retry_delay_base: float = 0.5,
    ):
        self.partial_id = re.sub(r"[^a-zA-Z0-9_-]", "", partial_id)
        self.url = url
        self.dest_dir = dest_dir
        self.filename = sanitize_filename(filename) if filename else ""
        self.headers = dict(headers or {})
        self.cookies = cookies or ""
        self.referer = referer or ""
        self.chunk_size = chunk_size
        self.progress_callback = safe_callback(progress_callback)
        self.status_callback = safe_callback(status_callback)
        self.method = (method or "GET").upper()
        self.post_data = post_data
        self.max_retries = max(0, int(max_retries))
        self.retry_delay_base = max(0.0, float(retry_delay_base))

        self._is_stopped = False
        self._is_paused = False
        self.final_file_path = ""

    def stop(self):
        """다운로드를 중지/취소합니다."""
        self._is_paused = False
        self._is_stopped = True

    def pause(self):
        """다운로드를 일시 중지합니다 (이어받기 파일 보존)."""
        self._is_paused = True
        self._is_stopped = True

    def run(self) -> dict:
        """다운로드를 시작하고 완료 시 결과 딕셔너리를 반환합니다."""
        os.makedirs(self.dest_dir, exist_ok=True)

        req_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Encoding": "identity",
        }
        req_headers.update(self.headers)
        if self.referer:
            req_headers["Referer"] = self.referer
        if self.cookies:
            if isinstance(self.cookies, dict):
                req_headers["Cookie"] = "; ".join(
                    f"{k}={v}" for k, v in self.cookies.items()
                )
            else:
                req_headers["Cookie"] = self.cookies

        # 1. 요청 세션 및 재개할 임시 파일 확인
        session = requests.Session()
        session.headers.update({k: v for k, v in req_headers.items() if k.lower() != "range"})

        def _request_stream(req_h: dict):
            if self.method == "POST":
                return session.post(
                    self.url,
                    data=self.post_data,
                    headers=req_h,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
            return session.get(
                self.url,
                headers=req_h,
                stream=True,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )

        # A fresh GET already supplies filename/size headers. Only probe when
        # an unnamed resumed task may need to locate a previously named partial.
        partial_suffix = f".{self.partial_id}.part" if self.partial_id else ".part"
        needs_resume_probe = not self.filename and bool(
            glob.glob(os.path.join(glob.escape(self.dest_dir), "*" + partial_suffix))
        )
        if needs_resume_probe and self.method == "GET":
            try:
                head_resp = session.head(
                    self.url,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                )
                if head_resp.status_code in (405, 501):  # HEAD 미지원 서버
                    head_resp.close()
                    head_resp = session.get(
                        self.url,
                        timeout=REQUEST_TIMEOUT,
                        allow_redirects=True,
                        stream=True,
                    )
                discovered = extract_filename_from_headers(
                    head_resp.headers, head_resp.url or self.url
                )
                if discovered:
                    self.filename = sanitize_filename(discovered)
                head_resp.close()
            except Exception as e:
                logger.warning(f"HEAD 요청 실패, URL에서 파일명 추정: {e}")

        if not self.filename:
            parsed = urlparse(self.url)
            self.filename = sanitize_filename(
                os.path.basename(parsed.path) or "download.dat"
            )

        # 대상 파일명 및 .part 임시 파일 경로 결정
        dest_filename = self.filename
        target_path = os.path.join(self.dest_dir, dest_filename)
        part_path = target_path + (
            f".{self.partial_id}.part" if self.partial_id else ".part"
        )
        # 2. 스트리밍 다운로드 및 자동 이어받기 재시도 루프
        MAX_RETRIES = self.max_retries
        stream_retry_count = 0
        total_bytes = None
        downloaded_bytes = 0
        header_resolved = False
        resp = None

        try:
            while not self._is_stopped:
                resp = None
                is_partial = False
                attempt_start_bytes = (
                    os.path.getsize(part_path) if os.path.exists(part_path) else 0
                )
                existing_size = attempt_start_bytes
                downloaded_bytes = existing_size

                # 이미 전체 파일 크기만큼 다운로드되어 있다면 완료로 진행
                if total_bytes is not None and existing_size >= total_bytes:
                    break

                if existing_size > 0:
                    req_headers["Range"] = f"bytes={existing_size}-"
                    logger.info(f"이어받기 시도: {existing_size} bytes ({dest_filename})")
                else:
                    req_headers.pop("Range", None)

                try:
                    resp = _request_stream(req_headers)

                    # 서버가 416 Range Not Satisfiable 반환 시 처음부터 재시도
                    if resp.status_code == 416 and existing_size > 0:
                        logger.warning("Range 416 오류: 처음부터 다시 다운로드합니다.")
                        resp.close()
                        req_headers.pop("Range", None)
                        existing_size = 0
                        attempt_start_bytes = 0
                        downloaded_bytes = 0
                        resp = _request_stream(req_headers)

                    resp.raise_for_status()

                    ct, is_explicit_html = _validate_file_response(resp, self.url)

                    # 첫 번째 요청 시 응답 헤더에서 더 정확한 파일명이 있으면 업데이트
                    if not header_resolved:
                        header_filename = extract_filename_from_headers(
                            resp.headers, None if self.filename else resp.url
                        )
                        if (
                            header_filename
                            and sanitize_filename(header_filename) != self.filename
                        ):
                            new_sanitized = sanitize_filename(header_filename)
                            new_target = os.path.join(self.dest_dir, new_sanitized)
                            new_part = new_target + (
                                f".{self.partial_id}.part" if self.partial_id else ".part"
                            )
                            if not os.path.exists(part_path) or existing_size == 0:
                                self.filename = new_sanitized
                                dest_filename = self.filename
                                target_path = new_target
                                part_path = new_part

                        # 확장자가 없는 파일명인 경우 Content-Type 헤더로 확장자 보완
                        if "." not in self.filename and existing_size == 0:
                            mime_ext = _MIME_MAP.get(ct) or mimetypes.guess_extension(ct)
                            if mime_ext:
                                self.filename = f"{self.filename}{mime_ext}"
                                dest_filename = self.filename
                                target_path = os.path.join(self.dest_dir, dest_filename)
                                part_path = target_path + (
                                    f".{self.partial_id}.part"
                                    if self.partial_id
                                    else ".part"
                                )
                        header_resolved = True

                    # 3. 이어받기 성공 여부 확인
                    is_partial = resp.status_code == 206
                    if is_partial:
                        cr = _parse_content_range(resp.headers.get("Content-Range", ""))
                        if not cr or cr[0] != existing_size:
                            logger.warning("서버 Content-Range 불일치로 처음부터 재시도합니다.")
                            resp.close()
                            req_headers.pop("Range", None)
                            existing_size = 0
                            attempt_start_bytes = 0
                            downloaded_bytes = 0
                            resp = _request_stream(req_headers)
                            resp.raise_for_status()
                            if resp.status_code == 206:
                                retry_range = _parse_content_range(
                                    resp.headers.get("Content-Range", "")
                                )
                                if not retry_range or retry_range[0] != 0:
                                    raise ValueError(
                                        "Invalid Content-Range on full download retry"
                                    )
                            is_partial = resp.status_code == 206

                    ct, is_explicit_html = _validate_file_response(resp, self.url)
                    if is_partial:
                        content_range = _parse_content_range(resp.headers.get("Content-Range", ""))
                        if not content_range or content_range[2] is None:
                            raise ValueError("Cannot verify partial response total size")
                        length = resp.headers.get("Content-Length")
                        if length and (not length.isdigit() or int(length) != content_range[1] - content_range[0] + 1):
                            raise ValueError("Content-Length does not match Content-Range")
                        if total_bytes is not None and total_bytes != content_range[2]:
                            raise ValueError("Remote file size changed during resume")

                    if is_partial:
                        file_mode = "ab" if existing_size > 0 else "wb"
                        downloaded_bytes = existing_size
                        cr = _parse_content_range(resp.headers.get("Content-Range", ""))
                        if total_bytes is None:
                            total_bytes = cr[2] if cr else None
                            if total_bytes is None:
                                cl = resp.headers.get("Content-Length")
                                if cl and cl.isdigit():
                                    total_bytes = int(cl) + existing_size
                    else:
                        if existing_size > 0:
                            logger.warning("서버가 이어받기(Range)를 지원하지 않아 처음부터 다시 다운로드합니다.")
                        file_mode = "wb"
                        downloaded_bytes = 0
                        attempt_start_bytes = 0
                        cl = resp.headers.get("Content-Length")
                        total_bytes = int(cl) if cl and cl.isdigit() else None

                    # 4. 스트리밍 다운로드 루프
                    response_bytes = 0
                    response_limit = (
                        content_range[1] - content_range[0] + 1 if is_partial else total_bytes
                    )
                    last_report_time = time.time()
                    speed_calc_start = last_report_time
                    bytes_since_calc = 0
                    current_speed = 0.0

                    with open(part_path, file_mode) as f:
                        for chunk in resp.iter_content(chunk_size=self.chunk_size):
                            if self._is_stopped:
                                resp.close()
                                session.close()
                                if self._is_paused:
                                    logger.info(f"다운로드 일시중지됨: {dest_filename}")
                                    return {
                                        "success": False,
                                        "status": "paused",
                                        "message": "일시중지되었습니다.",
                                        "downloaded_bytes": downloaded_bytes,
                                        "total_bytes": total_bytes,
                                    }
                                else:
                                    logger.info(f"다운로드 취소됨: {dest_filename}")
                                    f.close()
                                    if os.path.exists(part_path):
                                        try:
                                            os.remove(part_path)
                                        except Exception:
                                            pass
                                    return {
                                        "success": False,
                                        "status": "cancelled",
                                        "message": "다운로드가 취소되었습니다.",
                                    }

                            if not chunk:
                                continue

                            # 첫 번째 청크에서 HTML 태그 및 Cloudflare 보안 페이지 감지
                            if downloaded_bytes == 0:
                                prefix = chunk[:512].lstrip().lower()
                                if prefix.startswith((b"<!doctype html", b"<html")):
                                    is_cf = (
                                        b"challenges.cloudflare.com" in chunk
                                        or b"just a moment..." in chunk.lower()
                                        or b"cf-browser-verification" in chunk.lower()
                                    )
                                    if not is_explicit_html or is_cf:
                                        resp.close()
                                        raise ValueError(
                                            "다운로드 대상이 웹페이지(HTML) 또는 보안 확인(Cloudflare) 페이지입니다. 직링크를 확인해주세요."
                                        )

                            chunk_len = len(chunk)
                            response_bytes += chunk_len
                            if response_limit is not None and response_bytes > response_limit:
                                raise ValueError("Download response exceeds declared length")
                            f.write(chunk)
                            downloaded_bytes += chunk_len
                            bytes_since_calc += chunk_len

                            now = time.time()
                            elapsed = now - speed_calc_start
                            if elapsed >= 0.5:
                                current_speed = bytes_since_calc / elapsed
                                speed_calc_start = now
                                bytes_since_calc = 0

                            if now - last_report_time >= 0.3:
                                last_report_time = now
                                if self.progress_callback:
                                    percent = (
                                        (downloaded_bytes / total_bytes * 100.0)
                                        if total_bytes and total_bytes > 0
                                        else 0.0
                                    )
                                    remaining_sec = (
                                        (total_bytes - downloaded_bytes) / current_speed
                                        if (total_bytes and current_speed > 0)
                                        else None
                                    )
                                    self.progress_callback(
                                        downloaded_bytes=downloaded_bytes,
                                        total_bytes=total_bytes,
                                        speed_bps=current_speed,
                                        progress=percent,
                                        eta_seconds=remaining_sec,
                                    )

                    if self._is_stopped:
                        if not self._is_paused and os.path.exists(part_path):
                            os.remove(part_path)
                        return {
                            "success": False,
                            "status": "paused" if self._is_paused else "cancelled",
                        }

                    if (response_limit is not None and response_bytes != response_limit) or (
                        total_bytes is not None and downloaded_bytes != total_bytes
                    ):
                        raise ValueError(
                            f"Incomplete download: expected {total_bytes}, received {downloaded_bytes}"
                        )

                    # 정상적으로 스트리밍 완료되었으므로 재시도 루프 탈출
                    resp.close()
                    break

                except Exception as stream_err:
                    if resp is not None:
                        try:
                            resp.close()
                        except Exception:
                            pass

                    if self._is_stopped:
                        session.close()
                        return {
                            "success": False,
                            "status": "paused" if self._is_paused else "cancelled",
                        }

                    # 영구적인 오류는 재시도 없이 상위로 전달
                    is_html_error = (
                        isinstance(stream_err, ValueError)
                        and "웹페이지(HTML)" in str(stream_err)
                    )
                    if is_html_error:
                        session.close()
                        raise stream_err

                    if isinstance(stream_err, requests.HTTPError):
                        code = getattr(stream_err.response, "status_code", 0)
                        if code in (401, 403, 404, 410):
                            session.close()
                            raise stream_err

                    # 이번 시도에서 파일 크기가 증가(진전)했는지 확인
                    curr_part_size = (
                        os.path.getsize(part_path)
                        if os.path.exists(part_path)
                        else downloaded_bytes
                    )
                    bytes_gained = curr_part_size - attempt_start_bytes
                    if is_partial and bytes_gained > 0:
                        stream_retry_count = 0
                        logger.info(
                            f"이어받기 진행 중 {bytes_gained} bytes 수신됨. 재시도 카운트 초기화."
                        )

                    stream_retry_count += 1
                    if stream_retry_count > MAX_RETRIES:
                        logger.error(
                            f"최대 연속 재시도 횟수({MAX_RETRIES}회) 초과로 다운로드 실패: {stream_err}"
                        )
                        session.close()
                        raise stream_err

                    wait_sec = min(self.retry_delay_base * (0.5 * stream_retry_count), 5.0)
                    logger.warning(
                        f"다운로드 중단 발생 ({stream_err}). {wait_sec:.1f}초 후 이어받기 재시도 ({stream_retry_count}/{MAX_RETRIES})..."
                    )
                    if self.status_callback:
                        try:
                            self.status_callback(
                                f"연결 끊김 감지: {wait_sec:.0f}초 후 이어받기 재시도 ({stream_retry_count}/{MAX_RETRIES})"
                            )
                        except Exception:
                            pass

                    try:
                        session.close()
                    except Exception:
                        pass
                    session = requests.Session()
                    session.headers.update({k: v for k, v in req_headers.items() if k.lower() != "range"})

                    if wait_sec > 0:
                        steps = int(wait_sec / 0.1)
                        for _ in range(max(1, steps)):
                            if self._is_stopped:
                                break
                            time.sleep(0.1)

                    if self._is_stopped:
                        session.close()
                        return {
                            "success": False,
                            "status": "paused" if self._is_paused else "cancelled",
                        }

            if self._is_stopped:
                if not self._is_paused and os.path.exists(part_path):
                    os.remove(part_path)
                return {"success": False,
                        "status": "paused" if self._is_paused else "cancelled"}

            # 5. 다운로드 완료 후 .part -> 최종 파일명으로 rename
            if resp is not None:
                resp.close()
            session.close()

            # 파일명 충돌 방지
            with _FINALIZE_LOCK:
                unique_name = get_unique_filename(self.dest_dir, dest_filename)
                final_target_path = os.path.join(self.dest_dir, unique_name)
                os.replace(part_path, final_target_path)
            self.final_file_path = final_target_path
            self.filename = unique_name

            if self.progress_callback:
                self.progress_callback(
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=downloaded_bytes,
                    speed_bps=0.0,
                    progress=100.0,
                    eta_seconds=0,
                )

            logger.info(f"다운로드 성공 완료: {final_target_path}")
            return {
                "success": True,
                "status": "completed",
                "filename": self.filename,
                "file_path": final_target_path,
                "total_bytes": downloaded_bytes,
            }

        except Exception as e:
            logger.exception(f"다운로드 중 오류 발생: {e}")
            if resp is not None:
                resp.close()
            session.close()
            actual_downloaded = (
                os.path.getsize(part_path)
                if (part_path and os.path.exists(part_path))
                else downloaded_bytes
            )
            # 웹페이지(HTML) 오인 또는 다운로드된 데이터가 없는(0바이트) 실패인 경우 불필요한 .part 파일 정리
            is_html_error = isinstance(e, ValueError) and "웹페이지(HTML)" in str(e)
            if (
                part_path
                and os.path.exists(part_path)
                and (is_html_error or actual_downloaded == 0)
            ):
                try:
                    os.remove(part_path)
                except Exception:
                    pass
                actual_downloaded = 0

            return {
                "success": False,
                "status": "failed",
                "error": str(e),
                "downloaded_bytes": actual_downloaded,
                "total_bytes": total_bytes,
            }

        finally:
            if resp is not None:
                resp.close()
            session.close()
