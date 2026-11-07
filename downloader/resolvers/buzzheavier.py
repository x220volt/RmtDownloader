"""Buzzheavier (buzzheavier.com / bzzhr.to) 파일 다운로드 링크 리졸버."""

import logging
import re
import time
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import requests

from ..browser_solver import stealth_browser_session
from ..engine import extract_filename_from_headers, sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25


def _is_buzzheavier_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return (
        host in ("buzzheavier.com", "www.buzzheavier.com", "bzzhr.to", "www.bzzhr.to")
        or host.endswith(".buzzheavier.com")
        or host.endswith(".bzzhr.to")
    )


def _is_direct_cdn_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("ts.buzzheavier.com", "f.buzzheavier.com") or "buzzheavier" in host


class BuzzheavierResolver(BaseResolver):
    """Buzzheavier (buzzheavier.com / bzzhr.to)의 파일 다운로드 페이지를 분석하여 직링크를 추출하는 리졸버."""

    name: str = "Buzzheavier"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if host in ("ts.buzzheavier.com", "f.buzzheavier.com") and (parsed.path or "").startswith("/d/"):
                return True
            if not _is_buzzheavier_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(
                re.match(r"^/[a-zA-Z0-9_-]{4,}", path)
                or path.startswith("/download")
                or parsed.query.strip()
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not (_is_buzzheavier_host(parsed.hostname) or _is_direct_cdn_host(parsed.hostname)):
            raise ValueError(f"지원하지 않는 Buzzheavier URL 형태입니다: {url}")

        # 1. 이미 발급된 직링크 (ts.buzzheavier.com/d/... 등) 형태인 경우 바로 반환
        if (parsed.hostname or "").lower().startswith("ts.") or "/d/" in (parsed.path or ""):
            logger.info(f"[Buzzheavier] 이미 발급된 직링크 감지: {url}")
            filename = extract_filename_from_headers(fallback_url=url)
            clean_title = ""
            folder_base = ""
            if filename:
                clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", filename).strip()
                folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title
            return {
                "url": url,
                "filename": sanitize_filename(filename) if filename else None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://buzzheavier.com/",
                    "Accept": "*/*",
                },
                "cookies": None,
                "source_service": self.name,
                "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
                "article_title": clean_title,
            }

        logger.info(f"[Buzzheavier] 파일 상세 페이지 분석 시작: {url}")

        path_parts = [p for p in (parsed.path or "").split("/") if p]
        file_code_hint = path_parts[0] if path_parts else ""
        filename_hint: Optional[str] = None

        req_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://buzzheavier.com/",
        }

        # 2. 초기 HTTP GET 요청으로 빠른 확인 (Cloudflare 미적용 또는 404 신속 판별)
        direct_url: Optional[str] = None
        extracted_filename: Optional[str] = None
        try:
            resp = requests.get(url, headers=req_headers, timeout=10, allow_redirects=True)
            if resp.status_code == 404:
                raise FileNotFoundError(f"Buzzheavier 파일을 찾을 수 없습니다 (404): {url}")
            body_text = resp.text
            if "<title>Not Found</title>" in body_text or "file was deleted" in body_text.lower():
                raise FileNotFoundError(f"Buzzheavier 파일이 존재하지 않거나 삭제되었습니다: {url}")

            # 만약 Cloudflare 차단 없이 페이지가 로드된 경우 HTMX 엔드포인트 직접 호출 시도
            if resp.status_code == 200 and "download-btn" in body_text:
                m_title = re.search(r"<title>([^<]+)</title>", body_text)
                if m_title and m_title.group(1).strip() not in ("Just a moment...", "Not Found"):
                    extracted_filename = m_title.group(1).strip()
                m_hx = re.search(r'hx-get="([^"]+)"', body_text)
                if m_hx:
                    hx_url = urljoin(url, m_hx.group(1))
                    hx_headers = {
                        "User-Agent": USER_AGENT,
                        "HX-Request": "true",
                        "Referer": url,
                    }
                    r_hx = requests.get(hx_url, headers=hx_headers, allow_redirects=False, timeout=10)
                    if r_hx.status_code in (200, 204, 302, 303, 307):
                        direct_url = r_hx.headers.get("hx-redirect") or r_hx.headers.get("Location")
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.debug(f"[Buzzheavier] 초기 요청 예외 (스텔스 브라우저로 전환): {e}")

        # 3. 직링크를 얻지 못한 경우 스텔스 브라우저 세션 활용
        cookie_dict: dict = {}
        if not direct_url:
            logger.info(f"[Buzzheavier] 스텔스 브라우저 로딩 시작: {url}")
            with stealth_browser_session(prefix="buzzheavier_") as page:
                page.get(url)
                page.wait.doc_loaded()
                time.sleep(2.5)

                title = (page.title or "").strip()
                if title == "Not Found":
                    raise FileNotFoundError(f"Buzzheavier 파일을 찾을 수 없습니다 (Not Found): {url}")

                if title and title not in ("Just a moment...", "Cloudflare"):
                    extracted_filename = title

                # JS로 hx-get 호출 및 hx-redirect 획득
                fetch_js = """
                return (async () => {
                    const btn = document.querySelector('.download-btn') || document.querySelector('[hx-get]');
                    let hxGet = btn ? btn.getAttribute('hx-get') : null;
                    if (!hxGet) {
                        const copyBtn = document.querySelector('.copy');
                        const onclick = copyBtn ? copyBtn.getAttribute('onclick') : '';
                        const m = onclick ? onclick.match(/copyDownloadLink\\(['"]([^'"]+)['"]\\)/) : null;
                        if (m) hxGet = m[1].replace(/\\\\/g, '');
                    }
                    if (!hxGet) return {error: 'No download button found'};
                    
                    const resp = await fetch(hxGet, {
                        headers: {'HX-Request': 'true'}
                    });
                    return {
                        status: resp.status,
                        hxRedirect: resp.headers.get('hx-redirect'),
                        url: resp.url
                    };
                })();
                """
                res = page.run_js(fetch_js)
                if isinstance(res, dict) and res.get("hxRedirect"):
                    direct_url = res["hxRedirect"]
                elif isinstance(res, dict) and res.get("error"):
                    logger.warning(f"[Buzzheavier] 다운로드 링크 탐색 실패: {res['error']}")

                if page.user_agent:
                    req_headers["User-Agent"] = page.user_agent

                cookies_list = page.cookies() or []
                cookie_dict = {
                    c["name"]: c["value"]
                    for c in cookies_list
                    if isinstance(c, dict) and "name" in c and "value" in c
                }

        if not direct_url:
            raise ValueError(f"Buzzheavier 다운로드 직링크 추출에 실패했습니다: {url}")

        # 4. 직링크 헤더에서 최종 파일명 및 크기 확인
        if not extracted_filename:
            try:
                head_resp = requests.head(
                    direct_url,
                    headers={"User-Agent": req_headers["User-Agent"], "Referer": "https://buzzheavier.com/"},
                    timeout=10,
                    allow_redirects=True,
                )
                extracted_filename = extract_filename_from_headers(head_resp.headers, fallback_url=direct_url)
            except Exception as e:
                logger.debug(f"[Buzzheavier] HEAD 헤더 확인 예외 (무시): {e}")

        clean_title = ""
        folder_base = ""
        if extracted_filename:
            extracted_filename = sanitize_filename(extracted_filename)
            clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", extracted_filename).strip()
            folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title

        logger.info(f"[Buzzheavier] 직링크 발급 성공: {extracted_filename} -> {direct_url[:60]}...")

        return {
            "url": direct_url,
            "filename": extracted_filename,
            "headers": {
                "User-Agent": req_headers.get("User-Agent", USER_AGENT),
                "Referer": "https://buzzheavier.com/",
                "Accept": "*/*",
            },
            "cookies": cookie_dict or None,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
            "article_title": clean_title,
        }

