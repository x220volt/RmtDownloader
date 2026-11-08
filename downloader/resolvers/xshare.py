"""x-share.net 파일 및 묶음(bundle) 리졸버."""

import json
import logging
import os
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


def _resolve_xshare_via_flaresolverr(
    flaresolverr_url: str, page_url: str, file_id: str, timeout: int = 60
) -> tuple[str, str]:
    """FlareSolverr 서비스(Docker 등)를 활용하여 x-share.net Turnstile을 우회합니다."""
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
    cookies = {
        c["name"]: c["value"]
        for c in solution.get("cookies", [])
        if "name" in c and "value" in c
    }

    # Turnstile 토큰 추출
    token_match = re.search(
        r'name=["\']cf-turnstile-response["\']\s+value=["\']([^"\']+)["\']', html
    ) or re.search(
        r'value=["\']([^"\']+)["\']\s+name=["\']cf-turnstile-response["\']', html
    )
    turnstile_token = token_match.group(1) if token_match else ""

    # 파일명 추출 (HTML 내 fileData 객체 또는 title)
    filename = ""
    fn_match = re.search(r'fileData\s*=\s*\{[^}]*name:\s*["\']([^"\']+)["\']', html)
    if fn_match:
        filename = fn_match.group(1)
    else:
        soup_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if soup_match:
            filename = soup_match.group(1).split(" - ")[0].strip()

    token_url = "https://x-share.net/api/download-token"
    headers = {
        "User-Agent": solution.get("userAgent") or DEFAULT_USER_AGENT,
        "Referer": page_url,
        "Origin": "https://x-share.net",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
    }
    post_payload = {
        "fileId": file_id,
        "turnstileToken": turnstile_token,
        "referer": page_url,
    }
    r = requests.post(
        token_url, json=post_payload, headers=headers, cookies=cookies, timeout=15
    )
    if r.ok:
        res_data = r.json()
        key = res_data.get("key")
        if key:
            return key, filename
    raise ValueError(f"FlareSolverr로 x-share 토큰 획득 실패 (HTTP {r.status_code})")


def _resolve_xshare_via_drission(
    page_url: str, file_id: str = "", timeout: int = 45
) -> tuple[str, str]:
    """로컬 브라우저(DrissionPage)를 무음(화면 밖)으로 띄워 x-share.net Turnstile을 자동 통과하고 (key, filename)을 획득합니다."""
    if not file_id:
        m = re.search(r"/(?:s|api/download)/([a-zA-Z0-9_-]+)", page_url)
        if m:
            file_id = m.group(1)

    with stealth_browser_session(
        prefix="xshare_session_", window_size="800,600"
    ) as page:
        logger.info(
            f"[X-Share] Cloudflare Turnstile 우회를 위해 로컬 브라우저 접속 시작: {page_url}"
        )
        page.get(page_url)

        for i in range(timeout):
            time.sleep(1)

            # 1. 다운로드 버튼 상태 및 텍스트 점검 (다국어 지원: 한국어, 영어 등)
            btn = page.ele("#dl-btn")
            btn_text = ""
            btn_el = page.ele("#dl-btn-text")
            if btn_el:
                btn_text = (btn_el.text or "").strip()
            elif btn:
                btn_text = (btn.text or "").strip()

            is_btn_ready = False
            if btn:
                btn_disabled = btn.attr("disabled")
                is_disabled = (
                    btn_disabled is not None
                    and btn_disabled != ""
                    and str(btn_disabled).lower() != "false"
                )
                has_download_kw = any(
                    w in btn_text.lower()
                    for w in ("다운로드", "download", "받기", "telecharger", "descargar")
                )
                btn_class = btn.attr("class") or ""
                # 다운로드 키워드가 있거나, disabled 속성이 해제되었거나, 준비 상태 클래스가 있는 경우
                if has_download_kw or not is_disabled or any(c in btn_class.lower() for c in ("ready", "active", "enabled")):
                    is_btn_ready = True

            # 2. DOM 내 Turnstile 토큰 존재 여부 확인
            token = ""
            try:
                token = page.run_js("""
                    return (typeof dlTurnstileToken !== 'undefined' && dlTurnstileToken)
                        || document.querySelector('[name="cf-turnstile-response"]')?.value
                        || (window.turnstile && typeof window.turnstile.getResponse === 'function' ? window.turnstile.getResponse() : '')
                        || '';
                """) or ""
            except Exception:
                pass

            # 버튼이 준비되었거나, Turnstile 토큰이 생성되었거나, 3초 이상 경과한 경우 fetch 시도
            if is_btn_ready or token or i >= 3:
                js_fetch = f"""
                    const fid = {json.dumps(file_id)} || (typeof FILE_ID !== 'undefined' ? FILE_ID : '');
                    const ref = {json.dumps(page_url)} || (typeof PAGE_REFERER !== 'undefined' ? PAGE_REFERER : window.location.href);
                    const tkn = (typeof dlTurnstileToken !== 'undefined' && dlTurnstileToken)
                        || document.querySelector('[name="cf-turnstile-response"]')?.value
                        || (window.turnstile && typeof window.turnstile.getResponse === 'function' ? window.turnstile.getResponse() : '')
                        || {json.dumps(token)};
                    return new Promise(async (resolve) => {{
                        try {{
                            const r = await fetch('/api/download-token', {{
                                method: 'POST',
                                headers: {{'Content-Type': 'application/json'}},
                                body: JSON.stringify({{fileId: fid, turnstileToken: tkn, referer: ref}})
                            }});
                            const d = await r.json();
                            const fn = (typeof fileData !== 'undefined' && fileData) ? fileData.name : '';
                            resolve({{ok: r.ok, status: r.status, data: d, filename: fn, file: (typeof fileData !== 'undefined' ? fileData : null)}});
                        }} catch(e) {{
                            resolve({{ok: false, error: String(e)}});
                        }}
                    }});
                """
                res = None
                try:
                    res = page.run_js(js_fetch)
                except Exception as e:
                    logger.debug(f"[X-Share] fetch JS 실행 오류: {e}")

                if res and res.get("ok") and res.get("data", {}).get("key"):
                    key = res["data"]["key"]
                    filename = res.get("filename") or (res.get("file") or {}).get("name", "")
                    logger.info(
                        f"[X-Share] Cloudflare Turnstile 우회 성공! Key 획득 ({filename})"
                    )
                    return key, filename

            # 3. 5초 이상 경과했는데도 미해결 상태라면 Turnstile iframe 내부 체크박스 클릭 보조
            if i >= 5:
                try:
                    cf_iframe = (
                        page.ele('css:iframe[src*="challenges.cloudflare.com"]', timeout=0.2)
                        or page.ele('css:iframe[src*="turnstile"]', timeout=0.2)
                        or page.ele('css:iframe[src*="cloudflare"]', timeout=0.2)
                    )
                    if cf_iframe:
                        checkbox = cf_iframe.ele(
                            'css:input[type="checkbox"], .ctp-checkbox-label, #challenge-stage',
                            timeout=0.2,
                        )
                        if checkbox:
                            checkbox.click()
                except Exception:
                    pass

        raise ValueError("x-share.net 보안 검증(Turnstile) 대기 시간 초과")


class XShareResolver(BaseResolver):
    """x-share.net 개별 파일(/s/...) 및 묶음(/b/...) 리졸버."""

    name = "X-Share"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        return bool(_URL_PATTERN.search(url))

    def _get_download_key(
        self, file_id: str, referer_url: str, options: dict | None = None
    ) -> str:
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

        page_url = (
            referer_url if "/s/" in referer_url else f"https://x-share.net/s/{file_id}"
        )

        # 1. FlareSolverr가 설정되어 있으면 우선 시도
        opts = options or {}
        flaresolverr_url = (
            opts.get("flaresolverr_url")
            or opts.get("xshare_flaresolverr_url")
            or os.environ.get("FLARESOLVERR_URL")
        )
        if not flaresolverr_url:
            try:
                import config

                flaresolverr_url = str(
                    config.load_settings().get("flaresolverr_url") or ""
                ).strip()
            except Exception:
                pass

        if flaresolverr_url:
            try:
                logger.info(
                    f"[X-Share] FlareSolverr({flaresolverr_url})를 통해 Turnstile 우회 시도..."
                )
                key, _ = _resolve_xshare_via_flaresolverr(
                    flaresolverr_url, page_url, file_id
                )
                return key
            except Exception as e:
                logger.warning(
                    f"[X-Share] FlareSolverr 우회 실패, 로컬 브라우저 자동 우회 시도: {e}"
                )

        # 2. Cloudflare Turnstile 보안 검증이 필요한 경우 로컬 브라우저 자동 우회 시도
        logger.info(
            "[X-Share] 보안 검증(Turnstile) 감지: 로컬 브라우저(DrissionPage) 자동 우회 진행..."
        )
        key, _ = _resolve_xshare_via_drission(page_url, file_id=file_id)
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
            first_key = self._get_download_key(first_file["id"], url, options=options)
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

            key = self._get_download_key(file_id, url, options=options)
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
