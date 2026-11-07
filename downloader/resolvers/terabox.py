"""TeraBox (1024terabox.com, terabox.app 등) 공유 링크 리졸버."""

import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlparse

import requests

from ..engine import format_bytes, sanitize_filename
from .base import BaseResolver

logger = logging.getLogger(__name__)

TERABOX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Only these service domains may receive the configured account cookies.
TERABOX_DOMAINS = (
    "terabox.com",
    "terabox.app",
    "1024tera.com",
    "1024terabox.com",
    "dubox.com",
    "mirrobox.com",
    "nephobox.com",
    "freeterabox.com",
    "4funbox.com",
    "gibibox.com",
    "pebibox.com",
)


def _is_terabox_host(hostname: str | None) -> bool:
    host = (hostname or "").lower().rstrip(".")
    return any(
        host == domain or host.endswith("." + domain) for domain in TERABOX_DOMAINS
    )


def _extract_pwd(url: str, options: dict | None = None) -> tuple[str, str]:
    """URL 및 옵션에서 테라박스 추출 비밀번호(pwd)와 순수 URL을 분리합니다."""
    from ..url_helper import extract_service_password

    return extract_service_password(url, options, max_short_len=8)


def _extract_surl(url: str) -> str:
    """테라박스 URL에서 surl 키를 추출합니다."""
    parsed = urlparse(url)
    # 1. 쿼리 파라미터 surl
    if parsed.query:
        qs = parse_qs(parsed.query)
        if "surl" in qs and qs["surl"]:
            return qs["surl"][0].strip()

    # 2. 경로 /s/1xxxxxxxx 또는 /s/xxxxxxxx
    path_match = re.search(r"/s/(?:1)?([a-zA-Z0-9_-]+)", parsed.path)
    if path_match:
        return path_match.group(1)

    return ""


def _get_root_domain(hostname: str) -> str:
    """호스트명에서 와일드카드 쿠키용 루트 도메인(.terabox.com 등)을 추출합니다."""
    parts = (hostname or "").split(":")[0].strip().split(".")
    if len(parts) >= 2:
        return "." + ".".join(parts[-2:])
    return "." + hostname if hostname else ""


def _apply_terabox_cookies(
    session: requests.Session, cookie_raw: str, host: str = ""
) -> dict[str, str]:
    """설정된 테라박스 쿠키(ndus 등)를 세션 및 도메인에 적용하고 딕셔너리로 반환합니다."""
    raw = (cookie_raw or "").strip().strip('"').strip("'")
    if not raw:
        return {}

    cookie_dict = {}
    if "=" not in raw:
        # 사용자가 토큰 값만 입력한 경우 (예: YbKdYCKteHuicEtlbb3GKtpcs14VvNnA-IRpu92C)
        if len(raw) < 15 or raw.lower() in (
            "admin",
            "root",
            "user",
            "guest",
            "test",
            "none",
            "null",
            "undefined",
        ):
            logger.warning(f"유효하지 않은 테라박스 쿠키 무시됨: '{raw}'")
            return {}
        cookie_dict["ndus"] = raw
    else:
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            if "=" in part:
                k, v = part.split("=", 1)
                k_clean = k.strip()
                v_clean = v.strip().strip('"')
                if k_clean.lower() == "ndus" and len(v_clean) < 15:
                    continue
                cookie_dict[k_clean] = v_clean
            elif len(part) >= 15 and "ndus" not in cookie_dict:
                cookie_dict["ndus"] = part

    # 중복 쿠키 전송 방지: 요청 대상의 루트 도메인(.terabox.com 등)에만 1회 등록
    if host and not _is_terabox_host(host):
        raise ValueError("Untrusted TeraBox cookie host")
    target_domains = {"." + domain for domain in TERABOX_DOMAINS}

    for k, v in cookie_dict.items():
        for d in target_domains:
            try:
                session.cookies.set(k, v, domain=d)
            except Exception:
                pass

    return cookie_dict


class TeraBoxResolver(BaseResolver):
    """TeraBox 및 미러 사이트 링크 리졸버."""

    name = "TeraBox"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        if not (
            parsed.scheme in ("http", "https") and _is_terabox_host(parsed.hostname)
        ):
            return False
        # 이미 테라박스 직다운로드 링크(d.1024tera.com/file/..., dstime=, sign= 등)인 경우 공유 페이지 리졸버 대상에서 제외 (직접 다운로드 수행)
        if (
            parsed.path.startswith("/file/")
            or "dstime=" in (parsed.query or "")
            or "sign=" in (parsed.query or "")
        ):
            return False
        return True

    def resolve(self, url: str, options: dict | None = None) -> dict:
        from ..url_helper import get_candidate_passwords

        clean_url, pwd = _extract_pwd(url, options)
        pwd_candidates = get_candidate_passwords(url, options)
        if pwd and pwd not in pwd_candidates:
            pwd_candidates.insert(0, pwd)

        if not _is_terabox_host(urlparse(clean_url).hostname):
            raise ValueError("Untrusted TeraBox URL")
        with requests.Session() as session:
            session.headers.update(
                {
                    "User-Agent": TERABOX_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
                }
            )

            # 설정에 등록된 테라박스 쿠키가 있다면 세션에 적용
            opts = options or {}
            terabox_cookie = str(opts.get("terabox_cookie") or "").strip()
            if not terabox_cookie:
                try:
                    import config

                    terabox_cookie = str(
                        config.load_settings().get("terabox_cookie") or ""
                    ).strip()
                except Exception:
                    pass

            parsed_url = urlparse(clean_url)
            cookie_dict = _apply_terabox_cookies(
                session, terabox_cookie, parsed_url.hostname or ""
            )
            if cookie_dict:
                logger.info(f"테라박스 쿠키 적용 완료: {list(cookie_dict.keys())}")

            logger.info(f"테라박스 페이지 접속 중: {clean_url}")
            resp = session.get(clean_url, allow_redirects=True, timeout=25, stream=True)
            if resp.status_code in (404, 410):
                raise FileNotFoundError(
                    "테라박스 파일이 삭제되었거나 존재하지 않는 링크입니다."
                )
            resp.raise_for_status()
            if not _is_terabox_host(urlparse(resp.url).hostname):
                resp.close()
                raise ValueError("Untrusted TeraBox redirect")

            # 만약 이미 파일 바이너리 스트림인 경우 직링크로 즉시 반환
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if not any(
                t in content_type
                for t in (
                    "text/html",
                    "application/xhtml",
                    "application/json",
                    "text/plain",
                )
            ):
                filename = ""
                cd = resp.headers.get("Content-Disposition", "")
                if "filename=" in cd:
                    m_fn = re.search(
                        r"filename\*?=['\"]?(?:UTF-8'')?([^;'\"]+)", cd, re.I
                    )
                    if m_fn:
                        filename = unquote(m_fn.group(1))
                total_sz = int(resp.headers.get("Content-Length", 0))
                return {
                    "url": resp.url,
                    "filename": filename or "terabox_download.bin",
                    "size": total_sz,
                    "cookies": "; ".join(f"{k}={v}" for k, v in cookie_dict.items()),
                    "source_service": "TeraBox",
                }

            final_url = resp.url
            parsed_final = urlparse(final_url)
            final_root = _get_root_domain(parsed_final.netloc)
            if final_root and cookie_dict:
                for k, v in cookie_dict.items():
                    try:
                        session.cookies.set(k, v, domain=final_root)
                    except Exception:
                        pass

            surl = _extract_surl(final_url) or _extract_surl(clean_url)
            if not surl:
                raise ValueError(
                    f"테라박스 링크에서 공유 식별자(surl)를 찾을 수 없습니다: {final_url}"
                )

            # jsToken 추출
            js_token_match = re.search(r"fn%28%22([A-F0-9]+)%22%29", resp.text)
            js_token = js_token_match.group(1) if js_token_match else ""
            if not js_token:
                alt_match = re.search(r'jsToken\s*=\s*["\']([^"\']+)["\']', resp.text)
                if alt_match:
                    js_token = alt_match.group(1)

            # 암호(비밀번호)가 필요한 페이지인지 확인
            is_password_required = (
                "/init" in parsed_final.path
                or "init?surl=" in final_url
                or "init?surl=" in clean_url
                or "암호를 입력하세요" in resp.text
                or "암호로 보호되어 있습니다" in resp.text
                or bool(
                    re.search(
                        r'<input[^>]+(?:id|name)=["\'](?:pwd|password)["\']',
                        resp.text,
                        re.I,
                    )
                )
                or bool(
                    re.search(
                        r'placeholder=["\'](?:추출\s*암호|암호|비밀번호|extraction\s*code|extract\s*code)["\']',
                        resp.text,
                        re.I,
                    )
                )
            )

            verified = False
            if is_password_required:
                if not pwd_candidates:
                    raise ValueError(
                        "이 테라박스 링크는 비밀번호(추출 암호)가 필요합니다. "
                        "웹 대시보드 ⚙️ 설정에서 공통 비밀번호를 등록하거나, 링크 뒤에 ?pwd=비밀번호 또는 | 비밀번호 형태로 함께 입력해 주세요."
                    )

                verify_url = f"https://{parsed_final.netloc}/share/verify?surl={surl}"
                tried = []
                last_err = None
                for idx, cand in enumerate(pwd_candidates, 1):
                    tried.append(cand)
                    logger.info(
                        f"테라박스 암호 확인 시도 ({idx}/{len(pwd_candidates)}): '{cand}' (surl: {surl})"
                    )
                    verify_resp = session.post(
                        verify_url,
                        data={"pwd": cand, "vcode": "", "vcode_str": ""},
                        headers={
                            "Referer": final_url,
                            "Accept": "application/json, text/plain, */*",
                        },
                        timeout=20,
                    )
                    verify_data = verify_resp.json() if verify_resp.ok else {}
                    if verify_data.get("errno") == 0:
                        pwd = cand
                        verified = True
                        logger.info(f"테라박스 암호 인증 성공! (적용된 암호: '{pwd}')")
                        break
                    else:
                        err_code = verify_data.get("errno")
                        last_err = err_code
                        logger.warning(
                            f"테라박스 암호 '{cand}' 인증 실패 (errno: {err_code})"
                        )
                        if err_code == 400136 and len(pwd_candidates) == 1:
                            break

                if not verified:
                    tried_str = ", ".join(repr(p) for p in tried)
                    if last_err == 400136:
                        raise ValueError(
                            f"테라박스 암호 확인 실패: 보안 인증(캡차)이 발생했거나 암호가 일치하지 않습니다 (입력값: {tried_str}). "
                            "공유자가 제공한 올바른 4자리 추출 암호를 확인해 주세요."
                        )
                    raise ValueError(
                        f"테라박스 암호가 일치하지 않습니다 (입력값: {tried_str}). "
                        "공유자가 제공한 올바른 추출 암호를 확인하고 다시 입력해 주세요."
                    )

                randsk = verify_data.get("randsk")
                if randsk:
                    for d in {
                        final_root,
                        ".terabox.com",
                        ".terabox.app",
                        ".1024tera.com",
                    }:
                        if d:
                            try:
                                session.cookies.set("BOXCLND", randsk, domain=d)
                                session.cookies.set("BDCLND", randsk, domain=d)
                            except Exception:
                                pass

                # 암호 인증 성공 후 세션을 갱신하여 최신 인증 쿠키 및 유효한 jsToken 재추출
                try:
                    refreshed_resp = session.get(
                        final_url, headers={"Referer": final_url}, timeout=20
                    )
                    if refreshed_resp.ok:
                        token_m = re.search(
                            r"fn%28%22([A-F0-9]+)%22%29", refreshed_resp.text
                        )
                        if token_m:
                            js_token = token_m.group(1)
                        else:
                            alt_m = re.search(
                                r'jsToken\s*=\s*["\']([^"\']+)["\']',
                                refreshed_resp.text,
                            )
                            if alt_m:
                                js_token = alt_m.group(1)
                except Exception as e:
                    logger.warning(f"테라박스 암호 인증 후 세션 갱신 실패 ({e})")

            # 공유 파일 목록 조회 (/share/list)
            api_url = f"https://{parsed_final.netloc}/share/list"
            params = {
                "app_id": "250528",
                "web": "1",
                "channel": "dubox",
                "clienttype": "0",
                "jsToken": js_token,
                "page": "1",
                "num": "100",
                "by": "name",
                "order": "asc",
                "site_referer": final_url,
                "shorturl": surl,
                "root": "1",
            }
            headers = {
                "Accept": "application/json, text/plain, */*",
                "Referer": final_url,
            }

            list_resp = session.get(api_url, params=params, headers=headers, timeout=25)
            list_data = list_resp.json() if list_resp.ok else {}

            if list_data.get("errno") != 0:
                err_code = list_data.get("errno")
                if not verified and pwd_candidates:
                    logger.info(
                        f"테라박스 추가 암호 인증 시도 중 (surl: {surl}, 후보군: {len(pwd_candidates)}개, 이전 errno: {err_code})"
                    )
                    verify_url = (
                        f"https://{parsed_final.netloc}/share/verify?surl={surl}"
                    )
                    tried_fallback = []
                    for cand in pwd_candidates:
                        tried_fallback.append(cand)
                        verify_resp = session.post(
                            verify_url,
                            data={"pwd": cand, "vcode": "", "vcode_str": ""},
                            headers={
                                "Referer": final_url,
                                "Accept": "application/json, text/plain, */*",
                            },
                            timeout=20,
                        )
                        verify_data = verify_resp.json() if verify_resp.ok else {}
                        if verify_data.get("errno") == 0:
                            pwd = cand
                            verified = True
                            randsk = verify_data.get("randsk")
                            if randsk:
                                for d in {
                                    final_root,
                                    ".terabox.com",
                                    ".terabox.app",
                                    ".1024tera.com",
                                }:
                                    if d:
                                        try:
                                            session.cookies.set("BOXCLND", randsk, domain=d)
                                            session.cookies.set("BDCLND", randsk, domain=d)
                                        except Exception:
                                            pass
                            list_resp = session.get(
                                api_url, params=params, headers=headers, timeout=25
                            )
                            list_data = list_resp.json() if list_resp.ok else {}
                            err_code = list_data.get("errno")
                            if err_code == 0:
                                logger.info(f"테라박스 추가 암호 인증 성공! (적용된 암호: '{pwd}')")
                                break
                    if not verified and tried_fallback and err_code != 0:
                        tried_str = ", ".join(repr(p) for p in tried_fallback)
                        raise ValueError(
                            f"테라박스 암호가 일치하지 않습니다 (시도한 비밀번호: {tried_str}). 올바른 추출 암호를 확인해 주세요."
                        )
                if err_code == -9:
                    raise ValueError(
                        "테라박스 공유 링크가 만료되었거나 암호 인증이 필요합니다. "
                        "링크 뒤에 ?pwd=비밀번호 형태로 암호를 함께 입력해 주세요."
                    )
                if err_code != 0:
                    logger.warning(f"테라박스 share/list 응답 오류 (errno: {err_code})")

            file_list = list_data.get("list", [])
            if not file_list:
                raise FileNotFoundError(
                    "테라박스 공유 목록에서 다운로드 가능한 파일을 찾을 수 없습니다."
                )

            first_file = file_list[0]

            # 동작 모드 확인: "download"(PC 직접 다운로드) 또는 "save"/"transfer"(내 테라박스에 저장)
            mode = (
                str(opts.get("terabox_mode") or opts.get("mode") or "").lower().strip()
            )
            if not mode:
                try:
                    import config

                    mode = (
                        str(config.load_settings().get("terabox_mode") or "download")
                        .lower()
                        .strip()
                    )
                except Exception:
                    mode = "download"

            # PC 다운로드 모드에서 첫 번째 항목이 폴더인 경우 하위 파일 목록 탐색
            if str(first_file.get("isdir", "0")) == "1" and mode not in (
                "save",
                "transfer",
                "cloud",
            ):
                try:
                    folder_params = dict(params)
                    folder_params["root"] = "0"
                    folder_params["dir"] = first_file.get("path") or ""
                    folder_resp = session.get(
                        api_url, params=folder_params, headers=headers, timeout=25
                    )
                    if folder_resp.ok:
                        f_data = folder_resp.json()
                        inner_files = [
                            f
                            for f in f_data.get("list", [])
                            if f.get("dlink")
                            or (
                                str(f.get("isdir", "0")) == "0"
                                and int(f.get("size", 0)) > 0
                            )
                        ]
                        if inner_files:
                            file_list = inner_files
                            first_file = file_list[0]
                except Exception as e:
                    logger.warning(f"테라박스 하위 폴더 탐색 실패: {e}")

            filename = sanitize_filename(
                first_file.get("server_filename") or "terabox_download"
            )
            size_bytes = int(first_file.get("size") or 0)
            dlink = first_file.get("dlink") or ""
            share_id = list_data.get("share_id")
            uk = list_data.get("uk")
            fs_ids = [item.get("fs_id") for item in file_list if item.get("fs_id")]

            if mode in ("save", "transfer", "cloud"):
                if not cookie_dict:
                    pwd_hint = f" (비밀번호 '{pwd}' 인증 완료)" if pwd else ""
                    raise ValueError(
                        f"테라박스 파일 정보를 정상 확인했습니다: '{filename}' ({format_bytes(size_bytes)}){pwd_hint}.\n"
                        f"내 테라박스에 저장하기 위해서는 유효한 테라박스 계정 로그인 쿠키(ndus)가 필요합니다.\n"
                        f"웹 대시보드 ⚙️ 설정에서 '테라박스 쿠키(ndus)'를 등록해 주세요."
                    )

                target_cloud_path = (opts.get("terabox_save_path") or "/").strip()
                if not target_cloud_path.startswith("/"):
                    target_cloud_path = "/" + target_cloud_path

                logger.info(
                    f"테라박스 내 클라우드 저장 시도: {filename} ({len(fs_ids)}개 항목) -> {target_cloud_path}"
                )
                transfer_url = f"https://{parsed_final.netloc}/share/transfer"
                transfer_params = {
                    "app_id": "250528",
                    "web": "1",
                    "channel": "dubox",
                    "clienttype": "0",
                    "jsToken": js_token,
                    "shareid": share_id,
                    "from": uk,
                }
                transfer_data = {
                    "fsidlist": json.dumps(fs_ids),
                    "path": target_cloud_path,
                }
                transfer_headers = {
                    "Accept": "application/json, text/plain, */*",
                    "Referer": final_url,
                }
                t_resp = session.post(
                    transfer_url,
                    params=transfer_params,
                    data=transfer_data,
                    headers=transfer_headers,
                    timeout=25,
                )
                t_data = t_resp.json() if t_resp.ok else {}

                # 만약 대상 폴더가 존재하지 않아 오류(errno 2 등) 발생 시 루트('/')로 재시도
                if t_data.get("errno") != 0 and target_cloud_path != "/":
                    logger.warning(
                        f"테라박스 폴더({target_cloud_path}) 저장 실패(errno={t_data.get('errno')}), 루트('/')로 재시도합니다."
                    )
                    transfer_data["path"] = "/"
                    target_cloud_path = "/"
                    t_resp = session.post(
                        transfer_url,
                        params=transfer_params,
                        data=transfer_data,
                        headers=transfer_headers,
                        timeout=25,
                    )
                    t_data = t_resp.json() if t_resp.ok else {}

                err_no = t_data.get("errno")
                if err_no in (0, 17):
                    is_duplicate = err_no == 17
                    logger.info(
                        f"테라박스 클라우드 저장 {'성공' if not is_duplicate else '이미 보관됨'}: {filename} -> {target_cloud_path}"
                    )
                    total_size = sum(int(item.get("size", 0)) for item in file_list)
                    file_count = len(file_list)
                    display_name = (
                        filename
                        if file_count <= 1
                        else f"{filename} 외 {file_count - 1}개 파일"
                    )
                    status_suffix = " (이미 저장되어 있음)" if is_duplicate else ""
                    return {
                        "is_cloud_transfer": True,
                        "url": clean_url,
                        "filename": display_name,
                        "cloud_path": f"내 테라박스: {target_cloud_path}{status_suffix}",
                        "size": total_size,
                        "source_service": "TeraBox (클라우드 저장)",
                    }
                else:
                    err_msg = t_data.get("show_msg") or f"오류 코드 {err_no}"
                    raise ValueError(
                        f"내 테라박스에 저장 실패 ({err_msg}). 테라박스 계정 쿠키 유효성 또는 남은 용량을 확인해 주세요."
                    )

            # 다중 파일 구성
            all_cookies = {**cookie_dict, **session.cookies.get_dict()}
            cookie_str = "; ".join(f"{k}={v}" for k, v in all_cookies.items())
            download_headers = {
                "User-Agent": TERABOX_USER_AGENT,
                "Referer": f"https://{parsed_final.netloc}/",
                "Cookie": cookie_str,
            }

            files = []
            for f in file_list:
                fname = sanitize_filename(f.get("server_filename") or "")
                f_dlink = f.get("dlink") or ""
                f_size = int(f.get("size") or 0)
                if f_dlink:
                    files.append(
                        {
                            "url": f_dlink,
                            "name": fname,
                            "size": f_size,
                            "referer": f"https://{parsed_final.netloc}/",
                            "headers": download_headers,
                            "cookies": cookie_str,
                        }
                    )

            # 직링크가 직접 반환된 경우
            if dlink:
                logger.info(
                    f"테라박스 직링크 해석 완료: {filename} ({format_bytes(size_bytes)})"
                )
                return {
                    "url": dlink,
                    "filename": filename,
                    "headers": download_headers,
                    "cookies": cookie_str,
                    "referer": f"https://{parsed_final.netloc}/",
                    "source_service": "TeraBox",
                    "files": files if len(files) > 1 else [],
                }

            # 만약 직링크가 반환되지 않은 경우 (테라박스 계정 ndus 쿠키 요구 시 또는 웹 직다운로드 차단 시)
            # 파일 정보(파일명, 크기)는 성공적으로 파싱되었으므로 안내 메시지 제공
            pwd_hint = f" (비밀번호 '{pwd}' 인증 완료)" if pwd else ""
            if cookie_dict:
                raise ValueError(
                    f"테라박스 파일 정보를 정상 확인했습니다: '{filename}' ({format_bytes(size_bytes)}){pwd_hint}.\n"
                    f"테라박스 서버에서 웹 직링크 생성이 제한되었습니다. "
                    f"작업 방식을 '☁️ 내 테라박스에 저장'으로 선택하시면 1초 만에 내 계정으로 즉시 보관되어 "
                    f"공식 앱에서 VIP 초고속으로 다운로드하실 수 있습니다."
                )
            raise ValueError(
                f"테라박스 파일 정보를 정상 확인했습니다: '{filename}' ({format_bytes(size_bytes)}){pwd_hint}.\n"
                f"테라박스 다운로드에는 유효한 계정 로그인 세션(ndus 쿠키)이 필요합니다.\n"
                f"웹 대시보드 ⚙️ 설정에서 '테라박스 쿠키(ndus)'를 등록하시면 바로 다운로드가 시작됩니다."
            )
