"""URL 및 Base64 인코딩 링크 처리 유틸리티 모듈."""

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

URL_REGEX = re.compile(r"https?://[^\s<>\"']+|magnet:\?[^\s<>\"']+")

SHORTENER_DOMAINS = {
    "xgf.nu",
    "bit.ly",
    "tinyurl.com",
    "is.gd",
    "v.gd",
    "t.ly",
    "url.kr",
    "me2.do",
    "vo.la",
    "shorturl.at",
    "cutt.ly",
    "rb.gy",
    "clck.ru",
    "short.gy",
    "rebrand.ly",
    "linkvertise.com",
    "ow.ly",
    "buff.ly",
    "soo.gd",
}


def is_shortened_url(url: str) -> bool:
    """주어진 URL이 잘 알려진 단축 URL 서비스의 링크인지 확인합니다."""
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if any(host == d or host.endswith("." + d) for d in SHORTENER_DOMAINS):
            return True
        path = parsed.path.strip("/")
        if (
            len(host.split(".")) == 2
            and len(host) <= 10
            and 2 <= len(path) <= 12
            and "/" not in path
        ):
            return True
        return False
    except Exception:
        return False


def unshorten_url(url: str, timeout: int = 10) -> str:
    """단축 URL 또는 리다이렉트 링크의 최종 목적지 URL을 안전하게 조회합니다."""
    if not url or not isinstance(url, str):
        return url
    if not is_shortened_url(url):
        return url
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        with requests.get(
            url, headers=headers, allow_redirects=True, stream=True, timeout=timeout
        ) as resp:
            final_u = resp.url
            if final_u and final_u != url:
                logger.info(f"단축 URL 전개 성공: {url} -> {final_u}")
                return final_u
    except Exception as e:
        logger.debug(f"단축 URL 전개 실패 ({url}): {e}")
    return url


def detect_url_service_type(url: str) -> tuple[str, str]:
    """
    URL의 도메인, 경로 및 리다이렉트 정보를 분석하여 어떤 다운로드 서비스/형식인지 판별합니다.
    단축 URL인 경우 전개하여 대상 서비스를 분석합니다.
    반환값: (서비스명 또는 형식, 최종 분석된 URL)
    예: ('GigaFile', 'https://23.gigafile.nu/...'), ('TeraBox', ...), ('Google Drive', ...), ('Direct File', ...)
    """
    if not url:
        return ("Unknown", "")

    current_u = url.strip()
    if current_u.lower().startswith("magnet:"):
        return ("Magnet", current_u)

    if is_shortened_url(current_u):
        expanded = unshorten_url(current_u)
        if expanded and expanded != current_u:
            current_u = expanded

    from .resolvers.dispatcher import default_dispatcher

    for r in default_dispatcher.resolvers:
        if r.name not in (
            "WebPage Post",
            "Direct Link",
            "Tistory",
            "NaverCafe",
        ) and r.can_handle(current_u):
            return (r.name, current_u)

    from .resolvers.webpage import _has_downloadable_extension

    if _has_downloadable_extension(current_u):
        return ("Direct File", current_u)

    return ("WebPage", current_u)


def decode_base64_string(raw: str) -> str:
    """
    단일 문자열이 Base64 인코딩된 링크인 경우 디코딩하여 반환합니다.
    - 패딩 생략(= 부재), URL-safe Base64(-, _) 지원
    - common prefix (base64:, b64:, data:...;base64,) 자동 제거
    - 2중/3중 중첩 Base64 인코딩 재귀 디코딩 지원
    - UTF-8, CP949, EUC-KR 인코딩 지원
    """
    current = (raw or "").strip()
    if not current:
        return ""

    # 접두사 및 둘러싼 따옴표/괄호 제거
    cleaned = re.sub(
        r"^(?:base64:|b64:|data:[^;]+;base64,)", "", current, flags=re.I
    ).strip()
    cleaned = cleaned.strip("\"'<>[]()")

    # 이미 명백한 URL/magnet 형태인 경우
    if re.match(r"^(https?://|magnet:\?)", cleaned, re.I):
        unwrapped = unwrap_url_parameter_base64(cleaned)
        if unwrapped and unwrapped != cleaned:
            return unwrapped
        return cleaned

    # 문자열 내부에 Base64 토큰이 포함되어 있는 경우 (예: "다운로드: aHR0cHM6Ly94Z2YubnUvM2NNOG8= 비번:1234")
    # 토큰 부분만 안전하게 디코딩하여 원본 문맥을 유지한 채 치환
    replaced_cleaned = cleaned
    has_replacement = False
    for m in re.finditer(
        r"(?<![a-zA-Z0-9/])(?:(?:aHR0c|bWFnbmV0)[A-Za-z0-9+/_-]{4,}={0,2}|[A-Za-z0-9+/_-]{12,}={1,2})(?![a-zA-Z0-9/])",
        cleaned,
    ):
        tok = m.group(0)
        pad = (4 - (len(tok) % 4)) % 4
        padded = tok + ("=" * pad)
        for fn in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                dec_bytes = fn(padded)
                for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
                    try:
                        dec_text = dec_bytes.decode(enc).strip()
                        if any(
                            sch in dec_text
                            for sch in (
                                "http://",
                                "https://",
                                "magnet:",
                                "xgf.nu",
                                "bit.ly",
                            )
                        ):
                            logger.info(
                                f"Base64 링크 디코딩 성공: {tok[:30]}... -> {dec_text[:60]}..."
                            )
                            replaced_cleaned = replaced_cleaned.replace(tok, dec_text)
                            has_replacement = True
                        break
                    except Exception:
                        pass
            except Exception:
                pass
    if has_replacement:
        return replaced_cleaned

    for _ in range(3):
        # Base64 문자열 내 줄바꿈/공백 제거
        cand = re.sub(r"\s+", "", cleaned)
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", cand) or len(cand) < 8:
            break

        # 패딩 보정
        pad = (4 - (len(cand) % 4)) % 4
        padded = cand + ("=" * pad)

        decoded_str = None
        for fn in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw_bytes = fn(padded)
                for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
                    try:
                        decoded_str = raw_bytes.decode(enc)
                        break
                    except Exception:
                        pass
                if decoded_str:
                    break
            except Exception:
                continue

        if not decoded_str:
            break

        decoded_clean = decoded_str.strip()
        # 디코딩 결과에 URL이나 magnet 스킴이 포함되어 있는 경우
        if any(
            scheme in decoded_clean for scheme in ("http://", "https://", "magnet:")
        ):
            logger.info(
                f"Base64 링크 디코딩 성공: {cand[:30]}... -> {decoded_clean[:60]}..."
            )
            return decoded_clean
        # 디코딩 결과가 또 다른 Base64 문자열인 경우 루프 진행 (다중 인코딩 해제)
        elif (
            re.fullmatch(r"[A-Za-z0-9+/=_\s-]+", decoded_clean)
            and len(decoded_clean) >= 8
        ):
            cleaned = decoded_clean
            continue
        else:
            break

    return current


def unwrap_url_parameter_base64(url: str) -> str:
    """URL의 쿼리 스트링 또는 프래그먼트에 Base64로 감싸진 다운로드 링크가 있는 경우 추출합니다."""
    try:
        parsed = urlparse(url)
        # 1. 쿼리 파라미터 검사 (?url=..., ?link=..., ?target=..., ?b64=...)
        if parsed.query:
            qs = parse_qs(parsed.query)
            for key in (
                "url",
                "link",
                "target",
                "download",
                "file",
                "dest",
                "b64",
                "data",
                "to",
            ):
                if key in qs:
                    for val in qs[key]:
                        decoded = decode_base64_string(val)
                        if decoded and re.match(
                            r"^(https?://|magnet:\?)", decoded, re.I
                        ):
                            logger.info(
                                f"URL 쿼리 파라미터({key})에서 Base64 링크 추출: {decoded}"
                            )
                            return decoded

        # 2. 프래그먼트 검사 (#aHR0cHM6...)
        if parsed.fragment:
            decoded = decode_base64_string(parsed.fragment)
            if decoded and re.match(r"^(https?://|magnet:\?)", decoded, re.I):
                logger.info(f"URL 프래그먼트에서 Base64 링크 추출: {decoded}")
                return decoded
    except Exception:
        pass
    return url


PASSWORD_REGEX = re.compile(
    r"(?:(?:추출\s*코드|추출코드|비밀\s*번호|비밀번호|비번|암호|패스워드|pwd|pw|password|dlkey|提取码|密码|パスワード|合言葉|합言葉|아이코토바)[\s:=：]+|(?:코드|code|key|pass)\s*[:=：]\s*)"
    r"([a-zA-Z0-9_-]{3,16})",
    re.IGNORECASE,
)


def extract_urls_and_passwords_from_text(text: str) -> list[tuple[str, str]]:
    """
    텍스트 내에 포함된 모든 URL과 각 URL에 대응되는 비밀번호/추출코드를 순서대로 추출합니다.
    URL이 여러 개 있을 때 각 URL 주변(직후 또는 직전)의 암호를 독립적으로 매핑합니다.
    """
    if not text:
        return []

    matches = list(URL_REGEX.finditer(text))
    if not matches:
        return []

    results = []
    for i, m in enumerate(matches):
        raw_u = m.group(0).rstrip(".,;!?'\"<>)]}")

        # 해당 URL 주변 텍스트 범위 (이전 URL 끝 ~ 다음 URL 시작)
        prev_end = matches[i - 1].end() if i > 0 else 0
        next_start = matches[i + 1].start() if i + 1 < len(matches) else len(text)

        pwd = ""
        # 1. URL 직후부터 다음 URL 전까지의 텍스트 탐색 (예: "https://... 추출코드:1234")
        after_text = text[m.end() : next_start]
        pwd_match = PASSWORD_REGEX.search(after_text)
        if pwd_match:
            pwd = pwd_match.group(1).strip()
        else:
            # 2. URL 직전 텍스트 탐색 (예: "비번:1234 https://...")
            before_text = text[prev_end : m.start()]
            pwd_match_before = PASSWORD_REGEX.search(before_text)
            if pwd_match_before:
                pwd = pwd_match_before.group(1).strip()

        # 암호 뒤에 https나 http가 섞여 들어가지 않도록 보호
        if pwd and re.search(r"https?://", pwd, re.I):
            pwd = re.sub(r"https?://.*$", "", pwd).strip()

        # 파이프 구분 암호 fallback (예: "https://... | 1234")
        if not pwd and " | " in after_text:
            parts = after_text.split(" | ")
            for p in parts[1:]:
                p_strip = p.strip().split()[0] if p.strip() else ""
                if (
                    3 <= len(p_strip) <= 12
                    and not ("." in p_strip)
                    and not ("/" in p_strip)
                ):
                    pwd = p_strip
                    break

        results.append((raw_u, pwd))

    return results


def extract_clean_url_and_password(text: str) -> tuple[str, str]:
    """
    URL과 설명 텍스트, 추출 코드 등이 혼합된 문자열에서 순수 URL과 비밀번호를 분리 추출합니다.
    예: 'https://1024terabox.com/s/1MH2QsQ4CzHumsqiYaI_eag 추출 코드:dao5'
       -> ('https://1024terabox.com/s/1MH2QsQ4CzHumsqiYaI_eag', 'dao5')
    """
    extracted = extract_urls_and_passwords_from_text(text)
    if extracted:
        return extracted[0]
    return "", ""


def extract_service_password(
    url: str,
    options: dict | None = None,
    max_short_len: int = 16,
) -> tuple[str, str]:
    """URL(주변 텍스트, 파이프, 쿼리, 해시 프래그먼트) 및 옵션/설정에서 비밀번호와 순수 URL을 추출합니다."""
    pure_u, found_pwd = extract_clean_url_and_password(url)
    clean_u = pure_u if pure_u else url.strip()
    pwd = found_pwd

    if not pwd:
        m = PASSWORD_REGEX.search(url)
        if m:
            pwd = m.group(1).strip()

    if " | " in clean_u:
        u_part, name_or_pwd = clean_u.split(" | ", 1)
        clean_u = u_part.strip()
        name_or_pwd = name_or_pwd.strip()
        if name_or_pwd.lower().startswith("pwd:"):
            pwd = name_or_pwd[4:].strip()
        elif name_or_pwd.lower().startswith("pw:"):
            pwd = name_or_pwd[3:].strip()
        elif not pwd and len(name_or_pwd) <= max_short_len and not ("." in name_or_pwd):
            pwd = name_or_pwd

    parsed = urlparse(clean_u)
    if parsed.query:
        qs = parse_qs(parsed.query)
        for k in ("pwd", "password", "p", "pass", "code", "key"):
            if k in qs and qs[k]:
                pwd = qs[k][0].strip()
                break

    if parsed.fragment and not pwd:
        frag = parsed.fragment.strip()
        if "=" in frag:
            f_qs = parse_qs(frag)
            for k in ("pwd", "password", "p", "code"):
                if k in f_qs and f_qs[k]:
                    pwd = f_qs[k][0].strip()
                    break
        elif len(frag) <= max_short_len and not ("/" in frag):
            pwd = frag

    if not pwd and options:
        pwd = str(
            options.get("password")
            or options.get("pwd")
            or options.get("code")
            or options.get("terabox_password")
            or ""
        ).strip()

    if not pwd and (
        options is None
        or ("password" not in options and "terabox_password" not in options)
    ):
        try:
            import config

            pwd = str(config.load_settings().get("terabox_password") or "").strip()
        except Exception:
            pass

    return clean_u, pwd


def with_password(url: str, password: str) -> str:
    parsed = urlparse(url)
    if any(
        key in parse_qs(parsed.query)
        for key in ("pwd", "password", "dlkey", "code", "key")
    ):
        return url
    host = (parsed.hostname or "").lower()
    key = (
        "dlkey"
        if (
            host in ("gigafile.nu", "xgf.nu")
            or host.endswith(".gigafile.nu")
            or host.endswith(".xgf.nu")
        )
        else "pwd"
    )
    query = parsed.query + ("&" if parsed.query else "") + urlencode({key: password})
    return urlunparse(parsed._replace(query=query))


def clean_url(url: Any) -> str:
    """단일 URL 문자열을 검사하여 Base64 해제 및 주변 텍스트/비밀번호 분리를 수행합니다."""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if not url:
        return ""
    decoded = decode_base64_string(url)
    unwrapped = unwrap_url_parameter_base64(decoded).strip()

    # URL과 주변 텍스트(설명, 추출 코드 등)가 섞여 있는 경우 정제
    clean_u, extracted_pwd = extract_clean_url_and_password(unwrapped)
    if not clean_u:
        # unwrapped 내에 Base64 토큰이 남아 있는 경우 (예: "aHR0cHM6Ly94Z2YubnUvM2NNOG8= 비번: 1234")
        for tok_m in re.finditer(r"[A-Za-z0-9+/_-]{12,}={0,2}", unwrapped):
            tok = tok_m.group(0)
            d_tok = decode_base64_string(tok)
            if (
                d_tok
                and d_tok != tok
                and ("http://" in d_tok or "https://" in d_tok or "magnet:" in d_tok)
            ):
                replaced = unwrapped.replace(tok, d_tok)
                clean_u, extracted_pwd = extract_clean_url_and_password(replaced)
                if clean_u:
                    break
    if clean_u:
        if not extracted_pwd and isinstance(url, str):
            orig_pwd_m = PASSWORD_REGEX.search(url)
            if orig_pwd_m:
                extracted_pwd = orig_pwd_m.group(1).strip()
        if extracted_pwd:
            parsed = urlparse(clean_u)
            qs = parse_qs(parsed.query)
            # URL에 아직 암호 파라미터가 없다면 안전하게 쿼리스트링으로 병합
            if not any(k in qs for k in ("pwd", "password", "dlkey", "code", "key")):
                clean_u = with_password(clean_u, extracted_pwd)
        return clean_u

    # .torrent 로컬 파일 경로 또는 file:// 링크 지원
    if unwrapped.lower().endswith(".torrent"):
        cand_path = (
            re.sub(r"^file://[a-zA-Z]:?", "", unwrapped)
            if unwrapped.startswith("file://")
            else unwrapped
        )
        cand_path = (
            cand_path.lstrip("/\\") if unwrapped.startswith("file://") else cand_path
        )
        if os.path.exists(unwrapped):
            return str(Path(unwrapped).resolve())
        elif os.path.exists(cand_path):
            return str(Path(cand_path).resolve())
        return unwrapped

    return ""


def parse_download_candidates(
    raw_text: str = "",
    raw_urls: list | None = None,
    raw_items: list | None = None,
    single_filename: str = "",
    single_referer: str = "",
    single_password: str = "",
    single_terabox_mode: str = "",
    single_source_title: str = "",
    single_source_url: str = "",
    single_is_auto: bool | None = None,
) -> list:
    """
    다양한 형태(일반 URL, Base64 문자열, 텍스트 라인, 다중 객체 등)로부터
    유효한 다운로드 후보 항목 목록을 정제하여 추출합니다.
    """
    if (
        not isinstance(raw_text, str)
        or not isinstance(raw_urls or [], list)
        or not isinstance(raw_items or [], list)
    ):
        raise ValueError("Invalid download input types")
    candidates = []

    def _make_opts(base_opts: dict | None = None) -> dict:
        if base_opts is not None and not isinstance(base_opts, dict):
            raise ValueError("options must be a JSON object")
        opts = dict(base_opts or {})
        if single_referer and "referer" not in opts:
            opts["referer"] = single_referer
        if single_password and "password" not in opts:
            opts["password"] = single_password
        if single_terabox_mode and "terabox_mode" not in opts:
            opts["terabox_mode"] = single_terabox_mode
        if single_source_title and "source_title" not in opts:
            opts["source_title"] = single_source_title
        if single_source_url and "source_url" not in opts:
            opts["source_url"] = single_source_url
        if single_is_auto is not None and "is_auto" not in opts:
            opts["is_auto"] = single_is_auto
        return opts

    # 1. raw_items (딕셔너리 리스트)
    if raw_items:
        for it in raw_items:
            if isinstance(it, dict) and it.get("url"):
                u = clean_url(it["url"])
                if u:
                    opts_item = (
                        {"referer": it.get("referer", "")} if it.get("referer") else {}
                    )
                    if it.get("password"):
                        opts_item["password"] = it["password"]
                    if "is_auto" in it:
                        opts_item["is_auto"] = it["is_auto"]
                    candidates.append(
                        {
                            "url": u,
                            "name": it.get("filename") or it.get("name") or "",
                            "options": _make_opts(opts_item),
                        }
                    )

    # 2. raw_text (문자열 라인들 또는 통째로 인코딩된 Base64)
    if raw_text:
        trimmed = raw_text.strip()
        # 텍스트 전체가 Base64 블록으로 인코딩되어 있는지 검사
        decoded_entire = decode_base64_string(trimmed)
        text_to_process = (
            decoded_entire
            if (
                decoded_entire != trimmed
                and (
                    "\n" in decoded_entire
                    or "http" in decoded_entire
                    or "magnet:" in decoded_entire
                )
            )
            else raw_text
        )

        pending_pwd = ""
        for line in text_to_process.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue

            line_str = re.sub(
                r"(?<![a-zA-Z0-9])h?\s*ttps://", "https://", line_str, flags=re.I
            )
            name_part = single_filename
            # 'URL | 파일명' 형식 분리
            if " | " in line_str:
                u_part, n_part = line_str.split(" | ", 1)
                line_str = u_part.strip()
                name_part = n_part.strip()

            # 라인 단위 Base64 디코딩 시도
            decoded_line = decode_base64_string(line_str)
            if " | " in decoded_line and not name_part:
                u_part, n_part = decoded_line.split(" | ", 1)
                decoded_line = u_part.strip()
                name_part = n_part.strip()

            # 라인 내에서 암호/추출코드 패턴 탐지
            line_pwd_match = PASSWORD_REGEX.search(
                decoded_line
            ) or PASSWORD_REGEX.search(line_str)
            line_pwd = line_pwd_match.group(1).strip() if line_pwd_match else ""

            # URL 및 개별 비밀번호 정밀 매칭
            urls_with_pwds = extract_urls_and_passwords_from_text(decoded_line)

            # 만약 디코딩된 라인에 URL이 없는데, 라인 내에 공백으로 구분된 여러 개의 개별 Base64 토큰이 있거나
            # 한국어/기호와 붙어 있는 Base64 URL 패턴(예: aHR0c..., bWFnbmV0...)이 존재하는 경우 처리
            if not urls_with_pwds:
                # 1) 공백 분리 토큰 검사
                if " " in line_str:
                    for token in line_str.split():
                        token = token.strip().strip("\"'<>[]()")
                        if len(token) >= 8:
                            d_tok = decode_base64_string(token)
                            if d_tok and d_tok != token:
                                urls_with_pwds.extend(
                                    extract_urls_and_passwords_from_text(d_tok)
                                )

                # 2) 텍스트에 붙어 있는 Base64 인코딩 URL 패턴 탐지 (범용 Base64 포함)
                for b64_match in re.finditer(r"[A-Za-z0-9+/_-]{12,}={0,2}", line_str):
                    cand_b64 = b64_match.group(0)
                    if "=" in cand_b64:
                        cand_b64 = cand_b64[: cand_b64.rfind("=") + 1]
                    d_b64 = decode_base64_string(cand_b64)
                    if (
                        d_b64
                        and d_b64 != cand_b64
                        and (
                            "http://" in d_b64
                            or "https://" in d_b64
                            or "magnet:" in d_b64
                            or is_shortened_url(d_b64)
                        )
                    ):
                        urls_with_pwds.extend(
                            extract_urls_and_passwords_from_text(d_b64)
                        )

            if urls_with_pwds:
                for u, item_pwd in urls_with_pwds:
                    effective_pwd = item_pwd or line_pwd or pending_pwd
                    pending_pwd = ""
                    u_clean = clean_url(u)
                    cand_opts = {}
                    if effective_pwd:
                        cand_opts["password"] = effective_pwd
                        parsed_u = urlparse(u_clean)
                        qs_u = parse_qs(parsed_u.query)
                        if not any(
                            k in qs_u
                            for k in ("pwd", "password", "dlkey", "code", "key")
                        ):
                            u_clean = with_password(u_clean, effective_pwd)

                    candidates.append(
                        {
                            "url": u_clean,
                            "name": name_part if len(urls_with_pwds) == 1 else "",
                            "options": _make_opts(cand_opts),
                        }
                    )
            else:
                if line_pwd:
                    if candidates and not candidates[-1]["options"].get("password"):
                        candidates[-1]["options"]["password"] = line_pwd
                        prev_u = candidates[-1]["url"]
                        parsed_prev = urlparse(prev_u)
                        qs_prev = parse_qs(parsed_prev.query)
                        if not any(
                            k in qs_prev
                            for k in ("pwd", "password", "dlkey", "code", "key")
                        ):
                            candidates[-1]["url"] = with_password(prev_u, line_pwd)
                    else:
                        pending_pwd = line_pwd
                cleaned_line_u = clean_url(decoded_line)
                if re.match(
                    r"^(https?://|magnet:\?)", cleaned_line_u, re.I
                ) or cleaned_line_u.lower().endswith(".torrent"):
                    cand_opts = {}
                    if line_pwd:
                        cand_opts["password"] = line_pwd
                    candidates.append(
                        {
                            "url": cleaned_line_u,
                            "name": name_part,
                            "options": _make_opts(cand_opts),
                        }
                    )

    # 3. raw_urls (리스트)
    if raw_urls:
        for item in raw_urls:
            if isinstance(item, dict):
                u = clean_url(item.get("url", ""))
                if u:
                    candidates.append(
                        {
                            "url": u,
                            "name": item.get("name") or item.get("filename") or "",
                            "options": _make_opts(item.get("options") or {}),
                        }
                    )
            else:
                cleaned_item = clean_url(str(item))
                urls = URL_REGEX.findall(cleaned_item)
                if urls:
                    for u in urls:
                        candidates.append(
                            {"url": u, "name": "", "options": _make_opts()}
                        )
                elif re.match(r"^(https?://|magnet:\?)", cleaned_item, re.I):
                    candidates.append(
                        {"url": cleaned_item, "name": "", "options": _make_opts()}
                    )

    # 중복 제거 (URL 기준, 입력 순서 보존)
    seen = set()
    deduped = []
    for c in candidates:
        u = c["url"] if isinstance(c, dict) else str(c)
        if u and u not in seen:
            seen.add(u)
            deduped.append(c)

    return deduped


def canonical_url_key(url: str) -> str:
    """
    동일한 다운로드 대상을 가리키는 서로 다른 URL 변형(파라미터 차이, 도메인 별칭 등)을
    단일한 고유 정규화 키로 변환하여 중복 다운로드를 방지합니다.
    """
    if not isinstance(url, str):
        return ""
    cleaned = clean_url(url)
    if not cleaned:
        return ""

    # 마그넷 링크: BTIH 해시값 기반 고유 키 생성
    if cleaned.lower().startswith("magnet:"):
        m_btih = re.search(r"xt=urn:btih:([a-zA-Z0-9]+)", cleaned, re.IGNORECASE)
        if m_btih:
            return f"magnet:btih:{m_btih.group(1).lower()}"
        return cleaned.split("&")[0].lower()

    try:
        parsed = urlparse(cleaned)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")

        # 구글 드라이브 (공유 링크, 직접 다운로드 링크, 파일 뷰어 링크 등)
        if "drive.google.com" in host:
            m_gd = re.search(r"/d/([a-zA-Z0-9_-]+)", path)
            if m_gd:
                return f"gdrive:{m_gd.group(1)}"
            qs = parse_qs(parsed.query)
            if "id" in qs and qs["id"]:
                return f"gdrive:{qs['id'][0]}"

        # 테라박스 계열 (1024terabox, terabox.com, teraboxapp, mirrobox, nephobox, 4funbox 등)
        if any(
            k in host
            for k in (
                "terabox",
                "1024tera",
                "teraboxapp",
                "teraboxlink",
                "4funbox",
                "mirrobox",
                "nephobox",
                "momerybox",
            )
        ):
            m_tb = re.search(r"/s/([a-zA-Z0-9_-]+)", path)
            if m_tb:
                skey = m_tb.group(1)
                return f"terabox:{skey}"
            qs = parse_qs(parsed.query)
            if "surl" in qs and qs["surl"]:
                surl = qs["surl"][0]
                if not surl.startswith("1"):
                    surl = "1" + surl
                return f"terabox:{surl}"

        # 메가 (mega.nz / mega.co.nz)
        if "mega.nz" in host or "mega.co.nz" in host:
            m_mega = re.search(r"/(?:file|folder)/([a-zA-Z0-9_-]+)", path)
            if m_mega:
                return f"mega:{m_mega.group(1)}"

        # GoFile (gofile.io/d/<contentId>)
        if "gofile.io" in host:
            m_gf = re.search(r"/d/([a-zA-Z0-9_-]+)", path)
            if m_gf:
                return f"gofile:{m_gf.group(1)}"

        # 일반 웹 다운로드 URL: 트래킹/불필요 쿼리 파라미터 제외하고 정규화
        if parsed.query:
            qs = parse_qs(parsed.query)
            filtered_query = sorted(
                (k, v[0])
                for k, v in qs.items()
                if not k.startswith("utm_")
                and k not in ("usp", "ref", "source", "fbclid", "gclid")
            )
            query_str = "&".join(f"{k}={v}" for k, v in filtered_query)
            return f"{host}{path}" + (f"?{query_str}" if query_str else "")

        return f"{host}{path}"
    except Exception:
        return cleaned.lower()
