"""FileKeeper (filekeeper.net) 파일 다운로드 링크 리졸버."""

import logging
import re
import time
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


def _is_filekeeper_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("filekeeper.net", "www.filekeeper.net") or host.endswith(".filekeeper.net")


def _is_direct_edge_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return "fetchio.cc" in host


class FileKeeperResolver(BaseResolver):
    """FileKeeper (filekeeper.net)의 파일 다운로드 페이지를 분석하여 직링크를 추출하는 리졸버."""

    name: str = "FileKeeper"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if _is_direct_edge_host(parsed.hostname):
                return True
            if not _is_filekeeper_host(parsed.hostname):
                return False
            path = parsed.path or ""
            # /7lkwblvxjags or /7lkwblvxjags/filename.ext or /download
            return bool(
                re.match(r"^/[a-zA-Z0-9_-]{6,}", path)
                or path.startswith("/download")
                or parsed.query.strip()
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not (_is_filekeeper_host(parsed.hostname) or _is_direct_edge_host(parsed.hostname)):
            raise ValueError(f"지원하지 않는 FileKeeper URL 형태입니다: {url}")

        # 1. 이미 발급된 직링크 (fetchio.cc 등) 형태인 경우 바로 반환
        if _is_direct_edge_host(parsed.hostname):
            logger.info(f"[FileKeeper] 이미 발급된 직링크 감지: {url}")
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
                    "Referer": "https://filekeeper.net/",
                    "Accept": "*/*",
                },
                "cookies": None,
                "source_service": self.name,
                "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
                "article_title": clean_title,
            }

        logger.info(f"[FileKeeper] 파일 상세 페이지 분석 시작: {url}")

        # URL 경로에서 파일 코드 및 파일명 힌트 추출
        path_parts = [p for p in (parsed.path or "").split("/") if p]
        file_code_hint = path_parts[0] if path_parts else ""
        filename_hint = path_parts[1] if len(path_parts) > 1 else None

        session = requests.Session()
        req_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://filekeeper.net/",
        }

        html = ""
        need_browser_fallback = False
        direct_url: Optional[str] = None
        extracted_filename: Optional[str] = filename_hint

        try:
            resp = session.get(url, headers=req_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                raise FileNotFoundError(f"FileKeeper 파일을 찾을 수 없습니다 (404): {url}")

            if resp.status_code in (403, 503) or "Just a moment" in resp.text or "cf-chl" in resp.text:
                need_browser_fallback = True
            else:
                html = resp.text
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.warning(f"[FileKeeper] requests 조회 실패 ({e}), 스텔스 브라우저로 전환합니다.")
            need_browser_fallback = True

        if not need_browser_fallback and html:
            soup = BeautifulSoup(html, "html.parser")
            countdown_elem = soup.find(id="download-countdown")

            # 파일 삭제 또는 존재하지 않는 페이지 확인
            if not countdown_elem:
                body_lower = soup.get_text().lower()
                if any(
                    k in body_lower
                    for k in (
                        "not found",
                        "deleted",
                        "file removed",
                        "uploads disabled",
                        "access restricted",
                    )
                ):
                    raise FileNotFoundError(f"FileKeeper 파일이 존재하지 않거나 삭제되었습니다: {url}")
                need_browser_fallback = True
            else:
                code = countdown_elem.get("data-code") or file_code_hint
                rand = countdown_elem.get("data-rand") or ""
                ref = countdown_elem.get("data-referer") or ""
                method = countdown_elem.get("data-method") or ""
                has_pwd = countdown_elem.get("data-has-password") == "true"
                has_captcha = countdown_elem.get("data-has-captcha") == "true"

                # H1/H2 또는 meta 정보에서 파일명 탐색
                for heading in soup.find_all(["h1", "h2"]):
                    t = heading.get_text(strip=True)
                    if t and t != "FileKeeper" and not t.startswith("Preparing"):
                        extracted_filename = t
                        break

                if has_captcha:
                    logger.info("[FileKeeper] 캡차(Captcha)가 감지되어 스텔스 브라우저로 전환합니다.")
                    need_browser_fallback = True
                else:
                    post_data = {
                        "op": "download2",
                        "id": code,
                        "rand": rand,
                        "referer": ref,
                        "method_free": method,
                        "down_direct": "1",
                    }
                    from ..url_helper import get_candidate_passwords

                    candidates = (
                        get_candidate_passwords(url, options) if has_pwd else [""]
                    )
                    if not candidates:
                        candidates = [""]

                    for cand in candidates:
                        if cand:
                            post_data["password"] = cand
                        try:
                            post_resp = session.post(
                                "https://filekeeper.net/download",
                                data=post_data,
                                headers=req_headers,
                                allow_redirects=False,
                                timeout=REQUEST_TIMEOUT,
                            )
                            if "Location" in post_resp.headers:
                                direct_url = post_resp.headers["Location"]
                                break
                            else:
                                post_soup = BeautifulSoup(post_resp.text, "html.parser")
                                dl_link = post_soup.find(
                                    "a", id="download-link"
                                ) or post_soup.find(
                                    "a", href=re.compile(r"fetchio|/d/|download")
                                )
                                if dl_link and dl_link.get("href"):
                                    direct_url = str(dl_link.get("href"))
                                    break
                                elif not has_pwd:
                                    need_browser_fallback = True
                        except Exception as e:
                            logger.warning(
                                f"[FileKeeper] 다운로드 요청 실패 ({e}), 스텔스 브라우저로 전환합니다."
                            )
                            need_browser_fallback = True

        # 필요시 스텔스 브라우저 폴백
        cookie_dict: dict = {}
        if need_browser_fallback and not direct_url:
            logger.info(f"[FileKeeper] 스텔스 브라우저 로딩 시작: {url}")
            with stealth_browser_session(prefix="filekeeper_") as page:
                page.get(url)
                for _ in range(15):
                    if (
                        page.ele("#download-countdown")
                        or page.ele("#download-button")
                        or page.ele("#download-link")
                        or page.ele("a[id='download-link']")
                    ):
                        break
                    time.sleep(1)

                cookies_list = page.cookies() or []
                cookie_dict = {
                    c["name"]: c["value"]
                    for c in cookies_list
                    if isinstance(c, dict) and "name" in c and "value" in c
                }
                if page.user_agent:
                    req_headers["User-Agent"] = page.user_agent

                # 브라우저 DOM에서 countdown 요소 조회
                cd_elem = page.ele("#download-countdown")
                if cd_elem:
                    code = cd_elem.attr("data-code") or file_code_hint
                    rand = cd_elem.attr("data-rand") or ""
                    ref = cd_elem.attr("data-referer") or ""
                    method = cd_elem.attr("data-method") or ""

                    post_data = {
                        "op": "download2",
                        "id": code,
                        "rand": rand,
                        "referer": ref,
                        "method_free": method,
                        "down_direct": "1",
                    }
                    from ..url_helper import get_candidate_passwords

                    candidates = get_candidate_passwords(url, options)
                    if not candidates:
                        candidates = [""]

                    for cand in candidates:
                        if cand:
                            post_data["password"] = cand
                        # 브라우저 컨텍스트에서 POST 요청 실행 또는 폼 제출
                        post_resp = session.post(
                            "https://filekeeper.net/download",
                            data=post_data,
                            headers=req_headers,
                            cookies=cookie_dict,
                            allow_redirects=False,
                            timeout=REQUEST_TIMEOUT,
                        )
                        if (
                            post_resp.status_code in (301, 302, 303, 307, 308)
                            and "Location" in post_resp.headers
                        ):
                            direct_url = post_resp.headers["Location"]
                            break

                if not direct_url:
                    # 다운로드 버튼 클릭 시도
                    dl_btn = page.ele("#download-button") or page.ele("#download-link")
                    if dl_btn:
                        dl_btn.click()
                        time.sleep(3)
                        # 페이지 이동 여부 또는 네트워크 링크 확인
                        if _is_direct_edge_host(urlparse(page.url).hostname):
                            direct_url = page.url

        if not direct_url:
            raise ValueError(f"FileKeeper 다운로드 링크를 추출하지 못했습니다: {url}")

        # HEAD 요청으로 최종 파일명 확인
        try:
            head_headers = {
                "User-Agent": req_headers["User-Agent"],
                "Referer": "https://filekeeper.net/",
                "Accept": "*/*",
            }
            raw_head_cookies = cookie_dict or session.cookies.get_dict()
            head_cookies = {
                k: v for k, v in raw_head_cookies.items() if v is not None
            }
            head_resp = session.head(
                direct_url,
                headers=head_headers,
                cookies=head_cookies,
                timeout=10,
                allow_redirects=True,
            )
            if head_resp.status_code == 200:
                h_name = extract_filename_from_headers(head_resp.headers, fallback_url=direct_url)
                if h_name:
                    extracted_filename = h_name
        except Exception as e:
            logger.debug(f"[FileKeeper] HEAD 확인 예외 (무시): {e}")

        if not extracted_filename:
            extracted_filename = extract_filename_from_headers(fallback_url=url)

        clean_title = ""
        folder_base = ""
        if extracted_filename:
            clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", extracted_filename).strip()
            folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title

        logger.info(f"[FileKeeper] 직링크 발급 성공: {extracted_filename} -> {direct_url[:60]}...")

        return {
            "url": direct_url,
            "filename": sanitize_filename(extracted_filename) if extracted_filename else None,
            "headers": {
                "User-Agent": req_headers["User-Agent"],
                "Referer": "https://filekeeper.net/",
                "Accept": "*/*",
            },
            "cookies": cookie_dict or session.cookies.get_dict() or None,
            "referer": url,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
            "article_title": clean_title,
        }
