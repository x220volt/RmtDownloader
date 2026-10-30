"""Pixeldrain 링크 리졸버."""

import logging
import re
from urllib.parse import urlparse

import requests

from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15


def _is_pixeldrain_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("pixeldrain.com", "pixeldrain.net", "pixeldra.in") or host.endswith(
        (".pixeldrain.com", ".pixeldrain.net")
    )


class PixeldrainResolver(BaseResolver):
    """Pixeldrain 파일 공유 링크(/u/<id> 등)를 직링크로 변환하는 리졸버."""

    name: str = "Pixeldrain"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_pixeldrain_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(
                re.match(r"^/(u|api/file|d)/([a-zA-Z0-9_-]+)", path)
                or re.match(r"^/l/([a-zA-Z0-9_-]+)", path)
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        path = parsed.path or ""

        m_file = re.match(r"^/(?:u|api/file|d)/([a-zA-Z0-9_-]+)", path)
        if not m_file:
            m_list = re.match(r"^/l/([a-zA-Z0-9_-]+)", path)
            if m_list:
                list_id = m_list.group(1)
                raise ValueError(
                    f"Pixeldrain 파일 목록(리스트 ID: {list_id}) 링크입니다. 개별 파일 링크(/u/<id>)를 입력해주세요."
                )
            raise ValueError(f"지원하지 않는 Pixeldrain URL 형태입니다: {url}")

        file_id = m_file.group(1)
        api_info_url = f"https://pixeldrain.com/api/file/{file_id}/info"
        direct_dl_url = f"https://pixeldrain.com/api/file/{file_id}?download"

        filename = file_id
        file_size = None

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        try:
            resp = requests.get(api_info_url, headers=headers, timeout=TIMEOUT)
            if resp.status_code == 404:
                raise ValueError(
                    f"Pixeldrain 파일(ID: {file_id})을 찾을 수 없거나 이미 삭제/만료되었습니다 (404)."
                )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    filename = data.get("name") or file_id
                    file_size = data.get("size")
                    logger.info(
                        "Pixeldrain 메타데이터 조회 성공: %s (크기: %s bytes)",
                        filename,
                        file_size,
                    )
        except ValueError:
            raise
        except Exception as e:
            logger.warning(
                "Pixeldrain 메타데이터 조회 중 오류 (직링크로 대체 진행): %s", e
            )

        return {
            "url": direct_dl_url,
            "filename": filename,
            "headers": {"User-Agent": USER_AGENT},
            "options": options or {},
            "service_name": "Pixeldrain",
        }
