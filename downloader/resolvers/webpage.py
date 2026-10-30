"""웹페이지 및 커뮤니티 게시글(디시인사이드, 아카라이브, 코네, 네이버 블로그, 루리웹 등) 내 다운로드 링크 자동 검출 리졸버."""

import json
import logging
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import fix_mojibake, sanitize_filename, sanitize_folder_name
from ..url_helper import (
    PASSWORD_REGEX,
    clean_url,
    decode_base64_string,
    parse_download_candidates,
    with_password,
)
from .base import BaseResolver

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 커뮤니티/블로그/포럼 게시글 호스트 목록
COMMUNITY_HOSTS = (
    "dcinside.com",
    "kone.gg",
    "arca.live",
    "ruliweb.com",
    "blog.naver.com",
    "post.naver.com",
    "clien.net",
    "fmkorea.com",
    "inven.co.kr",
    "ppomppu.co.kr",
    "quasarzone.com",
    "coolenjoy.net",
    "dogdrip.net",
    "humoruniv.com",
    "etoland.co.kr",
    "tcafe2a.com",
    "reddit.com",
    "itch.io",
    "gamebanana.com",
    "kemono.su",
)

# 일반 웹사이트에서 게시글/문서 뷰어로 판단할 경로 패턴
ARTICLE_PATH_REGEX = re.compile(
    r"/(?:board/view|mgallery/board/view|mini/board/view|bbs/board\.php|article|articles|post|posts|entry|threads|topic)(?:/|$|\?)",
    re.I,
)

# 직다운로드 파일로 인정할 확장자 목록
DOWNLOADABLE_EXTENSIONS = (
    ".zip",
    ".7z",
    ".rar",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".zst",
    ".tgz",
    ".tbz2",
    ".alz",
    ".egg",
    ".z01",
    ".z02",
    ".z03",
    ".z04",
    ".z05",
    ".z06",
    ".z07",
    ".z08",
    ".z09",
    ".z10",
    ".001",
    ".002",
    ".003",
    ".004",
    ".005",
    ".006",
    ".007",
    ".008",
    ".009",
    ".010",
    ".r00",
    ".r01",
    ".r02",
    ".r03",
    ".r04",
    ".r05",
    ".a00",
    ".a01",
    ".a02",
    ".a03",
    ".iso",
    ".bin",
    ".cue",
    ".img",
    ".chd",
    ".rvz",
    ".wbfs",
    ".wad",
    ".mdf",
    ".mds",
    ".nsp",
    ".nsz",
    ".xci",
    ".xcz",
    ".nca",
    ".nro",
    ".ovl",
    ".3ds",
    ".cia",
    ".nds",
    ".gba",
    ".gbc",
    ".gb",
    ".sfc",
    ".smc",
    ".nes",
    ".fds",
    ".md",
    ".gen",
    ".pce",
    ".cdi",
    ".gdi",
    ".vpk",
    ".pbp",
    ".cso",
    ".pkg",
    ".apk",
    ".apks",
    ".xapk",
    ".ipa",
    ".exe",
    ".msi",
    ".dmg",
    ".deb",
    ".rpm",
    ".appimage",
    ".ips",
    ".bps",
    ".ups",
    ".xdelta",
    ".ppf",
    ".ct",
    ".pak",
    ".mod",
    ".unitypackage",
    ".torrent",
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".mp3",
    ".flac",
    ".wav",
    ".ogg",
    ".pdf",
    ".epub",
    ".cbz",
    ".cbr",
)

# 분할 압축 파일 확장자 정규식 (.z01~.z999, .r00~.r99, .s00~.s99, .a00~.a99, .001~.9999 등)
SPLIT_ARCHIVE_REGEX = re.compile(
    r"\.(?:z\d{2,3}|r\d{2,3}|s\d{2,3}|a\d{2,3}|c\d{2,3}|\d{3,4})$",
    re.I,
)


def _is_naver_attachment_url(url: str) -> bool:
    """네이버 블로그/카페 등 전용 첨부파일 다운로드 도메인 여부를 판별합니다."""
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        return host in (
            "download.blog.naver.com",
            "blogfiles.pstatic.net",
            "attach.mail.naver.com",
        )
    except Exception:
        return False


IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".ico",
    ".avif",
)

# 게시글 제목 뒤에 붙는 사이트 접미사 제거 패턴
TITLE_SUFFIX_REGEX = re.compile(
    r"\s*(?:"
    r"-\s*[^-|]+\s*\|\s*(?:Kone|코네).*|"
    r"[-|:]\s*(?:[^-|:]*(?:갤러리|디시인사이드|dcinside|아카라이브|arca\.live|루리웹|네이버\s*블로그|클리앙|에펨코리아|인벤|퀘이사존|Kone|코네).*)"
    r")$",
    re.I,
)


def _decode_response_html(resp: requests.Response) -> str:
    """Content-Type에 charset이 누락되어 ISO-8859-1로 오판된 응답도 UTF-8로 안전하게 디코딩합니다."""
    enc = (resp.encoding or "").lower()
    if not enc or enc in ("iso-8859-1", "latin-1", "latin1", "ascii"):
        for candidate in ("utf-8", "cp949", "euc-kr"):
            try:
                return resp.content.decode(candidate)
            except Exception:
                pass
    return fix_mojibake(resp.text)


def _has_downloadable_extension(url_or_name: str) -> bool:
    if not url_or_name:
        return False
    lower = unquote(url_or_name).lower().split("#")[0]
    path_part = lower.split("?")[0]
    if (
        any(path_part.endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS)
        or bool(SPLIT_ARCHIVE_REGEX.search(path_part))
        or _is_naver_attachment_url(url_or_name)
    ):
        return True
    parsed = urlparse(url_or_name)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for key in ("f_no", "filename", "file", "name", "knm", "attach"):
            for val in qs.get(key, []):
                val_lower = unquote(val).lower()
                if any(
                    val_lower.endswith(ext) for ext in DOWNLOADABLE_EXTENSIONS
                ) or bool(SPLIT_ARCHIVE_REGEX.search(val_lower)):
                    return True
    return False


def _is_image_url_or_name(url_or_name: str) -> bool:
    if not url_or_name:
        return False
    lower = unquote(url_or_name).lower().split("#")[0]
    path_part = lower.split("?")[0]
    if any(path_part.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    parsed = urlparse(url_or_name)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for key in ("f_no", "filename", "file", "name", "knm"):
            for val in qs.get(key, []):
                if any(unquote(val).lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                    return True
    return False


def _extract_post_title(soup: BeautifulSoup, url: str = "") -> str:
    """다양한 커뮤니티/블로그 HTML에서 게시글 제목을 정제하여 추출합니다."""
    selectors = (
        ".title_subject",  # 디시인사이드 PC
        ".gallview-tit-box .tit",  # 디시인사이드 모바일
        ".article-head .title-row .title",  # 아카라이브
        ".se-title-text",  # 네이버 블로그 SmartEditor ONE
        "#postViewArea .pcol1",  # 네이버 블로그 구형
        ".subject_inner_text",  # 루리웹
        ".np_18px_span",  # 에펨코리아
        ".post_subject span",  # 클리앙
        "article h1",
        "main h1",
        ".entry-title",
        ".post-title",
        ".view_title",
        "h1",
    )
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            txt = fix_mojibake(el.get_text(" ", strip=True))
            if txt and len(txt) >= 2:
                cleaned = TITLE_SUFFIX_REGEX.sub("", txt).strip()
                return sanitize_folder_name(cleaned or txt)

    for meta_sel in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        meta = soup.select_one(meta_sel)
        if meta and meta.get("content"):
            txt = fix_mojibake(str(meta.get("content")).strip())
            if txt:
                cleaned = TITLE_SUFFIX_REGEX.sub("", txt).strip()
                return sanitize_folder_name(cleaned or txt)

    if soup.title and soup.title.string:
        txt = fix_mojibake(soup.title.string.strip())
        cleaned = TITLE_SUFFIX_REGEX.sub("", txt).strip()
        return sanitize_folder_name(cleaned or txt)

    return "Web_Post"


def _find_content_containers(soup: BeautifulSoup) -> list:
    """게시글 본문 영역 컨테이너들을 우선순위에 따라 반환합니다."""
    selectors = (
        "#post-article",  # Kone.gg 본문 컨테이너
        ".write_div",  # 디시인사이드 PC 본문
        ".thum-txtin",  # 디시인사이드 모바일 본문
        ".gallview-contents",  # 디시인사이드 모바일 전체
        ".article-body .fr-view",  # 아카라이브 본문
        ".article-body",
        ".se-main-container",  # 네이버 블로그 본문
        "#postViewArea",
        ".view_content",  # 루리웹 본문
        ".board_main_view",
        "article .xe_content",  # 에펨코리아 / XE 본문
        ".rd_body",
        ".post_article",  # 클리앙 본문
        ".post_content",
        ".entry-content",  # 티스토리 / 워드프레스
        ".tt_article_useless_p_margin",
        ".article_skin",
        ".article-view",
        ".markdown-body",  # GitHub
        ".prose",  # Kone.gg / Tailwind prose
        "article",
        "main",
        '[role="main"]',
    )
    containers = []
    for sel in selectors:
        found = soup.select(sel)
        if found:
            containers.extend(found)
            break
    if not containers:
        containers = [soup.body or soup]
    return containers


def _html_to_lined_text(element) -> str:
    """HTML 요소의 줄바꿈(<br>, <p>, <div>, <li>)을 보존하여 텍스트로 변환합니다."""
    clone = BeautifulSoup(str(element), "html.parser")
    for br in clone.find_all("br"):
        br.replace_with("\n")
    for block in clone.find_all(
        ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote", "pre"]
    ):
        block.append("\n")
    return clone.get_text()


class WebPageResolver(BaseResolver):
    """커뮤니티 게시글이나 일반 웹페이지 내부에 포함된 다운로드 링크를 검출하여 해석하는 리졸버."""

    name = "WebPage Post"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False

        # 이미 명백한 바이너리 파일 확장자를 가진 직링크는 DirectResolver로 넘김
        if _has_downloadable_extension(url):
            return False
        if "image.dcinside.com/download.php" in url.lower():
            return False

        host = (parsed.hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in COMMUNITY_HOSTS):
            return True

        if ARTICLE_PATH_REGEX.search(parsed.path or ""):
            return True

        return False

    def _fetch_html(self, url: str, options: dict | None = None) -> tuple[str, str]:
        """대상 웹페이지의 HTML을 가져옵니다 (필요 시 네이버 블로그 프레임 추적 및 스텔스 브라우저 폴백)."""
        opts = options or {}
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if opts.get("referer"):
            headers["Referer"] = str(opts["referer"])

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        if "kone.gg" in host:
            headers["User-Agent"] = (
                "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
            )

        need_browser = False
        html_text = ""
        final_url = url

        if not need_browser:
            try:
                with requests.Session() as session:
                    session.headers.update(headers)
                    resp = session.get(url, allow_redirects=True, timeout=20)
                    final_url = resp.url
                    if (
                        resp.status_code in (403, 503)
                        or "Just a moment..." in resp.text
                        or "cf-browser-verification" in resp.text
                    ):
                        need_browser = True
                    else:
                        resp.raise_for_status()
                        html_text = _decode_response_html(resp)

                        # 네이버 블로그 mainFrame 언래핑
                        if (
                            "blog.naver.com"
                            in (urlparse(final_url).hostname or "").lower()
                        ):
                            soup_frame = BeautifulSoup(html_text, "html.parser")
                            main_frame = soup_frame.find(
                                ["iframe", "frame"], id="mainFrame"
                            )
                            if main_frame and main_frame.get("src"):
                                frame_url = urljoin(
                                    final_url, str(main_frame.get("src"))
                                )
                                resp_inner = session.get(
                                    frame_url,
                                    headers={"Referer": final_url},
                                    timeout=20,
                                )
                                if resp_inner.ok:
                                    html_text = _decode_response_html(resp_inner)
                                    final_url = resp_inner.url
            except Exception as e:
                logger.warning(
                    f"[WebPageResolver] 일반 HTTP 요청 실패 ({e}) -> 브라우저 폴백 시도"
                )
                need_browser = True

        if need_browser or not html_text:
            try:
                from ..browser_solver import StealthBrowserSolver

                flaresolverr_url = str(opts.get("flaresolverr_url") or "")
                browser_html = StealthBrowserSolver.fetch_html(
                    url,
                    flaresolverr_url=flaresolverr_url,
                    wait_selector=".write_div, .article-body, .prose, article, main, body",
                    extra_wait_sec=2.5,
                    headless=True,
                )
                if browser_html:
                    html_text = browser_html
            except Exception as e:
                logger.warning(f"[WebPageResolver] 스텔스 브라우저 패치 실패: {e}")

        if not html_text:
            raise ValueError(f"웹페이지 내용을 가져올 수 없습니다: {url}")

        return html_text, final_url

    def extract_candidates_from_html(
        self,
        html_text: str,
        page_url: str,
        options: dict | None = None,
    ) -> tuple[str, list[dict]]:
        """HTML 문서에서 게시글 제목과 다운로드 가능한 후보 링크 목록(우선순위 정렬)을 추출합니다."""
        from .dispatcher import default_dispatcher

        soup = BeautifulSoup(html_text, "html.parser")
        post_title = _extract_post_title(soup, page_url)
        containers = _find_content_containers(soup)

        specialized_resolvers = [
            r
            for r in default_dispatcher.resolvers
            if r.name not in ("WebPage Post", "Direct Link", "Tistory", "NaverCafe")
        ]

        from ..url_helper import is_shortened_url, unshorten_url

        def _is_cloud_service_url(u: str) -> bool:
            if not u:
                return False
            if u.lower().startswith("magnet:"):
                return True
            if any(r.can_handle(u) for r in specialized_resolvers):
                return True
            if is_shortened_url(u):
                expanded = unshorten_url(u)
                if expanded and expanded != u:
                    return any(
                        r.can_handle(expanded) for r in specialized_resolvers
                    ) or _has_downloadable_extension(expanded)
            return False

        cloud_candidates: list[dict] = []
        direct_file_candidates: list[dict] = []
        seen_urls: set[str] = set()

        def _add_candidate(
            target_list: list[dict],
            u: str,
            name: str = "",
            pwd: str = "",
            extra_opts: dict | None = None,
        ):
            u_clean = clean_url(u)
            if not u_clean:
                return
            if is_shortened_url(u_clean):
                expanded = unshorten_url(u_clean)
                if expanded and expanded != u_clean:
                    u_clean = expanded
            # 현재 게시글 자기 자신 링크는 제외
            if u_clean.rstrip("/") == page_url.rstrip("/"):
                return
            dedup_key = u_clean.split("#")[0]
            if dedup_key in seen_urls:
                return
            seen_urls.add(dedup_key)

            cand_opts = dict(options or {})
            cand_opts["referer"] = page_url
            if extra_opts:
                cand_opts.update(extra_opts)
            if pwd:
                cand_opts["password"] = pwd
                parsed_u = urlparse(u_clean)
                qs_u = parse_qs(parsed_u.query)
                if not any(
                    k in qs_u for k in ("pwd", "password", "dlkey", "code", "key")
                ):
                    u_clean = with_password(u_clean, pwd)

            target_list.append(
                {
                    "url": u_clean,
                    "name": name,
                    "options": cand_opts,
                }
            )

        # 1. 본문 컨테이너 내부의 <a> 태그 및 텍스트(Base64/난독화 링크/암호 포함) 정밀 스캔
        for container in containers:
            body_text = _html_to_lined_text(container)
            # 본문 전체에서 기본 암호 후보 추출 (링크 주변에 암호가 따로 적힌 경우 대비)
            global_pwd_match = PASSWORD_REGEX.search(body_text)
            fallback_body_pwd = (
                global_pwd_match.group(1).strip() if global_pwd_match else ""
            )

            # (a) <a> 태그 스캔
            for a in container.find_all("a", href=True):
                raw_href = str(a.get("href") or "").strip()
                if not raw_href or raw_href.startswith(
                    ("#", "javascript:", "mailto:", "tel:")
                ):
                    continue
                full_href = urljoin(page_url, raw_href)

                # 주변 문맥(부모 블록 텍스트)에서 암호 탐색
                parent_block = a.find_parent(["p", "div", "li", "td"]) or a
                context_text = parent_block.get_text(" ", strip=True)
                ctx_pwd_m = PASSWORD_REGEX.search(context_text)
                link_pwd = (
                    ctx_pwd_m.group(1).strip() if ctx_pwd_m else fallback_body_pwd
                )

                link_text = a.get_text(strip=True)
                attr_fn = (
                    a.get("download")
                    or a.get("data-ke-file-name")
                    or a.get("data-filename")
                    or ""
                )
                explicit_name = str(attr_fn).strip() if isinstance(attr_fn, str) else ""

                if not explicit_name:
                    # 스마트에디터 ONE 등 첨부파일 모듈 구조 탐색
                    file_mod = a.find_parent(
                        class_=re.compile(
                            r"se-module-file|file_attach|attach_file", re.I
                        )
                    )
                    if file_mod:
                        fn_el = file_mod.find(
                            class_=re.compile(r"se-file-name|file_name|filename", re.I)
                        )
                        ext_el = file_mod.find(
                            class_=re.compile(
                                r"se-file-extension|file_ext|file-extension", re.I
                            )
                        )
                        if fn_el and ext_el:
                            fn_str = fn_el.get_text(strip=True)
                            ext_str = ext_el.get_text(strip=True)
                            if ext_str and not fn_str.lower().endswith(ext_str.lower()):
                                explicit_name = f"{fn_str}{ext_str}"
                            else:
                                explicit_name = fn_str
                        elif fn_el:
                            explicit_name = fn_el.get_text(strip=True)

                if not explicit_name:
                    path_leaf = unquote(urlparse(full_href).path).split("/")[-1].strip()
                    if path_leaf and _has_downloadable_extension(path_leaf):
                        explicit_name = path_leaf

                if _is_cloud_service_url(full_href):
                    _add_candidate(cloud_candidates, full_href, pwd=link_pwd)
                elif (
                    a.has_attr("download")
                    or explicit_name
                    or _has_downloadable_extension(full_href)
                ):
                    if not _is_image_url_or_name(full_href):
                        fn = explicit_name or (
                            link_text
                            if "." in link_text and not link_text.startswith("http")
                            else ""
                        )
                        _add_candidate(
                            direct_file_candidates, full_href, name=fn, pwd=link_pwd
                        )

            # (b) 본문 텍스트 전체에서 URL, Base64 인코딩 링크, ttps:// 난독화 링크, 암호 추출
            parsed_text_cands = parse_download_candidates(
                raw_text=body_text,
                single_referer=page_url,
            )
            for tc in parsed_text_cands:
                tc_url = tc.get("url", "")
                tc_opts = tc.get("options") or {}
                tc_pwd = tc_opts.get("password") or fallback_body_pwd
                if _is_cloud_service_url(tc_url):
                    _add_candidate(
                        cloud_candidates,
                        tc_url,
                        name=tc.get("name", ""),
                        pwd=tc_pwd,
                        extra_opts=tc_opts,
                    )
                elif _has_downloadable_extension(tc_url) and not _is_image_url_or_name(
                    tc_url
                ):
                    _add_candidate(
                        direct_file_candidates,
                        tc_url,
                        name=tc.get("name", ""),
                        pwd=tc_pwd,
                        extra_opts=tc_opts,
                    )

            # (c) 줄바꿈으로 쪼개진 멀티라인 Base64 블록(aHR0cHM6, bWFnbmV0 등) 추가 탐지
            compact_text = re.sub(r"[\r\n\t ]+", "", body_text)
            for b64_m in re.finditer(
                r"(?:aHR0c|bWFnbmV0)[A-Za-z0-9+/_-]{8,}={0,2}|[A-Za-z0-9+/_-]{16,}={1,2}",
                compact_text,
            ):
                decoded_b64 = decode_base64_string(b64_m.group(0))
                if decoded_b64 and decoded_b64 != b64_m.group(0):
                    for sub_c in parse_download_candidates(
                        raw_text=decoded_b64, single_referer=page_url
                    ):
                        sub_u = sub_c.get("url", "")
                        sub_opts = sub_c.get("options") or {}
                        sub_pwd = sub_opts.get("password") or fallback_body_pwd
                        if _is_cloud_service_url(sub_u):
                            _add_candidate(
                                cloud_candidates,
                                sub_u,
                                pwd=sub_pwd,
                                extra_opts=sub_opts,
                            )
                        elif _has_downloadable_extension(
                            sub_u
                        ) or not _is_image_url_or_name(sub_u):
                            _add_candidate(
                                direct_file_candidates,
                                sub_u,
                                pwd=sub_pwd,
                                extra_opts=sub_opts,
                            )

        # 2. 디시인사이드 첨부파일 박스 (.appending_file li a) 확인
        for a in soup.select(".appending_file li a[href]"):
            href = urljoin(page_url, str(a.get("href") or "").strip())
            fn = a.get_text(strip=True)
            # 본문에 이미 클라우드/압축파일 링크가 있으면 단순 게시글 짤방(.png/.jpg 등)은 제외하고 실제 압축/실행 첨부파일만 포함
            if not _is_image_url_or_name(fn) and not _is_image_url_or_name(href):
                _add_candidate(direct_file_candidates, href, name=sanitize_filename(fn))

        # 3. 네이버 블로그 첨부파일 메타데이터 (encodedAttachFileData) 확인
        m_naver_files = re.search(r"encodedAttachFileData\s*=\s*'([^']+)'", html_text)
        if m_naver_files:
            try:
                attach_list = json.loads(unquote(m_naver_files.group(1)))
                for item in attach_list:
                    f_url = item.get("encodedAttachFileUrl") or item.get(
                        "attachFileUrl"
                    )
                    f_name = (
                        item.get("encodedAttachFileName")
                        or item.get("attachFileName")
                        or ""
                    )
                    if f_url:
                        _add_candidate(
                            direct_file_candidates,
                            f_url,
                            name=sanitize_filename(unquote(f_name)),
                        )
            except Exception:
                pass

        # 4. Kone.gg SSR 바이너리 데이터 (api.kone.gg/v1/article/get) 확인
        ssr_script = soup.find(
            "script", attrs={"data-url": re.compile(r"api\.kone\.gg/v1/article/get")}
        )
        if ssr_script and ssr_script.text:
            try:
                import base64 as _b64

                from ..kone_watcher import extract_kone_ssr_strings

                ssr_data = json.loads(ssr_script.text)
                b64_body = ssr_data.get("body", "")
                if b64_body:
                    raw_bytes = _b64.b64decode(b64_body)
                    ssr_text = "\n".join(extract_kone_ssr_strings(raw_bytes))
                    ssr_pwd_m = PASSWORD_REGEX.search(ssr_text)
                    ssr_pwd = ssr_pwd_m.group(1).strip() if ssr_pwd_m else ""
                    for sc in parse_download_candidates(
                        raw_text=ssr_text, single_referer=page_url
                    ):
                        sc_url = sc.get("url", "")
                        sc_opts = sc.get("options") or {}
                        sc_pwd = sc_opts.get("password") or ssr_pwd
                        if _is_cloud_service_url(sc_url):
                            _add_candidate(
                                cloud_candidates,
                                sc_url,
                                name=sc.get("name", ""),
                                pwd=sc_pwd,
                                extra_opts=sc_opts,
                            )
                        elif _has_downloadable_extension(
                            sc_url
                        ) and not _is_image_url_or_name(sc_url):
                            _add_candidate(
                                direct_file_candidates,
                                sc_url,
                                name=sc.get("name", ""),
                                pwd=sc_pwd,
                                extra_opts=sc_opts,
                            )
            except Exception:
                pass

        return post_title, cloud_candidates + direct_file_candidates

    def resolve_from_html(
        self,
        html_text: str,
        page_url: str,
        options: dict | None = None,
    ) -> dict:
        """주어진 HTML에서 다운로드 후보 링크들을 추출하고 첫 번째 유효한 작업을 해석하여 반환합니다."""
        from .dispatcher import default_dispatcher

        post_title, all_candidates = self.extract_candidates_from_html(
            html_text, page_url, options=options
        )
        if not all_candidates:
            raise FileNotFoundError(
                f"게시글 '{post_title}' 본문에서 다운로드 가능한 클라우드 링크나 첨부파일을 찾을 수 없습니다."
            )

        logger.info(
            f"[WebPageResolver] 게시글 '{post_title}'에서 {len(all_candidates)}개의 다운로드 링크 감지: "
            f"{[c['url'] for c in all_candidates]}"
        )

        last_err = None
        for idx, cand in enumerate(all_candidates):
            cand_url = cand["url"]
            cand_opts = dict(cand.get("options") or {})
            if cand.get("name") and not cand_opts.get("filename"):
                cand_opts["filename"] = cand["name"]

            try:
                # 자기 자신(WebPageResolver)으로 무한 재귀하지 않도록 리졸버 선택
                resolver = next(
                    (
                        r
                        for r in default_dispatcher.resolvers
                        if r.name != "WebPage Post" and r.can_handle(cand_url)
                    ),
                    None,
                )
                if not resolver:
                    continue
                resolved = resolver.resolve(cand_url, cand_opts)
                if cand.get("name") and (
                    not resolved.get("filename")
                    or resolved.get("filename") == "download.php"
                ):
                    resolved["filename"] = cand["name"]
                if not resolved.get("referer"):
                    resolved["referer"] = page_url
                if (
                    isinstance(resolved.get("headers"), dict)
                    and "Referer" not in resolved["headers"]
                ):
                    resolved["headers"]["Referer"] = page_url

                resolved.setdefault("source_page_url", page_url)
                resolved.setdefault("article_title", post_title)
                resolved.setdefault("subfolder", post_title)

                # 게시글 내 추가 다운로드 링크가 더 있다면 extra_jobs로 함께 큐에 등록
                remaining = all_candidates[idx + 1 :]
                if remaining:
                    extra_jobs = list(resolved.get("extra_jobs") or [])
                    for rem in remaining:
                        extra_jobs.append(
                            {
                                "url": rem["url"],
                                "filename": rem.get("name", ""),
                                "options": rem.get("options") or {},
                            }
                        )
                    resolved["extra_jobs"] = extra_jobs

                return resolved
            except Exception as e:
                logger.warning(
                    f"[WebPageResolver] 후보 링크 해석 실패 ({cand_url}): {e}"
                )
                last_err = e

        if last_err:
            raise last_err
        raise FileNotFoundError(
            f"게시글 '{post_title}' 내 다운로드 링크를 해석하지 못했습니다."
        )

    def resolve(self, url: str, options: dict | None = None) -> dict:
        html_text, final_url = self._fetch_html(url, options=options)
        return self.resolve_from_html(html_text, final_url, options=options)
