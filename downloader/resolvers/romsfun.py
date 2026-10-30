"""romsfun.com 게임 ROM 다운로드 링크 리졸버."""

import html
import logging
import re
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 15


def _is_romsfun_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("romsfun.com", "www.romsfun.com") or host.endswith(".romsfun.com")


class RomsfunResolver(BaseResolver):
    """Romsfun.com의 게임 소개 및 다운로드 페이지를 해석하여 최신 직링크를 발급하는 리졸버."""

    name: str = "Romsfun"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_romsfun_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(re.search(r"^/download/", path) or re.search(r"^/roms/", path))
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_romsfun_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 Romsfun URL 형태입니다: {url}")

        session = requests.Session()
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        # 1. /roms/<console>/<slug>.html 형태인 경우 -> 내부의 /download/ 링크 탐색
        if "/roms/" in parsed.path:
            logger.info(f"[Romsfun] 게임 상세 페이지에서 다운로드 링크 탐색 중: {url}")
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if not resp.ok:
                raise ValueError(
                    f"Romsfun 게임 페이지 접속 실패 (HTTP {resp.status_code}): {url}"
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
                    f"Romsfun 게임 페이지에서 다운로드 링크를 찾을 수 없습니다: {url}"
                )
            logger.info(f"[Romsfun] 다운로드 페이지 링크 발견: {dl_link}")
            url = dl_link
            parsed = urlparse(url)

        # 2. /download/<slug> (하위 번호 없음) 형태인 경우 -> /1, /2 등 하위 다운로드 옵션 탐색
        path = parsed.path.rstrip("/")
        m_sub = re.search(r"/download/[^/]+/(\d+)$", path)
        if not m_sub:
            logger.info(f"[Romsfun] 다운로드 버전 선택 목록 확인 중: {url}")
            resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"Romsfun 다운로드 페이지를 찾을 수 없습니다 (404): {url}"
                )
            if not resp.ok:
                raise ValueError(
                    f"Romsfun 다운로드 목록 페이지 접속 실패 (HTTP {resp.status_code}): {url}"
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
                    f"[Romsfun] {len(sub_links)}개의 다운로드 버전 발견. 첫 번째 버전({first_url}) 해석 시작"
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
        logger.info(f"[Romsfun] 다운로드 페이지 처리 시작: {url}")
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"Romsfun 다운로드 페이지를 찾을 수 없습니다 (404): {url}"
            )
        if not resp.ok:
            raise ValueError(
                f"Romsfun 다운로드 페이지 요청 실패 (HTTP {resp.status_code}): {url}"
            )

        soup = BeautifulSoup(resp.text, "html.parser")

        # 게임 제목 및 서브폴더 추출
        raw_title = (
            str(soup.title.string).strip() if (soup.title and soup.title.string) else ""
        )
        clean_title = re.sub(r"^(?:Download\s+)+", "", raw_title, flags=re.I).strip()
        clean_title = re.sub(r"\s*[-|].*$", "", clean_title).strip()

        container = soup.find(id="download-container")
        ajax_url = "https://romsfun.com/wp-admin/admin-ajax.php"
        if container and container.get("data-ajax-url"):
            ajax_url = urljoin(url, str(container["data-ajax-url"]))

        fresh_url = ""
        # Romsfun은 페이지 로드 후 AJAX (action=k_get_download)를 통해 만료되지 않은 서명된 직링크를 동적으로 발급합니다.
        try:
            ajax_headers = dict(headers)
            ajax_headers["Content-Type"] = (
                "application/x-www-form-urlencoded; charset=UTF-8"
            )
            ajax_headers["X-Requested-With"] = "XMLHttpRequest"
            ajax_headers["Referer"] = url

            ajax_resp = session.post(
                ajax_url,
                data={"action": "k_get_download"},
                headers=ajax_headers,
                timeout=REQUEST_TIMEOUT,
            )
            if ajax_resp.ok:
                data = ajax_resp.json()
                if data.get("success") and data.get("data", {}).get("html"):
                    dl_soup = BeautifulSoup(data["data"]["html"], "html.parser")
                    a_tag = dl_soup.find("a", href=True)
                    if a_tag and a_tag.get("href"):
                        fresh_url = html.unescape(str(a_tag["href"])).strip()
                        logger.info("[Romsfun] AJAX를 통한 동적 직링크 발급 성공")
        except Exception as e:
            logger.warning(
                f"[Romsfun] AJAX 직링크 발급 예외 (초기 HTML 링크로 대체 시도): {e}"
            )

        # AJAX 발급 실패 시 초기 HTML에 렌더링된 링크로 대체
        if not fresh_url:
            dl_link_tag = soup.find(id="download-link")
            if dl_link_tag and dl_link_tag.get("href"):
                fresh_url = html.unescape(str(dl_link_tag["href"])).strip()

        if not fresh_url:
            raise ValueError(f"Romsfun 다운로드 직링크를 추출할 수 없습니다: {url}")

        # 파일명 추출 (URL 경로의 마지막 파일명)
        filename = ""
        parsed_dl = urlparse(fresh_url)
        path_leaf = unquote(parsed_dl.path).split("/")[-1].strip()
        if path_leaf and "." in path_leaf:
            filename = sanitize_filename(path_leaf)

        if not filename and clean_title:
            filename = sanitize_filename(f"{clean_title}.zip")

        logger.info(f"[Romsfun] 직링크 해석 완료: {filename} -> {fresh_url[:60]}...")
        return {
            "url": fresh_url,
            "filename": filename,
            "source_service": self.name,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": url,
            },
            "subfolder": sanitize_folder_name(clean_title) if clean_title else "",
            "article_title": clean_title,
        }
