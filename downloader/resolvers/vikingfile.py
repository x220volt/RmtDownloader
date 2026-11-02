"""VikingFile (vikingfile.com, vik1ngfile.site) 링크 리졸버.

Cloudflare Turnstile 보호를 DrissionPage(로컬 스텔스 브라우저) 또는 FlareSolverr를 통해
자동으로 우회하고 고속 직링크(S3/OVH 클라우드 스토리지)를 추출합니다.
"""

import logging
import os
import time
from typing import Any, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..browser_solver import stealth_browser_session
from ..engine import extract_filename_from_headers
from .base import BaseResolver

logger = logging.getLogger(__name__)

VIKINGFILE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 30


def _is_vikingfile_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("vikingfile.com", "vik1ngfile.site") or host.endswith(
        (".vikingfile.com", ".vik1ngfile.site")
    )


def _extract_download_url(html: str, page_url: str) -> Optional[str]:
    """HTML 본문에서 VikingFile /d/ 다운로드 링크 또는 #download-link 요소를 추출합니다."""
    soup = BeautifulSoup(html or "", "html.parser")

    # 1. #download-link 태그 우선 확인
    dl_btn = soup.find(id="download-link")
    if dl_btn and dl_btn.get("href"):
        href = str(dl_btn.get("href")).strip()
        if href and (
            href.startswith(("http://", "https://", "/"))
            and not href.startswith(("#", "javascript:"))
        ):
            candidate = urljoin(page_url, href)
            p = urlparse(candidate)
            if p.scheme in ("http", "https") and (
                _is_vikingfile_host(p.hostname) or "/d/" in p.path
            ):
                return candidate

    # 2. <a> 태그 순회
    for anchor in soup.find_all("a", href=True):
        raw_href = anchor.get("href")
        if not raw_href or not isinstance(raw_href, str):
            continue
        candidate = urljoin(page_url, raw_href.strip())
        parsed = urlparse(candidate)
        if (
            parsed.scheme in ("http", "https")
            and _is_vikingfile_host(parsed.hostname)
            and parsed.path.startswith("/d/")
        ):
            return candidate

    return None


def _filename_from_page(html: str) -> str:
    """페이지 HTML의 #filename 또는 <title> 태그에서 파일명을 추출합니다."""
    soup = BeautifulSoup(html or "", "html.parser")
    fn_el = soup.find(id="filename")
    if fn_el and fn_el.get_text(strip=True):
        return fn_el.get_text(strip=True)

    if soup.title:
        title = soup.title.get_text(strip=True)
        # 보통 "파일명 - VikingFile" 같은 형태이므로 정리
        if " - " in title:
            return title.rsplit(" - ", 1)[0].strip()
        return title
    return ""


def _resolve_via_drission(page_url: str, timeout: int = 35) -> tuple[str, str, str]:
    """DrissionPage 및 로컬 브라우저(Chrome/Edge)를 사용해 Cloudflare Turnstile을 자동 우회합니다.

    화면 밖(-2400, -2400) 가상 위치에서 무음 실행되므로 사용자 화면을 방해하지 않습니다.
    """
    logger.info(f"[VikingFile] 스텔스 브라우저로 접속 시작: {page_url}")
    with stealth_browser_session(prefix="vikingfile_session_") as page:
        page.get(page_url)

        # 파일 미존재/삭제 여부 검사
        page_html = page.html or ""
        if (
            "file not found" in page_html.lower()
            or "파일을 찾을 수 없습니다" in page_html
        ):
            raise FileNotFoundError("VikingFile 파일이 삭제되었거나 만료되었습니다.")

        # Cloudflare Turnstile 자동 해결 및 다운로드 링크 생성 대기 (최대 timeout초)
        dl_link = None
        start_time = time.time()
        while time.time() - start_time < timeout:
            time.sleep(1.0)
            # 1. #download-link 요소의 href 확인
            try:
                el = page.ele("#download-link", timeout=0.5)
                if el:
                    href = el.attr("href")
                    if (
                        href
                        and href.startswith("http")
                        and not href.startswith(("#", "javascript:"))
                    ):
                        dl_link = href
                        break
            except Exception:
                pass

            # 2. HTML 전체에서 /d/ 링크 검색
            found = _extract_download_url(page.html or "", page.url or page_url)
            if found:
                dl_link = found
                break

            # 7초 이상 지났는데도 링크가 없고 상호작용 체크박스가 남아있는 경우에만 1회 보조 클릭
            elapsed = time.time() - start_time
            if elapsed >= 7.0:
                try:
                    cf_iframe = page.ele(
                        'css:iframe[src*="challenges.cloudflare.com"]', timeout=0.2
                    )
                    if cf_iframe:
                        checkbox = cf_iframe.ele(
                            'css:input[type="checkbox"], .ctp-checkbox-label',
                            timeout=0.2,
                        )
                        if checkbox:
                            checkbox.click()
                except Exception:
                    pass

        if not dl_link:
            raise ValueError(
                "VikingFile Cloudflare Turnstile 검증 시간 초과 또는 다운로드 링크 추출 실패"
            )

        filename = _filename_from_page(page.html or "")
        cookies = "; ".join(
            f"{c['name']}={c['value']}"
            for c in page.cookies()
            if "name" in c and "value" in c
        )
        logger.info(
            f"[VikingFile] Cloudflare Turnstile 우회 성공! 직링크 획득: {dl_link[:80]}..."
        )
        return dl_link, filename, cookies


def _resolve_via_flaresolverr(
    flaresolverr_url: str, page_url: str, timeout: int = 60
) -> tuple[str, str, str]:
    """FlareSolverr 서비스(Docker 등)를 활용하여 Cloudflare를 우회합니다."""
    endpoint = flaresolverr_url.rstrip("/") + "/v1"
    payload = {
        "cmd": "request.get",
        "url": page_url,
        "maxTimeout": timeout * 1000,
    }
    res = requests.post(endpoint, json=payload, timeout=timeout + 10)
    res.raise_for_status()
    data = res.json()
    solution = data.get("solution", {})
    html = solution.get("response", "")
    dl_url = _extract_download_url(html, page_url)
    fn = _filename_from_page(html)
    cookies_list = solution.get("cookies", [])
    cookie_str = "; ".join(
        f"{c['name']}={c['value']}"
        for c in cookies_list
        if "name" in c and "value" in c
    )
    if not dl_url:
        raise ValueError(
            "FlareSolverr: VikingFile 다운로드 링크를 추출하지 못했습니다."
        )
    return dl_url, fn, cookie_str


class VikingFileResolver(BaseResolver):
    """VikingFile (vikingfile.com, vik1ngfile.site) 다운로드 링크 리졸버."""

    name = "VikingFile"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        return parsed.scheme in ("http", "https") and _is_vikingfile_host(
            parsed.hostname
        )

    def resolve(self, url: str, options: dict | None = None) -> dict:
        page_url = url.strip()
        opts = dict(options or {})
        parsed = urlparse(page_url)
        is_file_page = parsed.path.startswith("/f/")
        is_download_link = parsed.path.startswith("/d/")

        if not (is_file_page or is_download_link):
            raise ValueError(f"올바른 VikingFile 파일 페이지가 아닙니다: {page_url}")

        download_url = page_url if is_download_link else ""
        filename = ""
        cookies = ""
        resolved_page_url = page_url

        if is_file_page:
            # 1. 빠른 HTTP 요청으로 파일 삭제 여부 및 Turnstile 유무 1차 확인
            turnstile_detected = False
            with requests.Session() as session:
                session.headers.update(
                    {
                        "User-Agent": VIKINGFILE_USER_AGENT,
                        "Referer": "https://vikingfile.com/",
                    }
                )
                try:
                    resp = session.get(page_url, timeout=10, allow_redirects=True)
                    if resp.status_code in (404, 410):
                        raise FileNotFoundError(
                            "VikingFile 파일이 삭제되었거나 만료되었습니다."
                        )
                    resp.raise_for_status()
                    resolved_page_url = resp.url
                    page_html = resp.text

                    if "file not found" in page_html.lower():
                        raise FileNotFoundError(
                            "VikingFile 파일이 삭제되었거나 만료되었습니다."
                        )

                    if (
                        "challenges.cloudflare.com" in page_html
                        or "turnstile" in page_html.lower()
                        or 'id="captcha"' in page_html
                    ):
                        turnstile_detected = True

                    filename = _filename_from_page(page_html)
                    dl = _extract_download_url(page_html, resolved_page_url)
                    if dl:
                        download_url = dl
                except FileNotFoundError:
                    raise
                except Exception as e:
                    logger.debug(f"[VikingFile] 기본 HTTP 요청 실패: {e}")
                    turnstile_detected = True

            # 2. Turnstile 감지 또는 다운로드 링크 미발견 시 브라우저/FlareSolverr 우회 실행
            if not download_url or turnstile_detected:
                flaresolverr_url = (
                    opts.get("flaresolverr_url")
                    or opts.get("vikingfile_flaresolverr_url")
                    or os.environ.get("FLARESOLVERR_URL")
                    or ""
                ).strip()
                if not flaresolverr_url:
                    try:
                        import config

                        flaresolverr_url = str(
                            config.load_settings().get("flaresolverr_url") or ""
                        ).strip()
                    except Exception:
                        pass

                # (a) FlareSolverr 설정이 있는 경우 우선 시도
                if flaresolverr_url:
                    try:
                        logger.info(
                            f"[VikingFile] FlareSolverr({flaresolverr_url})를 통해 우회 시도..."
                        )
                        dl, fn, ck = _resolve_via_flaresolverr(
                            flaresolverr_url, resolved_page_url
                        )
                        download_url = dl
                        if fn and not filename:
                            filename = fn
                        if ck:
                            cookies = ck
                    except Exception as e:
                        logger.warning(
                            f"[VikingFile] FlareSolverr 우회 실패, 로컬 브라우저 자동 우회 시도: {e}"
                        )

                # (b) 로컬 DrissionPage 스텔스 브라우저 우회 시도
                if not download_url:
                    try:
                        dl, fn, ck = _resolve_via_drission(resolved_page_url)
                        download_url = dl
                        if fn and not filename:
                            filename = fn
                        if ck:
                            cookies = ck
                    except FileNotFoundError:
                        raise
                    except Exception as e:
                        logger.error(f"[VikingFile] DrissionPage 우회 실패: {e}")
                        raise ValueError(
                            f"VikingFile Cloudflare 우회 및 다운로드 링크 추출 실패: {e}"
                        )

        if not download_url:
            raise ValueError("VikingFile 다운로드 링크를 추출하지 못했습니다.")

        # 3. 리다이렉트 추적하여 최종 직링크(S3/클라우드 저장소) 및 파일명/크기 확보
        final_url = download_url
        size = 0
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": VIKINGFILE_USER_AGENT,
                    "Referer": resolved_page_url,
                }
            )
            if cookies:
                session.headers["Cookie"] = cookies

            try:
                # S3 사전 서명(presigned) URL은 GET 메서드로 서명되어 HEAD 요청 시 403이 발생하므로 stream=True GET 사용
                with session.get(
                    download_url,
                    headers={"Referer": resolved_page_url},
                    timeout=TIMEOUT,
                    allow_redirects=True,
                    stream=True,
                ) as get_resp:
                    get_resp.raise_for_status()
                    final_url = get_resp.url
                    if not filename:
                        filename = extract_filename_from_headers(
                            get_resp.headers, final_url
                        )
                    cl = get_resp.headers.get("content-length")
                    if cl and cl.isdigit():
                        size = int(cl)
            except Exception as e:
                logger.warning(
                    f"VikingFile 최종 링크 사전 해석 실패, /d/ 링크를 직접 사용합니다: {e}"
                )

        if not filename:
            parsed_final = urlparse(final_url)
            filename = unquote(os.path.basename(parsed_final.path))

        res_dict: dict[str, Any] = {
            "url": final_url,
            "filename": filename,
            "headers": {"User-Agent": VIKINGFILE_USER_AGENT},
            "cookies": cookies,
            "referer": resolved_page_url,
            "source_service": "VikingFile",
        }
        if size > 0:
            res_dict["size"] = size
        return res_dict
