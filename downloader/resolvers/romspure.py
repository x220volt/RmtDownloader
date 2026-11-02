"""romspure.cc 게임 ROM 다운로드 링크 리졸버."""

import html
import logging
import re
import time
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..browser_solver import stealth_browser_session
from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15


def _is_romspure_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("romspure.cc", "www.romspure.cc") or host.endswith(".romspure.cc")


class RomspureResolver(BaseResolver):
    """Romspure.cc의 게임 상세 및 다운로드 페이지를 분석하여 동적 직링크를 발급하는 리졸버."""

    name: str = "Romspure"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_romspure_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(re.search(r"^/download/", path) or re.search(r"^/roms/", path))
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_romspure_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 Romspure URL 형태입니다: {url}")

        session = requests.Session()
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        # 1. /roms/<console>/<slug> 게임 상세 페이지인 경우 -> 내부 /download/ 링크 탐색
        if "/roms/" in parsed.path:
            logger.info(f"[Romspure] 게임 상세 페이지에서 다운로드 링크 탐색 중: {url}")
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if not resp.ok:
                raise ValueError(
                    f"Romspure 게임 페이지 접속 실패 (HTTP {resp.status_code}): {url}"
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            dl_link = None
            for a in soup.find_all("a", href=True):
                href_str = str(a.get("href") or "")
                if "/download/" in href_str:
                    dl_link = urljoin(url, href_str)
                    break
            if not dl_link:
                raise ValueError(
                    f"Romspure 게임 페이지에서 다운로드 링크를 찾을 수 없습니다: {url}"
                )
            logger.info(f"[Romspure] 다운로드 페이지 링크 발견: {dl_link}")
            url = dl_link
            parsed = urlparse(url)

        # 2. /download/<slug> (하위 번호 없음) 형태인 경우 -> /1, /2 등 하위 다운로드 옵션 탐색
        path = parsed.path.rstrip("/")
        m_sub = re.search(r"/download/[^/]+/(\d+)$", path)
        if not m_sub:
            logger.info(f"[Romspure] 다운로드 버전 선택 목록 확인 중: {url}")
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"Romspure 다운로드 페이지를 찾을 수 없습니다 (404): {url}"
                )
            if not resp.ok:
                raise ValueError(
                    f"Romspure 다운로드 목록 페이지 접속 실패 (HTTP {resp.status_code}): {url}"
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            sub_links: list[tuple[str, str]] = []
            for a in soup.find_all("a", href=True):
                href_str = str(a.get("href") or "")
                full_a = urljoin(url, href_str)
                if re.search(r"/download/[^/]+/\d+", full_a):
                    if not any(item[0] == full_a for item in sub_links):
                        sub_links.append((full_a, a.get_text(strip=True)))

            if sub_links:
                first_url, first_text = sub_links[0]
                logger.info(
                    f"[Romspure] {len(sub_links)}개의 다운로드 버전 발견. 첫 번째 버전({first_url}) 해석 시작"
                )
                first_result = self.resolve(first_url, options=options)
                if len(sub_links) > 1:
                    extra_jobs = []
                    for extra_u, extra_t in sub_links[1:]:
                        extra_jobs.append(
                            {
                                "url": extra_u,
                                "filename": "",
                                "options": {"referer": url},
                            }
                        )
                    first_result["extra_jobs"] = extra_jobs
                return first_result

        # 3. /download/<slug>/<num> 개별 다운로드 페이지 처리
        logger.info(f"[Romspure] 다운로드 페이지 처리 시작: {url}")
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"Romspure 다운로드 페이지를 찾을 수 없습니다 (404): {url}"
            )
        if not resp.ok:
            raise ValueError(
                f"Romspure 다운로드 페이지 요청 실패 (HTTP {resp.status_code}): {url}"
            )

        soup = BeautifulSoup(resp.text, "html.parser")

        # 게임 제목 및 서브폴더 추출
        raw_title = (
            str(soup.title.string).strip() if (soup.title and soup.title.string) else ""
        )
        clean_title = re.sub(r"^(?:Download\s+)+", "", raw_title, flags=re.I).strip()
        clean_title = re.sub(r"\s*[-|].*$", "", clean_title).strip()

        # DOM 태그 또는 URL에서 post_id 및 index 추출
        post_id = ""
        index = ""
        widget = soup.find(attrs={"data-download-ajax": True})
        if widget:
            post_id = str(widget.get("data-post-id") or "").strip()
            index = str(widget.get("data-index") or "").strip()

        if not post_id or not index:
            m = re.search(r"/download/[^/]+-(\d+)/(\d+)", parsed.path)
            if m:
                post_id = m.group(1)
                index = m.group(2)

        if not post_id or not index:
            raise ValueError(
                f"Romspure 다운로드 파라미터(post_id, index)를 추출할 수 없습니다: {url}"
            )

        ajax_url = "https://romspure.cc/wp-admin/admin-ajax.php"
        ajax_headers = dict(headers)
        ajax_headers["Content-Type"] = (
            "application/x-www-form-urlencoded; charset=UTF-8"
        )
        ajax_headers["X-Requested-With"] = "XMLHttpRequest"
        ajax_headers["Referer"] = url

        # 3-1. 동적 Nonce 발급 (action=romspure_get_nonce)
        nonce = ""
        try:
            nonce_resp = session.post(
                ajax_url,
                data={"action": "romspure_get_nonce"},
                headers=ajax_headers,
                timeout=REQUEST_TIMEOUT,
            )
            if nonce_resp.ok:
                nonce_data = nonce_resp.json()
                if nonce_data.get("success") and nonce_data.get("data", {}).get("nonce"):
                    nonce = str(nonce_data["data"]["nonce"]).strip()
                    logger.info(f"[Romspure] Nonce 발급 성공: {nonce}")
        except Exception as e:
            logger.warning(f"[Romspure] Nonce 발급 예외: {e}")

        # 3-2. 직링크 발급 (action=app_get_download_link)
        dl_payload = {
            "action": "app_get_download_link",
            "post_id": str(post_id),
            "index": str(index),
        }
        if nonce:
            dl_payload["nonce"] = nonce

        fresh_url = ""
        api_name = ""
        try:
            link_resp = session.post(
                ajax_url,
                data=dl_payload,
                headers=ajax_headers,
                timeout=REQUEST_TIMEOUT,
            )
            if link_resp.ok:
                link_data = link_resp.json()
                if link_data.get("success") and link_data.get("data", {}).get("url"):
                    data_obj = link_data["data"]
                    fresh_url = html.unescape(str(data_obj["url"])).strip()
                    api_name = str(data_obj.get("name") or "").strip()
                    logger.info("[Romspure] AJAX를 통한 동적 직링크 발급 성공")
        except Exception as e:
            logger.warning(f"[Romspure] AJAX 직링크 발급 예외: {e}")

        if not fresh_url:
            raise ValueError(f"Romspure 다운로드 직링크를 추출할 수 없습니다: {url}")

        # 파일 확장자 및 파일명 산출
        parsed_dl = urlparse(fresh_url)
        path_leaf = unquote(parsed_dl.path).split("/")[-1].strip()
        ext = ""
        if "." in path_leaf:
            ext = "." + path_leaf.split(".")[-1]

        filename = ""
        if api_name:
            if ext and not api_name.lower().endswith(ext.lower()):
                filename = sanitize_filename(f"{api_name}{ext}")
            else:
                filename = sanitize_filename(api_name)
        elif path_leaf and "." in path_leaf:
            filename = sanitize_filename(path_leaf)
        elif clean_title:
            filename = sanitize_filename(f"{clean_title}{ext or '.zip'}")

        # 3-3. Cloudflare 보호 여부 검사 및 cf_clearance 자동 우회
        req_headers = {
            "User-Agent": USER_AGENT,
            "Referer": url,
        }
        cookies_str: str | None = None

        try:
            check_resp = session.get(
                fresh_url,
                headers={"User-Agent": USER_AGENT, "Referer": url, "Range": "bytes=0-0"},
                timeout=6,
                stream=True,
            )
            is_cf_challenge = (
                check_resp.status_code in (403, 503)
                or "challenge" in str(check_resp.headers.get("cf-mitigated", "")).lower()
            )
            if is_cf_challenge:
                logger.info(
                    f"[Romspure] 저장소 호스트 Cloudflare 감지. 스텔스 브라우저로 cf_clearance 획득 시도: {fresh_url[:60]}..."
                )
                with stealth_browser_session(prefix="romspure_cf_") as page:
                    page.get(fresh_url)
                    browser_ua = page.user_agent or USER_AGENT
                    for _ in range(12):
                        time.sleep(1)
                        for c in page.cookies():
                            if c.get("name") == "cf_clearance":
                                cookies_str = f"cf_clearance={c['value']}"
                                req_headers["User-Agent"] = browser_ua
                                logger.info("[Romspure] cf_clearance 쿠키 획득 성공")
                                break
                        if cookies_str:
                            break
        except Exception as e:
            logger.warning(f"[Romspure] 직링크 사전 상태 검사 중 예외 (기본 다운로드 계속 진행): {e}")

        logger.info(f"[Romspure] 직링크 해석 완료: {filename} -> {fresh_url[:60]}...")
        return {
            "url": fresh_url,
            "filename": filename,
            "source_service": self.name,
            "headers": req_headers,
            "cookies": cookies_str,
            "subfolder": sanitize_folder_name(clean_title) if clean_title else "",
            "article_title": clean_title,
        }

