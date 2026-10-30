"""1fichier.com (무료/게스트 다운로드 지원) 링크 리졸버 모듈."""

import logging
import re
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)


def parse_size_to_bytes(size_str: str) -> int:
    """'500.51 MB', '1.2 Go' 등의 문자열을 바이트 정수로 변환합니다."""
    if not size_str:
        return 0
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B|[KMGT]o|Bytes?)", size_str, re.I)
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    multipliers = {
        "B": 1,
        "BYTES": 1,
        "BYTE": 1,
        "O": 1,
        "KB": 1024,
        "KO": 1024,
        "MB": 1024**2,
        "MO": 1024**2,
        "GB": 1024**3,
        "GO": 1024**3,
        "TB": 1024**4,
        "TO": 1024**4,
    }
    return int(val * multipliers.get(unit, 1))


ONEFICHIER_HOSTS = (
    "1fichier.com",
    "alterupload.com",
    "cjoint.net",
    "desfichiers.com",
    "dfichiers.com",
    "megadl.fr",
    "mesfichiers.org",
    "piecejointe.net",
    "pjointe.com",
    "tenvoi.com",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class OneFichierResolver(BaseResolver):
    """1fichier.com 파일 공유 서비스 리졸버 (무료 Free / Guest 모드 지원)."""

    name = "1fichier"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()

        # 이미 최종 직다운로드 링크(예: a-34.1fichier.com/c1280450870)인 경우 DirectResolver로 넘김
        if re.match(r"^a-\d+\.1fichier\.com", host) or parsed.path.startswith("/c"):
            return False

        return any(host == h or host.endswith("." + h) for h in ONEFICHIER_HOSTS)

    def resolve(self, url: str, options: dict | None = None) -> dict:
        opts = options or {}
        password = (
            opts.get("password") or opts.get("pwd") or opts.get("code") or ""
        ).strip()

        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
                    "Referer": url,
                }
            )

            # 중간 제휴(Affiliate) 리다이렉트 랜딩 페이지 우회용 쿠키 세팅
            parsed_orig = urlparse(url)
            session.cookies.set(
                "AF", "1951572", domain=parsed_orig.hostname or "1fichier.com"
            )

            logger.info(f"1fichier 파일 페이지 접속 중: {url}")
            resp1 = session.get(url, timeout=20, allow_redirects=True)
            if resp1.status_code in (404, 410):
                raise FileNotFoundError(
                    "1fichier 파일이 삭제되었거나 존재하지 않는 링크입니다."
                )
            resp1.raise_for_status()

            html1 = resp1.text
            soup1 = BeautifulSoup(html1, "html.parser")

            # 파일명 및 크기 탐색
            filename = ""
            size_bytes = 0

            tier_name = soup1.find("span", class_="tier-name")
            if tier_name:
                filename = sanitize_filename(tier_name.get_text(strip=True))

            tier_feat = soup1.find("span", class_="tier-feat")
            if tier_feat:
                size_str = tier_feat.get_text(strip=True)
                size_bytes = parse_size_to_bytes(size_str)

            # 쿨다운 제한(대기 시간) 사전 검사
            wait_m = re.search(
                r"(?:You must wait|must wait|attendre)\s+([0-9]+)\s+(?:minutes?|min)",
                html1,
                re.I,
            )
            if wait_m:
                raise ValueError(
                    f"1fichier 무료 다운로드 대기 시간(쿨다운)이 적용 중입니다. "
                    f"약 {wait_m.group(1)}분 후에 다시 시도하거나 1fichier 프리미엄을 이용해 주세요."
                )

            # 폼(form) 탐색
            form = soup1.find("form", id="f1") or soup1.find("form", action=True)
            action_val = form.get("action") if form else None
            post_url = str(action_val) if action_val else url
            if not post_url.startswith("http"):
                post_url = urljoin(resp1.url, post_url)

            # Free download POST 데이터 구성
            post_data = {
                "dl_no_ssl": "on",
                "dlinline": "on",
            }
            if password:
                post_data["pass"] = password

            logger.info(f"1fichier Free Download 직링크 요청 중 (POST: {post_url})...")
            resp2 = session.post(
                post_url, data=post_data, timeout=25, allow_redirects=True
            )
            resp2.raise_for_status()

            html2 = resp2.text
            soup2 = BeautifulSoup(html2, "html.parser")

            # 최종 다운로드 링크 탐색
            dl_link = ""
            # 1. 'ok btn-general btn-orange' 클래스를 가진 a 태그
            for a in soup2.find_all("a", href=True):
                raw_href = a.get("href")
                if not raw_href or not isinstance(raw_href, str):
                    continue
                href = raw_href.strip()
                raw_cls = a.get("class")
                if isinstance(raw_cls, list):
                    cls = " ".join(raw_cls)
                elif isinstance(raw_cls, str):
                    cls = raw_cls
                else:
                    cls = ""
                txt = a.get_text(strip=True).lower()
                if (
                    "ok" in cls
                    or "start your download" in txt
                    or "download" in txt
                    or "cliquer ici" in txt
                ):
                    if (
                        re.search(r"^https?://[a-zA-Z0-9.-]*1fichier\.com/", href)
                        or "/c" in href
                    ):
                        dl_link = href
                        break

            # 2. 정규식으로 직다운로드 URL 탐색 fallback (예: http://a-34.1fichier.com/c1280450870)
            if not dl_link:
                m_link = re.search(
                    r"https?://[a-zA-Z0-9.-]*1fichier\.com/c[0-9a-zA-Z]+", html2
                )
                if m_link:
                    dl_link = m_link.group(0)

            # 3. 만약 여전히 링크를 찾지 못했다면 쿨다운 또는 비밀번호 오류인지 확인
            if not dl_link:
                if (
                    "bad password" in html2.lower()
                    or "mot de passe" in html2.lower()
                    or "password protected" in html2.lower()
                ):
                    raise ValueError(
                        "1fichier 파일 비밀번호가 필요하거나 입력한 암호가 일치하지 않습니다."
                    )
                wait_m2 = re.search(
                    r"(?:You must wait|must wait|attendre)\s+([0-9]+)\s+(?:minutes?|min)",
                    html2,
                    re.I,
                )
                if wait_m2:
                    raise ValueError(
                        f"1fichier 무료 다운로드 대기 시간(쿨다운)이 적용 중입니다. "
                        f"약 {wait_m2.group(1)}분 후에 다시 시도해 주세요."
                    )
                raise ValueError(
                    "1fichier에서 직다운로드 링크를 추출하지 못했습니다. 잠시 후 다시 시도해 주세요."
                )

            logger.info(
                f"1fichier 링크 분석 완료: {filename or dl_link} ({size_bytes} B) -> {dl_link}"
            )

            return {
                "url": dl_link,
                "filename": filename,
                "size": size_bytes,
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": url,
                },
                "source_service": "1fichier",
            }
