"""TeeTeeTee (teeteetee-korean.com) 한글패치 아카이브 리졸버 모듈."""

import logging
import re
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

TEETEETEE_HOSTS = ("teeteetee-korean.com", "www.teeteetee-korean.com")

SLUG_TO_ROOT = {
    "tales-of-rebirth": "rebirth",
    "sengoku-basara-2": "basara",
    "narikiri-dungeon-x": "tonx",
    "god-eater-burst": "geb",
    "gundam-seed-rengou-vs-zaft-portable": "rengou",
    "tales-of-the-heroes-twin-brave": "twinbrave",
    "monhun-nikki-g": "monhun",
    "big3-gun-shooting": "big3",
    "dissidia-duodecim": "dissidia",
    "castlevania-harmony-of-despair": "cvhd",
}

PACKAGE_DOWNLOADS = {
    "castlevania-harmony-of-despair": "https://teeteetee-korean.com/downloads/cvhd-v1.0.zip",
}


def _extract_filename_from_cd(cd_header: str) -> str:
    """Content-Disposition 헤더에서 파일명을 추출합니다."""
    if not cd_header:
        return ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^;\r\n"]+)"?', cd_header)
    if m:
        return urllib.parse.unquote(m.group(1).strip("\"'"))
    return ""


class TeeTeeTeeResolver(BaseResolver):
    """TeeTeeTee 한글패치 아카이브의 프로젝트 페이지에서 최신 xdelta 패치 직링크를 추출하는 리졸버."""

    name = "TeeTeeTee"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = (parsed.hostname or "").lower()
            if any(host == h or host.endswith("." + h) for h in TEETEETEE_HOSTS):
                path = (parsed.path or "").lower()
                return (
                    "/projects/" in path
                    or "/api/patch-releases" in path
                    or ("/api/" in path and "-xdelta" in path)
                    or "/downloads/" in path
                )
        except Exception:
            pass
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict[str, Any]:
        """TeeTeeTee 페이지 또는 API URL을 해석하여 xdelta 패치 직링크를 반환합니다."""
        parsed = urllib.parse.urlparse(url.strip())
        slug = ""
        m_slug = re.search(r"/projects/([a-zA-Z0-9_\-]+)", parsed.path)
        if m_slug:
            slug = m_slug.group(1).lower()
        else:
            qs = urllib.parse.parse_qs(parsed.query)
            if "project" in qs and qs["project"]:
                slug = qs["project"][0].lower()

        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": "https://teeteetee-korean.com/",
        })

        # 1. 이미 직링크 엔드포인트 형태인 경우 (예: /api/*-xdelta, /downloads/*, mobile=1 등)
        is_direct_endpoint = (
            ("/api/" in parsed.path and "-xdelta" in parsed.path)
            or "/downloads/" in parsed.path
            or "mobile=1" in parsed.query
        )
        if is_direct_endpoint:
            logger.info(f"[TeeTeeTeeResolver] 직링크 엔드포인트 요청: {url}")
            r = session.head(url, allow_redirects=True, timeout=15)
            cd = r.headers.get("Content-Disposition", "")
            fn = _extract_filename_from_cd(cd)
            cl = r.headers.get("Content-Length")
            return {
                "url": r.url if r.url else url,
                "filename": sanitize_filename(fn or "patch.xdelta"),
                "size": int(cl) if cl and cl.isdigit() else None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://teeteetee-korean.com/",
                },
                "source_service": "TeeTeeTee",
                "source_page_url": url,
            }

        if not slug:
            raise ValueError(f"TeeTeeTee URL에서 프로젝트 식별자(slug)를 찾을 수 없습니다: {url}")

        # 프로젝트 페이지 접속하여 제목 및 메타데이터 파싱
        clean_title = slug
        try:
            page_resp = session.get(url, timeout=10)
            if page_resp.status_code == 200:
                soup = BeautifulSoup(page_resp.text, "html.parser")
                raw_title = (
                    soup.title.string.strip()
                    if soup.title and soup.title.string
                    else ""
                )
                clean_title = re.sub(
                    r"\s*[\|-]\s*한글패치\s*아카이브.*$", "", raw_title, flags=re.I
                ).strip()
                clean_title = re.sub(
                    r"\s*(?:한국어|한글)\s*패치.*$", "", clean_title, flags=re.I
                ).strip()
        except Exception as e:
            logger.debug(f"[TeeTeeTeeResolver] 프로젝트 페이지 메타데이터 조회 예외 (무시): {e}")

        subfolder_name = sanitize_folder_name(clean_title) if clean_title else slug
        exclusion_reason = ""

        # 2. 모바일 xdelta API 엔드포인트 확인 (/api/patch-releases?project={slug}&mobile=1)
        mobile_api = f"https://teeteetee-korean.com/api/patch-releases?project={slug}&mobile=1"
        try:
            logger.info(f"[TeeTeeTeeResolver] 모바일 xdelta 엔드포인트 확인: {mobile_api}")
            r = session.get(mobile_api, stream=True, timeout=10)
            if r.status_code == 200:
                cd = r.headers.get("Content-Disposition", "")
                if cd and "attachment" in cd.lower():
                    fn = _extract_filename_from_cd(cd)
                    cl = r.headers.get("Content-Length")
                    return {
                        "url": mobile_api,
                        "filename": sanitize_filename(fn or f"{slug}.xdelta"),
                        "size": int(cl) if cl and cl.isdigit() else None,
                        "headers": {
                            "User-Agent": USER_AGENT,
                            "Referer": url,
                        },
                        "subfolder": subfolder_name,
                        "article_title": clean_title,
                        "source_service": "TeeTeeTee",
                        "source_page_url": url,
                    }
            elif r.status_code == 503:
                try:
                    meta_r = session.get(
                        f"https://teeteetee-korean.com/api/patch-releases?project={slug}",
                        timeout=5,
                    )
                    if meta_r.status_code == 200:
                        meta_data = meta_r.json()
                        delivery = meta_data.get("manifest", {}).get("delivery", {})
                        exclusion_reason = (
                            delivery.get("mobileExclusionReason") or r.text.strip()
                        )
                except Exception:
                    exclusion_reason = r.text.strip()
        except Exception as e:
            logger.debug(f"[TeeTeeTeeResolver] 모바일 API 확인 실패 (폴백 시도): {e}")

        # 3. 사전 패키지 다운로드 경로 확인
        if slug in PACKAGE_DOWNLOADS:
            pkg_url = PACKAGE_DOWNLOADS[slug]
            logger.info(f"[TeeTeeTeeResolver] 사전 패키지 다운로드 엔드포인트 확인: {pkg_url}")
            r = session.head(pkg_url, timeout=10)
            if r.status_code == 200:
                cd = r.headers.get("Content-Disposition", "")
                fn = _extract_filename_from_cd(cd)
                cl = r.headers.get("Content-Length")
                return {
                    "url": pkg_url,
                    "filename": sanitize_filename(fn or f"{slug}.zip"),
                    "size": int(cl) if cl and cl.isdigit() else None,
                    "headers": {
                        "User-Agent": USER_AGENT,
                        "Referer": url,
                    },
                    "subfolder": subfolder_name,
                    "article_title": clean_title,
                    "source_service": "TeeTeeTee",
                    "source_page_url": url,
                }

        # 4. 레거시 xdelta 엔드포인트 확인 (/api/{root}-xdelta)
        root = SLUG_TO_ROOT.get(slug, slug.replace("-", ""))
        legacy_url = f"https://teeteetee-korean.com/api/{root}-xdelta"
        try:
            logger.info(f"[TeeTeeTeeResolver] 레거시 xdelta 엔드포인트 확인: {legacy_url}")
            r = session.get(legacy_url, stream=True, timeout=10)
            if r.status_code == 200:
                cd = r.headers.get("Content-Disposition", "")
                if cd and "attachment" in cd.lower():
                    fn = _extract_filename_from_cd(cd)
                    cl = r.headers.get("Content-Length")
                    return {
                        "url": legacy_url,
                        "filename": sanitize_filename(fn or f"{slug}.xdelta"),
                        "size": int(cl) if cl and cl.isdigit() else None,
                        "headers": {
                            "User-Agent": USER_AGENT,
                            "Referer": url,
                        },
                        "subfolder": subfolder_name,
                        "article_title": clean_title,
                        "source_service": "TeeTeeTee",
                        "source_page_url": url,
                    }
        except Exception as e:
            logger.debug(f"[TeeTeeTeeResolver] 레거시 API 확인 실패: {e}")

        reason_msg = f" (사유: {exclusion_reason})" if exclusion_reason else ""
        raise FileNotFoundError(
            f"'{clean_title or slug}' 프로젝트에서 다운로드 가능한 xdelta 패치 파일을 찾을 수 없습니다. "
            f"이 프로젝트는 사이트 관리자가 웹패치 전용으로 설정하여 별도의 xdelta/zip 패치 파일 다운로드를 제공하지 않습니다{reason_msg}."
        )
