"""MegaUp (megaup.net) 링크 리졸버.

GitHub 도구 및 브라우저 우회 기법(DrissionPage / FlareSolverr)을 참조하여
Cloudflare Turnstile 보호를 자동으로 우회하고 고속 직링크(megadl.boats)를 추출합니다.
"""

import logging
import os
import re
import time
from html import unescape
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .base import BaseResolver

logger = logging.getLogger(__name__)

MEGAUP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 30


def _is_megaup_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in (
        "megaup.net",
        "www.megaup.net",
        "download.megaup.net",
        "megadl.boats",
    )


from ..browser_solver import stealth_browser_session


def _extract_token_url(html: str) -> Optional[str]:
    match = re.search(
        r"https://download\.megaup\.net/\?url=[^'\"<>\s]+",
        html or "",
    )
    if not match:
        return None
    token_url = unescape(match.group(0))
    p = urlparse(token_url)
    if p.scheme == "https" and p.hostname == "download.megaup.net":
        return token_url
    return None


def _extract_final_url(html: str) -> Optional[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    for a in soup.find_all("a", href=True):
        raw_href = a.get("href")
        if not raw_href or not isinstance(raw_href, str):
            continue
        candidate = unescape(raw_href.strip())
        p = urlparse(candidate)
        if (
            p.scheme == "https"
            and p.hostname in ("megadl.boats", "download.megaup.net")
            and ("download_token=" in p.query or p.path.startswith("/download/"))
        ):
            return candidate
    return None


def _resolve_via_drission(page_url: str, timeout: int = 35) -> str:
    """DrissionPage 및 로컬 브라우저(Chrome/Edge)를 사용해 Cloudflare Turnstile을 자동 우회합니다.

    화면 밖(-2400, -2400) 가상 위치에서 무음 실행되므로 사용자 화면을 방해하지 않습니다.
    """
    with stealth_browser_session(
        prefix="megaup_session_", window_size="500,400"
    ) as page:
        logger.info(f"[MegaUp] 로컬 브라우저로 접속 시작: {page_url}")
        page.get(page_url)

        # 1단계: 다운로드 준비 카운트다운 타이머 버튼 대기 (최대 15초)
        target_href = None
        for _ in range(15):
            btn = page.ele(
                'css:.download-timer a, a[href*="download.megaup.net"]', timeout=1
            )
            if btn and btn.link:
                target_href = btn.link
                break
            time.sleep(1)

        if not target_href:
            # 페이지 내 HTML 정규식 재확인
            target_href = _extract_token_url(page.html)

        if not target_href:
            # 이미 직링크가 있는지 확인
            found_dl = _extract_final_url(page.html)
            if found_dl:
                return found_dl
            raise ValueError(
                "MegaUp 다운로드 토큰 버튼(download.megaup.net)을 찾을 수 없습니다."
            )

        # 2단계: 광고 팝업 스크립트를 피하고 정상 Referer 유지를 위해 target_href로 직접 이동
        logger.info(f"[MegaUp] 토큰 페이지로 브라우저 이동: {target_href[:60]}...")
        page.get(target_href)

        # 3단계: Cloudflare Turnstile 챌린지 통과 및 최종 직링크 추출 (최대 25초)
        final_link = None
        for step in range(25):
            time.sleep(1)
            dl_btn = page.ele('css:a[href*="megadl.boats"]', timeout=0.5)
            if dl_btn:
                href = dl_btn.attr("href")
                if href and ("megadl.boats" in href or "download_token=" in href):
                    final_link = href
                    break
            # 페이지 내 HTML에서도 확인
            final_link = _extract_final_url(page.html)
            if final_link:
                break

        if not final_link:
            raise ValueError(
                "Cloudflare Turnstile 검증 시간 초과 또는 최종 다운로드 직링크 추출 실패"
            )

        logger.info(f"[MegaUp] Cloudflare 우회 성공! 직링크 획득: {final_link[:80]}...")
        return final_link


def _resolve_via_flaresolverr(
    flaresolverr_url: str, page_url: str, token_url: Optional[str] = None
) -> str:
    """FlareSolverr 서비스(Docker 등)를 활용하여 Cloudflare를 우회합니다."""
    endpoint = flaresolverr_url.rstrip("/") + "/v1"

    # 1. token_url이 없으면 page_url에서 획득
    if not token_url:
        payload = {
            "cmd": "request.get",
            "url": page_url,
            "maxTimeout": 60000,
        }
        res = requests.post(endpoint, json=payload, timeout=70)
        res.raise_for_status()
        data = res.json()
        html = data.get("solution", {}).get("response", "")
        token_url = _extract_token_url(html)
        if not token_url:
            final = _extract_final_url(html)
            if final:
                return final
            raise ValueError("FlareSolverr: MegaUp 토큰 링크를 찾을 수 없습니다.")

    # 2. token_url 해석하여 megadl.boats 링크 획득
    payload = {
        "cmd": "request.get",
        "url": token_url,
        "maxTimeout": 60000,
    }
    res = requests.post(endpoint, json=payload, timeout=70)
    res.raise_for_status()
    data = res.json()
    html = data.get("solution", {}).get("response", "")
    final_url = _extract_final_url(html)
    if not final_url:
        raise ValueError("FlareSolverr: 최종 megadl.boats 링크 추출에 실패했습니다.")
    return final_url


class MegaUpResolver(BaseResolver):
    """MegaUp (megaup.net) 다운로드 링크 리졸버."""

    name = "MegaUp"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and _is_megaup_host(parsed.hostname)

    def resolve(self, url: str, options: dict | None = None) -> dict:
        page_url = url.strip()
        opts = options or {}

        # 0. 이미 megadl.boats 직링크인 경우 즉시 반환
        parsed = urlparse(page_url)
        if parsed.hostname == "megadl.boats" or "download_token=" in parsed.query:
            return {
                "url": page_url,
                "filename": "",
                "headers": {"User-Agent": MEGAUP_USER_AGENT},
                "cookies": "",
                "referer": "https://download.megaup.net/",
                "source_service": "MegaUp",
            }

        # 1. FlareSolverr가 설정되어 있는 경우 시도
        flaresolverr_url = (
            opts.get("flaresolverr_url")
            or opts.get("megaup_flaresolverr_url")
            or os.environ.get("FLARESOLVERR_URL")
            or ""
        ).strip()
        if flaresolverr_url:
            try:
                logger.info(
                    f"[MegaUp] FlareSolverr({flaresolverr_url})를 통해 우회 시도..."
                )
                final_url = _resolve_via_flaresolverr(flaresolverr_url, page_url)
                return {
                    "url": final_url,
                    "filename": "",
                    "headers": {"User-Agent": MEGAUP_USER_AGENT},
                    "cookies": "",
                    "referer": "https://download.megaup.net/",
                    "source_service": "MegaUp",
                }
            except Exception as e:
                logger.warning(
                    f"[MegaUp] FlareSolverr 우회 실패, 로컬 브라우저 자동 우회 시도: {e}"
                )

        # 2. 로컬 DrissionPage 자동 브라우저 우회 시도 (기본 권장 방식)
        drission_error = None
        try:
            import DrissionPage  # noqa

            final_url = _resolve_via_drission(page_url)
            return {
                "url": final_url,
                "filename": "",
                "headers": {"User-Agent": MEGAUP_USER_AGENT},
                "cookies": "",
                "referer": "https://download.megaup.net/",
                "source_service": "MegaUp",
            }
        except ImportError:
            drission_error = "DrissionPage 라이브러리가 설치되지 않았습니다."
        except Exception as e:
            drission_error = str(e)
            logger.warning(f"[MegaUp] DrissionPage 우회 실패: {e}")

        # 3. 폴백: 일반 requests 시도 (Cloudflare 없을 경우 대비)
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": MEGAUP_USER_AGENT,
                    "Referer": "https://megaup.net/",
                }
            )

            logger.info(f"MegaUp 기본 요청 시도: {page_url}")
            resp = session.get(page_url, timeout=TIMEOUT)
            if resp.status_code in (404, 410):
                raise FileNotFoundError(
                    "MegaUp 링크가 만료되었거나 파일이 삭제되었습니다."
                )
            resp.raise_for_status()

            token_url = _extract_token_url(resp.text)
            if not token_url:
                final = _extract_final_url(resp.text)
                if final:
                    return {
                        "url": final,
                        "filename": "",
                        "headers": {"User-Agent": MEGAUP_USER_AGENT},
                        "cookies": "",
                        "referer": page_url,
                        "source_service": "MegaUp",
                    }
                raise ValueError(
                    f"MegaUp 다운로드 토큰 링크를 찾을 수 없습니다. ({drission_error or ''})"
                )

            token_resp = session.get(
                token_url,
                headers={"Referer": page_url},
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            if (
                token_resp.status_code == 403
                or "challenges.cloudflare.com" in token_resp.text
                or "Just a moment..." in token_resp.text
            ):
                msg = "MegaUp 다운로드 서버(download.megaup.net)에 Cloudflare Turnstile 챌린지가 적용되어 있습니다.\n"
                if drission_error:
                    msg += f"자동 브라우저 우회 오류: {drission_error}\n"
                msg += (
                    "- PC에 Chrome 또는 Edge 브라우저가 정상 설치되어 있는지 확인해 주세요.\n"
                    "- 또는 브라우저에서 'DOWNLOAD NOW' 버튼을 누른 후 생성되는 최종 직링크(https://megadl.boats/download/...) 주소를 직접 붙여넣으셔도 바로 다운로드됩니다."
                )
                raise ValueError(msg)

            token_resp.raise_for_status()
            final_url = _extract_final_url(token_resp.text) or token_resp.url

            cookies = "; ".join(
                f"{k}={v}" for k, v in session.cookies.get_dict().items()
            )

            return {
                "url": final_url,
                "filename": "",
                "headers": {"User-Agent": MEGAUP_USER_AGENT},
                "cookies": cookies,
                "referer": page_url,
                "source_service": "MegaUp",
            }
