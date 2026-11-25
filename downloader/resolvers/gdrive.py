"""Google Drive (구글 드라이브) 파일 공유 링크 리졸버 모듈."""

import json
import logging
import os
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

GDRIVE_HOSTS = (
    "drive.google.com",
    "docs.google.com",
    "drive.usercontent.google.com",
)


def extract_gdrive_folder_id(url: str) -> str:
    """Google Drive URL에서 폴더 ID를 추출합니다."""
    if not url:
        return ""
    m = re.search(r"/(?:drive/)?(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    return ""


def extract_gdrive_id(url: str) -> str:
    """Google Drive URL에서 파일 ID를 추출합니다."""
    if not url:
        return ""

    # 1. /file/(u/\d+/)?d/{id} or /document/(u/\d+/)?d/{id}
    m = re.search(r"/(?:file|document)/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    # 2. Query param: id={id}
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]
    except Exception:
        pass

    # 3. /d/{id}
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)

    return ""


class GoogleDriveResolver(BaseResolver):
    """Google Drive 파일 공유 링크 리졸버 (소용량 및 대용량 바이러스 검사 경고 자동 통과 지원)."""

    name = "gdrive"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url.strip())
            host = (parsed.hostname or "").lower()
            if any(host == h or host.endswith("." + h) for h in GDRIVE_HOSTS):
                # 파일 ID 또는 폴더 ID가 있거나 드라이브 경로가 포함된 경우
                return (
                    bool(extract_gdrive_id(url))
                    or bool(extract_gdrive_folder_id(url))
                    or "/drive/folders/" in parsed.path
                    or "/folders/" in parsed.path
                )
        except Exception:
            pass
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        """
        Google Drive 공유 링크를 다운로드 가능한 직링크로 변환합니다.
        대용량 파일 바이러스 검사 경고(HTML confirmation form/link) 자동 승인 및 토큰 전달 지원.
        공유 폴더 링크(drive/folders)인 경우 내부 파일들을 자동 추출하여 큐잉합니다.
        """
        folder_id = extract_gdrive_folder_id(url)
        if folder_id or "/drive/folders/" in url or "/folders/" in url:
            return self._resolve_folder(url, folder_id, options)

        file_id = extract_gdrive_id(url)
        if not file_id:
            raise ValueError(f"Google Drive 링크에서 파일 ID를 찾을 수 없습니다: {url}")

        cookie_val = str(
            (options or {}).get("cookies")
            or (options or {}).get("cookie")
            or ""
        ).strip()
        if not cookie_val:
            try:
                from config import load_settings

                s = load_settings()
                cookie_val = str(
                    s.get("gdrive_cookie")
                    or s.get("google_cookie")
                    or ""
                ).strip()
            except Exception:
                pass
        if not cookie_val:
            import os

            cookie_val = str(
                os.environ.get("GDRIVE_COOKIE")
                or os.environ.get("GOOGLE_COOKIE")
                or ""
            ).strip()

        with requests.Session() as session:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            if cookie_val:
                headers["Cookie"] = cookie_val
            session.headers.update(headers)

            filename = ""

            # 1. View 페이지 메타데이터(og:title, title)에서 파일명 추출 시도
            try:
                view_url = f"https://drive.google.com/file/d/{file_id}/view"
                resp_view = session.get(view_url, timeout=10)
                if resp_view.status_code == 200:
                    soup = BeautifulSoup(resp_view.text, "html.parser")
                    og_title = soup.find("meta", property="og:title")
                    og_content = og_title.get("content") if og_title else None
                    if isinstance(og_content, str) and og_content.strip():
                        filename = og_content.strip()
                    elif soup.title and soup.title.string:
                        t = soup.title.string.strip()
                        t = re.sub(
                            r"\s*-\s*Google (?:Drive|드라이브)\s*$", "", t, flags=re.I
                        )
                        if t:
                            filename = t
            except Exception as e:
                logger.debug(
                    f"Google Drive view 페이지 메타데이터 파싱 예외 (무시 후 다운로드 계속): {e}"
                )

            # 2. 직링크 요청
            dl_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download"
            logger.info(f"[GoogleDriveResolver] 다운로드 엔드포인트 요청: {dl_url}")
            with session.get(
                dl_url, stream=True, allow_redirects=True, timeout=15
            ) as resp:
                if resp.status_code == 404:
                    raise FileNotFoundError(
                        f"Google Drive 파일을 찾을 수 없거나 삭제되었습니다 (404 Not Found): id={file_id}"
                    )
                elif resp.status_code == 403:
                    raise PermissionError(
                        "Google Drive 파일 접근 권한이 없거나 다운로드가 금지되어 있습니다 (403 Forbidden)."
                    )

                content_type = str(resp.headers.get("Content-Type") or "").lower()
                content_disp = str(resp.headers.get("Content-Disposition") or "")
                final_url = resp.url

                # Content-Disposition 헤더에서 파일명 파싱
                if content_disp:
                    m_fn = re.search(
                        r"filename\*?=(?:UTF-8\'\')?\"?([^;\r\n\"]+)\"?", content_disp
                    )
                    if m_fn:
                        extracted_fn = urllib.parse.unquote(m_fn.group(1).strip("\"'"))
                        if extracted_fn:
                            filename = extracted_fn

                # 3. HTML 응답인 경우: 대용량 파일 바이러스 검사 경고 페이지 또는 에러 페이지 분석
                if "text/html" in content_type:
                    text = resp.text

                    # 할당량 초과 또는 권한 필요 여부 확인
                    if (
                        "Too many users have viewed or downloaded this file recently"
                        in text
                        or "Quota exceeded" in text
                        or "최근에 너무 많은 사용자" in text
                        or "용량 초과" in text
                    ):
                        raise PermissionError(
                            "Google Drive 일일 다운로드 할당량(24시간)이 초과되어 현재 직접 다운로드할 수 없습니다. "
                            "(최근에 너무 많은 사용자가 이 파일을 보거나 다운로드했습니다.)\n\n"
                            "💡 [우회 다운로드 방법 - 사본 만들기]:\n"
                            "1. 웹 브라우저에서 본인 구글 계정으로 로그인한 상태로 해당 파일 링크를 엽니다.\n"
                            "2. 우측 상단의 '내 드라이브에 바로가기 추가' 또는 메뉴에서 '사본 만들기(Make a copy)'를 실행합니다.\n"
                            "3. 내 드라이브에 새로 생성된 사본 파일('사본 - ...')의 공유 링크를 원격 다운로더에 입력하시면 대역폭 제한 없이 즉시 다운로드됩니다."
                        )
                    if (
                        "You need access" in text
                        or "액세스 권한 필요" in text
                        or "로그인" in text
                        and "권한" in text
                    ):
                        raise PermissionError(
                            "Google Drive 파일 접근 권한이 필요합니다. 공유 설정을 확인하세요."
                        )
                    if (
                        "infected with a virus" in text.lower()
                        or "infected file" in text.lower()
                        or "감염된 파일" in text
                    ):
                        raise PermissionError(
                            "Google Drive 파일 다운로드 불가: 구글 바이러스 검사에서 악성 파일로 감지되어 공유 다운로드가 차단되었습니다 "
                            "('Sorry, this file is infected with a virus. Only the owner is allowed to download infected files.'). "
                            "구글 보안 정책상 파일 소유자(업로더) 계정으로만 다운로드가 허용되는 파일입니다."
                        )

                    soup = BeautifulSoup(text, "html.parser")

                    # Form 기반 확인 토큰 추출 (#download-form 또는 form[action*='download'])
                    form = soup.select_one("#download-form") or soup.select_one(
                        "form[action*='download']"
                    )
                    if form:
                        raw_action = form.get("action")
                        action = (
                            str(raw_action)
                            if raw_action
                            else "https://drive.usercontent.google.com/download"
                        )
                        if not action.startswith("http"):
                            action = urllib.parse.urljoin(final_url, action)
                        params = {}
                        for inp in form.find_all("input"):
                            name = inp.get("name")
                            val = inp.get("value")
                            if name and val is not None:
                                params[str(name)] = str(val)
                        if params:
                            final_url = (
                                action
                                + ("&" if "?" in action else "?")
                                + urllib.parse.urlencode(params)
                            )
                            logger.info(
                                f"[GoogleDriveResolver] 대용량 바이러스 경고 폼 파싱 완료: {final_url}"
                            )
                    else:
                        # 링크 기반 확인 토큰 추출 (#uc-download-link 등)
                        link = soup.select_one("#uc-download-link") or soup.select_one(
                            "a[href*='export=download']"
                        )
                        href_val = link.get("href") if link else None
                        if href_val and isinstance(href_val, str):
                            final_url = urllib.parse.urljoin(final_url, href_val)
                            logger.info(
                                f"[GoogleDriveResolver] 대용량 다운로드 링크 파싱 완료: {final_url}"
                            )
                        else:
                            m_href = re.search(
                                r'href="([^"]*export=download[^"]*)"', text
                            )
                            if m_href:
                                final_url = urllib.parse.urljoin(
                                    final_url, m_href.group(1).replace("&amp;", "&")
                                )
                                logger.info(
                                    f"[GoogleDriveResolver] 정규식 다운로드 링크 파싱 완료: {final_url}"
                                )
                            else:
                                err_caption = soup.select_one(".uc-error-caption, .uc-warning-caption")
                                err_sub = soup.select_one(".uc-error-subcaption")
                                if err_caption:
                                    detail = err_caption.get_text(strip=True)
                                    if err_sub:
                                        detail += f" ({err_sub.get_text(strip=True)})"
                                    if any(
                                        term in detail
                                        for term in (
                                            "Too many users",
                                            "Quota exceeded",
                                            "최근에 너무 많은 사용자",
                                            "다운로드할 수 없습니다",
                                        )
                                    ):
                                        raise PermissionError(
                                            "Google Drive 일일 다운로드 할당량(24시간)이 초과되어 현재 직접 다운로드할 수 없습니다. "
                                            f"({detail})\n\n"
                                            "💡 [우회 다운로드 방법 - 사본 만들기]:\n"
                                            "1. 웹 브라우저에서 본인 구글 계정으로 로그인한 상태로 해당 파일 링크를 엽니다.\n"
                                            "2. 우측 상단의 '내 드라이브에 바로가기 추가' 또는 메뉴에서 '사본 만들기(Make a copy)'를 실행합니다.\n"
                                            "3. 내 드라이브에 새로 생성된 사본 파일('사본 - ...')의 공유 링크를 원격 다운로더에 입력하시면 대역폭 제한 없이 즉시 다운로드됩니다."
                                        )
                                    raise PermissionError(f"Google Drive 파일 다운로드 거부: {detail}")
                                raise RuntimeError(
                                    f"Google Drive 직링크 추출 실패: 파일 대신 안내 웹페이지가 수신되었습니다 ({soup.title.string.strip() if soup.title else 'HTML'})."
                                )

                if not filename and options and options.get("filename"):
                    opt_fn = str(options["filename"]).strip()
                    if opt_fn and not opt_fn.startswith("http"):
                        filename = opt_fn

                if not filename:
                    filename = f"gdrive_{file_id}.bin"

                # 세션 쿠키 수집
                cookies_dict = session.cookies.get_dict()
                cookie_str = (
                    "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
                    if cookies_dict
                    else ""
                )

                resolved_data: dict[str, Any] = {
                    "url": final_url,
                    "filename": sanitize_filename(filename),
                    "headers": {
                        "User-Agent": USER_AGENT,
                        "Accept": "*/*",
                    },
                    "cookies": cookie_str,
                    "referer": f"https://drive.google.com/file/d/{file_id}/view",
                    "source_service": "Google Drive",
                }

                # 크기 헤더가 존재하는 경우 반영
                cl = resp.headers.get("Content-Length")
                if (
                    cl
                    and cl.isdigit()
                    and int(cl) > 0
                    and "text/html" not in content_type
                ):
                    resolved_data["size"] = int(cl)

                return resolved_data

    def _resolve_folder(
        self, url: str, folder_id: str, options: dict | None = None
    ) -> dict:
        """Google Drive 공유 폴더를 해석하여 첫 번째 파일 직링크 및 나머지 파일들을 extra_jobs로 큐잉합니다."""
        folder_id = folder_id or extract_gdrive_folder_id(url)
        cookie_val = str(
            (options or {}).get("cookies")
            or (options or {}).get("cookie")
            or ""
        ).strip()
        if not cookie_val:
            try:
                from config import load_settings

                s = load_settings()
                cookie_val = str(
                    s.get("gdrive_cookie")
                    or s.get("google_cookie")
                    or ""
                ).strip()
            except Exception:
                pass
        if not cookie_val:
            cookie_val = str(
                os.environ.get("GDRIVE_COOKIE")
                or os.environ.get("GOOGLE_COOKIE")
                or ""
            ).strip()

        with requests.Session() as session:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            if cookie_val:
                headers["Cookie"] = cookie_val
            session.headers.update(headers)

            folder_url = (
                f"https://drive.google.com/drive/folders/{folder_id}"
                if folder_id
                else url
            )
            logger.info(f"[GoogleDriveResolver] 폴더 페이지 요청: {folder_url}")
            resp = session.get(folder_url, timeout=15)
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"Google Drive 폴더를 찾을 수 없거나 삭제되었습니다 (404 Not Found): {folder_url}"
                )
            if resp.status_code == 403:
                raise PermissionError("Google Drive 폴더 접근 권한이 없습니다 (403 Forbidden).")

            if "accounts.google.com" in resp.url and (
                "signin" in resp.url or "ServiceLogin" in resp.url
            ):
                raise PermissionError(
                    "Google Drive 폴더가 비공개 상태이거나 접근 권한이 필요합니다. (구글 로그인 필요)"
                )

            if any(term in resp.text for term in ("You need access", "액세스 권한 필요")):
                raise PermissionError(
                    "Google Drive 폴더 접근 권한이 필요합니다. 공유 권한(링크가 있는 모든 사용자)을 확인하세요."
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            title = soup.title.string if soup.title else ""
            clean_title = re.sub(
                r"\s*-\s*Google (?:Drive|드라이브)\s*$", "", title.strip(), flags=re.I
            ).strip()
            folder_title = (
                sanitize_folder_name(clean_title)
                if clean_title
                else f"gdrive_folder_{folder_id}"
            )

            collected_files = self._collect_folder_files_recursive(
                session, folder_id, resp.text, rel_path="", depth=0
            )
            if not collected_files:
                raise FileNotFoundError("Google Drive 폴더에 다운로드 가능한 파일이 없습니다.")

            first_item = collected_files[0]
            first_url = f"https://drive.google.com/file/d/{first_item['id']}/view"
            child_opts = dict(options or {})
            if cookie_val and not child_opts.get("cookies"):
                child_opts["cookies"] = cookie_val
            child_opts["filename"] = first_item["name"]

            first_resolved = self.resolve(first_url, options=child_opts)

            eff_subfolder = (
                os.path.join(folder_title, first_item["subpath"])
                if first_item.get("subpath")
                else folder_title
            )
            first_resolved["subfolder"] = eff_subfolder
            first_resolved["article_title"] = folder_title
            first_resolved["source_page_url"] = url
            first_resolved["source_service"] = "Google Drive"

            extra_jobs = []
            for item in collected_files[1:]:
                item_subfolder = (
                    os.path.join(folder_title, item["subpath"])
                    if item.get("subpath")
                    else folder_title
                )
                extra_jobs.append({
                    "url": f"https://drive.google.com/file/d/{item['id']}/view",
                    "filename": item["name"],
                    "options": {
                        "subfolder": item_subfolder,
                        "article_title": folder_title,
                        "source_page_url": url,
                        "cookies": cookie_val,
                    },
                })

            if extra_jobs:
                existing = list(first_resolved.get("extra_jobs") or [])
                existing.extend(extra_jobs)
                first_resolved["extra_jobs"] = existing

            return first_resolved

    def _extract_items_from_html(self, html: str) -> list[dict]:
        items = []
        seen = set()

        # 1. window['_DRIVE_ivd'] parsing
        m = re.search(r"window\['_DRIVE_ivd'\]\s*=\s*'([^']+)'", html)
        if m:
            try:
                raw = m.group(1).replace(r"\/", "/")
                raw = re.sub(
                    r"\\x([0-9a-fA-F]{2})",
                    lambda match: chr(int(match.group(1), 16)),
                    raw,
                )
                data = json.loads(raw)
                if (
                    isinstance(data, list)
                    and len(data) > 0
                    and isinstance(data[0], list)
                ):
                    for entry in data[0]:
                        if isinstance(entry, list) and len(entry) > 3:
                            fid = str(entry[0]) if entry[0] else ""
                            name = str(entry[2]) if entry[2] else ""
                            mime = str(entry[3]) if entry[3] else ""
                            size = (
                                entry[13]
                                if len(entry) > 13
                                and isinstance(entry[13], (int, float))
                                else None
                            )
                            if fid and name and fid not in seen:
                                seen.add(fid)
                                is_folder = (
                                    mime == "application/vnd.google-apps.folder"
                                )
                                items.append({
                                    "id": fid,
                                    "name": name,
                                    "mime": mime,
                                    "size": size,
                                    "is_folder": is_folder,
                                })
            except Exception as e:
                logger.debug(f"[GoogleDriveResolver] _DRIVE_ivd 파싱 예외: {e}")

        # 2. DOM fallback
        if not items:
            soup = BeautifulSoup(html, "html.parser")
            for el in soup.select("[data-id]"):
                fid = el.get("data-id", "").strip()
                if not fid or len(fid) < 20 or fid in seen:
                    continue
                strong = el.find("strong")
                name = strong.get_text(strip=True) if strong else ""
                if not name:
                    tooltip = el.get("data-tooltip", "")
                    if tooltip:
                        name = tooltip.split(" ")[0].strip()
                if fid and name:
                    seen.add(fid)
                    items.append({
                        "id": fid,
                        "name": name,
                        "mime": "",
                        "size": None,
                        "is_folder": False,
                    })

        # 3. Regex fallback
        if not items:
            for match in re.finditer(
                r'\["([a-zA-Z0-9_-]{25,})",\s*\["[^"]*"\],\s*"([^"]+)",\s*"([^"]+)"',
                html,
            ):
                fid, name, mime = match.groups()
                if fid not in seen:
                    seen.add(fid)
                    is_folder = mime == "application/vnd.google-apps.folder"
                    items.append({
                        "id": fid,
                        "name": name,
                        "mime": mime,
                        "size": None,
                        "is_folder": is_folder,
                    })

        return items

    def _collect_folder_files_recursive(
        self,
        session: requests.Session,
        current_folder_id: str,
        current_html: str,
        rel_path: str = "",
        depth: int = 0,
    ) -> list[dict]:
        if depth > 3:
            return []

        items = self._extract_items_from_html(current_html)
        collected_files = []

        for item in items:
            clean_name = sanitize_filename(item["name"])
            if item["is_folder"]:
                child_rel = (
                    os.path.join(rel_path, clean_name) if rel_path else clean_name
                )
                try:
                    sub_url = f"https://drive.google.com/drive/folders/{item['id']}"
                    sub_resp = session.get(sub_url, timeout=15)
                    if sub_resp.status_code == 200:
                        child_files = self._collect_folder_files_recursive(
                            session,
                            item["id"],
                            sub_resp.text,
                            rel_path=child_rel,
                            depth=depth + 1,
                        )
                        collected_files.extend(child_files)
                except Exception as e:
                    logger.warning(
                        f"Google Drive 하위 폴더({item['name']}) 조회 실패: {e}"
                    )
            else:
                item_copy = dict(item)
                item_copy["subpath"] = rel_path
                collected_files.append(item_copy)

        return collected_files
