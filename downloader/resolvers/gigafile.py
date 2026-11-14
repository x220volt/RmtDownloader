"""기가파일 (GigaFile / ギガファイル便) 링크 리졸버."""

import html
import json
import logging
import os
import re
import secrets
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup
import requests

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

_FILE_ID_RE = re.compile(r"([0-9]{4}-[a-zA-Z0-9]+)")
_MATOMETE_DL_RE = re.compile(
    r"download\(\s*\d+\s*,\s*['\"]([0-9]{4}-[a-zA-Z0-9]+)['\"](?:\s*,\s*(true|false))?"
)
_SINGLE_DL_RE = re.compile(
    r"download\(\s*['\"]([0-9]{4}-[a-zA-Z0-9]+)['\"](?:\s*,\s*(true|false))?"
)
_ZIP_DL_RE = re.compile(
    r"download_zip\(\s*['\"]([0-9]{4}-[a-zA-Z0-9]+)['\"](?:\s*,\s*(true|false))?"
)


def _is_valid_display_filename(raw_name: str) -> str:
    """마스킹(`*`)이나 로딩 플레이스홀더가 아닌 실제 표시 파일명만 정규화해 반환합니다."""
    cleaned = html.unescape(raw_name).strip()
    if not cleaned or "*" in cleaned or "ファイル名表示中" in cleaned:
        return ""
    return cleaned


def _parse_var_files(page_html: str) -> list[dict[str, Any]]:
    """번들 페이지의 `var files = [...]` 자바스크립트 배열에서 파일 메타데이터를 추출합니다."""
    m_files = re.search(r"\bvar\s+files\s*=\s*", page_html)
    if not m_files:
        return []

    pos = m_files.end()
    while pos < len(page_html) and page_html[pos].isspace():
        pos += 1
    if pos >= len(page_html) or page_html[pos] != "[":
        return []

    parsed_entries: list[dict[str, Any]] = []
    try:
        decoded, _ = json.JSONDecoder().raw_decode(page_html, pos)
        if isinstance(decoded, list):
            for entry in decoded:
                if not isinstance(entry, dict):
                    continue
                fid = str(entry.get("file") or "").strip()
                if not fid:
                    continue
                try:
                    size_val = int(entry.get("size") or 0)
                except (TypeError, ValueError):
                    size_val = 0
                parsed_entries.append({"file_id": fid, "size": size_val})
            return parsed_entries
    except Exception:
        pass

    end_match = re.search(r"\];", page_html[pos : pos + 65536])
    js_slice = (
        page_html[pos : pos + end_match.end()]
        if end_match
        else page_html[pos : pos + 32768]
    )
    for obj_m in re.finditer(
        r"\{[^{}]*?['\"]?file['\"]?\s*:\s*['\"]([0-9]{4}-[a-zA-Z0-9]+)['\"][^{}]*?\}",
        js_slice,
    ):
        fid = obj_m.group(1).strip()
        size_m = re.search(r"['\"]?size['\"]?\s*:\s*([0-9]+)", obj_m.group(0))
        size_val = int(size_m.group(1)) if size_m else 0
        parsed_entries.append({"file_id": fid, "size": size_val})
    return parsed_entries


def _extract_bundle_files(
    page_html: str, soup: BeautifulSoup | None = None
) -> list[dict[str, Any]]:
    """다중 파일 묶음(`matomete`) 페이지에서 개별 파일 ID, 크기, 암호 여부, 표시 이름을 추출합니다."""
    if soup is None:
        soup = BeautifulSoup(page_html, "html.parser")

    ordered: dict[str, dict[str, Any]] = {}

    def _ensure_item(fid: str) -> dict[str, Any]:
        if fid not in ordered:
            ordered[fid] = {
                "file_id": fid,
                "size": 0,
                "dlkey_exists": None,
                "filename": "",
            }
        return ordered[fid]

    for entry in _parse_var_files(page_html):
        item = _ensure_item(entry["file_id"])
        if entry["size"] > 0:
            item["size"] = entry["size"]

    for block in soup.select(".matomete_file"):
        onclick_text = ""
        dl_btn = block.select_one(".download_panel_btn_dl")
        if dl_btn:
            raw_onclick = dl_btn.get("onclick")
            if isinstance(raw_onclick, str):
                onclick_text = raw_onclick
        if not onclick_text:
            for tag in block.select("[onclick]"):
                raw_onclick = tag.get("onclick")
                if isinstance(raw_onclick, str) and "download(" in raw_onclick:
                    onclick_text = raw_onclick
                    break
        dl_m = _MATOMETE_DL_RE.search(onclick_text)
        if not dl_m:
            continue

        fid = dl_m.group(1).strip()
        item = _ensure_item(fid)
        if dl_m.group(2) is not None:
            item["dlkey_exists"] = dl_m.group(2) == "true"

        name_el = block.select_one(".matomete_file_name") or block.select_one(
            ".matomete_file_info > span:nth-child(2)"
        )
        if name_el:
            display_name = _is_valid_display_filename(name_el.get_text(" ", strip=True))
            if display_name:
                item["filename"] = display_name

    for dl_m in _MATOMETE_DL_RE.finditer(page_html):
        fid = dl_m.group(1).strip()
        item = _ensure_item(fid)
        if item["dlkey_exists"] is None and dl_m.group(2) is not None:
            item["dlkey_exists"] = dl_m.group(2) == "true"

    return list(ordered.values())


def _extract_zip_filename(soup: BeautifulSoup) -> str:
    """번들 페이지의 `#matomete_zip_filename` 텍스트를 추출합니다."""
    zip_el = soup.select_one("#matomete_zip_filename")
    if not zip_el:
        return ""
    return _is_valid_display_filename(zip_el.get_text(" ", strip=True))


def _has_enabled_dlkey_input(
    soup: BeautifulSoup,
    *,
    input_ids: tuple[str, ...] = ("dlkey",),
    input_classes: tuple[str, ...] = (),
) -> bool:
    """HTML 내에 활성화된(disabled가 없는) 다운로드 키 입력 필드가 있는지 확인합니다."""
    for inp in soup.find_all("input"):
        inp_id = str(inp.get("id") or "").strip()
        raw_classes = inp.get("class")
        inp_classes = (
            [str(c).strip() for c in raw_classes]
            if isinstance(raw_classes, list)
            else ([str(raw_classes).strip()] if raw_classes else [])
        )
        matched = (inp_id in input_ids) or any(
            cls in input_classes for cls in inp_classes
        )
        if matched and not inp.has_attr("disabled"):
            return True
    return False


class GigaFileResolver(BaseResolver):
    """GigaFile(gigafile.nu) 대용량 파일 다운로드 링크 리졸버."""

    name = "GigaFile"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            netloc = (parsed.hostname or "").lower()
            return (
                netloc in ("gigafile.nu", "xgf.nu")
                or netloc.endswith(".gigafile.nu")
                or netloc.endswith(".xgf.nu")
            )
        except Exception:
            return False

    def _normalize_target_url(
        self, url: str, parsed_url, qs: dict[str, list[str]], opts: dict[str, Any]
    ) -> tuple[str, str, str, bool]:
        """직접 엔드포인트(`download.php` / `dl_zip.php`) URL인 경우 안내 페이지 URL과 대상 파일 ID를 추출합니다."""
        path_lower = (parsed_url.path or "").lower().rstrip("/")
        is_download_php = path_lower.endswith("/download.php")
        is_dl_zip_php = path_lower.endswith("/dl_zip.php")
        if not (is_download_php or is_dl_zip_php):
            return url, "", "", False

        q_file_raw = (qs.get("file") or [""])[0].strip()
        q_file_m = _FILE_ID_RE.search(q_file_raw)
        query_file_id = q_file_m.group(1) if q_file_m else q_file_raw
        requested_file_id = query_file_id if (is_download_php and query_file_id) else ""

        scheme = parsed_url.scheme or "https"
        direct_file_page = (
            f"{scheme}://{parsed_url.netloc}/{query_file_id}"
            if (query_file_id and parsed_url.netloc)
            else ""
        )

        ref_url = str(opts.get("referer") or "").strip()
        ref_valid = False
        if ref_url and self.can_handle(ref_url):
            ref_parsed = urlparse(ref_url)
            ref_path_lower = (ref_parsed.path or "").lower().rstrip("/")
            if not ref_path_lower.endswith(("/download.php", "/dl_zip.php")) and (
                _FILE_ID_RE.search(ref_path_lower)
            ):
                ref_valid = True

        if ref_valid:
            fallback_url = (
                direct_file_page
                if (direct_file_page and direct_file_page != ref_url)
                else ""
            )
            return ref_url, fallback_url, requested_file_id, is_dl_zip_php
        if direct_file_page:
            return direct_file_page, "", requested_file_id, is_dl_zip_php
        return url, "", requested_file_id, is_dl_zip_php

    def _fetch_page_with_fallback(
        self, session: requests.Session, target_url: str, fallback_url: str
    ) -> tuple[str, str]:
        """안내 페이지를 조회하고 삭제/만료 여부를 확인합니다."""
        urls_to_try = [target_url]
        if fallback_url and fallback_url != target_url:
            urls_to_try.append(fallback_url)

        last_err: Exception | None = None
        for idx, page_url in enumerate(urls_to_try):
            has_more = idx < len(urls_to_try) - 1
            try:
                resp = session.get(page_url, timeout=20, allow_redirects=True)
                resp.raise_for_status()
            except Exception as e:
                last_err = e
                if has_more:
                    continue
                raise ValueError(f"기가파일 페이지 접속 실패: {e}")

            page_html = resp.text
            if (
                "ファイルは削除されました" in page_html
                or "ファイルが存在しません" in page_html
                or "ダウンロード期限が切" in page_html
            ):
                if has_more:
                    continue
                raise FileNotFoundError(
                    "기가파일(GigaFile) 링크의 파일이 삭제되었거나 다운로드 보관 기간이 만료되었습니다."
                )
            return resp.url, page_html

        raise ValueError(f"기가파일 페이지 접속 실패: {last_err}")

    def _verify_dlkey(
        self,
        session: requests.Session,
        server: str,
        file_id: str,
        final_url: str,
        source_url: str,
        opts: dict[str, Any],
        dlkey: str,
        *,
        is_zip: int = 0,
        cached_key: str = "",
    ) -> str:
        """후보 비밀번호 목록을 `check_dlkey.php`로 검증해 유효한 다운로드 키를 반환합니다."""
        from ..url_helper import get_candidate_passwords

        candidates = list(get_candidate_passwords(source_url, opts))
        for preferred in (dlkey, cached_key):
            if preferred:
                if preferred in candidates:
                    candidates.remove(preferred)
                candidates.insert(0, preferred)

        if not candidates:
            raise ValueError(
                "이 기가파일(GigaFile) 링크는 다운로드 키(비밀번호 4자리)가 필요합니다.\n"
                "웹 대시보드 ⚙️ 설정에서 공통 비밀번호를 등록하거나, 링크 뒤에 ?dlkey=비밀번호 형태로 입력해 주세요."
            )

        check_url = f"https://{server}/check_dlkey.php"
        tried_keys: list[str] = []
        for idx, cand in enumerate(candidates, 1):
            tried_keys.append(cand)
            try:
                logger.info(
                    f"기가파일 다운로드 키 확인 시도 ({idx}/{len(candidates)}): '{cand}'"
                )
                c_resp = session.get(
                    check_url,
                    params={"file": file_id, "dlkey": cand, "is_zip": is_zip},
                    headers={"Referer": final_url},
                    timeout=15,
                )
                if c_resp.ok:
                    c_data = c_resp.json()
                    status = c_data.get("status")
                    if status == 0:
                        logger.info(f"기가파일 다운로드 키 인증 성공: '{cand}'")
                        return cand
                    if status == 3:
                        raise ValueError(
                            "기가파일 비밀번호 입력 오류 횟수 초과로 일시적으로 접근이 차단되었습니다."
                        )
                    if status != 1:
                        logger.warning(
                            f"check_dlkey 응답 코드({status}), 계속 시도합니다."
                        )
            except ValueError:
                raise
            except Exception as e:
                logger.warning(f"기가파일 check_dlkey 시도 중 오류: {e}")

        tried_str = ", ".join(repr(k) for k in tried_keys)
        raise ValueError(
            f"기가파일 다운로드 키(비밀번호)가 일치하지 않습니다 (시도한 키: {tried_str}). 올바른 비밀번호를 확인해 주세요."
        )

    def _fetch_filename(
        self,
        session: requests.Session,
        dl_ajax_server: str,
        file_id: str,
        final_url: str,
        fallback_name: str,
    ) -> str:
        """AJAX API로 원본 파일명을 조회하고, 실패 시 안전한 대체 파일명을 반환합니다."""
        try:
            name_api = f"https://{dl_ajax_server}/get_uploaded_file_name_jx.php"
            name_resp = session.get(
                name_api,
                params={"file": file_id},
                headers={"Referer": final_url},
                timeout=10,
            )
            if name_resp.ok:
                name_data = name_resp.json()
                if name_data.get("status") == 0 and name_data.get("filename"):
                    filename = sanitize_filename(name_data["filename"])
                    logger.info(f"GigaFile 파일명 조회 성공: {filename}")
                    return filename
        except Exception as e:
            logger.warning(f"GigaFile 파일명 API 조회 실패: {e}")

        return sanitize_filename(fallback_name or f"gigafile_{file_id}")

    @staticmethod
    def _build_cookie_header(session: requests.Session) -> str:
        """세션 쿠키에 `prog_key` 토큰을 추가해 Cookie 헤더 문자열을 생성합니다."""
        cookie_dict = session.cookies.get_dict()
        cookie_dict["prog_key"] = secrets.token_hex(4)
        return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    def resolve(self, url: str, options: dict | None = None) -> dict:
        opts = dict(options or {})
        parsed_url = urlparse(url)

        # 1. 다운로드 키(비밀번호) 파라미터 추출
        # ?dlkey=..., ?pwd=..., ?key=... 또는 options['password']
        qs = parse_qs(parsed_url.query)
        dlkey = ""
        for k in ("dlkey", "pwd", "key", "password"):
            if k in qs and qs[k]:
                dlkey = qs[k][0].strip()
                break
        if not dlkey:
            from ..url_helper import parse_password_list

            raw_pwd = (
                opts.get("password") or opts.get("dlkey") or opts.get("pwd") or ""
            ).strip()
            parsed_pwds = parse_password_list(raw_pwd)
            dlkey = parsed_pwds[0] if parsed_pwds else ""

        target_url, fallback_page_url, requested_file_id, is_dl_zip_php = (
            self._normalize_target_url(url, parsed_url, qs, opts)
        )
        valid_opt_filename = _is_valid_display_filename(str(opts.get("filename") or ""))

        # 2. 기가파일 다운로드 안내 페이지 접속 및 세션 쿠키 수집
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,ja-JP,ja;q=0.8,en-US;q=0.7,en;q=0.6",
                }
            )

            final_url, page_html = self._fetch_page_with_fallback(
                session, target_url, fallback_page_url
            )
            parsed_final = urlparse(final_url)

            # 3. 서버 정보 및 번들(다중 파일 묶음) 구조 추출
            server_match = re.search(
                r'var\s+server\s*=\s*["\']([^"\']+)["\']', page_html
            )
            server = server_match.group(1).strip() if server_match else parsed_final.netloc

            ajax_match = re.search(
                r'var\s+dl_ajax_server\s*=\s*["\']([^"\']+)["\']', page_html
            )
            if ajax_match:
                dl_ajax_server = ajax_match.group(1).strip()
            elif "." in server:
                sub, rest = server.split(".", 1)
                dl_ajax_server = f"{sub}x.{rest}"
            else:
                dl_ajax_server = server

            soup = BeautifulSoup(page_html, "html.parser")
            bundle_files = _extract_bundle_files(page_html, soup)
            path_id_m = _FILE_ID_RE.search(parsed_final.path or "")
            bundle_or_path_id = path_id_m.group(1).strip() if path_id_m else ""
            common_headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": final_url,
                "Accept": "*/*",
            }

            # Case B: 번들/단일 파일의 직접 다운로드 URL(download.php?file=...) 재해석
            if requested_file_id:
                matched_item = next(
                    (f for f in bundle_files if f["file_id"] == requested_file_id),
                    None,
                )
                size_bytes = matched_item["size"] if matched_item else 0
                if size_bytes == 0:
                    size_m = re.search(r"var\s+size\s*=\s*([0-9]+);", page_html)
                    if size_m:
                        try:
                            size_bytes = int(size_m.group(1))
                        except ValueError:
                            size_bytes = 0

                if matched_item and matched_item["dlkey_exists"] is not None:
                    is_dlkey_required = bool(matched_item["dlkey_exists"])
                else:
                    esc_id = re.escape(requested_file_id)
                    is_dlkey_required = bool(
                        re.search(
                            rf"download\((?:\s*\d+\s*,)?\s*['\"]{esc_id}['\"]\s*,\s*true",
                            page_html,
                        )
                        or _has_enabled_dlkey_input(
                            soup,
                            input_ids=("dlkey",),
                            input_classes=("matomete_dlkey",),
                        )
                    )

                active_key = dlkey
                if is_dlkey_required:
                    active_key = self._verify_dlkey(
                        session,
                        server,
                        requested_file_id,
                        final_url,
                        url,
                        opts,
                        dlkey,
                        is_zip=0,
                    )

                html_fallback = ""
                if matched_item and matched_item["filename"]:
                    html_fallback = matched_item["filename"]
                else:
                    dl_el = soup.select_one("#dl")
                    if dl_el:
                        html_fallback = _is_valid_display_filename(
                            dl_el.get_text(" ", strip=True)
                        )

                filename = self._fetch_filename(
                    session,
                    dl_ajax_server,
                    requested_file_id,
                    final_url,
                    valid_opt_filename or html_fallback or f"gigafile_{requested_file_id}",
                )
                cookie_str = self._build_cookie_header(session)
                download_url = f"https://{server}/download.php?file={requested_file_id}"
                if active_key:
                    download_url += f"&dlkey={quote(active_key)}"

                return {
                    "url": download_url,
                    "filename": filename,
                    "headers": common_headers,
                    "cookies": cookie_str,
                    "size": size_bytes,
                    "source_service": "GigaFile",
                }

            zip_match = _ZIP_DL_RE.search(page_html)
            is_matomete_dom = bool(soup.select_one("#contents_matomete")) or bool(
                zip_match
            )

            # Case A: 다중 파일 묶음(matomete) 페이지 -> 개별 파일 목록 해석 및 자동 큐잉
            if bundle_files and not is_dl_zip_php:
                resolved_items: list[dict[str, Any]] = []
                cached_key = ""
                fallback_key_required = _has_enabled_dlkey_input(
                    soup,
                    input_ids=("dlkey",),
                    input_classes=("matomete_dlkey",),
                )

                for item in bundle_files:
                    child_id = item["file_id"]
                    child_needs_key = (
                        bool(item["dlkey_exists"])
                        if item["dlkey_exists"] is not None
                        else fallback_key_required
                    )
                    child_key = dlkey
                    if child_needs_key:
                        child_key = self._verify_dlkey(
                            session,
                            server,
                            child_id,
                            final_url,
                            url,
                            opts,
                            dlkey,
                            is_zip=0,
                            cached_key=cached_key,
                        )
                        cached_key = child_key

                    child_filename = self._fetch_filename(
                        session,
                        dl_ajax_server,
                        child_id,
                        final_url,
                        item["filename"] or f"gigafile_{child_id}",
                    )
                    child_url = f"https://{server}/download.php?file={child_id}"
                    if child_key:
                        child_url += f"&dlkey={quote(child_key)}"

                    resolved_items.append(
                        {
                            "url": child_url,
                            "name": child_filename,
                            "size": item["size"],
                            "referer": final_url,
                        }
                    )

                cookie_str = self._build_cookie_header(session)
                file_items = [
                    {
                        **entry,
                        "headers": dict(common_headers),
                        "cookies": cookie_str,
                    }
                    for entry in resolved_items
                ]

                first = file_items[0]
                logger.info(
                    f"GigaFile 다중 파일 묶음 감지: 총 {len(file_items)}개 파일 ({first['name']} 등)"
                )
                result: dict[str, Any] = {
                    "url": first["url"],
                    "filename": first["name"],
                    "headers": common_headers,
                    "cookies": cookie_str,
                    "size": first["size"],
                    "source_service": "GigaFile",
                }
                if len(file_items) > 1:
                    result["files"] = file_items
                zip_display_name = _extract_zip_filename(soup)
                if zip_display_name:
                    zip_stem = (
                        os.path.splitext(zip_display_name)[0]
                        if zip_display_name.lower().endswith(".zip")
                        else zip_display_name
                    )
                    if zip_stem.strip():
                        result["subfolder"] = sanitize_filename(zip_stem.strip())
                return result

            # Case C: 개별 파일 목록이 없는 dl_zip.php 전용 묶음 페이지 또는 dl_zip.php 직접 요청
            if is_dl_zip_php or (is_matomete_dom and not bundle_files):
                zip_id = (
                    (zip_match.group(1).strip() if zip_match else "")
                    or (qs.get("file") or [""])[0].strip()
                    or bundle_or_path_id
                )
                if not zip_id:
                    raise ValueError(
                        "기가파일 묶음 링크에서 파일 고유 ID를 찾을 수 없습니다. 올바른 GigaFile 링크인지 확인해 주세요."
                    )

                is_zip_dlkey_required = bool(
                    (zip_match and zip_match.group(2) == "true")
                    or _has_enabled_dlkey_input(
                        soup,
                        input_ids=("matomete_zip_dlkey", "dlkey"),
                    )
                )
                active_key = dlkey
                if is_zip_dlkey_required:
                    active_key = self._verify_dlkey(
                        session,
                        server,
                        zip_id,
                        final_url,
                        url,
                        opts,
                        dlkey,
                        is_zip=1,
                    )

                zip_name = (
                    _extract_zip_filename(soup)
                    or valid_opt_filename
                    or f"gigafile_{zip_id}.zip"
                )
                if not zip_name.lower().endswith(".zip"):
                    zip_name += ".zip"
                zip_name = sanitize_filename(zip_name)

                total_size = sum(f["size"] for f in bundle_files)
                cookie_str = self._build_cookie_header(session)
                zip_url = f"https://{server}/dl_zip.php?file={zip_id}"
                if active_key:
                    zip_url += f"&dlkey={quote(active_key)}"

                return {
                    "url": zip_url,
                    "filename": zip_name,
                    "headers": common_headers,
                    "cookies": cookie_str,
                    "size": total_size,
                    "source_service": "GigaFile",
                }

            # Case D: 일반 단일 파일 페이지
            file_id = ""
            file_match = re.search(r'var\s+file\s*=\s*["\']([^"\']+)["\']', page_html)
            if file_match:
                file_id = file_match.group(1).strip()
            elif bundle_or_path_id:
                file_id = bundle_or_path_id
            else:
                onclick_match = _SINGLE_DL_RE.search(page_html)
                if onclick_match:
                    file_id = onclick_match.group(1).strip()

            if not file_id:
                raise ValueError(
                    "기가파일 링크에서 파일 고유 ID를 찾을 수 없습니다. 올바른 GigaFile 링크인지 확인해 주세요."
                )

            size_bytes = 0
            size_match = re.search(r"var\s+size\s*=\s*([0-9]+);", page_html)
            if size_match:
                try:
                    size_bytes = int(size_match.group(1))
                except ValueError:
                    size_bytes = 0

            # 4. 다운로드 키(비밀번호) 검증
            # onclick="download('...', true, ...)" 이거나 disabled가 없는 dlkey 인풋이 있는 경우
            single_dl_m = _SINGLE_DL_RE.search(page_html)
            is_dlkey_required = bool(
                (single_dl_m and single_dl_m.group(2) == "true")
                or _has_enabled_dlkey_input(soup, input_ids=("dlkey",))
            )

            if is_dlkey_required:
                dlkey = self._verify_dlkey(
                    session,
                    server,
                    file_id,
                    final_url,
                    url,
                    opts,
                    dlkey,
                    is_zip=0,
                )

            # 5. 원본 파일명 추출 (AJAX API 호출)
            dl_el = soup.select_one("#dl")
            html_dl_name = (
                _is_valid_display_filename(dl_el.get_text(" ", strip=True))
                if dl_el
                else ""
            )
            filename = self._fetch_filename(
                session,
                dl_ajax_server,
                file_id,
                final_url,
                valid_opt_filename or html_dl_name or f"gigafile_{file_id}",
            )

            # 6. 세션 쿠키에 prog_key 등록 (Gigafile 다운로드 토큰)
            cookie_str = self._build_cookie_header(session)

            # 7. 최종 다운로드 직링크 생성
            download_url = f"https://{server}/download.php?file={file_id}"
            if dlkey:
                download_url += f"&dlkey={quote(dlkey)}"

            return {
                "url": download_url,
                "filename": filename,
                "headers": common_headers,
                "cookies": cookie_str,
                "size": size_bytes,
                "source_service": "GigaFile",
            }
