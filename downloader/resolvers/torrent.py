"""BitTorrent 마그넷 링크 및 .torrent 파일 리졸버."""

import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from config import TORRENTS_DIR as TORRENTS_CACHE_DIR

from ..torrent_engine import TorrentDownloadTask, parse_torrent_file
from .base import BaseResolver

logger = logging.getLogger(__name__)


class TorrentResolver(BaseResolver):
    """마그넷(magnet:?...) 및 .torrent 파일 다운로드 리졸버."""

    name = "BitTorrent"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        u = url.strip()
        if u.lower().startswith("magnet:?"):
            return True
        if u.lower().endswith(".torrent") or ".torrent?" in u.lower():
            return True
        if os.path.isfile(u) and u.lower().endswith(".torrent"):
            return True
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        url = url.strip()
        opts = options or {}
        explicit_fn = opts.get("filename")

        # 1. 마그넷 링크
        if url.lower().startswith("magnet:?"):
            parsed = urlparse(url)
            qs = parse_qs(parsed.query)

            dn = ""
            if "dn" in qs and qs["dn"]:
                dn = unquote(qs["dn"][0])

            if not dn:
                xt = qs.get("xt", [""])[0]
                m = re.search(r"urn:btih:([a-zA-Z0-9]+)", xt, re.IGNORECASE)
                if m:
                    dn = f"Torrent_{m.group(1)[:10]}"
                else:
                    dn = "Torrent_Download"

            filename = explicit_fn or dn

            return {
                "url": url,
                "filename": filename,
                "source_service": "BitTorrent (마그넷)",
                "task_factory": lambda job, dest_dir, progress_cb: TorrentDownloadTask(
                    source=job.resolved_url,
                    dest_dir=dest_dir,
                    filename=job.filename,
                    partial_id=job.id,
                    progress_callback=progress_cb,
                ),
            }

        # 2. 로컬 .torrent 파일 경로
        elif os.path.isfile(url) and url.lower().endswith(".torrent"):
            try:
                info = parse_torrent_file(url)
                filename = explicit_fn or info.get("name") or os.path.basename(url)
            except Exception as e:
                logger.warning(f".torrent 메타데이터 파싱 실패: {e}")
                filename = explicit_fn or os.path.basename(url)

            return {
                "url": url,
                "filename": filename,
                "source_service": "BitTorrent (.torrent)",
                "task_factory": lambda job, dest_dir, progress_cb: TorrentDownloadTask(
                    source=job.resolved_url,
                    dest_dir=dest_dir,
                    filename=job.filename,
                    partial_id=job.id,
                    progress_callback=progress_cb,
                ),
            }

        # 3. 원격 웹 .torrent 파일 링크 (HTTP/HTTPS)
        else:
            TORRENTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info(f"원격 .torrent 파일 다운로드 시도: {url}")

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            }
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()

            with tempfile.NamedTemporaryFile(
                dir=str(TORRENTS_CACHE_DIR),
                suffix=".torrent",
                delete=False,
            ) as temp_torrent:
                temp_torrent.write(resp.content)
                local_torrent_path = temp_torrent.name
            try:
                info = parse_torrent_file(local_torrent_path)
                filename = (
                    explicit_fn or info.get("name") or Path(urlparse(url).path).name
                )
            except Exception as e:
                logger.warning(f"원격 .torrent 파일 파싱 실패: {e}")
                filename = (
                    explicit_fn or Path(urlparse(url).path).name or "Torrent_Download"
                )

            return {
                "url": local_torrent_path,
                "filename": filename,
                "source_service": "BitTorrent (원격 .torrent)",
                "task_factory": lambda job, dest_dir, progress_cb: TorrentDownloadTask(
                    source=job.resolved_url,
                    dest_dir=dest_dir,
                    filename=job.filename,
                    partial_id=job.id,
                    progress_callback=progress_cb,
                ),
            }
