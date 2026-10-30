"""일반 다이렉트 링크 리졸버."""

import os
from urllib.parse import unquote, urlparse

from .base import BaseResolver

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class DirectResolver(BaseResolver):
    """일반 HTTP/HTTPS 직링크를 처리하는 리졸버."""

    name = "Direct Link"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    def resolve(self, url: str, options: dict | None = None) -> dict:
        url = url.strip()
        parsed = urlparse(url)
        path = unquote(parsed.path or "")
        filename = (options or {}).get("filename") or os.path.basename(path)
        if "?" in filename:
            filename = filename.split("?")[0]

        return {
            "url": url,
            "filename": filename,
            "headers": {"User-Agent": DEFAULT_USER_AGENT},
            "cookies": "",
            "referer": "",
            "source_service": "Direct",
        }
