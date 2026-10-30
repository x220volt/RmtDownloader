"""WoWroms (wowroms.com) 게임 ROM / ISO / 에뮬레이터 다운로드 링크 리졸버."""

import hashlib
import html
import logging
import re
import time
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20


def _is_wowroms_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("wowroms.com", "www.wowroms.com") or host.endswith(".wowroms.com")


class WowromsResolver(BaseResolver):
    """WoWroms (wowroms.com) 상세 및 다운로드 페이지를 분석하여 실시간 직링크를 발급하는 리졸버."""

    name: str = "WoWroms"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_wowroms_host(parsed.hostname):
                return False
            path = parsed.path.lower()
            return bool(
                re.search(r"/(?:roms|isos|emulators|bios)/", path)
                or "/download/" in path
                or path.endswith(".html")
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_wowroms_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 WoWroms URL 형태입니다: {url}")

        session = requests.Session()
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://wowroms.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        logger.info(f"[WoWroms] 페이지 접속 시도: {url}")
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            raise FileNotFoundError(f"WoWroms 페이지를 찾을 수 없습니다 (404): {url}")
        if not resp.ok:
            raise ValueError(
                f"WoWroms 페이지 접속 실패 (HTTP {resp.status_code}): {url}"
            )

        soup = BeautifulSoup(resp.text, "html.parser")
        current_page_url = url

        # 1. 게임 소개/상세 페이지인 경우 다운로드 링크를 찾아 다운로드 페이지로 이동
        form = soup.find("form", id="submitForm")
        if not form:
            logger.info(f"[WoWroms] 게임 상세 페이지 감지 -> 다운로드 페이지 링크 검색 중...")
            dl_link = None
            for a in soup.find_all("a", href=True):
                href_str = str(a.get("href") or "").strip()
                link_text = (a.get_text() or "").lower()
                if (
                    "download-" in href_str
                    or "/download/" in href_str
                    or ("download" in link_text and href_str.endswith(".html"))
                ):
                    dl_link = urljoin(url, href_str)
                    break

            if not dl_link:
                raise ValueError(
                    f"WoWroms 게임 페이지에서 다운로드 링크를 찾을 수 없습니다: {url}"
                )

            logger.info(f"[WoWroms] 다운로드 페이지 발견: {dl_link}")
            headers["Referer"] = url
            current_page_url = dl_link
            resp = session.get(dl_link, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"WoWroms 다운로드 페이지를 찾을 수 없습니다 (404): {dl_link}"
                )
            if not resp.ok:
                raise ValueError(
                    f"WoWroms 다운로드 페이지 접속 실패 (HTTP {resp.status_code}): {dl_link}"
                )
            soup = BeautifulSoup(resp.text, "html.parser")
            form = soup.find("form", id="submitForm")

        if not form:
            raise ValueError(
                f"WoWroms 다운로드 폼(submitForm)을 찾을 수 없습니다: {current_page_url}"
            )

        # 2. submitForm 폼 필드 추출
        form_data: dict[str, str] = {}
        for inp in form.find_all("input"):
            inp_name = inp.get("name")
            if inp_name:
                form_data[str(inp_name)] = str(inp.get("value") or "")

        raw_filename = form_data.get("file", "").strip()
        if not raw_filename:
            # title 태그나 h1에서 추정
            title_text = soup.title.string if soup.title else ""
            if title_text and "-" in title_text:
                raw_filename = title_text.split("-")[0].strip() + ".zip"
            else:
                raw_filename = "download.zip"
        filename = sanitize_filename(raw_filename)

        # 3. ajaxLinkUrl 엔드포인트 추출
        m = re.search(r"var\s+ajaxLinkUrl\s*=\s*['\"]([^'\"]+)['\"]", resp.text)
        if not m:
            raise ValueError(
                f"WoWroms 토큰 엔드포인트(ajaxLinkUrl)를 찾을 수 없습니다: {current_page_url}"
            )
        ajax_path = m.group(1).strip()
        ajax_url = urljoin(current_page_url, ajax_path)

        # 4. 실시간 토큰 생성 및 AJAX 요청
        k = str(int(time.time() * 1000))
        t = hashlib.md5(k.encode("utf-8")).hexdigest()

        ajax_headers = dict(headers)
        ajax_headers["Referer"] = current_page_url
        ajax_headers["X-Requested-With"] = "XMLHttpRequest"

        token_req_url = f"{ajax_url}?k={k}&t={t}"
        logger.info(f"[WoWroms] 직링크 토큰 발급 요청 중: {ajax_path}")
        ajax_resp = session.post(
            token_req_url, headers=ajax_headers, timeout=REQUEST_TIMEOUT
        )
        if not ajax_resp.ok:
            raise ValueError(
                f"WoWroms AJAX 토큰 발급 실패 (HTTP {ajax_resp.status_code}): {token_req_url}"
            )

        try:
            data = ajax_resp.json()
        except Exception as e:
            raise ValueError(
                f"WoWroms AJAX 응답 파싱 실패 ({e}): {ajax_resp.text[:100]}"
            )

        action_url = data.get("link")
        if not action_url:
            raise ValueError(
                f"WoWroms 다운로드 직링크 추출 실패: {ajax_resp.text[:150]}"
            )

        logger.info(
            f"[WoWroms] 직링크 발급 성공: {filename} -> {action_url[:60]}..."
        )

        return {
            "url": action_url,
            "method": "POST",
            "post_data": form_data,
            "filename": filename,
            "headers": {
                "User-Agent": USER_AGENT,
            },
            "referer": current_page_url,
            "source_service": "WoWroms",
        }
