"""네이버 MYBOX (mybox.naver.com) 공유 링크 리졸버 모듈."""

import logging
import os
from typing import Optional
from urllib.parse import parse_qs, quote, urlparse

import requests

from ..engine import sanitize_filename, sanitize_folder_name
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TIMEOUT = 20

PROPERTY_API_URL = "https://api.mybox.naver.com/service/v2/link/property"
FILE_PROPERTY_API_URL = "https://api.mybox.naver.com/service/link/file/property"
FOLDER_LIST_API_URL = "https://api.mybox.naver.com/service/link/folder/list"
DOWNLOAD_API_BASE = "https://fs.mybox.naver.com/file/download.api"


def _is_mybox_host(hostname: Optional[str]) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return host == "mybox.naver.com" or host.endswith(".mybox.naver.com")


def _fetch_folder_files(
    session: requests.Session,
    share_key: str,
    folder_resource_key: str,
    subfolder_rel: str = "",
    depth: int = 0,
) -> list[dict]:
    """MYBOX 공유 폴더의 하위 항목을 재귀적으로 탐색하여 다운로드 파일 목록을 생성합니다."""
    if depth > 5:
        return []

    files = []
    try:
        url = (
            f"{FOLDER_LIST_API_URL}?shareKey={quote(share_key)}"
            f"&resourceKey={quote(folder_resource_key)}&pageCount=1000"
        )
        resp = session.get(url, timeout=TIMEOUT)
        if not resp.ok:
            logger.warning(
                f"네이버 MYBOX 폴더 목록 조회 실패 (HTTP {resp.status_code})"
            )
            return files

        data = resp.json()
        if data.get("code") != "0" or not isinstance(data.get("result"), dict):
            logger.warning(
                f"네이버 MYBOX 폴더 목록 응답 오류: {data.get('message', data.get('code'))}"
            )
            return files

        raw_list = data["result"].get("list") or []
        for item in raw_list:
            itype = item.get("resourceType")
            ikey = item.get("resourceKey")
            if not ikey:
                continue

            sub_path = item.get("subPath") or ""
            raw_name = (
                sub_path.strip("/").split("/")[-1]
                if sub_path
                else (item.get("resourceName") or f"file_{ikey[:8]}")
            )
            clean_name = sanitize_filename(raw_name)

            if itype == "folder":
                child_subfolder = (
                    os.path.join(subfolder_rel, clean_name)
                    if subfolder_rel
                    else clean_name
                )
                files.extend(
                    _fetch_folder_files(
                        session,
                        share_key,
                        ikey,
                        subfolder_rel=child_subfolder,
                        depth=depth + 1,
                    )
                )
            elif itype == "file":
                file_rel_name = (
                    os.path.join(subfolder_rel, clean_name)
                    if subfolder_rel
                    else clean_name
                )
                token = item.get("authToken")
                dl_url = (
                    f"{DOWNLOAD_API_BASE}?svcType=MYBOX-WEB&resourceKey={quote(ikey)}"
                )
                if token:
                    dl_url += f"&authToken={quote(token)}"

                files.append(
                    {
                        "name": file_rel_name,
                        "url": dl_url,
                        "size": item.get("resourceSize"),
                    }
                )
    except Exception as e:
        logger.warning(f"네이버 MYBOX 폴더 파일 수집 중 오류: {e}")

    return files


class NaverMyBoxResolver(BaseResolver):
    """네이버 MYBOX (mybox.naver.com) 공유 링크 리졸버."""

    name: str = "Naver MYBOX"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            from ..url_helper import extract_service_password

            clean_u, _ = extract_service_password(url)
            parsed = urlparse(clean_u)
            if not _is_mybox_host(parsed.hostname):
                return False
            qs = parse_qs(parsed.query)
            if "shareKey" in qs or "fileKey" in qs:
                return True
            path = parsed.path or ""
            return "/share/" in path
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        from ..url_helper import extract_service_password

        clean_u, extracted_pwd = extract_service_password(url, options)
        parsed = urlparse(clean_u)
        qs = parse_qs(parsed.query)

        share_key = None
        file_key = None

        if "shareKey" in qs and qs["shareKey"]:
            share_key = qs["shareKey"][0]
        if "fileKey" in qs and qs["fileKey"]:
            file_key = qs["fileKey"][0]

        target_key = share_key or file_key
        if not target_key and "/share/" in parsed.path:
            parts = parsed.path.split("/share/")
            if len(parts) > 1 and parts[1].strip("/"):
                sub = parts[1].strip("/").split("/")[0]
                if sub and sub not in ("list", "view"):
                    target_key = sub

        if not target_key:
            raise ValueError(
                f"네이버 MYBOX 공유 키(shareKey / fileKey)를 찾을 수 없습니다: {url}"
            )

        options = options or {}
        manual_pwd = (
            extracted_pwd
            or str(
                options.get("password")
                or options.get("pwd")
                or options.get("code")
                or ""
            ).strip()
        )

        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://mybox.naver.com/",
                "Accept": "application/json, text/plain, */*",
            }
        )

        logger.info(f"네이버 MYBOX 메타데이터 조회 중 (Key: {target_key[:16]}...)...")

        resource_key = None
        filename = None
        resource_type = None
        file_size: Optional[int] = None
        has_password = False

        # 1. v2 link/property 호출 시도
        try:
            prop_url = f"{PROPERTY_API_URL}?shareKey={quote(target_key)}"
            resp = session.get(prop_url, timeout=TIMEOUT)
            if resp.ok:
                prop_data = resp.json()
                if prop_data.get("code") == "0" and isinstance(
                    prop_data.get("result"), dict
                ):
                    res = prop_data["result"]
                    resource_key = res.get("resourceKey")
                    filename = res.get("resourceName")
                    resource_type = str(res.get("resourceType") or "").lower()
                    has_password = bool(res.get("hasPassword"))
                    if res.get("blockDownload"):
                        raise ValueError(
                            "네이버 MYBOX 소유자에 의해 다운로드가 차단된 파일 또는 폴더입니다."
                        )
                elif prop_data.get("code") not in ("0", None):
                    err_msg = (
                        prop_data.get("message") or f"에러 코드 {prop_data.get('code')}"
                    )
                    logger.warning(f"네이버 MYBOX link/property 응답 메시지: {err_msg}")
        except ValueError:
            raise
        except Exception as e:
            logger.warning(f"네이버 MYBOX link/property 조회 실패: {e}")

        # 공유 폴더(resourceType == 'folder') 처리
        if resource_type == "folder" and resource_key:
            files = _fetch_folder_files(session, target_key, resource_key)
            if not files:
                raise FileNotFoundError(
                    f"네이버 MYBOX 공유 폴더 '{filename or target_key}'에 다운로드 가능한 파일이 없습니다."
                )

            first_file = files[0]
            folder_name = sanitize_folder_name(filename or f"MYBOX_{target_key[:8]}")

            cookies_dict = dict(session.cookies.get_dict())
            download_headers = {
                "User-Agent": USER_AGENT,
                "Referer": "https://mybox.naver.com/",
            }

            logger.info(
                f"네이버 MYBOX 폴더 해석 완료: {folder_name} (총 {len(files)}개 파일)"
            )

            return {
                "url": first_file["url"],
                "filename": first_file["name"],
                "file_size": first_file.get("size"),
                "headers": download_headers,
                "cookies": cookies_dict,
                "referer": "https://mybox.naver.com/",
                "source_service": "Naver MYBOX",
                "files": files,
                "subfolder": folder_name,
                "article_title": folder_name,
            }

        # 2. link/file/property 호출 시도 (단일 파일 크기 및 대체 정보 획득)
        try:
            file_prop_url = f"{FILE_PROPERTY_API_URL}?fileKey={quote(target_key)}"
            f_resp = session.get(file_prop_url, timeout=TIMEOUT)
            if f_resp.ok:
                f_data = f_resp.json()
                if f_data.get("code") == "0" and isinstance(f_data.get("result"), dict):
                    f_res = f_data["result"]
                    if not resource_key:
                        resource_key = f_res.get("resourceKey")
                    if not filename:
                        filename = f_res.get("fileName")
                    if f_res.get("resourceSize") is not None:
                        try:
                            file_size = int(f_res["resourceSize"])
                        except (ValueError, TypeError):
                            pass
        except Exception as e:
            logger.debug(f"네이버 MYBOX file/property 조회 실패: {e}")

        # 비밀번호 보호 확인
        if has_password and not resource_key:
            if not manual_pwd:
                raise ValueError(
                    "네이버 MYBOX 비밀번호로 보호되어 있는 링크입니다. 링크 뒤에 '?pwd=비밀번호'를 붙여주세요."
                )
            raise ValueError(
                f"네이버 MYBOX 비밀번호가 일치하지 않거나 지원되지 않는 인증 형식입니다 (시도: '{manual_pwd}')."
            )

        if not resource_key:
            raise ValueError(
                "네이버 MYBOX 파일 리소스 키를 획득하지 못했습니다. 삭제되었거나 만료된 공유 링크일 수 있습니다."
            )

        # 파일명 정제
        if filename:
            filename = sanitize_filename(filename)
        else:
            filename = f"mybox_{target_key[:8]}.bin"

        # 직링크 URL 구성
        dl_url = (
            f"{DOWNLOAD_API_BASE}?svcType=MYBOX-WEB&resourceKey={quote(resource_key)}"
        )

        cookies_dict = dict(session.cookies.get_dict())
        download_headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://mybox.naver.com/",
        }

        logger.info(
            f"네이버 MYBOX 직링크 해석 완료: {filename} (크기: {file_size or '알 수 없음'})"
        )

        return {
            "url": dl_url,
            "filename": filename,
            "file_size": file_size,
            "headers": download_headers,
            "cookies": cookies_dict,
            "referer": "https://mybox.naver.com/",
            "source_service": "Naver MYBOX",
        }
