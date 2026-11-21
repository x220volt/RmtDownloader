"""셀리카 공방 (selica.kr) MSX 한글화 패치 및 도구 전용 리졸버."""

import logging
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import (
    extract_filename_from_headers,
    fix_mojibake,
    sanitize_filename,
    sanitize_folder_name,
)
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 셀리카 공방 다운로드 사전 안내 통과 쿠키
SELICA_COOKIE = "dl_ok=1"

# 페이지 제목 정리 정규식
TITLE_CLEANUP_REGEX = re.compile(
    r"\s*(?:—|-|\|)\s*(?:셀리카\s*공방|SELICA\s*WORKSHOP).*$",
    re.I,
)


class SelicaResolver(BaseResolver):
    """셀리카 공방 (selica.kr) 패치 및 다운로드 API 링크 해석기."""

    name = "Selica Workshop"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url.strip())
            host = (parsed.hostname or "").lower()
            return host in ("selica.kr", "www.selica.kr") or host.endswith(".selica.kr")
        except Exception:
            return False

    def _extract_page_title(self, soup: BeautifulSoup, page_url: str) -> str:
        """HTML 문서에서 패치/프로젝트 제목을 추출합니다."""
        # 1. 메인 h1 태그 확인 (가장 깔끔한 게임/프로젝트 명칭)
        h1 = soup.select_one("main h1, article h1, h1")
        if h1:
            t = h1.get_text(" ", strip=True)
            if t:
                cleaned = TITLE_CLEANUP_REGEX.sub("", t).strip()
                if cleaned:
                    return sanitize_folder_name(cleaned)

        # 2. title 태그 확인
        if soup.title and soup.title.string:
            t = fix_mojibake(soup.title.string.strip())
            cleaned = TITLE_CLEANUP_REGEX.sub("", t).strip()
            if cleaned:
                return sanitize_folder_name(cleaned)

        # 3. DownloadBox 내의 relTitle 또는 head 제목 확인
        rel_title_el = soup.select_one(
            "[class*='relTitle'], [class*='DownloadBox'] h1, [class*='DownloadBox'] h2"
        )
        if rel_title_el:
            t = rel_title_el.get_text(" ", strip=True)
            if t:
                cleaned = TITLE_CLEANUP_REGEX.sub("", t).strip()
                if cleaned:
                    return sanitize_folder_name(cleaned)

        # 4. URL path 기반 폴백
        path_leaf = unquote(urlparse(page_url).path).rstrip("/").split("/")[-1]
        return sanitize_folder_name(path_leaf or "Selica_Patch")

    def _extract_download_links(
        self, soup: BeautifulSoup, page_url: str
    ) -> list[tuple[str, str]]:
        """페이지 내의 /api/dl/... 다운로드 링크들을 추출하고 중복/번들을 정리합니다."""
        raw_links: list[tuple[str, str]] = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = str(a.get("href") or "").strip()
            if "/api/dl/" in href:
                full_url = urljoin(page_url, href)
                if full_url not in seen:
                    seen.add(full_url)
                    link_text = a.get_text(" ", strip=True)
                    raw_links.append((full_url, link_text))

        # 1. "둘 다 받기" (?v=both) 옵션이 있는 경우 개별 일반/치트판 링크는 번들에 포함되므로 제외
        has_both = any("?v=both" in u for u, _ in raw_links)
        # 2. "모든 판 번들" (/postdocs/) 옵션이 있는 경우 개별 postdoc 링크 제외
        has_postdocs = any("/api/dl/postdocs/" in u for u, _ in raw_links)

        filtered: list[tuple[str, str]] = []
        for u, txt in raw_links:
            if has_both and ("?v=normal" in u or "?v=cheat" in u):
                continue
            if has_postdocs and ("/api/dl/postdoc/" in u):
                continue
            filtered.append((u, txt))

        return filtered

    def _resolve_direct_download(
        self,
        dl_url: str,
        referer: str = "https://selica.kr/",
        page_title: str = "",
        session: requests.Session | None = None,
    ) -> dict:
        """/api/dl/... 직다운로드 URL에 대해 HEAD/GET 요청으로 파일명 및 크기를 확인하여 반환합니다."""
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": referer,
            "Cookie": SELICA_COOKIE,
            "Accept": "*/*",
        }
        session_to_use = session or requests.Session()
        close_session = session is None

        try:
            head_resp = session_to_use.head(
                dl_url,
                headers=headers,
                allow_redirects=True,
                timeout=15,
            )
            # HEAD가 405 등으로 거부되거나 오류인 경우 GET 스트리밍으로 폴백
            status_code = getattr(head_resp, "status_code", 200)
            if isinstance(status_code, int) and status_code >= 400:
                head_resp = session_to_use.get(
                    dl_url,
                    headers=headers,
                    stream=True,
                    allow_redirects=True,
                    timeout=15,
                )

            final_url = head_resp.url
            fn = extract_filename_from_headers(
                head_resp.headers, fallback_url=final_url
            )
            if not fn or fn.endswith(".php") or fn == "download":
                path_leaf = unquote(urlparse(final_url).path).split("/")[-1].strip()
                if path_leaf and "." in path_leaf:
                    fn = path_leaf
                elif page_title:
                    fn = f"{page_title}.zip"
                else:
                    fn = "selica_download.zip"

            size = None
            try:
                cl = head_resp.headers.get("Content-Length")
                if cl:
                    size = int(cl)
            except Exception:
                size = None

            return {
                "url": final_url,
                "filename": sanitize_filename(fn),
                "size": size,
                "headers": headers,
                "cookies": SELICA_COOKIE,
                "referer": referer,
                "source_service": self.name,
                "allow_parallel": False,
            }
        finally:
            if close_session:
                session_to_use.close()

    def resolve(self, url: str, options: dict | None = None) -> dict:
        url_clean = url.strip()
        parsed = urlparse(url_clean)

        # 1. 안내 페이지 (/download?next=...) 형태인 경우 대상 API 경로 언래핑
        if "/download" in parsed.path and parsed.query:
            qs = parse_qs(parsed.query)
            next_param = qs.get("next", [""])[0]
            if next_param:
                url_clean = urljoin(url_clean, unquote(next_param))
                parsed = urlparse(url_clean)

        # 2. 직다운로드 API URL인 경우 (/api/dl/...)
        if "/api/dl/" in parsed.path:
            logger.info(f"[SelicaResolver] 직다운로드 API 링크 감지: {url_clean}")
            return self._resolve_direct_download(
                url_clean, referer="https://selica.kr/"
            )

        # 3. 프로젝트/게임 상세 페이지 (예: /msx/msxtrain1, /tools/yama 등)
        logger.info(f"[SelicaResolver] 상세 페이지 분석 시작: {url_clean}")
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                }
            )
            resp = session.get(url_clean, allow_redirects=True, timeout=20)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            post_title = self._extract_page_title(soup, resp.url)
            dl_links = self._extract_download_links(soup, resp.url)

            if not dl_links:
                raise FileNotFoundError(
                    f"셀리카 공방 페이지 '{post_title}' 본문에서 다운로드 링크(/api/dl/...)를 찾을 수 없습니다."
                )

            logger.info(
                f"[SelicaResolver] '{post_title}'에서 {len(dl_links)}개의 다운로드 링크 검출: "
                f"{[u for u, _ in dl_links]}"
            )

            # 첫 번째 링크를 기본 작업으로 해석
            primary_url, _ = dl_links[0]
            primary_res = self._resolve_direct_download(
                primary_url,
                referer=resp.url,
                page_title=post_title,
                session=session,
            )
            primary_res["article_title"] = post_title
            primary_res["subfolder"] = post_title
            primary_res["source_page_url"] = resp.url

            # 추가 파일(다중 파일/공략집 PDF 등)이 있는 경우 files 목록 구성
            if len(dl_links) > 1:
                file_items = [
                    {
                        "url": primary_res["url"],
                        "name": primary_res["filename"],
                        "filename": primary_res["filename"],
                        "size": primary_res.get("size"),
                        "headers": primary_res.get("headers"),
                        "cookies": SELICA_COOKIE,
                        "referer": resp.url,
                    }
                ]
                for extra_u, _ in dl_links[1:]:
                    try:
                        extra_res = self._resolve_direct_download(
                            extra_u,
                            referer=resp.url,
                            page_title=post_title,
                            session=session,
                        )
                        file_items.append(
                            {
                                "url": extra_res["url"],
                                "name": extra_res["filename"],
                                "filename": extra_res["filename"],
                                "size": extra_res.get("size"),
                                "headers": extra_res.get("headers"),
                                "cookies": SELICA_COOKIE,
                                "referer": resp.url,
                            }
                        )
                    except Exception as e:
                        logger.warning(
                            f"[SelicaResolver] 추가 파일 해석 실패 ({extra_u}): {e}"
                        )
                        file_items.append(
                            {
                                "url": extra_u,
                                "name": "",
                                "filename": "",
                                "headers": primary_res.get("headers"),
                                "cookies": SELICA_COOKIE,
                                "referer": resp.url,
                            }
                        )
                primary_res["files"] = file_items

            return primary_res
