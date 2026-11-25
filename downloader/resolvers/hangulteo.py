"""한글터 (hangulteo.com) 한글패치 아카이브 리졸버 모듈."""

import json
import logging
import re
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

HANGULTEO_HOSTS = ("hangulteo.com", "www.hangulteo.com")


def extract_hangulteo_patch_id(url: str) -> str:
    """한글터 URL에서 패치 ID(슬러그)를 추출합니다."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url.strip())
        path = (parsed.path or "").strip("/")
        if path:
            return path.split("/")[0].lower()
        frag = (parsed.fragment or "").strip("/")
        if frag:
            return frag.split("/")[-1].lower()
    except Exception:
        pass
    return ""


class HangulteoResolver(BaseResolver):
    """한글터(hangulteo.com)의 게임 패치 페이지에서 PC 및 모바일용 패치 zip 직링크를 추출하는 리졸버."""

    name = "Hangulteo"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = (parsed.hostname or "").lower()
            return host in HANGULTEO_HOSTS or host.endswith(".hangulteo.com")
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict[str, Any]:
        """한글터 게임 페이지를 해석하여 최신 PC/모바일 패치 직링크 및 서브폴더 정보를 반환합니다."""
        patch_id = extract_hangulteo_patch_id(url)
        if not patch_id:
            raise ValueError(
                f"한글터 URL에서 패치 ID를 찾을 수 없습니다: {url}. "
                "특정 게임 패치 페이지 링크(예: https://hangulteo.com/lhzero)를 입력해주세요."
            )

        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": "https://hangulteo.com/",
        })

        target_url = f"https://hangulteo.com/{patch_id}"
        logger.info(f"[HangulteoResolver] 한글터 페이지 요청: {target_url}")
        resp = session.get(target_url, timeout=15)
        if resp.status_code == 404:
            raise FileNotFoundError(
                f"한글터 패치 페이지를 찾을 수 없습니다 (404 Not Found): {target_url}"
            )
        resp.raise_for_status()

        # HTML 내 window.__DATA 추출
        m = re.search(
            r"window\.__DATA\s*=\s*(\{.*?\});\s*(?:window\.|\n|</script>)",
            resp.text,
            re.DOTALL,
        )
        if not m:
            m = re.search(r"window\.__DATA\s*=\s*(\{.*)", resp.text)
        if not m:
            raise RuntimeError("한글터 페이지에서 패치 데이터(window.__DATA)를 추출할 수 없습니다.")

        data = None
        try:
            data = json.loads(m.group(1))
        except Exception:
            raw_json = m.group(1).split(";</script>")[0].split(";window.")[0].strip()
            data = json.loads(raw_json)

        patches = data.get("PATCHES", [])
        matched_patch = None
        for p in patches:
            if p.get("id", "").lower() == patch_id:
                matched_patch = p
                break
        if not matched_patch:
            # alias 매칭 확인
            for p in patches:
                aliases = [a.lower() for a in p.get("aliases", []) if a]
                if patch_id in aliases:
                    matched_patch = p
                    break

        if not matched_patch:
            raise FileNotFoundError(f"한글터에서 '{patch_id}' 패치 정보를 찾을 수 없습니다.")

        title = matched_patch.get("title", patch_id).strip()
        platform = matched_patch.get("platform", "").strip()
        releases = matched_patch.get("releases", [])
        if not releases:
            raise FileNotFoundError(f"한글터 '{title}' 패치에 다운로드 가능한 릴리스가 없습니다.")

        latest_rel = releases[0]
        version = latest_rel.get("v", "").strip()
        pc_file = latest_rel.get("file", "").strip()
        mobile_file = latest_rel.get("afile", "").strip()

        if not pc_file and not mobile_file:
            raise FileNotFoundError(f"한글터 '{title}' ({version}) 릴리스에 다운로드 링크가 없습니다.")

        # 서브폴더 구성: [플랫폼] 게임제목 버전 (예: [PSP] 영웅전설 : 제로의 궤적 v0.2.1)
        subfolder_parts = []
        if platform:
            subfolder_parts.append(f"[{platform}]")
        subfolder_parts.append(title)
        if version:
            subfolder_parts.append(version)
        subfolder_name = sanitize_folder_name(" ".join(subfolder_parts))

        primary_url = pc_file or mobile_file
        extra_url = (
            mobile_file
            if (pc_file and mobile_file and mobile_file != pc_file)
            else None
        )

        primary_filename = primary_url.split("/")[-1].split("?")[0]
        full_article_title = f"{title} {version}".strip()

        resolved: dict[str, Any] = {
            "url": primary_url,
            "filename": sanitize_filename(primary_filename),
            "headers": {
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
                "Referer": target_url,
            },
            "subfolder": subfolder_name,
            "article_title": full_article_title,
            "source_service": "Hangulteo",
            "source_page_url": target_url,
        }

        # 모바일용 패치가 별도로 존재하는 경우 extra_jobs로 큐잉
        if extra_url:
            extra_filename = extra_url.split("/")[-1].split("?")[0]
            resolved["extra_jobs"] = [
                {
                    "url": extra_url,
                    "filename": sanitize_filename(extra_filename),
                    "options": {
                        "subfolder": subfolder_name,
                        "article_title": full_article_title,
                        "source_page_url": target_url,
                    },
                }
            ]

        return resolved
