"""Rootz (rootz.so) 링크 리졸버."""

import html
import logging
import re
import urllib.parse
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 20


def _is_rootz_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "rootz.so" or host.endswith(".rootz.so")


class RootzResolver(BaseResolver):
    """Rootz.so 공유 링크(/d/<shortId>)를 직링크로 변환하는 리졸버."""

    name: str = "Rootz"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_rootz_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(re.match(r"^/d/([a-zA-Z0-9_-]+)", path))
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        path = parsed.path or ""
        m = re.match(r"^/d/([a-zA-Z0-9_-]+)", path)
        if not m:
            raise ValueError(f"올바른 Rootz 링크 형식이 아닙니다: {url}")

        short_id = m.group(1)
        canonical_url = f"https://www.rootz.so/d/{short_id}"

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
            }
        )

        title_filename: Optional[str] = None
        # 1. 파일 페이지 접속하여 제목(파일명) 확인
        try:
            page_resp = session.get(canonical_url, timeout=TIMEOUT)
            if page_resp.status_code == 404:
                raise ValueError(f"파일을 찾을 수 없습니다(404 Not Found): {url}")
            page_resp.raise_for_status()

            title_m = re.search(
                r"<title>(.*?)</title>", page_resp.text, re.IGNORECASE | re.DOTALL
            )
            if title_m:
                raw_title = html.unescape(title_m.group(1).strip())
                # ' - Rootz' 접미사 제거
                cleaned_title = re.sub(
                    r"\s*-\s*Rootz\s*$", "", raw_title, flags=re.IGNORECASE
                ).strip()
                if cleaned_title and cleaned_title.lower() != "rootz":
                    title_filename = cleaned_title
        except ValueError:
            raise
        except Exception as e:
            logger.warning(
                f"Rootz 메인 페이지 파싱 실패 ({e}), 프록시 다운로드를 계속 시도합니다."
            )

        # 2. 직링크 발급을 위한 프록시 다운로드 엔드포인트 호출
        proxy_url = f"https://www.rootz.so/api/files/proxy-download/{short_id}"
        proxy_headers = {
            "Referer": canonical_url,
            "Accept": "*/*",
        }

        try:
            dl_resp = session.get(
                proxy_url,
                headers=proxy_headers,
                allow_redirects=False,
                timeout=TIMEOUT,
            )
        except Exception as e:
            raise ValueError(f"Rootz 다운로드 주소 요청 실패: {e}")

        if dl_resp.status_code in (301, 302, 303, 307, 308):
            direct_url = dl_resp.headers.get("Location")
            if not direct_url:
                raise ValueError(
                    "Rootz 리다이렉트 응답에 다운로드 Location이 없습니다."
                )
        elif dl_resp.status_code == 200:
            direct_url = proxy_url
        elif dl_resp.status_code == 404:
            raise ValueError("Rootz 파일이 존재하지 않거나 삭제되었습니다.")
        elif dl_resp.status_code == 403:
            raise ValueError(
                "Rootz 다운로드 접근이 거부되었습니다 (비밀번호 또는 권한 필요)."
            )
        else:
            raise ValueError(
                f"Rootz 다운로드 링크 요청 실패 (HTTP {dl_resp.status_code})"
            )

        # 3. 파일명 및 파일 크기 파악
        filename = title_filename
        file_size: Optional[int] = None

        # URL 쿼리 파라미터에서 filename 확인
        parsed_direct = urlparse(direct_url)
        query_params = parse_qs(parsed_direct.query)
        if "filename" in query_params and query_params["filename"]:
            filename = urllib.parse.unquote(query_params["filename"][0])

        if not filename:
            path_part = parsed_direct.path.split("/")[-1]
            if path_part:
                filename = urllib.parse.unquote(path_part)
            else:
                filename = f"rootz_{short_id}.bin"

        # Content-Length 헤더 확인 시도 (HEAD)
        try:
            head_headers = {
                "User-Agent": USER_AGENT,
                "Referer": "https://www.rootz.so/",
            }
            head_resp = session.head(
                direct_url, headers=head_headers, allow_redirects=True, timeout=10
            )
            if head_resp.status_code == 200:
                if "Content-Length" in head_resp.headers:
                    try:
                        file_size = int(head_resp.headers["Content-Length"])
                    except (ValueError, TypeError):
                        pass
        except Exception as e:
            logger.debug(f"Rootz 직링크 HEAD 요청 생략/실패: {e}")

        return {
            "url": direct_url,
            "filename": filename,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": "https://www.rootz.so/",
            },
            "cookies": session.cookies.get_dict(),
            "referer": "https://www.rootz.so/",
            "file_size": file_size,
            "source_service": "Rootz",
        }
