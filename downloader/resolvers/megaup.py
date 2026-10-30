"""MegaUp (megaup.net) 링크 리졸버.

GitHub 도구 및 브라우저 우회 기법(DrissionPage / FlareSolverr)을 참조하여
Cloudflare Turnstile 보호 및 광고 하이재킹을 자동으로 우회하고 고속 직링크(?download_token=...)를 추출합니다.
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
    return (
        host in ("megaup.net", "www.megaup.net", "download.megaup.net")
        or host.endswith(".megaup.net")
        or host == "megadl.boats"
        or host.endswith(".megadl.boats")
        or host.endswith("megaupdownup.org")
    )


def _is_direct_download_url(url: str) -> bool:
    if not url:
        return False
    p = urlparse(url)
    return (
        "download_token=" in p.query
        or p.hostname == "megadl.boats"
        or (bool(p.hostname) and (p.hostname.endswith(".megaupdownup.org") or p.hostname.endswith(".megadl.boats")))
        or (p.hostname in ("megadl.boats", "download.megaup.net") and p.path.startswith("/download/"))
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
        if _is_direct_download_url(candidate):
            return candidate
    return None


def _resolve_via_drission(page_url: str, timeout: int = 40) -> str:
    """DrissionPage 및 로컬 브라우저(Chrome/Edge)를 사용해 Cloudflare Turnstile 및 광고를 우회하고
    고속 직링크(?download_token=...)를 추출합니다.

    화면 밖(-2400, -2400) 가상 위치에서 무음 실행되므로 사용자 화면을 방해하지 않습니다.
    """
    with stealth_browser_session(
        prefix="megaup_session_", window_size="1024,768", window_position="-2400,-2400"
    ) as page:
        # 0단계: 악성 광고 팝업 및 리다이렉트 스크립트 도메인 차단
        try:
            page.run_cdp("Network.enable")
            page.run_cdp(
                "Network.setBlockedURLs",
                urls=[
                    "*pcscango*",
                    "*shortenoverleg*",
                    "*geogenyveered*",
                    "*pubadx*",
                    "*a-ads*",
                    "*theharityhild*",
                ],
            )
        except Exception as e:
            logger.debug(f"[MegaUp] CDP 광고 차단 설정 무시됨: {e}")

        try:
            page.run_js("window.open = function() { return null; };")
        except Exception:
            pass

        # 네트워크 패킷 리스너 가동 (직링크 다운로드 패킷 실시간 감지)
        try:
            page.listen.start(["download_token", "megaupdownup", "megadl.boats"])
        except Exception as e:
            logger.debug(f"[MegaUp] page.listen 시작 실패: {e}")

        parsed_input = urlparse(page_url)
        main_page_url = page_url

        if parsed_input.hostname == "download.megaup.net":
            # 이미 download.megaup.net 토큰 URL로 입력된 경우
            # 토큰 페이지에 접속하여 원본 메인 URL(https://megaup.net/{id})을 복원
            logger.info(f"[MegaUp] 토큰 페이지에서 원본 메인 URL 복원 시도: {page_url[:60]}...")
            page.get(page_url)
            for _ in range(12):
                time.sleep(1)
                title = page.title or ""
                if "Download Page" in title or ("megaup" in (page.url or "") and "잠시" not in title and "Just a moment" not in title):
                    break
            
            m = re.search(r"https://megaup\.net/([a-zA-Z0-9]+)\?pt=", page.html)
            if m:
                main_page_url = f"https://megaup.net/{m.group(1)}"
                logger.info(f"[MegaUp] 원본 메인 URL 복원 성공: {main_page_url}")
            else:
                for a in page.eles('css:a[href*="megaup.net"]'):
                    href = a.attr("href") or ""
                    p = urlparse(href)
                    if p.hostname in ("megaup.net", "www.megaup.net") and p.path.strip("/") and not p.path.startswith(("/contact", "/terms", "/upgrade", "/faq", "/account")):
                        main_page_url = href
                        logger.info(f"[MegaUp] 토큰 페이지 링크에서 원본 메인 URL 감지: {main_page_url}")
                        break

        # 1단계: 메인 페이지에서 카운트다운 후 정식 토큰 링크 획득
        logger.info(f"[MegaUp] 메인 페이지로 로컬 브라우저 접속: {main_page_url}")
        page.get(main_page_url)

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
            target_href = _extract_token_url(page.html)

        if not target_href:
            found_dl = _extract_final_url(page.html)
            if found_dl:
                return found_dl
            raise ValueError(
                "MegaUp 다운로드 토큰 버튼(download.megaup.net)을 찾을 수 없습니다."
            )

        logger.info(f"[MegaUp] 토큰 페이지로 브라우저 이동: {target_href[:60]}...")
        page.get(target_href)

        # 2단계: Cloudflare Turnstile 검증 대기
        for _ in range(15):
            time.sleep(1)
            title = page.title or ""
            if (
                "Download Page" in title
                or ("megaup" in (page.url or "") and "잠시" not in title and "Just a moment" not in title)
            ):
                logger.info(f"[MegaUp] Cloudflare Turnstile 통과 확인 (제목: {title})")
                break

            # 혹시 Turnstile iframe이 유지되면 체크박스 클릭 보조 시도
            try:
                cf_iframe = page.ele('css:iframe[src*="cloudflare"], iframe[src*="turnstile"]', timeout=0.2)
                if cf_iframe:
                    cf_iframe.click.at(30, 32)
            except Exception:
                pass

        # 3단계: 다운로드 페이지(#btndownload) 활성화 대기 및 클릭
        logger.info("[MegaUp] 다운로드 버튼 활성화 대기 중...")
        for _ in range(15):
            time.sleep(1)

            # 혹시 네트워크 패킷에 이미 직링크가 포착되었는지 확인
            try:
                pkt = page.listen.wait(timeout=0.1)
                if pkt and getattr(pkt, "url", None) and _is_direct_download_url(pkt.url):
                    logger.info(f"[MegaUp] 네트워크에서 직링크 캡처 성공: {pkt.url[:80]}...")
                    return pkt.url
            except Exception:
                pass

            btn = page.ele('css:#btndownload, button[id*="download"]', timeout=0.5)
            if btn:
                cls = btn.attr("class") or ""
                txt = btn.text or ""
                if "disabled" not in cls and ("DOWNLOAD" in txt.upper() or "NOW" in txt.upper() or not txt):
                    logger.info("[MegaUp] 다운로드 버튼 준비 완료! 클릭 실행")
                    try:
                        page.run_js("document.getElementById('btndownload').click();")
                    except Exception:
                        try:
                            btn.click()
                        except Exception:
                            pass
                    break

        # 4단계: 직링크 획득 (패킷 캡처 대기, 열린 탭, 또는 페이지 링크)
        try:
            res_pkt = page.listen.wait(timeout=10)
            if res_pkt and getattr(res_pkt, "url", None) and _is_direct_download_url(res_pkt.url):
                logger.info(f"[MegaUp] 우회 성공! 직링크 캡처: {res_pkt.url[:80]}...")
                return res_pkt.url
        except Exception as e:
            logger.debug(f"[MegaUp] listen.wait 예외: {e}")

        final_link = None
        for _ in range(5):
            time.sleep(1)

            # 브라우저 주소창 또는 열린 탭 확인
            try:
                for tid in page.tab_ids:
                    tab = page.get_tab(tid)
                    if _is_direct_download_url(tab.url):
                        final_link = tab.url
                        break
            except Exception:
                pass

            if final_link:
                break

            # 페이지 내 링크 확인
            try:
                found = _extract_final_url(page.html)
                if found:
                    final_link = found
                    break
            except Exception:
                pass

        if not final_link:
            raise ValueError(
                "Cloudflare Turnstile 검증 시간 초과 또는 최종 다운로드 직링크 추출 실패"
            )

        logger.info(f"[MegaUp] Cloudflare 우회 성공! 최종 직링크 획득: {final_link[:80]}...")
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

    # 2. token_url 해석하여 직링크 획득
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
        raise ValueError("FlareSolverr: 최종 직링크 추출에 실패했습니다.")
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

        # 0. 이미 직링크(?download_token=...)인 경우 즉시 반환
        if _is_direct_download_url(page_url):
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
                    "- 또는 브라우저에서 다운로드 시작 후 생성되는 최종 직링크 주소를 직접 붙여넣으셔도 바로 다운로드됩니다."
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
