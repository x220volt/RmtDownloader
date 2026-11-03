"""MediaFire (mediafire.com) 파일 다운로드 링크 리졸버."""

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ..browser_solver import stealth_browser_session
from ..engine import extract_filename_from_headers, sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25


def _is_mediafire_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("mediafire.com", "www.mediafire.com") or host.endswith(".mediafire.com")


class MediaFireResolver(BaseResolver):
    """MediaFire (mediafire.com)의 파일 다운로드 페이지를 분석하여 직링크를 추출하는 리졸버."""

    name: str = "MediaFire"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_mediafire_host(parsed.hostname):
                return False
            # direct download domain (download*.mediafire.com)
            if (parsed.hostname or "").lower().startswith("download"):
                return True
            path = (parsed.path or "").lower()
            return bool(
                path.startswith(("/file/", "/download/", "/view/"))
                or parsed.query.strip()
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_mediafire_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 MediaFire URL 형태입니다: {url}")

        # 1. 이미 발급된 직링크 (download*.mediafire.com) 형태인 경우 바로 반환
        if (parsed.hostname or "").lower().startswith("download"):
            logger.info(f"[MediaFire] 이미 발급된 직링크 감지: {url}")
            filename = extract_filename_from_headers(fallback_url=url)
            return {
                "url": url,
                "filename": sanitize_filename(filename) if filename else None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://www.mediafire.com/",
                    "Accept": "*/*",
                },
                "cookies": None,
                "source_service": self.name,
            }

        logger.info(f"[MediaFire] 파일 상세 페이지 분석 시작: {url}")

        session = requests.Session()
        req_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.mediafire.com/",
        }

        html = ""
        need_browser_fallback = False

        try:
            resp = session.get(url, headers=req_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                raise FileNotFoundError(f"MediaFire 파일을 찾을 수 없습니다 (404): {url}")

            # Cloudflare 차단 또는 챌린지 응답 여부 확인
            if resp.status_code in (403, 503) or "Just a moment" in resp.text or "cf-chl" in resp.text:
                need_browser_fallback = True
            else:
                html = resp.text
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.warning(f"[MediaFire] requests 조회 실패 ({e}), 스텔스 브라우저로 전환합니다.")
            need_browser_fallback = True

        # 필요시 스텔스 브라우저로 폴백
        cookie_str: Optional[str] = None
        if need_browser_fallback or not html:
            logger.info(f"[MediaFire] 스텔스 브라우저 로딩 시작: {url}")
            with stealth_browser_session(prefix="mediafire_") as page:
                page.get(url)
                for _ in range(15):
                    if page.ele("#downloadButton") or page.ele(".download_link"):
                        break
                    import time
                    time.sleep(1)

                html = page.html or ""
                cookies_list = page.cookies() or []
                cookie_dict = {
                    c["name"]: c["value"]
                    for c in cookies_list
                    if isinstance(c, dict) and "name" in c and "value" in c
                }
                cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items()) or None
                if page.user_agent:
                    req_headers["User-Agent"] = page.user_agent

        soup = BeautifulSoup(html, "html.parser")

        # 파일 삭제 또는 차단 여부 확인
        body_lower = soup.get_text().lower()
        if any(term in body_lower for term in ("file removed", "invalid or deleted file", "dangerous file blocked", "file has been removed")):
            raise FileNotFoundError(f"MediaFire에서 파일이 삭제되었거나 차단되었습니다: {url}")

        # 다운로드 직링크 버튼 탐색
        dl_btn = (
            soup.find(id="downloadButton")
            or soup.find("a", attrs={"aria-label": "Download file"})
            or soup.find("a", class_="popsok")
            or soup.find("a", class_="download_link")
        )

        download_url: Optional[str] = None
        if dl_btn and dl_btn.get("href"):
            raw_href = str(dl_btn.get("href")).strip()
            if raw_href.startswith(("http://", "https://")):
                download_url = raw_href

        if not download_url:
            # JavaScript 변수 또는 onclick 내부 직링크 탐색
            m = re.search(r'https?://download\d+\.mediafire\.com/[^\s"\'<>]+', html)
            if m:
                download_url = m.group(0)

        if not download_url:
            raise ValueError(f"MediaFire 다운로드 직링크(#downloadButton)를 찾을 수 없습니다: {url}")

        # 파일명 추출 (DOM, Content-Disposition 또는 URL 경로)
        extracted_filename: Optional[str] = None
        fn_elem = (
            soup.find(class_="filename")
            or soup.find(class_="dl-btn-label")
            or soup.find("div", class_="file-name")
        )
        if fn_elem and fn_elem.get_text(strip=True):
            extracted_filename = fn_elem.get_text(strip=True)

        if not extracted_filename:
            # download_url 경로에서 파일명 추출
            extracted_filename = extract_filename_from_headers(fallback_url=download_url)

        # HEAD 요청으로 최종 파일명 및 크기 확인
        try:
            head_headers = {
                "User-Agent": req_headers["User-Agent"],
                "Referer": url,
                "Accept": "*/*",
            }
            head_cookies = session.cookies.get_dict() if not cookie_str else None
            head_resp = session.head(
                download_url,
                headers=head_headers,
                cookies=head_cookies,
                timeout=10,
                allow_redirects=True,
            )
            if head_resp.status_code == 200:
                h_name = extract_filename_from_headers(head_resp.headers, fallback_url=download_url)
                if h_name:
                    extracted_filename = h_name
        except Exception as e:
            logger.debug(f"[MediaFire] HEAD 확인 예외 (무시): {e}")

        clean_title = ""
        if extracted_filename:
            clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", extracted_filename).strip()

        logger.info(f"[MediaFire] 직링크 발급 성공: {extracted_filename} -> {download_url[:60]}...")

        return {
            "url": download_url,
            "filename": sanitize_filename(extracted_filename) if extracted_filename else None,
            "headers": {
                "User-Agent": req_headers["User-Agent"],
                "Referer": url,
                "Accept": "*/*",
            },
            "cookies": cookie_str,
            "referer": url,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(clean_title) if clean_title else "",
            "article_title": clean_title,
        }
