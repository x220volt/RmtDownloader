"""DataNodes (datanodes.to) 파일 다운로드 링크 리졸버."""

import logging
import re
import time
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

from ..browser_solver import stealth_browser_session
from ..engine import extract_filename_from_headers, sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25


def _is_datanodes_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("datanodes.to", "www.datanodes.to") or host.endswith(".datanodes.to")


def _is_direct_proxy_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return "dlproxy.uk" in host or host.endswith(".dlproxy.uk")


class DataNodesResolver(BaseResolver):
    """DataNodes (datanodes.to)의 파일 다운로드 페이지를 분석하여 직링크를 추출하는 리졸버."""

    name: str = "DataNodes"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if _is_direct_proxy_host(parsed.hostname):
                return True
            if not _is_datanodes_host(parsed.hostname):
                return False
            path = parsed.path or ""
            return bool(
                re.match(r"^/[a-zA-Z0-9_-]{6,}", path)
                or path.startswith("/download")
                or parsed.query.strip()
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not (_is_datanodes_host(parsed.hostname) or _is_direct_proxy_host(parsed.hostname)):
            raise ValueError(f"지원하지 않는 DataNodes URL 형태입니다: {url}")

        # 1. 이미 발급된 직링크 (dlproxy.uk 등) 형태인 경우 바로 반환
        if _is_direct_proxy_host(parsed.hostname):
            logger.info(f"[DataNodes] 이미 발급된 직링크 감지: {url}")
            filename = extract_filename_from_headers(fallback_url=url)
            clean_title = ""
            folder_base = ""
            if filename:
                clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", filename).strip()
                folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title
            return {
                "url": url,
                "filename": sanitize_filename(filename) if filename else None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://datanodes.to/",
                    "Accept": "*/*",
                },
                "cookies": None,
                "source_service": self.name,
                "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
                "article_title": clean_title,
            }

        logger.info(f"[DataNodes] 파일 상세 페이지 분석 시작: {url}")

        # URL 경로에서 파일 코드 및 파일명 힌트 추출
        path_parts = [p for p in (parsed.path or "").split("/") if p]
        file_code_hint = path_parts[0] if path_parts else ""
        filename_hint = path_parts[1] if len(path_parts) > 1 else None

        req_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://datanodes.to/",
        }

        # 2. 초기 requests GET으로 파일 삭제 여부 신속 사전 확인
        session = requests.Session()
        try:
            resp = session.get(url, headers=req_headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            if resp.status_code == 404:
                raise FileNotFoundError(f"DataNodes 파일을 찾을 수 없습니다 (404): {url}")
            body_lower = resp.text.lower()
            if any(term in body_lower for term in ("file was deleted", "file not found", "access restricted")):
                raise FileNotFoundError(f"DataNodes 파일이 삭제되었거나 존재하지 않습니다: {url}")
        except FileNotFoundError:
            raise
        except Exception as e:
            logger.debug(f"[DataNodes] 사전 확인 요청 경고 (무시하고 스텔스 브라우저로 진행): {e}")

        direct_url: Optional[str] = None
        extracted_filename: Optional[str] = filename_hint
        cookie_dict: dict = {}

        # 3. 스텔스 브라우저 세션을 통해 Turnstile 우회 및 직링크 발급
        logger.info(f"[DataNodes] 스텔스 브라우저 로딩 시작: {url}")
        with stealth_browser_session(prefix="datanodes_") as page:
            page.get(url)

            # Step 1: 폼 존재 여부 확인 및 Step 2 응답 패킷 리스너 가동
            step2_body: str = ""
            form_ele = page.ele("tag:form")
            if form_ele:
                page.listen.start("https://datanodes.to/download")
                submit_js = """
                const form = document.querySelector('form');
                if (form) {
                    let inp = form.querySelector('input[name="method_free"]');
                    if (!inp) {
                        inp = document.createElement('input');
                        inp.type = 'hidden';
                        inp.name = 'method_free';
                        inp.value = 'Free Download >>';
                        form.appendChild(inp);
                    }
                    form.submit();
                }
                """
                page.run_js(submit_js)
                packet = page.listen.wait(timeout=15)
                if packet and packet.response:
                    step2_body = packet.response.body or ""
                page.wait.doc_loaded()

            # 메타데이터 추출 (code, rand, dl-token, countdown)
            body_to_search = step2_body or page.html or ""
            code_match = re.search(r'code="([^"]+)"', body_to_search)
            rand_match = re.search(r'rand="([^"]+)"', body_to_search)
            dl_token_match = re.search(r'dl-token="([^"]+)"', body_to_search)
            name_match = re.search(r'name="([^"]+)"', body_to_search)
            countdown_match = re.search(r':countdown="(\d+)"', body_to_search)

            code = code_match.group(1) if code_match else file_code_hint
            rand = rand_match.group(1) if rand_match else ""
            dl_token = dl_token_match.group(1) if dl_token_match else ""
            if name_match and name_match.group(1) != "bm-site-verification":
                extracted_filename = name_match.group(1)
            countdown_sec = int(countdown_match.group(1)) if countdown_match else 10

            logger.info(
                f"[DataNodes] 메타데이터 추출 완료 (code={code}, dl_token={bool(dl_token)}, countdown={countdown_sec}s)"
            )

            # Turnstile 캡차 풀이 대기 (최대 20초)
            ts_token = ""
            for _ in range(20):
                try:
                    ts_val = page.run_js(
                        "return document.querySelector('[name=\"cf-turnstile-response\"]')?.value || '';"
                    )
                    if ts_val:
                        ts_token = ts_val
                        break
                except Exception:
                    pass
                time.sleep(1)

            # 카운트다운 타이머 잔여 시간 대기
            time.sleep(countdown_sec)

            cookies_list = page.cookies() or []
            cookie_dict = {
                c["name"]: c["value"]
                for c in cookies_list
                if isinstance(c, dict) and "name" in c and "value" in c
            }
            if page.user_agent:
                req_headers["User-Agent"] = page.user_agent

            # 4. 브라우저 컨텍스트에서 최종 다운로드 fetch 실행
            post_js = """
            const code = arguments[0];
            const rand = arguments[1];
            const dlToken = arguments[2];
            const tsToken = document.querySelector('[name="cf-turnstile-response"]')?.value || '';

            const f = new FormData();
            f.append("op", "download2");
            f.append("id", code);
            f.append("rand", rand);
            f.append("referer", window.location.href);
            f.append("method_free", "Free Download >>");
            f.append("method_premium", "");
            f.append("g_captch__a", "1");
            if (dlToken) f.append("dl_token", dlToken);
            f.append("cf-turnstile-response", tsToken);

            return (async () => {
                const res = await fetch("", {
                    method: "POST",
                    body: f,
                    headers: {"X-Dn-Dl": "1"}
                });
                const text = await res.text();
                try {
                    return JSON.parse(text);
                } catch(e) {
                    return {status: res.status, raw: text.substring(0, 300)};
                }
            })();
            """
            try:
                fetch_res = page.run_js(post_js, code, rand, dl_token)
                if isinstance(fetch_res, dict) and "url" in fetch_res:
                    direct_url = unquote(fetch_res["url"])
            except Exception as e:
                logger.warning(f"[DataNodes] JS fetch 실행 실패 ({e})")

            # 대안: Free Download 버튼 클릭 후 내비게이션 또는 링크 추적
            if not direct_url:
                btn_click_js = """
                const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Free Download'));
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
                """
                page.run_js(btn_click_js)
                for _ in range(12):
                    time.sleep(1)
                    if _is_direct_proxy_host(urlparse(page.url).hostname):
                        direct_url = page.url
                        break

        if not direct_url:
            raise ValueError(f"DataNodes 다운로드 링크를 추출하지 못했습니다: {url}")

        # HEAD 요청으로 최종 파일명 확인
        try:
            head_headers = {
                "User-Agent": req_headers["User-Agent"],
                "Referer": "https://datanodes.to/",
                "Accept": "*/*",
            }
            head_resp = session.head(
                direct_url,
                headers=head_headers,
                cookies=cookie_dict or None,
                timeout=10,
                allow_redirects=True,
            )
            if head_resp.status_code == 200:
                h_name = extract_filename_from_headers(head_resp.headers, fallback_url=direct_url)
                if h_name:
                    extracted_filename = h_name
        except Exception as e:
            logger.debug(f"[DataNodes] HEAD 확인 예외 (무시): {e}")

        if not extracted_filename:
            extracted_filename = extract_filename_from_headers(fallback_url=url)

        clean_title = ""
        folder_base = ""
        if extracted_filename:
            clean_title = re.sub(r"\.[a-zA-Z0-9]+$", "", extracted_filename).strip()
            folder_base = re.sub(r"[\._-]?part\d+$", "", clean_title, flags=re.IGNORECASE).strip() or clean_title

        logger.info(f"[DataNodes] 직링크 발급 성공: {extracted_filename} -> {direct_url[:60]}...")

        return {
            "url": direct_url,
            "filename": sanitize_filename(extracted_filename) if extracted_filename else None,
            "headers": {
                "User-Agent": req_headers["User-Agent"],
                "Referer": "https://datanodes.to/",
                "Accept": "*/*",
            },
            "cookies": cookie_dict or None,
            "referer": url,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(folder_base) if folder_base else "",
            "article_title": clean_title,
        }
