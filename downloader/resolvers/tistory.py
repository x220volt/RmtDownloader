"""티스토리 (Tistory) 및 카카오/다음 CDN 첨부파일 리졸버."""

import logging
import mimetypes
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import _MIME_MAP, DEFAULT_USER_AGENT
from .base import BaseResolver

logger = logging.getLogger(__name__)


# 첨부파일 직링크 URL 패턴
_ATTACH_URL_PATTERN = re.compile(
    r"(?:daumcdn\.net/(?:cfile|tfile)/|blog\.kakaocdn\.net/dn/|/attachment/|cfile\d+\.uf\.(?:tistory|daum)\.net/)"
)

# 이미지 확장자 (첨부파일이 아닌 본문 뷰어/라이트박스 필터링용)
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")


def _extract_filename_from_fragment(fragment: str) -> str:
    """URL의 해시 프래그먼트(#filename=foo.rar 또는 #foo.rar)에서 파일명을 추출합니다."""
    if not fragment:
        return ""
    frag = fragment.strip()
    if frag.lower().startswith("filename="):
        return unquote(frag[9:].strip().strip("\"'"))
    if "." in frag and not frag.startswith("/"):
        return unquote(frag)
    return ""


class TistoryResolver(BaseResolver):
    """티스토리 블로그 게시글 첨부파일 및 다음/카카오 CDN 직링크 리졸버."""

    name = "Tistory"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            p = urlparse(url.strip())
        except Exception:
            return False

        host = (p.hostname or "").lower()

        # 1. 티스토리 도메인 (*.tistory.com)
        if host == "tistory.com" or host.endswith(".tistory.com"):
            return True

        # 2. 다음 CDN 파일 링크 (*.daumcdn.net/cfile/ 또는 /tfile/)
        if host.endswith(".daumcdn.net") and (
            "/cfile/" in p.path or "/tfile/" in p.path
        ):
            return True

        # 3. 카카오 CDN 파일 링크 (blog.kakaocdn.net/dn/)
        if host == "blog.kakaocdn.net" and p.path.startswith("/dn/"):
            return True

        # 4. 구형 cfile 서버 (cfile*.uf.tistory.com, cfile*.uf.daum.net)
        if host.startswith("cfile") and (
            host.endswith(".tistory.com") or host.endswith(".daum.net")
        ):
            return True

        return False

    def is_direct_attachment_url(self, url: str) -> bool:
        """주어진 URL이 블로그 게시글이 아닌 파일 다운로드 직링크인지 판별합니다."""
        p = urlparse(url.strip())
        host = (p.hostname or "").lower()
        if host.endswith(".daumcdn.net") and (
            "/cfile/" in p.path or "/tfile/" in p.path
        ):
            return True
        if host == "blog.kakaocdn.net" and p.path.startswith("/dn/"):
            return True
        if host.startswith("cfile") and (
            host.endswith(".tistory.com") or host.endswith(".daum.net")
        ):
            return True
        if "/attachment/" in p.path:
            return True
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        url = url.strip()
        p = urlparse(url)
        options = options or {}

        # 1. URL fragment(#filename=...)에서 명시된 파일명 확인
        frag_filename = _extract_filename_from_fragment(p.fragment)
        clean_url = url.split("#")[0] if "#" in url else url

        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": options.get("referer") or "https://tistory.com/",
                }
            )

            # 2. 직링크(daumcdn, kakaocdn, attachment)인 경우
            if self.is_direct_attachment_url(clean_url):
                filename = frag_filename or options.get("filename") or ""

                # 쿼리 파라미터에서 파일명 확인 (예: knm=...)
                if not filename:
                    qs = parse_qs(p.query)
                    if "knm" in qs and qs["knm"]:
                        filename = unquote(qs["knm"][0].strip())

                # 패스에서 파일명 확인 (확장자가 있는 경우)
                if not filename:
                    base = unquote(p.path.split("/")[-1])
                    if base and "." in base:
                        filename = base

                # 파일명이 없거나 확장자가 없는 경우 HEAD 요청으로 Content-Disposition 또는 Content-Type 확인
                if not filename or "." not in filename:
                    try:
                        head_resp = session.head(
                            clean_url, timeout=10, allow_redirects=True
                        )
                        cd = head_resp.headers.get("Content-Disposition", "")
                        if "filename=" in cd or "filename*=" in cd:
                            m_star = re.search(
                                r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", cd
                            )
                            if m_star:
                                filename = unquote(m_star.group(1).strip().strip("\"'"))
                            else:
                                m_std = re.search(
                                    r'filename\s*=\s*"([^"]+)"|filename\s*=\s*([^;\s]+)',
                                    cd,
                                )
                                if m_std:
                                    raw_fn = m_std.group(1) or m_std.group(2)
                                    filename = unquote(raw_fn.strip().strip("\"'"))

                        # Content-Disposition이 없으면 Content-Type을 통해 확장자 보완
                        if not filename or "." not in filename:
                            ct = (
                                head_resp.headers.get("Content-Type", "")
                                .split(";")[0]
                                .strip()
                                .lower()
                            )
                            ext = (
                                _MIME_MAP.get(ct) or mimetypes.guess_extension(ct) or ""
                            )
                            if ext:
                                base_name = (
                                    filename
                                    or unquote(p.path.split("/")[-1])
                                    or "attachment"
                                )
                                if not base_name.endswith(ext):
                                    filename = f"{base_name}{ext}"
                    except Exception as e:
                        logger.warning(f"직링크 메타데이터 확인 실패: {e}")

                return {
                    "url": clean_url,
                    "filename": filename,
                    "headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": options.get("referer") or "https://tistory.com/",
                    },
                    "cookies": "",
                    "referer": options.get("referer") or "https://tistory.com/",
                    "source_service": "Tistory",
                }

            # 3. 티스토리 게시글 URL인 경우 크롤링하여 첨부파일 목록 추출
            logger.info(f"티스토리 게시글 분석 시작: {clean_url}")
            resp = session.get(clean_url, timeout=15)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # 게시글 제목 추출
            og_title = soup.find("meta", property="og:title")
            og_content = og_title.get("content") if og_title else None
            post_title = (
                og_content.strip()
                if isinstance(og_content, str) and og_content.strip()
                else (
                    soup.title.get_text(strip=True) if soup.title else "티스토리_게시글"
                )
            )

            # 본문 영역 탐색
            article = (
                soup.find("div", class_="entry-content")
                or soup.find("div", class_="article")
                or soup.find("div", class_="tt_article_useless_p_margin")
                or soup.find("div", class_="article_skin")
                or soup.find("div", class_="article-view")
                or soup.find("div", class_="post-content")
                or soup.find("div", class_="contents_style")
                or soup.find("div", class_="content")
                or soup.find("article")
                or soup
            )

            file_items = []
            seen_urls = set()

            for a in article.find_all("a", href=True):
                raw_href = a.get("href")
                if not raw_href or not isinstance(raw_href, str):
                    continue
                href = raw_href.strip()
                if not href or href.startswith("#") or href.startswith("javascript:"):
                    continue

                full_url = urljoin(clean_url, href)
                # 해시 제거된 순수 URL 기준 중복 방지
                url_no_hash = full_url.split("#")[0]
                if url_no_hash in seen_urls:
                    continue

                filename = ""
                is_attach = False

                # (1) HTML 태그 속성 우선 (download, data-ke-file-name, data-filename)
                attr_download = a.get("download")
                attr_kefile = a.get("data-ke-file-name")
                attr_datafn = a.get("data-filename")
                if isinstance(attr_download, str) and attr_download.strip():
                    filename = attr_download.strip()
                    is_attach = True
                elif isinstance(attr_kefile, str) and attr_kefile.strip():
                    filename = attr_kefile.strip()
                    is_attach = True
                elif isinstance(attr_datafn, str) and attr_datafn.strip():
                    filename = attr_datafn.strip()
                    is_attach = True

                # (2) 부모 파일 블록 컨테이너 확인 (modern tistory)
                parent_fig = a.find_parent(
                    class_=re.compile(r"file-attachment|file_box|file-block|tt-file")
                )
                if parent_fig:
                    is_attach = True
                    if not filename:
                        ntag = parent_fig.find(class_=re.compile(r"name|desc|filename"))
                        if ntag:
                            filename = ntag.get_text(strip=True)

                # (3) 첨부파일 URL 패턴 확인 (daumcdn / kakaocdn / cfile)
                if not is_attach and _ATTACH_URL_PATTERN.search(full_url):
                    is_attach = True

                if not is_attach:
                    continue

                # (4) <a> 태그 내부 텍스트를 파일명으로 매칭
                if not filename:
                    txt = a.get_text(strip=True)
                    if (
                        txt
                        and not txt.startswith("http://")
                        and not txt.startswith("https://")
                    ):
                        filename = txt

                # (5) 쿼리스트링(knm) 또는 URL 경로에서 파일명 추출
                if not filename or filename in (
                    "다운로드",
                    "내려받기",
                    "download",
                    "FILE",
                ):
                    parsed_u = urlparse(full_url)
                    qs = parse_qs(parsed_u.query)
                    if "knm" in qs and qs["knm"]:
                        filename = unquote(qs["knm"][0].strip())
                    else:
                        base = unquote(parsed_u.path.split("/")[-1])
                        if "." in base:
                            filename = base

                # (6) 단순 이미지 뷰어 링크 필터링 (아이콘이거나 명시적 첨부파일인 경우는 보존)
                if any(filename.lower().endswith(ext) for ext in _IMAGE_EXTENSIONS):
                    img_inside = a.find("img")
                    is_icon = img_inside and any(
                        k in (img_inside.get("src") or "")
                        for k in ("extension", "icon", "file")
                    )
                    if not is_icon and not a.has_attr("download") and not parent_fig:
                        continue

                if filename:
                    seen_urls.add(url_no_hash)
                    file_items.append(
                        {
                            "name": filename,
                            "url": full_url,
                            "referer": clean_url,
                        }
                    )

            if not file_items:
                from ..url_helper import is_shortened_url, parse_download_candidates, unshorten_url
                from .dispatcher import default_dispatcher

                # (1) 본문 및 전체 문서 내 <a> 태그에서 링크와 파일명/제목 함께 수집
                cand_items: list[dict] = []
                seen_cand_urls = set()

                containers_to_scan = [article] if article else []
                if soup not in containers_to_scan:
                    containers_to_scan.append(soup)

                for cont in containers_to_scan:
                    for a in cont.find_all("a", href=True):
                        raw_href = a.get("href")
                        if not raw_href or not isinstance(raw_href, str):
                            continue
                        href = raw_href.strip()
                        if not href or href.startswith("#") or href.startswith("javascript:"):
                            continue
                        full_u = urljoin(clean_url, href)
                        u_no_hash = full_u.split("#")[0]
                        if u_no_hash in seen_cand_urls or u_no_hash == clean_url:
                            continue

                        raw_txt = a.get_text(strip=True) or a.get("title") or a.get("download") or ""
                        link_txt = str(raw_txt).strip() if raw_txt else ""
                        link_txt = re.sub(r"^[\s▼▶▷►\-_]+", "", link_txt).strip()

                        cand_items.append({"url": full_u, "name": link_txt})
                        seen_cand_urls.add(u_no_hash)

                # (2) 본문 텍스트 내 평문 및 Base64 인코딩 링크 파싱
                raw_body_text = article.get_text("\n") if article else soup.get_text("\n")
                text_cands = parse_download_candidates(
                    raw_text=raw_body_text, single_referer=clean_url
                )
                for tc in text_cands:
                    t_url = tc.get("url", "")
                    if not t_url or t_url == clean_url:
                        continue
                    t_no_hash = t_url.split("#")[0]
                    if t_no_hash not in seen_cand_urls:
                        cand_items.append(tc)
                        seen_cand_urls.add(t_no_hash)

                # (3) 외부 클라우드 리졸버 매칭 및 해석 (다중 링크는 extra_jobs로 큐잉)
                resolved_first = None
                extra_jobs = []

                for cand in cand_items:
                    cand_url = cand.get("url", "")
                    if not cand_url:
                        continue
                    if is_shortened_url(cand_url):
                        expanded = unshorten_url(cand_url)
                        if expanded and expanded != cand_url:
                            cand_url = expanded

                    matched_resolver = next(
                        (
                            r
                            for r in default_dispatcher.resolvers
                            if r.name not in ("Tistory", "Direct", "WebPage Post")
                            and r.can_handle(cand_url)
                        ),
                        None,
                    )
                    if not matched_resolver:
                        continue

                    merged_opts = dict(options or {})
                    merged_opts.update(cand.get("options") or {})
                    if cand.get("name") and not merged_opts.get("filename"):
                        merged_opts["filename"] = cand["name"]
                    merged_opts.setdefault("referer", clean_url)
                    merged_opts.setdefault("subfolder", post_title)
                    merged_opts.setdefault("article_title", post_title)

                    if resolved_first is None:
                        try:
                            logger.info(
                                f"티스토리 본문에서 외부 클라우드 링크({matched_resolver.name}) 감지 및 해석: {cand_url}"
                            )
                            resolved_first = matched_resolver.resolve(cand_url, merged_opts)
                            resolved_first.setdefault("source_page_url", clean_url)
                            resolved_first.setdefault("article_title", post_title)
                            resolved_first.setdefault("subfolder", post_title)
                            if cand.get("name") and (
                                not resolved_first.get("filename")
                                or resolved_first.get("filename").startswith("gdrive_")
                            ):
                                resolved_first["filename"] = cand["name"]
                        except Exception as e:
                            logger.warning(
                                f"티스토리 외부 클라우드 링크 해석 실패 ({cand_url}): {e}"
                            )
                            continue
                    else:
                        extra_jobs.append(
                            {
                                "url": cand_url,
                                "filename": cand.get("name", ""),
                                "options": merged_opts,
                            }
                        )

                if resolved_first:
                    if extra_jobs:
                        existing_extras = list(resolved_first.get("extra_jobs") or [])
                        existing_extras.extend(extra_jobs)
                        resolved_first["extra_jobs"] = existing_extras
                    return resolved_first

                try:
                    from .webpage import WebPageResolver

                    wp_res = WebPageResolver().resolve_from_html(
                        resp.text, clean_url, options=options
                    )
                    wp_res.setdefault("article_title", post_title)
                    wp_res.setdefault("subfolder", post_title)
                    wp_res.setdefault("source_page_url", clean_url)
                    return wp_res
                except Exception:
                    pass
                raise FileNotFoundError(
                    f"티스토리 글 '{post_title}'에서 다운로드 가능한 첨부파일을 찾을 수 없습니다."
                )

            logger.info(
                f"티스토리 게시글에서 {len(file_items)}개의 첨부파일을 발견했습니다."
            )

            first_file = file_items[0]
            return {
                "url": first_file["url"],
                "filename": first_file["name"],
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": clean_url,
                },
                "cookies": "",
                "referer": clean_url,
                "source_service": "Tistory",
                "files": file_items,
                "article_title": post_title,
                "subfolder": post_title,
            }
