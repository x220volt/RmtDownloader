"""네이버 카페 (cafe.naver.com) 게시글 첨부파일 및 다운로드 링크 리졸버."""

import logging
import os
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

    qs = parse_qs(p.query)

    # 0. iframe_url 또는 iframe_url_utf8 파라미터가 있는 경우 (PC 웹 프레임 주소)
    for iframe_key in ("iframe_url", "iframe_url_utf8"):
        if iframe_key in qs:
            try:
                iframe_u = qs[iframe_key][0]
                parsed_iframe = urlparse(iframe_u)
                qs_iframe = parse_qs(parsed_iframe.query)
                c_id = qs_iframe.get("clubid", [""])[0] or qs_iframe.get("clubId", [""])[0]
                a_id = qs_iframe.get("articleid", [""])[0] or qs_iframe.get("articleId", [""])[0]
                if c_id and a_id:
                    path_parts = [seg for seg in p.path.strip("/").split("/") if seg]
                    cafe_name = path_parts[0] if path_parts else ""
                    return {"club_id": c_id, "article_id": a_id, "cafe_name": cafe_name}
            except Exception:
                pass

    # 1. 쿼리 스트링 패턴: ?clubid=123&articleid=456
    club_id = qs.get("clubid", [""])[0] or qs.get("clubId", [""])[0]
    article_id = qs.get("articleid", [""])[0] or qs.get("articleId", [""])[0]

    # 1-1. art JWT 토큰에서 cafeId / articleId 추출 (모바일 공유 링크 등)
    if qs.get("art"):
        try:
            art_token = qs["art"][0]
            import base64
            import json

            for part in art_token.split("."):
                try:
                    rem = len(part) % 4
                    if rem:
                        part += "=" * (4 - rem)
                    decoded = json.loads(
                        base64.urlsafe_b64decode(part.encode("ascii"))
                    )
                    if isinstance(decoded, dict) and decoded.get("cafeId"):
                        club_id = club_id or str(decoded["cafeId"])
                        if decoded.get("articleId"):
                            article_id = article_id or str(decoded["articleId"])
                        break
                except Exception:
                    continue
        except Exception:
            pass

    if club_id and article_id:
        path_clean = p.path.strip("/")
        cafe_name = ""
        m_cn = re.search(r"cafes/([A-Za-z0-9_-]+)", path_clean)
        if m_cn and not m_cn.group(1).isdigit():
            cafe_name = m_cn.group(1)
        elif path_clean and not path_clean.startswith(("ca-fe", "Article")):
            cafe_name = path_clean.split("/")[0]
        return {"club_id": club_id, "article_id": article_id, "cafe_name": cafe_name}

    # 2. 패스 패턴: /cafes/{club_id_or_cafe_name}/articles/{article_id}
    # 예: /ca-fe/web/cafes/py2jin/articles/14018
    # 예: /ca-fe/cafes/29771102/articles/358137
    # 예: /cafes/30771917/articles/14018
    m_cafe = re.search(r"/cafes/([A-Za-z0-9_-]+)/articles/(\d+)", p.path)
    if m_cafe:
        target = m_cafe.group(1)
        art_id = m_cafe.group(2)
        return {
            "club_id": target if target.isdigit() else club_id,
            "article_id": art_id,
            "cafe_name": "" if target.isdigit() else target,
        }

    # 3. /cafe_name/articles/{article_id} 패턴 (예: /py2jin/articles/14018)
    m_cafe_articles = re.match(r"^/([A-Za-z0-9_-]+)/articles/(\d+)$", p.path)
    if m_cafe_articles:
        target = m_cafe_articles.group(1)
        art_id = m_cafe_articles.group(2)
        if target.lower() not in ("ca-fe", "articles"):
            return {
                "club_id": target if target.isdigit() else club_id,
                "article_id": art_id,
                "cafe_name": "" if target.isdigit() else target,
            }

    # 4. 일반적인 /cafe_name/article_id 패턴 (예: /hansicgu/36206)
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
            return {
                "club_id": cafe_name if cafe_name.isdigit() else club_id,
                "article_id": article_id,
                "cafe_name": "" if cafe_name.isdigit() else cafe_name,
            }

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


def _normalize_naver_cookie(cookie_str: str) -> str:
    """네이버 쿠키 문자열을 정리하고 올바른 형식으로 보정합니다."""
    c = (cookie_str or "").strip()
    if c.lower().startswith("cookie:"):
        c = c[7:].strip()
    c = c.strip("\"'")
    if not c:
        return ""
    # 사용자가 키 이름 없이 값만 입력한 경우 (예: NID_AUT 토큰 값 단독 입력 등)
    if "=" not in c and len(c) > 50:
        logger.warning(
            "[Naver Cafe] 쿠키에 키 이름(NID_AUT=, NID_SES=)이 누락되었습니다. "
            "'NID_AUT=...; NID_SES=...' 형식으로 두 쿠키를 모두 입력해야 정상 인증됩니다."
        )
    return c


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
        options = options or {}

        # 0. 쿠키 파싱 (옵션 -> 서버 설정 -> 환경변수)
        cookie_val = str(
            options.get("cookies")
            or options.get("cookie")
            or ""
        ).strip()
        if not cookie_val:
            try:
                from config import load_settings

                s = load_settings()
                cookie_val = str(
                    s.get("naver_cafe_cookie")
                    or s.get("naver_cookie")
                    or ""
                ).strip()
            except Exception:
                pass
        if not cookie_val:
            cookie_val = (
                os.environ.get("NAVER_CAFE_COOKIE")
                or os.environ.get("NAVER_COOKIE")
                or ""
            ).strip()

        cookie_val = _normalize_naver_cookie(cookie_val)

        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://cafe.naver.com/",
                }
            )
            if cookie_val:
                session.headers["Cookie"] = cookie_val

            # 1. 이미 추출된 downapi.cafe.naver.com 직링크인 경우
            if host == "downapi.cafe.naver.com":
                headers = {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": "https://cafe.naver.com/",
                }
                if cookie_val:
                    headers["Cookie"] = cookie_val
                return {
                    "url": url,
                    "filename": "",
                    "headers": headers,
                    "cookies": cookie_val,
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
            resp = None
            try:
                resp = session.get(api_url, headers={"Referer": url}, timeout=15)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as http_err:
                status_code = (
                    http_err.response.status_code
                    if http_err.response is not None
                    else (resp.status_code if resp is not None else 0)
                )
                if status_code == 401:
                    err_msg = (
                        f"네이버 카페 '{cafe_name or club_id}' 게시글({article_id})은 "
                        f"로그인이 필요한 멤버 전용 글입니다. "
                        f"크롬 확장 프로그램에서 해당 페이지를 열어 스캔 전송하시거나, "
                        f"서버 설정에서 네이버 쿠키(naver_cafe_cookie)를 등록해 주세요."
                    )
                    raise PermissionError(err_msg) from http_err
                elif status_code == 404:
                    raise FileNotFoundError(
                        f"존재하지 않거나 삭제된 네이버 카페 게시글입니다: {url}"
                    ) from http_err
                raise

            data = resp.json()
            result = data.get("result", {})
            article = result.get("article", {})
            raw_subject = article.get("subject", "네이버_카페_게시글")
            import html
            from ..engine import fix_mojibake, sanitize_filename, sanitize_folder_name

            subject = fix_mojibake(html.unescape(raw_subject)).strip()
            clean_subfolder = sanitize_folder_name(subject) if subject else "Naver_Cafe"

            # 첨부파일 목록 추출 (result.attaches 중 type == 'F')
            attaches = result.get("attaches") or []
            file_items = []
            for item in attaches:
                if item.get("type") == "F" and item.get("url"):
                    raw_fn = item.get("fullName") or item.get("name") or "attachment.dat"
                    clean_fn = sanitize_filename(fix_mojibake(html.unescape(raw_fn)))
                    file_items.append(
                        {
                            "name": clean_fn,
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
                        wp_res["subfolder"] = clean_subfolder
                        wp_res["source_page_url"] = url
                        return wp_res
                    except (PermissionError, RuntimeError):
                        raise
                    except Exception as e:
                        logger.warning(f"[Naver Cafe] 본문 링크 해석 실패: {e}")
                raise FileNotFoundError(
                    f"게시글 '{subject}'에 다운로드 가능한 첨부파일이 없습니다."
                )

            logger.info(
                f"네이버 카페 게시글에서 {len(file_items)}개의 첨부파일을 발견했습니다."
            )

            first_file = file_items[0]

            headers = {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": f"https://cafe.naver.com/{cafe_name}/{article_id}"
                if cafe_name
                else "https://cafe.naver.com/",
            }
            if cookie_val:
                headers["Cookie"] = cookie_val

            # 단일 또는 다중 파일 결과 반환
            return {
                "url": first_file["url"],
                "filename": first_file["name"],
                "headers": headers,
                "cookies": cookie_val,
                "referer": url,
                "source_page_url": url,
                "source_service": "Naver Cafe",
                "files": file_items,  # 전체 첨부파일 목록
                "article_title": subject,
                "subfolder": clean_subfolder,
            }
