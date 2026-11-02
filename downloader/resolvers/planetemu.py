"""PlanetEmu (planetemu.net) 레트로 게임 ROM 다운로드 링크 리졸버."""

import logging
import re
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 25


def _is_planetemu_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("planetemu.net", "www.planetemu.net") or host.endswith(".planetemu.net")


class PlanetEmuResolver(BaseResolver):
    """PlanetEmu (planetemu.net)의 게임 ROM 다운로드 페이지를 분석하여 직링크를 발급하는 리졸버."""

    name: str = "PlanetEmu"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_planetemu_host(parsed.hostname):
                return False
            path = (parsed.path or "").lower()
            return bool(
                re.search(r"^/(?:[a-z]{2}/)?rom/", path)
                or re.search(r"^/(?:[a-z]{2}/)?roms/", path)
                or "/download/rom" in path
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_planetemu_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 PlanetEmu URL 형태입니다: {url}")

        base_origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"

        # 1. 이미 발급된 토큰 직링크 형태인 경우 (/download/rom?token=...)
        if "/download/rom" in parsed.path and "token=" in parsed.query:
            logger.info(f"[PlanetEmu] 이미 발급된 다운로드 토큰 URL 감지: {url}")
            return {
                "url": url,
                "filename": None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": base_origin + "/",
                },
                "cookies": None,
                "referer": base_origin + "/",
                "source_service": "PlanetEmu",
            }

        session = requests.Session()
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": base_origin + "/",
        }

        # 2. ROM 상세 페이지 조회
        logger.info(f"[PlanetEmu] ROM 페이지 분석 시작: {url}")
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            raise FileNotFoundError(f"PlanetEmu 페이지를 찾을 수 없습니다 (404): {url}")
        if not resp.ok:
            raise ValueError(f"PlanetEmu 페이지 접속 실패 (HTTP {resp.status_code}): {url}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # 3. 폼 및 파라미터 추출
        target_form: Optional[Tag] = None
        for form in soup.find_all("form"):
            classes = form.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            action_val = str(form.get("action") or "")
            if "downloadForm" in classes or "/download/rom" in action_val:
                target_form = form
                break

        if not target_form:
            raise ValueError(f"PlanetEmu 다운로드 폼을 찾을 수 없습니다: {url}")

        rom_id_input = target_form.find("input", attrs={"name": "id"})
        if not rom_id_input or not rom_id_input.get("value"):
            raise ValueError(f"PlanetEmu ROM ID를 찾을 수 없습니다: {url}")
        rom_id = str(rom_id_input.get("value")).strip()

        type_input = target_form.find("input", attrs={"name": "type"})
        rom_type = str(type_input.get("value")).strip() if type_input and type_input.get("value") else None

        # 파일명 추출 (버튼 텍스트 또는 폼 submit 값 또는 h1 타이틀)
        extracted_filename: Optional[str] = None
        submit_btn = target_form.find("input", attrs={"name": "download"}) or target_form.find("button", attrs={"name": "download"})
        if submit_btn:
            val = str(submit_btn.get("value") or submit_btn.get_text() or "").strip()
            # "Download <name>.zip" or "Télécharger <name>.zip"
            val = re.sub(r"^(?:Download|Télécharger|Telecharger)\s+", "", val, flags=re.IGNORECASE).strip()
            if val:
                extracted_filename = val

        if not extracted_filename:
            h1 = soup.find("h1")
            if h1:
                title_text = h1.get_text(strip=True)
                if title_text:
                    extracted_filename = title_text if title_text.endswith(".zip") else f"{title_text}.zip"

        # 4. 다운로드 쿼터(Quota) 확인 및 pass 토큰 획득
        quota_attr: Optional[str] = None
        if target_form.get("data-download-quota"):
            quota_attr = str(target_form.get("data-download-quota"))

        if not quota_attr:
            for el in soup.find_all(True):
                attr_val = el.get("data-download-quota")
                if attr_val:
                    quota_attr = str(attr_val)
                    break

        quota_endpoint = urljoin(base_origin, quota_attr or "/download/quota")
        quota_headers = {
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": url,
        }

        pass_token: Optional[str] = None
        for _ in range(3):
            q_resp = session.get(quota_endpoint, headers=quota_headers, timeout=REQUEST_TIMEOUT)
            if not q_resp.ok:
                logger.warning(f"[PlanetEmu] Quota 확인 실패 (HTTP {q_resp.status_code}), 재시도 중...")
                time.sleep(2)
                continue

            try:
                q_data = q_resp.json()
            except Exception as e:
                logger.warning(f"[PlanetEmu] Quota JSON 파싱 실패: {e}")
                time.sleep(2)
                continue

            if q_data.get("blocked"):
                retry_after = int(q_data.get("retryAfter") or 0)
                if 0 < retry_after <= 30:
                    logger.info(f"[PlanetEmu] 일시적 쿼터 대기 ({retry_after}초 후 재시도)...")
                    time.sleep(retry_after + 1)
                    continue
                else:
                    raise ValueError(
                        f"PlanetEmu 다운로드 할당량이 초과되었습니다. "
                        f"{retry_after}초 후 다시 시도해주세요."
                    )

            pass_token = q_data.get("pass")
            if pass_token:
                break
            time.sleep(2)

        if not pass_token:
            raise ValueError(f"PlanetEmu 다운로드 권한 토큰(pass) 발급에 실패했습니다: {url}")

        # 5. 다운로드 토큰 발급 (POST /download/rom)
        mint_endpoint = urljoin(base_origin, "/download/rom")
        post_headers = {
            "User-Agent": USER_AGENT,
            "Referer": url,
            "Origin": base_origin,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        post_data = {
            "id": rom_id,
            "pass": pass_token,
        }
        if rom_type:
            post_data["type"] = rom_type

        token_url: Optional[str] = None
        max_mint_retries = 3
        for mint_attempt in range(max_mint_retries):
            post_resp = session.post(
                mint_endpoint,
                headers=post_headers,
                data=post_data,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )

            # 429 Too Many Requests 인 경우 백오프 대기 후 재시도
            if post_resp.status_code == 429:
                backoff_sec = (mint_attempt + 1) * 15
                logger.warning(
                    f"[PlanetEmu] 속도 제한 (429) 감지. {backoff_sec}초 대기 후 재시도 "
                    f"({mint_attempt + 1}/{max_mint_retries})..."
                )
                time.sleep(backoff_sec)
                # 새로 pass를 갱신할 수도 있으므로 quota 재조회
                try:
                    q_resp = session.get(quota_endpoint, headers=quota_headers, timeout=REQUEST_TIMEOUT)
                    if q_resp.ok:
                        new_pass = q_resp.json().get("pass")
                        if new_pass:
                            post_data["pass"] = new_pass
                except Exception:
                    pass
                continue

            # 302 Found 리다이렉트 위치에서 token URL 획득
            location = post_resp.headers.get("Location")
            if location and "token=" in location:
                token_url = urljoin(base_origin, location)
                break

            # 본문에 리다이렉트 또는 토큰이 있는 경우 체크
            m = re.search(r"/download/rom\?token=([a-f0-9]+)", post_resp.text)
            if m:
                token_url = urljoin(base_origin, m.group(0))
                break

            if not post_resp.ok and post_resp.status_code != 302:
                logger.warning(
                    f"[PlanetEmu] 다운로드 토큰 발급 응답 실패 (HTTP {post_resp.status_code})"
                )

            time.sleep(2)

        if not token_url:
            raise ValueError(f"PlanetEmu 다운로드 직링크 토큰 생성에 실패했습니다: {url}")

        logger.info(f"[PlanetEmu] 다운로드 직링크 생성 완료: {token_url}")

        cookie_parts = []
        if hasattr(session.cookies, "items"):
            for k, v in session.cookies.items():
                cookie_parts.append(f"{k}={v}")
        elif isinstance(session.cookies, list):
            for c in session.cookies:
                c_name = getattr(c, "name", str(c))
                c_val = getattr(c, "value", "")
                cookie_parts.append(f"{c_name}={c_val}")
        cookies_str = "; ".join(cookie_parts) or None

        return {
            "url": token_url,
            "filename": sanitize_filename(extracted_filename) if extracted_filename else None,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": url,
            },
            "cookies": cookies_str,
            "referer": url,
            "source_service": "PlanetEmu",
        }
