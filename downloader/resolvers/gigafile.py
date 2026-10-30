"""기가파일 (GigaFile / ギガファイル便) 링크 리졸버."""

import logging
import re
import secrets
from urllib.parse import parse_qs, quote, urlparse

import requests

from ..engine import sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class GigaFileResolver(BaseResolver):
    """GigaFile(gigafile.nu) 대용량 파일 다운로드 링크 리졸버."""

    name = "GigaFile"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        try:
            parsed = urlparse(url)
            netloc = (parsed.hostname or "").lower()
            return (
                netloc in ("gigafile.nu", "xgf.nu")
                or netloc.endswith(".gigafile.nu")
                or netloc.endswith(".xgf.nu")
            )
        except Exception:
            return False

    def resolve(self, url: str, options: dict | None = None) -> dict:
        opts = dict(options or {})
        parsed_url = urlparse(url)

        # 1. 다운로드 키(비밀번호) 파라미터 추출
        # ?dlkey=..., ?pwd=..., ?key=... 또는 options['password']
        qs = parse_qs(parsed_url.query)
        dlkey = ""
        for k in ("dlkey", "pwd", "key", "password"):
            if k in qs and qs[k]:
                dlkey = qs[k][0].strip()
                break
        if not dlkey:
            dlkey = (
                opts.get("password") or opts.get("dlkey") or opts.get("pwd") or ""
            ).strip()

        # 2. 기가파일 다운로드 안내 페이지 접속 및 세션 쿠키 수집
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,ja-JP,ja;q=0.8,en-US;q=0.7,en;q=0.6",
                }
            )

            try:
                resp = session.get(url, timeout=20, allow_redirects=True)
                resp.raise_for_status()
            except Exception as e:
                raise ValueError(f"기가파일 페이지 접속 실패: {e}")

            final_url = resp.url
            parsed_final = urlparse(final_url)
            page_html = resp.text

            # 삭제된 파일 또는 만료된 파일 확인
            if (
                "ファイルは削除されました" in page_html
                or "ファイルが存在しません" in page_html
                or "ダウンロード期限が切" in page_html
            ):
                raise FileNotFoundError(
                    "기가파일(GigaFile) 링크의 파일이 삭제되었거나 다운로드 보관 기간이 만료되었습니다."
                )

            # 3. 파일 ID 및 서버 정보 추출
            # var file = "0203-s6576c8859c8e68bd1a83c47f382e03e1";
            file_id = ""
            file_match = re.search(r'var\s+file\s*=\s*["\']([^"\']+)["\']', page_html)
            if file_match:
                file_id = file_match.group(1).strip()
            else:
                # URL 경로에서 0203-... 추출
                path_match = re.search(r"([0-9]{4}-[a-zA-Z0-9]+)", parsed_final.path)
                if path_match:
                    file_id = path_match.group(1).strip()
                else:
                    onclick_match = re.search(
                        r"download\(\s*['\"]([^'\"]+)['\"]", page_html
                    )
                    if onclick_match:
                        file_id = onclick_match.group(1).strip()

            if not file_id:
                raise ValueError(
                    "기가파일 링크에서 파일 고유 ID를 찾을 수 없습니다. 올바른 GigaFile 링크인지 확인해 주세요."
                )

            server = ""
            server_match = re.search(
                r'var\s+server\s*=\s*["\']([^"\']+)["\']', page_html
            )
            if server_match:
                server = server_match.group(1).strip()
            else:
                server = parsed_final.netloc

            dl_ajax_server = ""
            ajax_match = re.search(
                r'var\s+dl_ajax_server\s*=\s*["\']([^"\']+)["\']', page_html
            )
            if ajax_match:
                dl_ajax_server = ajax_match.group(1).strip()
            else:
                if "." in server:
                    sub, rest = server.split(".", 1)
                    dl_ajax_server = f"{sub}x.{rest}"
                else:
                    dl_ajax_server = server

            # 파일 크기 (바이트) 추출
            size_bytes = 0
            size_match = re.search(r"var\s+size\s*=\s*([0-9]+);", page_html)
            if size_match:
                try:
                    size_bytes = int(size_match.group(1))
                except Exception:
                    size_bytes = 0

            # 4. 다운로드 키(비밀번호) 검증
            # onclick="download('...', true, ...)" 이거나 disabled가 없는 dlkey 인풋이 있는 경우
            is_dlkey_required = False
            if re.search(r"download\(\s*['\"][^'\"]+['\"],\s*true", page_html):
                is_dlkey_required = True
            else:
                input_match = re.search(
                    r'<input[^>]+id=[\'"]dlkey[\'"][^>]*>', page_html
                )
                if input_match:
                    input_tag = input_match.group(0)
                    if "disabled" not in input_tag.lower():
                        is_dlkey_required = True

            if is_dlkey_required:
                if not dlkey:
                    raise ValueError(
                        "이 기가파일(GigaFile) 링크는 다운로드 키(비밀번호 4자리)가 필요합니다.\n"
                        "링크 뒤에 ?dlkey=비밀번호 형태로 입력하거나 설정/암호 입력칸에 입력해 주세요."
                    )

                # 다운로드 키 유효성 사전 검사
                check_url = f"https://{server}/check_dlkey.php"
                try:
                    c_resp = session.get(
                        check_url,
                        params={"file": file_id, "dlkey": dlkey, "is_zip": 0},
                        headers={"Referer": final_url},
                        timeout=15,
                    )
                    if c_resp.ok:
                        c_data = c_resp.json()
                        status = c_data.get("status")
                        if status == 1:
                            raise ValueError(
                                f"기가파일 다운로드 키(비밀번호: '{dlkey}')가 일치하지 않습니다. 올바른 비밀번호를 확인해 주세요."
                            )
                        elif status == 3:
                            raise ValueError(
                                "기가파일 비밀번호 입력 오류 횟수 초과로 일시적으로 접근이 차단되었습니다."
                            )
                        elif status != 0:
                            logger.warning(
                                f"check_dlkey 응답 코드({status}), 계속 다운로드를 시도합니다."
                            )
                except ValueError:
                    raise
                except Exception as e:
                    logger.warning(
                        f"check_dlkey 확인 통신 실패 ({e}), 계속 진행합니다."
                    )

            # 5. 원본 파일명 추출 (AJAX API 호출)
            filename = ""
            try:
                name_api = f"https://{dl_ajax_server}/get_uploaded_file_name_jx.php"
                name_resp = session.get(
                    name_api,
                    params={"file": file_id},
                    headers={"Referer": final_url},
                    timeout=10,
                )
                if name_resp.ok:
                    name_data = name_resp.json()
                    if name_data.get("status") == 0 and name_data.get("filename"):
                        filename = sanitize_filename(name_data["filename"])
                        logger.info(f"GigaFile 파일명 조회 성공: {filename}")
            except Exception as e:
                logger.warning(f"GigaFile 파일명 API 조회 실패: {e}")

            if not filename:
                filename = sanitize_filename(
                    opts.get("filename") or f"gigafile_{file_id}"
                )

            # 6. 세션 쿠키에 prog_key 등록 (Gigafile 다운로드 토큰)
            prog_key = secrets.token_hex(4)
            cookie_dict = session.cookies.get_dict()
            cookie_dict["prog_key"] = prog_key

            # 쿠키 헤더 문자열 포맷팅
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

            # 7. 최종 다운로드 직링크 생성
            download_url = f"https://{server}/download.php?file={file_id}"
            if dlkey:
                download_url += f"&dlkey={quote(dlkey)}"

            return {
                "url": download_url,
                "filename": filename,
                "headers": {
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Referer": final_url,
                    "Accept": "*/*",
                },
                "cookies": cookie_str,
                "size": size_bytes,
                "source_service": "GigaFile",
            }
