"""Google Drive (구글 드라이브) 파일 공유 링크 리졸버 모듈."""

import logging
import re
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

GDRIVE_HOSTS = (
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
)


def extract_gdrive_id(url: str) -> str:
    """Google Drive URL에서 파일 ID를 추출합니다."""
    if not url:
        return ""

    # 1. /file/d/{id} or /document/d/{id}
    m = re.search(r"/(?:file|document)/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    # 2. Query param: id={id}
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "id" in qs and qs["id"]:
        return qs["id"][0]

    # 3. /d/{id}
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    return ""


class GoogleDriveResolver(BaseResolver):
    """Google Drive 파일 공유 링크 리졸버 (소용량 및 대용량 바이러스 검사 경고 자동 통과 지원)."""

    name = "gdrive"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or "").lower()
            if host in GDRIVE_HOSTS:
                # 파일 ID가 있거나 드라이브 경로가 포함된 경우
                return (
                    bool(extract_gdrive_id(url))
                    or "/drive/folders/" in parsed.path
                    or "/folders/" in parsed.path
                )
        except Exception:
            pass
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        """
        Google Drive 공유 링크를 다운로드 가능한 직링크로 변환합니다.
        대용량 파일 바이러스 검사 경고(HTML confirmation form/link) 자동 승인 및 토큰 전달 지원.
        """
        if "/drive/folders/" in url or "/folders/" in url:
            raise ValueError(
                "Google Drive 폴더 링크는 직접 다운로드가 지원되지 않습니다. "
                "폴더 내 개별 파일의 공유 링크(https://drive.google.com/file/d/.../view)를 입력해주세요."
            )

        file_id = extract_gdrive_id(url)
        if not file_id:
            raise ValueError(f"Google Drive 링크에서 파일 ID를 찾을 수 없습니다: {url}")

        with requests.Session() as session:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            session.headers.update(headers)

            filename = ""

            # 1. View 페이지 메타데이터(og:title, title)에서 파일명 추출 시도
            try:
                view_url = f"https://drive.google.com/file/d/{file_id}/view"
                resp_view = session.get(view_url, timeout=10)
                if resp_view.status_code == 200:
                    soup = BeautifulSoup(resp_view.text, "html.parser")
                    og_title = soup.find("meta", property="og:title")
                    og_content = og_title.get("content") if og_title else None
                    if isinstance(og_content, str) and og_content.strip():
                        filename = og_content.strip()
                    elif soup.title and soup.title.string:
                        t = soup.title.string.strip()
                        t = re.sub(
                            r"\s*-\s*Google (?:Drive|드라이브)\s*$", "", t, flags=re.I
                        )
                        if t:
                            filename = t
                elif resp_view.status_code == 404:
                    raise FileNotFoundError(
                        f"Google Drive 파일을 찾을 수 없거나 삭제되었습니다 (404 Not Found): id={file_id}"
                    )
                elif resp_view.status_code == 403:
                    raise PermissionError(
                        "Google Drive 파일 접근 권한이 없습니다 (403 Forbidden). 파일 공유 권한을 확인해주세요."
                    )
            except (FileNotFoundError, PermissionError):
                raise
            except Exception as e:
                logger.debug(
                    f"Google Drive view 페이지 메타데이터 파싱 예외 (무시 후 다운로드 계속): {e}"
                )

            # 2. 직링크 요청
            dl_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download"
            logger.info(f"[GoogleDriveResolver] 다운로드 엔드포인트 요청: {dl_url}")
            with session.get(
                dl_url, stream=True, allow_redirects=True, timeout=15
            ) as resp:
                if resp.status_code == 404:
                    raise FileNotFoundError(
                        f"Google Drive 파일을 찾을 수 없거나 삭제되었습니다 (404 Not Found): id={file_id}"
                    )
                elif resp.status_code == 403:
                    raise PermissionError(
                        "Google Drive 파일 접근 권한이 없거나 다운로드가 금지되어 있습니다 (403 Forbidden)."
                    )

                content_type = resp.headers.get("Content-Type", "").lower()
                content_disp = resp.headers.get("Content-Disposition", "")
                final_url = resp.url

                # Content-Disposition 헤더에서 파일명 파싱
                if content_disp:
                    m_fn = re.search(
                        r"filename\*?=(?:UTF-8\'\')?\"?([^;\r\n\"]+)\"?", content_disp
                    )
                    if m_fn:
                        extracted_fn = urllib.parse.unquote(m_fn.group(1).strip("\"'"))
                        if extracted_fn:
                            filename = extracted_fn

                # 3. HTML 응답인 경우: 대용량 파일 바이러스 검사 경고 페이지 또는 에러 페이지 분석
                if "text/html" in content_type:
                    text = resp.text

                    # 할당량 초과 또는 권한 필요 여부 확인
                    if (
                        "Too many users have viewed or downloaded this file recently"
                        in text
                        or "용량 초과" in text
                    ):
                        raise RuntimeError(
                            "Google Drive 일일 다운로드 할당량이 초과되었습니다. "
                            "(Too many users have downloaded this file recently) 잠시 후 다시 시도해주세요."
                        )
                    if (
                        "You need access" in text
                        or "액세스 권한 필요" in text
                        or "로그인" in text
                        and "권한" in text
                    ):
                        raise PermissionError(
                            "Google Drive 파일 접근 권한이 필요합니다. 공유 설정을 확인하세요."
                        )

                    soup = BeautifulSoup(text, "html.parser")

                    # Form 기반 확인 토큰 추출 (#download-form 또는 form[action*='download'])
                    form = soup.select_one("#download-form") or soup.select_one(
                        "form[action*='download']"
                    )
                    if form:
                        raw_action = form.get("action")
                        action = (
                            str(raw_action)
                            if raw_action
                            else "https://drive.usercontent.google.com/download"
                        )
                        if not action.startswith("http"):
                            action = urllib.parse.urljoin(final_url, action)
                        params = {}
                        for inp in form.find_all("input"):
                            name = inp.get("name")
                            val = inp.get("value")
                            if name and val is not None:
                                params[str(name)] = str(val)
                        if params:
                            final_url = (
                                action
                                + ("&" if "?" in action else "?")
                                + urllib.parse.urlencode(params)
                            )
                            logger.info(
                                f"[GoogleDriveResolver] 대용량 바이러스 경고 폼 파싱 완료: {final_url}"
                            )
                    else:
                        # 링크 기반 확인 토큰 추출 (#uc-download-link 등)
                        link = soup.select_one("#uc-download-link") or soup.select_one(
                            "a[href*='export=download']"
                        )
                        href_val = link.get("href") if link else None
                        if href_val and isinstance(href_val, str):
                            final_url = urllib.parse.urljoin(final_url, href_val)
                            logger.info(
                                f"[GoogleDriveResolver] 대용량 다운로드 링크 파싱 완료: {final_url}"
                            )
                        else:
                            m_href = re.search(
                                r'href="([^"]*export=download[^"]*)"', text
                            )
                            if m_href:
                                final_url = urllib.parse.urljoin(
                                    final_url, m_href.group(1).replace("&amp;", "&")
                                )
                                logger.info(
                                    f"[GoogleDriveResolver] 정규식 다운로드 링크 파싱 완료: {final_url}"
                                )

                if not filename:
                    filename = f"gdrive_{file_id}.bin"

                # 세션 쿠키 수집
                cookies_dict = session.cookies.get_dict()
                cookie_str = (
                    "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
                    if cookies_dict
                    else ""
                )

                resolved_data: dict[str, Any] = {
                    "url": final_url,
                    "filename": sanitize_filename(filename),
                    "headers": {
                        "User-Agent": USER_AGENT,
                        "Accept": "*/*",
                    },
                    "cookies": cookie_str,
                    "referer": f"https://drive.google.com/file/d/{file_id}/view",
                    "source_service": "Google Drive",
                }

                # 크기 헤더가 존재하는 경우 반영
                cl = resp.headers.get("Content-Length")
                if (
                    cl
                    and cl.isdigit()
                    and int(cl) > 0
                    and "text/html" not in content_type
                ):
                    resolved_data["size"] = int(cl)

                return resolved_data
