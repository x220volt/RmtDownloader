"""GitHub (github.com) 릴리즈 에셋, 저장소 아카이브 및 Raw 파일 다운로드 리졸버."""

import logging
import os
import re
from typing import Any, Optional
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 15

_GITHUB_WEB_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_CONTENT_HOSTS = frozenset(
    {
        "raw.githubusercontent.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "gist.githubusercontent.com",
    }
)
_RESERVED_OWNERS = frozenset(
    {
        "settings",
        "notifications",
        "explore",
        "trending",
        "topics",
        "marketplace",
        "sponsors",
        "login",
        "signup",
        "join",
        "orgs",
        "organizations",
        "users",
        "search",
        "features",
        "pricing",
        "enterprise",
        "about",
        "security",
        "customer-stories",
        "readme",
        "collections",
        "events",
        "codespaces",
        "copilot",
    }
)
_NON_DOWNLOADABLE_SUBPATHS = frozenset(
    {
        "issues",
        "pull",
        "pulls",
        "discussions",
        "actions",
        "wiki",
        "security",
        "pulse",
        "graphs",
        "settings",
        "commits",
        "commit",
        "compare",
        "network",
        "forks",
        "stargazers",
        "watchers",
        "contributors",
        "projects",
        "milestones",
        "labels",
        "branches",
        "tags",
        "search",
        "blame",
        "edit",
        "delete",
        "find",
        "new",
    }
)
_ALLOWED_SUBPATHS = frozenset(
    {"releases", "archive", "zipball", "tarball", "raw", "blob", "tree"}
)
_ARCHIVE_EXTS = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar")


class _NoReleaseAvailable(FileNotFoundError):
    """저장소는 존재하지만 등록된 릴리즈가 없을 때 사용하는 내부 예외."""


def _normalize_host(hostname: Optional[str]) -> str:
    return (hostname or "").lower().rstrip(".")


def _normalize_repo(repo: str) -> str:
    cleaned = unquote(repo).strip()
    if cleaned.lower().endswith(".git") and len(cleaned) > 4:
        return cleaned[:-4]
    return cleaned


def _extract_disposition_filename(query: str) -> str:
    if not query:
        return ""
    qs = parse_qs(query)
    for key in ("response-content-disposition", "content-disposition"):
        for val in qs.get(key, []):
            m_ext = re.search(
                r"filename\*\s*=\s*(?:UTF-8|utf-8)?''([^;]+)", val, flags=re.I
            )
            if m_ext:
                decoded = unquote(m_ext.group(1)).strip().strip("\"'")
                if decoded:
                    return sanitize_filename(decoded)
            m_plain = re.search(r'filename\s*=\s*("([^"]+)"|[^;]+)', val, flags=re.I)
            if m_plain:
                raw_fn = (m_plain.group(2) or m_plain.group(1) or "").strip().strip("\"'")
                if raw_fn:
                    return sanitize_filename(unquote(raw_fn))
    return ""


def _build_direct_archive_name(repo: str, raw_leaf: str, kind: str) -> str:
    """저장소 아카이브 URL에서 저장소 이름이 포함된 깔끔한 파일명을 생성합니다."""
    leaf = unquote(raw_leaf or "").strip()
    ext = ".tar.gz" if "tar" in kind.lower() else ".zip"
    if not leaf:
        base = f"{repo}{ext}" if repo else f"archive{ext}"
        return sanitize_filename(base)

    has_known_ext = any(leaf.lower().endswith(e) for e in _ARCHIVE_EXTS)
    if not has_known_ext:
        leaf = f"{leaf}{ext}"

    if repo and repo.lower() not in leaf.lower():
        return sanitize_filename(f"{repo}-{leaf}")
    return sanitize_filename(leaf)


class GitHubResolver(BaseResolver):
    """GitHub 릴리즈 에셋, 저장소 아카이브, Raw 파일 다운로드 리졸버."""

    name: str = "GitHub"

    def can_handle(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        try:
            parsed = urlparse(url.strip())
            if parsed.scheme not in ("http", "https"):
                return False
            host = _normalize_host(parsed.hostname)
            segments = [s for s in (parsed.path or "").split("/") if s]

            if host in _GITHUB_CONTENT_HOSTS:
                return len(segments) >= 2

            if host == "api.github.com":
                return bool(
                    re.match(
                        r"^/repos/[^/]+/[^/]+/(?:releases|zipball|tarball)(?:/|$)",
                        parsed.path or "",
                        re.I,
                    )
                )

            if host not in _GITHUB_WEB_HOSTS or len(segments) < 2:
                return False

            owner = segments[0].lower()
            repo = _normalize_repo(segments[1])
            if not owner or not repo or owner.startswith(".") or owner in _RESERVED_OWNERS:
                return False

            if len(segments) == 2:
                return True

            sub = segments[2].lower()
            if sub in _NON_DOWNLOADABLE_SUBPATHS or sub not in _ALLOWED_SUBPATHS:
                return False

            if sub == "releases":
                if len(segments) == 3:
                    return True
                action = segments[3].lower()
                if action == "latest":
                    return len(segments) == 4
                if action in ("tag", "expanded_assets"):
                    return len(segments) >= 5
                if action == "download":
                    return len(segments) >= 6
                return False

            if sub == "archive":
                return len(segments) >= 4
            if sub in ("zipball", "tarball"):
                return len(segments) >= 3
            if sub in ("blob", "raw"):
                return len(segments) >= 5
            if sub == "tree":
                return len(segments) >= 4

            return False
        except Exception:
            return False

    def _build_direct_result(
        self,
        dl_url: str,
        filename: str,
        repo: str = "",
        tag: str = "",
        referer: str = "",
    ) -> dict[str, Any]:
        clean_name = sanitize_filename(filename) if filename else ""
        label_parts = [p for p in (repo, tag) if p]
        if label_parts:
            folder_label = sanitize_folder_name(" ".join(label_parts))
        elif clean_name:
            folder_label = sanitize_folder_name(os.path.splitext(clean_name)[0])
        else:
            folder_label = ""

        headers: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer

        result: dict[str, Any] = {
            "url": dl_url,
            "filename": clean_name or None,
            "headers": headers,
            "source_service": self.name,
        }
        if folder_label:
            result["subfolder"] = folder_label
            result["article_title"] = folder_label
        return result

    def _resolve_release_via_api(
        self,
        session: requests.Session,
        owner: str,
        repo: str,
        tag: str = "",
    ) -> Optional[dict[str, Any]]:
        api_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
        }
        if tag:
            api_url = (
                f"https://api.github.com/repos/{owner}/{repo}"
                f"/releases/tags/{quote(tag, safe='')}"
            )
        else:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"

        try:
            resp = session.get(api_url, headers=api_headers, timeout=TIMEOUT)
        except Exception as e:
            logger.debug("[GitHub] REST API 요청 실패 -> HTML 폴백 시도 (%s)", e)
            return None

        rel_data: Any = None
        if resp.status_code == 200:
            try:
                rel_data = resp.json()
            except ValueError:
                return None
        elif resp.status_code == 404:
            if tag:
                raise FileNotFoundError(
                    f"GitHub 릴리즈(태그: {tag})를 찾을 수 없습니다 (404): "
                    f"https://github.com/{owner}/{repo}/releases/tag/{tag}"
                )
            # 프리릴리즈만 있거나 릴리즈가 전혀 없는 경우 목록 엔드포인트 확인
            list_url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=1"
            try:
                list_resp = session.get(list_url, headers=api_headers, timeout=TIMEOUT)
            except Exception as e:
                logger.debug("[GitHub] 릴리즈 목록 API 요청 실패 -> HTML 폴백 (%s)", e)
                return None
            if list_resp.status_code == 200:
                try:
                    items = list_resp.json()
                except ValueError:
                    return None
                if isinstance(items, list) and items:
                    rel_data = items[0]
                elif isinstance(items, list):
                    raise _NoReleaseAvailable(
                        f"GitHub 저장소({owner}/{repo})에 등록된 릴리즈가 없습니다."
                    )
            elif list_resp.status_code == 404:
                raise FileNotFoundError(
                    f"GitHub 저장소({owner}/{repo})를 찾을 수 없습니다 (404)."
                )
            else:
                return None
        elif resp.status_code in (403, 429) or resp.status_code >= 500:
            logger.info(
                "[GitHub] API 응답(HTTP %s) -> expanded_assets HTML 폴백 전환",
                resp.status_code,
            )
            return None
        else:
            return None

        if not isinstance(rel_data, dict):
            return None

        resolved_tag = str(rel_data.get("tag_name") or tag or "").strip()
        if not resolved_tag:
            return None

        rel_name = str(rel_data.get("name") or resolved_tag).strip()
        page_url = str(
            rel_data.get("html_url")
            or f"https://github.com/{owner}/{repo}/releases/tag/{quote(resolved_tag, safe='')}"
        )
        assets: list[dict[str, Any]] = []
        for asset in rel_data.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            dl_url = str(asset.get("browser_download_url") or "").strip()
            if not dl_url:
                continue
            assets.append(
                {
                    "name": str(asset.get("name") or "").strip(),
                    "url": dl_url,
                    "size": int(asset.get("size") or 0),
                }
            )
        return {
            "tag_name": resolved_tag,
            "rel_name": rel_name,
            "page_url": page_url,
            "assets": assets,
        }

    def _resolve_release_via_html(
        self,
        session: requests.Session,
        owner: str,
        repo: str,
        tag: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        web_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resolved_tag = tag.strip()
        rel_name = resolved_tag

        if not resolved_tag:
            target = f"https://github.com/{owner}/{repo}/releases/latest"
            resp = session.get(
                target, headers=web_headers, allow_redirects=True, timeout=TIMEOUT
            )
            if resp.status_code == 404:
                raise FileNotFoundError(
                    f"GitHub 릴리즈 페이지를 찾을 수 없습니다 (404): {target}"
                )
            resp.raise_for_status()

            final_tag_m = re.search(r"/releases/tag/([^/?#]+)", resp.url or "")
            if final_tag_m:
                resolved_tag = unquote(final_tag_m.group(1)).strip()

            soup = BeautifulSoup(resp.text, "html.parser")
            if not resolved_tag:
                for frag in soup.find_all("include-fragment"):
                    src = str(frag.get("src") or "")
                    frag_m = re.search(r"/releases/expanded_assets/([^/?#]+)", src)
                    if frag_m:
                        resolved_tag = unquote(frag_m.group(1)).strip()
                        break

            if not resolved_tag:
                tag_pat = re.compile(
                    rf"^/{re.escape(owner)}/{re.escape(repo)}/releases/tag/([^/?#]+)",
                    re.I,
                )
                for a in soup.find_all("a", href=True):
                    m_tag = tag_pat.match(str(a.get("href") or ""))
                    if m_tag:
                        resolved_tag = unquote(m_tag.group(1)).strip()
                        break

            if not resolved_tag:
                raise _NoReleaseAvailable(
                    f"GitHub 저장소({owner}/{repo})에서 릴리즈를 찾을 수 없습니다."
                )

            h1 = soup.select_one("h1.d-inline, [data-testid='release-name'], main h1")
            if h1 and h1.get_text(" ", strip=True):
                candidate_title = h1.get_text(" ", strip=True)
                if candidate_title.lower() not in ("releases", f"{owner}/{repo}".lower()):
                    rel_name = candidate_title

        exp_url = (
            f"https://github.com/{owner}/{repo}/releases/expanded_assets/"
            f"{quote(resolved_tag, safe='')}"
        )
        exp_resp = session.get(exp_url, headers=web_headers, timeout=TIMEOUT)
        if exp_resp.status_code == 404:
            raise FileNotFoundError(
                f"GitHub 릴리즈(태그: {resolved_tag})를 찾을 수 없습니다 (404)."
            )
        exp_resp.raise_for_status()

        exp_soup = BeautifulSoup(exp_resp.text, "html.parser")
        dl_pat = re.compile(
            rf"^(?:https?://(?:www\.)?github\.com)?/{re.escape(owner)}/{re.escape(repo)}/releases/download/",
            re.I,
        )
        assets: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for a in exp_soup.find_all("a", href=True):
            raw_href = str(a.get("href") or "").strip()
            if not dl_pat.match(raw_href):
                continue
            full_dl_url = urljoin("https://github.com", raw_href)
            if full_dl_url in seen_urls:
                continue
            seen_urls.add(full_dl_url)
            leaf = unquote(urlparse(full_dl_url).path.rsplit("/", 1)[-1]).strip()
            assets.append(
                {
                    "name": sanitize_filename(leaf) if leaf else a.get_text(strip=True),
                    "url": full_dl_url,
                    "size": 0,
                }
            )

        page_url = (
            f"https://github.com/{owner}/{repo}/releases/tag/{quote(resolved_tag, safe='')}"
        )
        return {
            "tag_name": resolved_tag,
            "rel_name": rel_name or resolved_tag,
            "page_url": page_url,
            "assets": assets,
        }

    def _resolve_release(
        self,
        session: requests.Session,
        owner: str,
        repo: str,
        tag: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        rel_info = self._resolve_release_via_api(session, owner, repo, tag=tag)
        if rel_info is None:
            rel_info = self._resolve_release_via_html(
                session, owner, repo, tag=tag, source_url=source_url
            )

        tag_name = rel_info["tag_name"]
        rel_name = (rel_info.get("rel_name") or "").strip() or tag_name
        page_url = rel_info.get("page_url") or (
            f"https://github.com/{owner}/{repo}/releases/tag/{quote(tag_name, safe='')}"
        )
        folder_label = sanitize_folder_name(f"{repo} {rel_name}".strip())

        raw_assets = rel_info.get("assets") or []
        file_items: list[dict[str, Any]] = []
        for asset in raw_assets:
            dl_url = str(asset.get("url") or "").strip()
            if not dl_url:
                continue
            raw_name = str(asset.get("name") or "").strip()
            if not raw_name:
                raw_name = unquote(urlparse(dl_url).path.rsplit("/", 1)[-1]).strip()
            file_items.append(
                {
                    "name": sanitize_filename(raw_name) or "asset",
                    "url": dl_url,
                    "size": int(asset.get("size") or 0),
                    "referer": page_url,
                }
            )

        if file_items:
            # 단순 텍스트/해시/체크섬 파일이 첫 번째로 지정되어 주 다운로드 대상을 가리지 않도록 후순위로 배치
            low_exts = (
                ".txt", ".md", ".sha256", ".sha1", ".md5", ".sums",
                ".json", ".xml", ".yml", ".yaml"
            )
            def _is_low_priority(item: dict[str, Any]) -> int:
                name = str(item.get("name") or "").lower()
                return 1 if (name.endswith(low_exts) or "sha256" in name or "checksum" in name) else 0

            file_items.sort(key=_is_low_priority)
            first = file_items[0]
            result: dict[str, Any] = {
                "url": first["url"],
                "filename": first["name"],
                "headers": {
                    "User-Agent": USER_AGENT,
                    "Referer": page_url,
                    "Accept": "*/*",
                },
                "size": first["size"],
                "source_service": self.name,
                "subfolder": folder_label,
                "article_title": folder_label,
                "source_page_url": page_url,
            }
            if len(file_items) > 1:
                result["files"] = file_items
            return result

        # 업로드된 에셋이 없는 릴리즈는 소스 아카이브(.zip)로 폴백
        zip_url = (
            f"https://github.com/{owner}/{repo}/archive/refs/tags/"
            f"{quote(tag_name, safe='')}.zip"
        )
        zip_name = sanitize_filename(f"{repo}-{tag_name}.zip")
        return {
            "url": zip_url,
            "filename": zip_name,
            "headers": {
                "User-Agent": USER_AGENT,
                "Referer": page_url,
                "Accept": "*/*",
            },
            "source_service": self.name,
            "subfolder": folder_label,
            "article_title": folder_label,
            "source_page_url": page_url,
        }

    def _resolve_repo_default(self, owner: str, repo: str) -> dict[str, Any]:
        """저장소의 최신 릴리즈를 해석하고, 릴리즈가 없으면 HEAD 아카이브로 폴백합니다."""
        with requests.Session() as session:
            try:
                return self._resolve_release(
                    session,
                    owner,
                    repo,
                    tag="",
                    source_url=f"https://github.com/{owner}/{repo}/releases/latest",
                )
            except _NoReleaseAvailable:
                head_url = f"https://github.com/{owner}/{repo}/archive/HEAD.zip"
                head_name = sanitize_filename(f"{repo}-HEAD.zip")
                return self._build_direct_result(
                    head_url,
                    head_name,
                    repo=repo,
                    tag="HEAD",
                    referer=f"https://github.com/{owner}/{repo}",
                )

    def resolve(self, url: str, options: dict | None = None) -> dict:
        if not self.can_handle(url):
            raise ValueError(f"지원하지 않는 GitHub URL 형태입니다: {url}")

        parsed = urlparse(url.strip())
        host = _normalize_host(parsed.hostname)
        segments = [s for s in (parsed.path or "").split("/") if s]

        # 1. raw / release-assets / objects / codeload / gist CDN 직링크
        if host in _GITHUB_CONTENT_HOSTS:
            disp_name = _extract_disposition_filename(parsed.query)
            if host == "codeload.github.com" and len(segments) >= 3:
                repo = _normalize_repo(segments[1])
                kind = segments[2]
                filename = disp_name or _build_direct_archive_name(
                    repo, segments[-1], kind
                )
                return self._build_direct_result(
                    url.strip(), filename, repo=repo, tag=unquote(segments[-1])
                )

            repo = _normalize_repo(segments[1]) if host == "raw.githubusercontent.com" and len(segments) >= 2 else ""
            leaf = disp_name or sanitize_filename(unquote(segments[-1]))
            return self._build_direct_result(url.strip(), leaf, repo=repo)

        # 2. GitHub REST API URL (api.github.com/repos/<owner>/<repo>/...)
        if host == "api.github.com":
            owner = segments[1]
            repo = _normalize_repo(segments[2])
            resource = segments[3].lower()
            if resource in ("zipball", "tarball"):
                ref = "/".join(segments[4:]) if len(segments) >= 5 else "HEAD"
                filename = _build_direct_archive_name(repo, ref, resource)
                return self._build_direct_result(
                    url.strip(), filename, repo=repo, tag=unquote(ref)
                )
            tag = ""
            if len(segments) >= 6 and segments[4].lower() == "tags":
                tag = unquote("/".join(segments[5:]))
            with requests.Session() as session:
                return self._resolve_release(
                    session, owner, repo, tag=tag, source_url=url.strip()
                )

        # 3. 일반 GitHub 웹 URL (github.com/<owner>/<repo>/...)
        owner = segments[0]
        repo = _normalize_repo(segments[1])

        if len(segments) == 2:
            return self._resolve_repo_default(owner, repo)

        sub = segments[2].lower()

        if sub in ("archive", "zipball", "tarball"):
            leaf = segments[-1] if len(segments) >= 4 else "HEAD"
            filename = _extract_disposition_filename(parsed.query) or _build_direct_archive_name(
                repo, leaf, sub
            )
            tag_label = re.sub(r"\.(?:zip|tar\.gz|tgz|tar\.bz2|tbz2|tar)$", "", unquote(leaf), flags=re.I)
            return self._build_direct_result(
                url.strip(),
                filename,
                repo=repo,
                tag=tag_label,
                referer=f"https://github.com/{owner}/{repo}",
            )

        if sub in ("blob", "raw"):
            leaf_raw = unquote(segments[-1])
            if sub == "blob" and re.match(
                r"^readme(?:\.[a-z0-9_-]+)?\.(?:md|markdown)$", leaf_raw, re.I
            ):
                return self._resolve_repo_default(owner, repo)
            ref_and_path = "/".join(segments[3:])
            raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref_and_path}"
            leaf = sanitize_filename(leaf_raw)
            return self._build_direct_result(
                raw_url,
                leaf,
                repo=repo,
                tag=leaf,
                referer=url.strip(),
            )

        if sub == "tree":
            ref = unquote("/".join(segments[3:]))
            dl_url = f"https://github.com/{owner}/{repo}/archive/{quote(ref, safe='/')}.zip"
            safe_ref = ref.replace("/", "_")
            filename = sanitize_filename(f"{repo}-{safe_ref}.zip")
            return self._build_direct_result(
                dl_url,
                filename,
                repo=repo,
                tag=safe_ref,
                referer=url.strip(),
            )

        # /<owner>/<repo>/releases/...
        if len(segments) >= 6 and segments[3].lower() == "download":
            tag = unquote(segments[4])
            leaf = sanitize_filename(unquote(segments[-1]))
            return self._build_direct_result(
                url.strip(),
                leaf,
                repo=repo,
                tag=tag,
                referer=f"https://github.com/{owner}/{repo}/releases/tag/{quote(tag, safe='')}",
            )

        tag = ""
        if len(segments) >= 5 and segments[3].lower() in ("tag", "expanded_assets"):
            tag = unquote("/".join(segments[4:]))

        with requests.Session() as session:
            return self._resolve_release(
                session, owner, repo, tag=tag, source_url=url.strip()
            )
