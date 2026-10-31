"""x-share.net 파일 및 묶음(bundle) 리졸버."""

import logging
import re
import time
from urllib.parse import urlparse

import requests

from ..browser_solver import stealth_browser_session
from .base import BaseResolver

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?x-share\.net/(?:b|s|api/download)/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


def _resolve_xshare_via_drission(page_url: str, timeout: int = 30) -> tuple[str, str]:
    """로컬 브라우저(DrissionPage)를 무음(화면 밖)으로 띄워 x-share.net Turnstile을 자동 통과하고 (key, filename)을 획득합니다."""
    with stealth_browser_session(
        prefix="xshare_session_", window_size="800,600"
    ) as page:
        logger.info(
            f"[X-Share] Cloudflare Turnstile 우회를 위해 로컬 브라우저 접속 시작: {page_url}"
        )
        page.get(page_url)

        for _ in range(timeout):
            time.sleep(1)
            btn = page.ele("#dl-btn")
            btn_text = page.ele("#dl-btn-text").text if page.ele("#dl-btn-text") else ""
            if btn and "다운로드" in btn_text:
                res = page.run_js("""
                    return new Promise(async (resolve) => {
                        try {
                            const r = await fetch('/api/download-token', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({fileId: FILE_ID, turnstileToken: dlTurnstileToken, referer: PAGE_REFERER})
                            });
                            const d = await r.json();
                            resolve({ok: r.ok, status: r.status, data: d, file: fileData});
                        } catch(e) {
                            resolve({ok: false, error: String(e)});
                        }
                    });
                """)
                if res and res.get("ok") and res.get("data", {}).get("key"):
                    key = res["data"]["key"]
                    filename = (res.get("file") or {}).get("name", "")
                    logger.info(
                        f"[X-Share] Cloudflare Turnstile 우회 성공! Key 획득 ({filename})"
                    )
                    return key, filename

        raise ValueError("x-share.net 보안 검증(Turnstile) 대기 시간 초과")


class XShareResolver(BaseResolver):
    """x-share.net 개별 파일(/s/...) 및 묶음(/b/...) 리졸버."""

    name = "X-Share"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        return bool(_URL_PATTERN.search(url))

    def _get_download_key(self, file_id: str, referer_url: str) -> str:
        """/api/download-token 엔드포인트에서 파일 다운로드 key를 발급받습니다."""
        token_url = "https://x-share.net/api/download-token"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": referer_url,
            "Origin": "https://x-share.net",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        }
        payload = {
            "fileId": file_id,
            "turnstileToken": "",
            "referer": referer_url,
        }
        try:
            resp = requests.post(token_url, json=payload, headers=headers, timeout=10)
            if resp.ok:
                data = resp.json()
                key = data.get("key")
                if key:
                    return key
        except Exception as e:
            logger.debug(f"X-Share 직접 토큰 발급 예외: {e}")

        # Cloudflare Turnstile 보안 검증이 필요한 경우 로컬 브라우저 자동 우회 시도
        logger.info(
            "[X-Share] 보안 검증(Turnstile) 감지: 로컬 브라우저(DrissionPage) 자동 우회 진행..."
        )
        page_url = (
            referer_url if "/s/" in referer_url else f"https://x-share.net/s/{file_id}"
        )
        key, _ = _resolve_xshare_via_drission(page_url)
        return key

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        path = parsed.path

        # 1. 묶음 다운로드 (/b/<bundleId>)
        if "/b/" in path:
            m = re.search(r"/b/([a-zA-Z0-9_-]+)", path)
            if not m:
                raise ValueError(
                    "올바른 x-share.net 묶음(bundle) ID를 찾을 수 없습니다."
                )
            bundle_id = m.group(1)

            api_url = f"https://x-share.net/api/bundle/{bundle_id}"
            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": url,
                "Accept": "application/json, text/plain, */*",
            }
            resp = requests.get(api_url, headers=headers, timeout=15)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    "x-share.net 묶음을 찾을 수 없습니다 (만료되었거나 삭제됨)."
                )
            if not resp.ok:
                raise ValueError(
                    f"x-share.net 묶음 정보 조회 실패 (HTTP {resp.status_code})"
                )

            bundle_data = resp.json()
            if bundle_data.get("expired"):
                raise FileNotFoundError("x-share.net 묶음 보관 기간이 만료되었습니다.")

            files = bundle_data.get("files", [])
            if not files:
                raise FileNotFoundError(
                    "x-share.net 묶음에 다운로드 가능한 파일이 없습니다."
                )

            logger.info(
                f"x-share.net 묶음({bundle_id})에서 {len(files)}개의 파일을 발견했습니다."
            )

            # 첫 번째 파일의 다운로드 key 발급
            first_file = files[0]
            first_key = self._get_download_key(first_file["id"], url)
            first_dl_url = (
                f"https://x-share.net/api/download/{first_file['id']}?key={first_key}"
            )

            if len(files) == 1:
                return {
                    "url": first_dl_url,
                    "filename": first_file.get("name", ""),
                    "headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": url,
                    },
                    "source_service": "X-Share",
                }

            # 다중 파일: manager의 extra_files 큐 등록 지원
            file_items = [
                {
                    "name": f.get("name", ""),
                    "url": f"https://x-share.net/s/{f['id']}",
                    "size": f.get("size", 0),
                    "referer": url,
                }
                for f in files
            ]

            return {
                "url": first_dl_url,
                "filename": first_file.get("name", ""),
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": url,
                },
                "source_service": "X-Share",
                "files": file_items,
                "subfolder": f"xshare_{bundle_id}",
            }

        # 2. 개별 파일 (/s/<fileId>)
        elif "/s/" in path:
            m = re.search(r"/s/([a-zA-Z0-9_-]+)", path)
            if not m:
                raise ValueError("올바른 x-share.net 파일 ID를 찾을 수 없습니다.")
            file_id = m.group(1)

            file_api_url = f"https://x-share.net/api/file/{file_id}"
            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": url,
                "Accept": "application/json, text/plain, */*",
            }
            resp = requests.get(file_api_url, headers=headers, timeout=15)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    "x-share.net 파일을 찾을 수 없습니다 (만료되었거나 삭제됨)."
                )
            if not resp.ok:
                raise ValueError(
                    f"x-share.net 파일 정보 조회 실패 (HTTP {resp.status_code})"
                )

            file_data = resp.json()
            filename = file_data.get("name", "")

            key = self._get_download_key(file_id, url)
            dl_url = f"https://x-share.net/api/download/{file_id}?key={key}"

            return {
                "url": dl_url,
                "filename": filename,
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": url,
                },
                "source_service": "X-Share",
            }

        # 3. 직접 다운로드 엔드포인트 (/api/download/<fileId>?key=...)
        elif "/api/download/" in path:
            return {
                "url": url,
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://x-share.net/",
                },
                "source_service": "X-Share",
            }

        raise ValueError(f"지원하지 않는 x-share.net URL 형식입니다: {url}")
