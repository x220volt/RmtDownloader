"""kio.ac (Kiosk) 파일 및 컬렉션 공유 링크 리졸버 모듈."""

import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# kio.ac 커스텀 Base64 64문자 색인
KIO_B64_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
KIO_B64_MAP = {c: i for i, c in enumerate(KIO_B64_CHARS)}

DEFAULT_EDGE_API = "https://alef.mittere.io"
API_COLLECTION_GET = "https://api.kio.ac/v0/collection/get"
API_DIRECTORY_GET = "https://api.kio.ac/v0/collection/directory/get"


def decode_kio_id(r: str) -> bytes:
    """kio.ac 22문자 ID를 16바이트 UUID 바이트 배열로 변환합니다."""
    if len(r) != 22:
        raise ValueError(f"유효하지 않은 kio ID 길이입니다: {len(r)} (22자리 필요)")
    e = bytearray(16)
    i = 0
    n = 0
    for t in range(21, -1, -1):
        ch = r[t]
        s = KIO_B64_MAP.get(ch)
        if s is None:
            raise ValueError(f"kio ID에 유효하지 않은 문자가 포함되어 있습니다: '{ch}'")
        for l in range(6):
            if n >= 16:
                break
            h = (s >> l) & 1
            e[n] |= h << i
            i += 1
            if i == 8:
                i = 0
                n += 1
    return bytes(e)


def encode_kio_id(u_bytes: bytes) -> str:
    """16바이트 UUID 바이트 배열을 kio.ac 22문자 ID로 변환합니다."""
    if len(u_bytes) != 16:
        raise ValueError(
            f"유효하지 않은 UUID 바이트 길이입니다: {len(u_bytes)} (16바이트 필요)"
        )
    r = [""] * 22
    e = 0
    i = 0
    for n in range(21, -1, -1):
        t = 0
        for s in range(6):
            if i < 16:
                l = (u_bytes[i] >> e) & 1
                t |= l << s
                e += 1
                if e == 8:
                    e = 0
                    i += 1
        r[n] = KIO_B64_CHARS[t]
    return "".join(r)


def _extract_kio_id(url: str) -> str:
    """URL에서 22자리 kio.ac 식별자를 추출합니다."""
    parsed = urlparse(url.strip())
    path = parsed.path.strip("/")
    # 예: c/aH9MT3gW09PLLZXLBjD6eb 또는 f/aH9MT3gW09PLLZXLBjD6eb
    parts = path.split("/")
    for part in parts:
        part_clean = part.split("?")[0].split("#")[0].strip()
        if len(part_clean) == 22 and all(c in KIO_B64_MAP for c in part_clean):
            return part_clean

    # 정규식 fallback
    m = re.search(r"/[cf]/([a-zA-Z0-9_-]{22})", url)
    if m:
        return m.group(1)
    return ""


def _extract_kio_pwd(url: str, options: dict | None = None) -> tuple[str, str]:
    """URL 및 옵션에서 kio 비밀번호와 정제된 URL을 추출합니다."""
    from ..url_helper import extract_service_password

    return extract_service_password(url, options, max_short_len=16)


class KioResolver(BaseResolver):
    """kio.ac (Kiosk) 파일 및 컬렉션 공유 서비스 링크 리졸버."""

    name = "kio"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower()

        # 이미 최종 엣지 직다운로드 링크인 경우 DirectResolver로 전달
        if "downloadFile" in parsed.path or "alef.mittere.io" in host:
            return False

        if host in ("kio.ac", "www.kio.ac", "kiosk.ac", "www.kiosk.ac"):
            return bool(_extract_kio_id(url))
        return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        import cbor2

        clean_url, pwd = _extract_kio_pwd(url, options)
        raw_id = _extract_kio_id(clean_url)
        if not raw_id:
            raise ValueError(
                f"kio.ac 링크에서 유효한 식별자(22자리 ID)를 찾을 수 없습니다: {url}"
            )

        uuid_bytes = decode_kio_id(raw_id)
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Origin": "https://kio.ac",
                    "Referer": clean_url,
                }
            )

            # 1. 메인 페이지에서 edge API URL 추출
            edge_api = DEFAULT_EDGE_API
            try:
                page_resp = session.get(clean_url, timeout=15)
                if page_resp.status_code in (404, 410):
                    raise FileNotFoundError(
                        "kio.ac 파일 또는 컬렉션이 삭제되었거나 존재하지 않는 링크입니다."
                    )
                m_edge = re.search(r'"PUBLIC_EDGE_API"\s*:\s*"([^"]+)"', page_resp.text)
                if m_edge:
                    edge_api = m_edge.group(1).rstrip("/")
            except Exception as e:
                logger.warning(
                    f"kio.ac 메인 페이지 접속/Edge API 감지 경고 ({e}), 기본 Edge API 사용: {edge_api}"
                )

            # 2. 컬렉션 메타데이터 조회 (/v0/collection/get) via CBOR
            cbor_headers = {
                "Content-Type": "application/cbor",
                "Accept": "application/cbor",
            }

            coll_payload: dict[str, Any] = {"uuid": uuid_bytes}
            if pwd:
                coll_payload["protector"] = [
                    {
                        "type": "password",
                        "data": {"password": pwd},
                    }
                ]

            logger.info(
                f"kio.ac 컬렉션 조회 중: id={raw_id}, pwd={'***' if pwd else 'None'}"
            )
            coll_resp = session.post(
                API_COLLECTION_GET,
                data=cbor2.dumps(coll_payload),
                headers=cbor_headers,
                timeout=20,
            )

            resp_data = None
            if coll_resp.content:
                try:
                    resp_data = cbor2.loads(coll_resp.content)
                except Exception as e:
                    logger.warning(f"kio.ac CBOR 응답 역직렬화 실패: {e}")

            if (
                coll_resp.status_code != 200
                or not isinstance(resp_data, dict)
                or "token" not in resp_data
            ):
                err_code = (resp_data or {}).get(
                    "code"
                ) or f"HTTP {coll_resp.status_code}"
                err_msg = (resp_data or {}).get("message") or ""

                if err_code == "collection:protector_not_provided":
                    raise ValueError(
                        "이 kio.ac 링크는 비밀번호(추출 암호)가 필요합니다. "
                        "링크 뒤에 ?pwd=비밀번호 또는 | 비밀번호 형태로 함께 입력해 주세요."
                    )
                elif err_code == "collection:invalid_protector_config":
                    raise ValueError(
                        f"kio.ac 암호가 일치하지 않습니다 (입력값: '{pwd}'). "
                        "올바른 비밀번호를 확인하고 다시 입력해 주세요."
                    )
                elif "not_found" in err_code or coll_resp.status_code == 404:
                    raise FileNotFoundError(
                        "kio.ac 컬렉션이 삭제되었거나 존재하지 않는 링크입니다."
                    )
                elif "deactivated" in err_code:
                    raise ValueError("kio.ac 컬렉션이 비활성화되었거나 만료되었습니다.")

                raise ValueError(
                    f"kio.ac 컬렉션 정보 조회 실패 ({err_code}: {err_msg})"
                )

            coll_name = sanitize_filename(resp_data.get("name") or "kio_collection")
            token = resp_data.get("token")
            root_id = resp_data.get("root")
            if not root_id or not token:
                raise ValueError(
                    "kio.ac 컬렉션에서 루트 디렉터리 식별자 또는 액세스 토큰을 찾지 못했습니다."
                )

            logger.info(
                f"kio.ac 컬렉션 인증 성공: '{coll_name}', 디렉터리 목록 탐색 중..."
            )

            # 3. 루트 디렉터리 파일 목록 조회 (/v0/collection/directory/get)
            dir_headers = dict(cbor_headers)
            dir_headers["Kiosk-CAT"] = token

            all_files = []

            def _traverse_dir(dir_uuid: bytes, dir_path: str = ""):
                d_resp = session.post(
                    API_DIRECTORY_GET,
                    data=cbor2.dumps({"id": dir_uuid}),
                    headers=dir_headers,
                    timeout=25,
                )
                if not d_resp.ok or not d_resp.content:
                    return
                try:
                    d_data = cbor2.loads(d_resp.content)
                except Exception:
                    return

                if not isinstance(d_data, dict):
                    return

                for f in d_data.get("files") or []:
                    f_name = sanitize_filename(f.get("name") or "")
                    f_size = int(f.get("size") or 0)
                    f_raw_id = f.get("id")
                    if not f_raw_id:
                        continue
                    f_id_str = encode_kio_id(f_raw_id)
                    f_dl_url = (
                        f"{edge_api}/edge/v4/downloadFile?id={f_id_str}&cat={token}"
                    )
                    full_display_name = f"{dir_path}/{f_name}" if dir_path else f_name
                    all_files.append(
                        {
                            "url": f_dl_url,
                            "name": full_display_name,
                            "size": f_size,
                            "referer": "https://kio.ac/",
                            "headers": {
                                "User-Agent": USER_AGENT,
                                "Origin": "https://kio.ac",
                                "Referer": "https://kio.ac/",
                            },
                        }
                    )

                for child in d_data.get("children") or []:
                    c_raw_id = child.get("id")
                    c_name = sanitize_filename(child.get("name") or "")
                    if c_raw_id:
                        sub_path = f"{dir_path}/{c_name}" if dir_path else c_name
                        _traverse_dir(c_raw_id, sub_path)

            _traverse_dir(root_id)

            if not all_files:
                raise FileNotFoundError(
                    f"kio.ac 컬렉션('{coll_name}')에서 다운로드 가능한 파일을 찾을 수 없습니다."
                )

            first = all_files[0]
            final_filename = first["name"] or coll_name
            logger.info(
                f"kio.ac 링크 분석 완료: {final_filename} ({first['size']} B, 총 {len(all_files)}개 파일)"
            )

            download_headers = {
                "User-Agent": USER_AGENT,
                "Origin": "https://kio.ac",
                "Referer": "https://kio.ac/",
            }

            return {
                "url": first["url"],
                "filename": final_filename,
                "size": first["size"],
                "headers": download_headers,
                "referer": "https://kio.ac/",
                "source_service": "kio.ac",
                "files": all_files if len(all_files) > 1 else [],
            }
