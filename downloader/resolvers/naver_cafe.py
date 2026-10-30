"""네이버 카페 (cafe.naver.com) 게시글 첨부파일 및 다운로드 링크 리졸버."""

import logging
import re
from urllib.parse import parse_qs, urlparse

import requests

from .base import BaseResolver

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 네이버 카페 게시글 URL 패턴
_CAFE_ARTICLE_PATH_RE = re.compile(r"^/([A-Za-z0-9_-]+)/(\d+)$")
_CLUB_ID_RE = re.compile(
    r'(?:clubid|club_id|g_sClubId)\s*[:=]\s*["\']?(\d+)', re.IGNORECASE
)


def parse_naver_cafe_url(url: str):
    """
    네이버 카페 URL에서 (club_id, article_id, cafe_name) 정보를 추출합니다.
    """
    try:
        p = urlparse(url.strip())
    except Exception:
        return None

    host = (p.hostname or "").lower()
    if not (
        host == "cafe.naver.com"
        or host == "m.cafe.naver.com"
        or host.endswith(".cafe.naver.com")
    ):
        return None

    # 1. 쿼리 스트링 패턴: ?clubid=123&articleid=456
    qs = parse_qs(p.query)
    club_id = qs.get("clubid", [""])[0] or qs.get("clubId", [""])[0]
    article_id = qs.get("articleid", [""])[0] or qs.get("articleId", [""])[0]
    if club_id and article_id:
        return {"club_id": club_id, "article_id": article_id, "cafe_name": ""}

    # 2. 패스 패턴: /ca-fe/web/cafes/{club_id}/articles/{article_id}
    m_cafe = re.search(r"/cafes/(\d+)/articles/(\d+)", p.path)
    if m_cafe:
        return {
            "club_id": m_cafe.group(1),
            "article_id": m_cafe.group(2),
            "cafe_name": "",
        }

    # 3. 일반적인 /cafe_name/article_id 패턴 (예: /hansicgu/36206)
    m_path = _CAFE_ARTICLE_PATH_RE.match(p.path)
    if m_path:
        cafe_name = m_path.group(1)
        article_id = m_path.group(2)
        # ArticleRead, ArticleList 등 특수 예약어 제외
        if cafe_name.lower() not in (
            "articleread.nhn",
            "articlelist.nhn",
            "ca-fe",
            "articles",
        ):
            return {"club_id": "", "article_id": article_id, "cafe_name": cafe_name}

    return None


def fetch_club_id_from_cafe(cafe_name: str, session: requests.Session) -> str:
    """카페 영문명/주소로부터 실제 숫자 club_id를 크롤링하여 가져옵니다."""
    url = f"https://cafe.naver.com/{cafe_name}"
    resp = session.get(url, headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=15)
    resp.raise_for_status()

    match = _CLUB_ID_RE.search(resp.text)
    if match:
        return match.group(1)
    raise ValueError(f"네이버 카페 '{cafe_name}'의 clubId를 찾을 수 없습니다.")


class NaverCafeResolver(BaseResolver):
    """네이버 카페 게시글 첨부파일 및 직링크 리졸버."""

    name = "Naver Cafe"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        p = urlparse(url.strip())
        host = (p.hostname or "").lower()

        # 1. 첨부파일 직링크 (downapi.cafe.naver.com)
        if host == "downapi.cafe.naver.com":
            return True

        # 2. 카페 게시글 링크
        info = parse_naver_cafe_url(url)
        return info is not None

    def resolve(self, url: str, options: dict | None = None) -> dict:
        url = url.strip()
        p = urlparse(url)
        host = (p.hostname or "").lower()

        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://cafe.naver.com/",
                }
            )

            # 1. 이미 추출된 downapi.cafe.naver.com 직링크인 경우
            if host == "downapi.cafe.naver.com":
                return {
                    "url": url,
                    "filename": "",
                    "headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": "https://cafe.naver.com/",
                    },
                    "cookies": "",
                    "referer": "https://cafe.naver.com/",
                    "source_service": "Naver Cafe",
                }

            # 2. 카페 게시글 URL인 경우
            info = parse_naver_cafe_url(url)
            if not info:
                raise ValueError(f"지원하지 않는 네이버 카페 URL입니다: {url}")

            club_id = info.get("club_id")
            article_id = info.get("article_id")
            cafe_name = info.get("cafe_name")

            # club_id가 없으면 cafe_name으로부터 조회
            if not club_id and cafe_name:
                club_id = fetch_club_id_from_cafe(cafe_name, session)

            if not club_id or not article_id:
                raise ValueError(f"네이버 카페 게시글 정보를 파싱할 수 없습니다: {url}")

            logger.info(
                f"네이버 카페 게시글 API 조회: club_id={club_id}, article_id={article_id}"
            )
            api_url = f"https://apis.naver.com/cafe-web/cafe-articleapi/v2.1/cafes/{club_id}/articles/{article_id}"
            resp = session.get(api_url, headers={"Referer": url}, timeout=15)
            resp.raise_for_status()

            data = resp.json()
            result = data.get("result", {})
            article = result.get("article", {})
            subject = article.get("subject", "네이버_카페_게시글")

            # 첨부파일 목록 추출 (result.attaches 중 type == 'F')
            attaches = result.get("attaches") or []
            file_items = []
            for item in attaches:
                if item.get("type") == "F" and item.get("url"):
                    file_items.append(
                        {
                            "name": item.get("fullName")
                            or item.get("name")
                            or "attachment.dat",
                            "url": item.get("url"),
                            "size": item.get("size"),
                        }
                    )

            if not file_items:
                content_html = article.get("contentHtml") or ""
                if content_html:
                    try:
                        from .webpage import WebPageResolver

                        wp_res = WebPageResolver().resolve_from_html(
                            f"<h1>{subject}</h1><article>{content_html}</article>",
                            url,
                            options=options,
                        )
                        wp_res["article_title"] = subject
                        wp_res["subfolder"] = subject
                        return wp_res
                    except Exception:
                        pass
                raise FileNotFoundError(
                    f"게시글 '{subject}'에 다운로드 가능한 첨부파일이 없습니다."
                )

            logger.info(
                f"네이버 카페 게시글에서 {len(file_items)}개의 첨부파일을 발견했습니다."
            )

            first_file = file_items[0]

            # 단일 또는 다중 파일 결과 반환
            return {
                "url": first_file["url"],
                "filename": first_file["name"],
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": f"https://cafe.naver.com/{cafe_name}/{article_id}"
                    if cafe_name
                    else "https://cafe.naver.com/",
                },
                "cookies": "",
                "referer": url,
                "source_service": "Naver Cafe",
                "files": file_items,  # 전체 첨부파일 목록
                "article_title": subject,
                "subfolder": subject,
            }
