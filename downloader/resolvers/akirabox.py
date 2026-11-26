"""AkiraBox (akirabox.to / akirabox.com / akirabox.net) 링크 리졸버.

Cloudflare Turnstile 보호를 DrissionPage(로컬 스텔스 브라우저) 또는 크롬 확장 프로그램을 통해
연동하고 고속 직링크를 추출합니다.
"""

import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import unquote, unquote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..browser_solver import stealth_browser_session
from ..engine import extract_filename_from_headers, sanitize_filename, sanitize_folder_name
from .base import BaseResolver

try:
    from config import load_settings
except ImportError:
    load_settings = None

logger = logging.getLogger(__name__)

AKIRABOX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25

AKIRABOX_HOST_RE = re.compile(
    r"^(?:[a-z0-9\-]+\.)*akirabox\.(?:to|com|net)$", re.I
)
FILE_ID_RE = re.compile(
    r"^/([a-zA-Z0-9_-]{8,32})(?:/file)?/?$", re.I
)


def _is_akirabox_host(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.lower().rstrip(".")
    return bool(AKIRABOX_HOST_RE.match(host))


def _extract_file_id(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        if not _is_akirabox_host(parsed.hostname):
            return None
        match = FILE_ID_RE.match(parsed.path)
        if match:
            return match.group(1)
        # /api/files/{id}/download 형태
        api_match = re.search(r"/api/files/([a-zA-Z0-9_-]{8,32})", parsed.path)
        if api_match:
            return api_match.group(1)
    except Exception:
        pass
    return None


def _format_size(size_bytes: int | float | None) -> str:
    if not size_bytes or size_bytes <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(size_bytes)
    idx = 0
    while val >= 1024.0 and idx < len(units) - 1:
        val /= 1024.0
        idx += 1
    return f"{val:.2f} {units[idx]}"


def _clean_duplicated_extension(filename: str) -> str:
    """AkiraBox 다운로드 엔드포인트에서 발생하는 .zip.zip 등의 중복 확장자를 정리합니다."""
    if not filename:
        return filename
    dup_re = re.compile(r"(\.[a-zA-Z0-9]{1,10})\1$", re.IGNORECASE)
    return dup_re.sub(r"\1", filename)


class AkiraBoxResolver(BaseResolver):
    """AkiraBox (akirabox.to / akirabox.com / akirabox.net) 다운로드 링크 리졸버."""

    name: str = "AkiraBox"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if not _is_akirabox_host(host):
                return False
            path = parsed.path or ""
            if path.startswith(("/download/", "/uploads/")):
                return True
            return bool(_extract_file_id(url))
        except Exception:
            return False

    def _fetch_metadata_from_html(self, page_url: str) -> dict:
        """페이지 HTML 또는 Next.js RSC 스트림에서 파일명, 크기, SHA256 정보를 추출합니다."""
        meta = {"filename": None, "size": None, "sha256": None, "title": None}
        try:
            resp = requests.get(
                page_url,
                headers={
                    "User-Agent": AKIRABOX_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://akirabox.to/",
                },
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code == 404:
                raise FileNotFoundError("AkiraBox 파일이 존재하지 않거나 삭제되었습니다.")
            html = resp.text
            soup = BeautifulSoup(html, "html.parser")

            # 1. OpenGraph / Twitter meta 태그 (순수 파일명이 그대로 저장되어 있어 가장 신뢰도 높음)
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                cand = og_title["content"].strip()
                cand = re.sub(r"\s*·\s*AkiraBox.*$", "", cand, flags=re.I).strip()
                if cand and cand != "Next.MetadataOutlet":
                    meta["filename"] = cand

            if not meta["filename"]:
                tw_title = soup.find("meta", attrs={"name": "twitter:title"})
                if tw_title and tw_title.get("content"):
                    cand = tw_title["content"].strip()
                    cand = re.sub(r"\s*·\s*AkiraBox.*$", "", cand, flags=re.I).strip()
                    if cand and cand != "Next.MetadataOutlet":
                        meta["filename"] = cand

            # 2. <h1> 태그 본문 파일명
            if not meta["filename"]:
                h1_el = soup.find("h1")
                if h1_el and h1_el.get_text(strip=True):
                    cand = h1_el.get_text(strip=True)
                    if cand and cand != "Next.MetadataOutlet":
                        meta["filename"] = cand

            # 3. <title> 태그 백업
            if not meta["filename"] and soup.title and soup.title.string:
                cand = re.sub(r"\s*·\s*AkiraBox.*$", "", soup.title.string.strip(), flags=re.I).strip()
                if cand and cand != "Next.MetadataOutlet":
                    meta["filename"] = cand

            # 4. Next.js 파일 메타데이터 (name, extension, size, sha256)
            next_file_m = re.search(
                r'(?:\\"|")name(?:\\"|")\s*:\s*(?:\\"|")([^\\"]+)(?:\\"|"),\s*(?:\\"|")extension(?:\\"|")\s*:\s*(?:\\"|")[^\\"]+(?:\\"|"),\s*(?:\\"|")size(?:\\"|")\s*:\s*(\d+)',
                html,
            )
            if next_file_m:
                raw_name = next_file_m.group(1)
                if r"\u" in raw_name:
                    try:
                        raw_name = raw_name.encode("utf-8").decode("unicode_escape")
                    except Exception:
                        pass
                raw_name = raw_name.replace(r"\/", "/")
                decoded_name = unquote(raw_name)
                if decoded_name and decoded_name != "Next.MetadataOutlet":
                    meta["filename"] = decoded_name
                meta["size"] = int(next_file_m.group(2))

            # 크기 백업 파싱
            if not meta.get("size"):
                size_m = re.search(r'(?:\\"|")size(?:\\"|")\s*:\s*(\d+)', html)
                if size_m:
                    meta["size"] = int(size_m.group(1))

            sha_m = re.search(
                r'(?:\\"|")sha256(?:\\"|")\s*:\s*\{[^}]*(?:\\"|")value(?:\\"|")\s*:\s*(?:\\"|")([a-fA-F0-9]{64})(?:\\"|")',
                html,
            )
            if sha_m:
                meta["sha256"] = sha_m.group(1)
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.debug(f"[AkiraBox] 메타데이터 초기 추출 예외 (무시 가능): {e}")

        return meta

    def _resolve_via_drission(self, page_url: str, timeout: int = 30) -> tuple[str, str, str]:
        """DrissionPage 스텔스 브라우저를 통해 Cloudflare Turnstile을 통과하고 직링크를 추출합니다."""
        logger.info(f"[AkiraBox] 스텔스 브라우저로 접속 시작: {page_url}")
        with stealth_browser_session(prefix="akirabox_session_") as page:
            page.get(page_url)

            page_html = page.html or ""
            if "file not found" in page_html.lower() or "404 not found" in page_html.lower():
                raise FileNotFoundError("AkiraBox 파일이 존재하지 않거나 삭제되었습니다.")

            dl_link = None
            start_time = time.time()
            while time.time() - start_time < timeout:
                time.sleep(1.0)
                # 1. #download 버튼의 href 확인
                try:
                    el = page.ele("#download", timeout=0.5)
                    if el:
                        href = el.attr("href")
                        if href and href.startswith("http") and not href.endswith("#") and not href.endswith("/file"):
                            dl_link = href
                            break
                except Exception:
                    pass

                # 2. Turnstile 체크박스가 남아있는 경우 4초 경과 시 보조 클릭
                elapsed = time.time() - start_time
                if elapsed >= 4.0:
                    try:
                        cf_iframe = page.ele('css:iframe[src*="challenges.cloudflare.com"]', timeout=0.2)
                        if cf_iframe:
                            cb = cf_iframe.ele('css:input[type="checkbox"], .ctp-checkbox-label, #challenge-stage', timeout=0.2)
                            if cb:
                                cb.click()
                    except Exception:
                        pass

            if not dl_link:
                raise ValueError("AkiraBox Turnstile 검증 시간 초과 또는 다운로드 링크 추출 실패")

            cookies = "; ".join(
                f"{c['name']}={c['value']}"
                for c in page.cookies()
                if "name" in c and "value" in c
            )
            logger.info(f"[AkiraBox] Turnstile 통과 및 직링크 획득 성공: {dl_link[:80]}...")
            return dl_link, page.title or "", cookies

    def _follow_cdn_redirect(self, direct_url: str, referer: str) -> str:
        """302 리디렉션 엔드포인트인 경우 최종 CDN 다운로드 주소로 전개합니다."""
        try:
            resp = requests.head(
                direct_url,
                headers={
                    "User-Agent": AKIRABOX_USER_AGENT,
                    "Referer": referer,
                    "Accept": "*/*",
                },
                allow_redirects=False,
                timeout=12,
            )
            if resp.status_code in (301, 302, 303, 307, 308) and "Location" in resp.headers:
                loc = resp.headers["Location"]
                logger.info(f"[AkiraBox] 직링크 302 리디렉션 확인 -> CDN 주소: {loc[:80]}...")
                return loc
        except Exception as e:
            logger.debug(f"[AkiraBox] CDN 리디렉션 확인 실패 (원래 URL 유지): {e}")
        return direct_url

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_akirabox_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 AkiraBox URL 형태입니다: {url}")

        options = options or {}
        file_id = _extract_file_id(url)
        page_url = f"https://akirabox.to/{file_id}/file" if file_id else url

        # 1. 이미 발급된 직링크이거나 옵션에 직접 전달된 경우
        direct_url = options.get("direct_url") or options.get("download_url")
        if not direct_url and parsed.path.startswith(("/download/", "/uploads/")):
            direct_url = url

        meta: dict[str, Any] = {}
        if file_id:
            try:
                meta = self._fetch_metadata_from_html(page_url)
            except Exception as e:
                logger.debug(f"[AkiraBox] 메타데이터 수집 에러: {e}")

        cookies = options.get("cookies") or ""

        # 2. 직링크가 없는 경우 스텔스 브라우저로 해결 시도
        if not direct_url:
            try:
                direct_url, browser_title, browser_cookies = self._resolve_via_drission(page_url)
                if not cookies and browser_cookies:
                    cookies = browser_cookies
            except Exception as e:
                logger.warning(f"[AkiraBox] 스텔스 브라우저 링크 추출 실패: {e}")
                filename_hint = meta.get("filename") or file_id or "파일"
                size_hint = _format_size(meta.get("size"))
                size_str = f" ({size_hint})" if size_hint else ""
                raise ValueError(
                    f"AkiraBox 파일 정보를 확인했습니다: '{filename_hint}'{size_str}.\n"
                    "Cloudflare Turnstile(봇 방지) 보안 검증이 필요합니다.\n"
                    "웹 브라우저에서 해당 페이지를 열어 체크박스를 1회 체크한 뒤,\n"
                    "원격 다운로더 크롬 확장의 [현재 탭 전송] 버튼을 누르시거나 다운로드 링크를 복사하여 추가해 주세요."
                ) from e

        # 3. 302 리디렉션 처리 (필요시)
        final_url = self._follow_cdn_redirect(direct_url, referer=page_url)

        # 4. 파일명 결정 및 중복 확장자 정리
        filename = meta.get("filename")
        if not filename or filename == "Next.MetadataOutlet":
            candidate = None
            if direct_url and "/download/" in direct_url:
                path_name = direct_url.split("?")[0].rstrip("/").split("/")[-1]
                if path_name and "." in path_name:
                    candidate = unquote_plus(path_name)
            if not candidate and final_url and "/uploads/" in final_url:
                path_name = final_url.split("?")[0].rstrip("/").split("/")[-1]
                path_name = re.sub(r"^[a-zA-Z0-9_-]{16,}-", "", path_name)
                if path_name and "." in path_name:
                    candidate = unquote_plus(path_name)
            if not candidate and final_url:
                candidate = extract_filename_from_headers(fallback_url=final_url)
            if candidate:
                filename = candidate

        if filename:
            filename = _clean_duplicated_extension(filename)
            filename = sanitize_filename(filename)

        clean_title = ""
        folder_base = ""
        if filename:
            clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", filename).strip()
            folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title

        return {
            "url": final_url,
            "filename": filename,
            "headers": {
                "User-Agent": AKIRABOX_USER_AGENT,
                "Referer": page_url,
                "Accept": "*/*",
            },
            "cookies": cookies or None,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
            "article_title": clean_title,
            "expected_size": meta.get("size"),
            "sha256": meta.get("sha256"),
        }
