"""Vimm's Lair (vimm.net) 레트로 게임 ROM 다운로드 링크 리졸버."""

import logging
import re
import time
import urllib.parse
from typing import Optional
from urllib.parse import urlparse

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


def _is_vimm_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host in ("vimm.net", "www.vimm.net") or host.endswith(".vimm.net")


class VimmResolver(BaseResolver):
    """Vimm's Lair (vimm.net)의 게임 ROM 페이지를 분석하여 직링크를 발급하는 리졸버."""

    name: str = "Vimm's Lair"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            if not _is_vimm_host(parsed.hostname):
                return False
            path = (parsed.path or "").lower()
            # vault 페이지 또는 dl*.vimm.net 직링크
            if "dl" in (parsed.hostname or "") and "mediaId=" in parsed.query:
                return True
            return "/vault" in path
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        parsed = urlparse(url)
        if not _is_vimm_host(parsed.hostname):
            raise ValueError(f"지원하지 않는 Vimm's Lair URL 형태입니다: {url}")

        # 만약 이미 직링크 형태(dl*.vimm.net/?mediaId=...&token=...)인 경우 바로 반환
        if "dl" in (parsed.hostname or "") and "mediaId=" in parsed.query:
            logger.info(f"[Vimm's Lair] 이미 발급된 직링크 URL 감지: {url}")
            return {
                "url": url,
                "filename": None,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": "https://vimm.net/",
                    "Accept": "*/*",
                },
                "cookies": None,
                "source_service": self.name,
            }

        logger.info(f"[Vimm's Lair] 게임 상세 페이지 분석 시작 (스텔스 브라우저 로딩): {url}")

        html = ""
        cookies_list: list = []
        browser_ua = USER_AGENT
        page_title = ""

        with stealth_browser_session(prefix="vimm_session_") as page:
            page.get(url)
            found_form = False
            for _ in range(20):
                # Cloudflare Turnstile 통과 및 #dl-form 탐지 대기
                if page.ele("#dl-form"):
                    found_form = True
                    break
                html_snippet = (page.html or "").lower()
                if any(term in html_snippet for term in ("unavailable", "removed", "esa request")):
                    break
                time.sleep(1)

            html = page.html or ""
            cookies_list = page.cookies() or []
            browser_ua = page.user_agent or USER_AGENT
            page_title = page.title or ""

        soup = BeautifulSoup(html, "html.parser")
        form = soup.find(id="dl-form")

        if not form:
            body_text = soup.get_text()
            if any(term in body_text.lower() for term in ("unavailable", "removed", "esa", "not found")):
                raise FileNotFoundError(f"Vimm's Lair에서 해당 게임 다운로드가 비활성화되어 있거나 삭제되었습니다: {url}")
            raise ValueError(f"Vimm's Lair 다운로드 폼(#dl-form)을 찾을 수 없습니다: {url}")

        action = form.get("action") or ""
        if str(action).startswith("//"):
            action = f"https:{action}"
        elif str(action).startswith("/"):
            action = f"https://vimm.net{action}"

        data: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name and not inp.has_attr("disabled"):
                val = inp.get("value") or ""
                data[str(name)] = str(val)

        for sel in form.find_all("select"):
            name = sel.get("name")
            if name and not sel.has_attr("disabled"):
                selected = sel.find("option", selected=True) or sel.find("option")
                if selected and selected.get("value") is not None:
                    data[str(name)] = str(selected["value"])

        if not data.get("mediaId") or not data.get("token"):
            raise ValueError(f"Vimm's Lair 다운로드 토큰 또는 mediaId를 추출하지 못했습니다: {url}")

        # Vimm의 submitDL 자바스크립트는 폼 전송 시 method를 'GET'으로 변경하여 요청함
        query_str = urllib.parse.urlencode(data)
        download_url = f"{action}?{query_str}" if query_str else str(action)

        cookie_dict = {
            c["name"]: c["value"]
            for c in cookies_list
            if isinstance(c, dict) and "name" in c and "value" in c
        }
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

        req_headers = {
            "User-Agent": browser_ua,
            "Referer": url,
            "Accept": "*/*",
        }

        # 게임 타이틀 및 정리
        clean_title = ""
        if page_title:
            clean_title = re.sub(r"^The Vault:\s*", "", page_title, flags=re.IGNORECASE).strip()
            clean_title = re.sub(r"^Vimm's Lair:\s*", "", clean_title, flags=re.IGNORECASE).strip()
        if not clean_title:
            h2 = soup.find("h2")
            clean_title = h2.get_text(strip=True) if h2 else ""

        # 파일명 확인 (HEAD 요청으로 Content-Disposition 추출 시도)
        filename: Optional[str] = None
        try:
            head_resp = requests.head(
                download_url,
                headers=req_headers,
                cookies=cookie_dict,
                timeout=10,
                allow_redirects=True,
            )
            if head_resp.status_code == 200:
                filename = extract_filename_from_headers(
                    head_resp.headers, fallback_url=download_url
                )
        except Exception as e:
            logger.debug(f"[Vimm's Lair] HEAD 요청으로 파일명 확인 실패 (무시): {e}")

        if not filename or filename.startswith("download"):
            if clean_title:
                filename = f"{clean_title}.zip"

        if filename:
            filename = sanitize_filename(filename)

        logger.info(f"[Vimm's Lair] 직링크 발급 성공: {filename} -> {download_url[:60]}...")

        return {
            "url": download_url,
            "filename": filename,
            "headers": req_headers,
            "cookies": cookie_str,
            "referer": url,
            "source_service": self.name,
            "subfolder": sanitize_folder_name(clean_title) if clean_title else "",
            "article_title": clean_title,
        }
