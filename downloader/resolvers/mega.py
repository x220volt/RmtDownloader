"""MEGA (mega.nz) 링크 리졸버 및 AES-128-CTR 스트리밍 복호화 다운로더 모듈."""

import base64
import logging
import os
import re
import struct
import time
from contextlib import ExitStack

import httpx
import requests
from Cryptodome.Cipher import AES
from Cryptodome.Util import Counter
from transferit._crypto import a32_to_bytes, attr_key, b64_to_a32, decrypt_attr

from ..callbacks import safe_callback
from ..engine import (
    _FINALIZE_LOCK,
    _parse_content_range,
    format_bytes,
    get_unique_filename,
    sanitize_filename,
)
from .base import BaseResolver

logger = logging.getLogger(__name__)

MEGA_API_URL = "https://g.api.mega.co.nz/cs"

MEGA_ERRORS = {
    -1: "EINTERNAL: MEGA 서버 내부 오류가 발생했습니다.",
    -2: "EARGS: 잘못된 요청 인자입니다.",
    -3: "EAGAIN: 일시적인 오류입니다. 잠시 후 다시 시도해 주세요.",
    -4: "ERATELIMIT: 다운로드 트래픽 제한(할당량 초과)에 도달했습니다. 잠시 후 다시 시도해 주세요.",
    -6: "ETOOMANY: 동시 요청 수가 너무 많습니다.",
    -9: "ENOENT: 파일이 존재하지 않거나 삭제되었습니다.",
    -11: "EACCESS: 파일에 접근할 수 없습니다 (권한 부족 또는 만료).",
    -14: "ETOOMANYCONNECTIONS: 동시 연결 수가 초과되었습니다.",
    -15: "EBLOCKED: 파일이 저작권 또는 정책 위반으로 차단되었습니다.",
    -16: "EOVERQUOTA: 무료 다운로드 할당량이 초과되었습니다.",
    -17: "ETEMPUNAVAIL: 파일 또는 서버를 일시적으로 사용할 수 없습니다.",
    -18: "ETOOMANYCONNECTIONS: 연결 수가 초과되었습니다.",
}


def decrypt_node_key(k_str: str, cipher: AES) -> list[int] | None:
    """MEGA 폴더 노드의 암호화된 키 문자열(k)을 복호화하여 32비트 정수 리스트(key_a32)로 반환합니다."""
    if not k_str:
        return None
    for part in k_str.split("/"):
        if ":" in part:
            _, enc_b64 = part.split(":", 1)
        else:
            enc_b64 = part
        try:
            pad = -len(enc_b64) % 4
            raw_b64 = enc_b64 + "=" * pad
            enc_bytes = base64.b64decode(raw_b64.replace("-", "+").replace("_", "/"))
            if len(enc_bytes) % 16 != 0:
                continue
            dec_bytes = cipher.decrypt(enc_bytes)
            return list(struct.unpack(">" + "I" * (len(dec_bytes) // 4), dec_bytes))
        except Exception:
            continue
    return None


def parse_mega_url(url: str) -> dict | None:
    """
    다양한 형태의 mega.nz / mega.co.nz URL을 파싱하여 식별자와 키를 추출합니다.
    - 파일:
      - https://mega.nz/file/{file_id}#{file_key}
      - https://mega.nz/#!{file_id}!{file_key}
      - https://mega.co.nz/#!{file_id}!{file_key}
    - 폴더:
      - https://mega.nz/folder/{folder_id}#{folder_key}
      - https://mega.nz/folder/{folder_id}#{folder_key}/file/{file_id}
      - https://mega.nz/#F!{folder_id}!{folder_key}
      - https://mega.nz/#F!{folder_id}!{folder_key}!{file_id}
    """
    if not url:
        return None
    url = url.strip()

    # 1. Folder 신규 포맷: /folder/<id>#<key> 또는 /folder/<id>#<key>/file/<file_id>
    m = re.search(
        r"mega\.(?:nz|co\.nz|io)/folder/([a-zA-Z0-9_-]{8})[#]([a-zA-Z0-9_-]{22,64})(?:/file/([a-zA-Z0-9_-]+))?",
        url,
    )
    if m:
        res = {"type": "folder", "id": m.group(1), "key": m.group(2)}
        if m.group(3):
            res["target_file_id"] = m.group(3)
        return res

    # 2. Folder 레거시 포맷: /#F!<id>!<key> 또는 /#F!<id>!<key>!<file_id>
    m = re.search(
        r"mega\.(?:nz|co\.nz|io)/#\??!?F!([a-zA-Z0-9_-]{8})!([a-zA-Z0-9_-]{22,64})(?:!([a-zA-Z0-9_-]+))?",
        url,
    )
    if m:
        res = {"type": "folder", "id": m.group(1), "key": m.group(2)}
        if m.group(3):
            res["target_file_id"] = m.group(3)
        return res

    # 3. File 신규 포맷: /file/<id>#<key>
    m = re.search(
        r"mega\.(?:nz|co\.nz|io)/file/([a-zA-Z0-9_-]{8})[#]([a-zA-Z0-9_-]{22,64})",
        url,
    )
    if m:
        return {"type": "file", "id": m.group(1), "key": m.group(2)}

    # 4. File 레거시 포맷: /#!<id>!<key> 또는 /#<id>!<key>
    m = re.search(
        r"mega\.(?:nz|co\.nz|io)/#\??!?([a-zA-Z0-9_-]{8})!([a-zA-Z0-9_-]{22,64})",
        url,
    )
    if m:
        return {"type": "file", "id": m.group(1), "key": m.group(2)}

    return None


class MegaDownloadTask:
    """MEGA AES-128-CTR 스트리밍 복호화 다운로드 태스크."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: str = "",
        file_id: str | None = None,
        key_a32: list[int] | None = None,
        file_size: int | None = None,
        folder_id: str | None = None,
        chunk_size: int = 512 * 1024,
        progress_callback=None,
        partial_id: str = "",
    ):
        self.partial_id = re.sub(r"[^a-zA-Z0-9_-]", "", partial_id)
        self.url = url
        self.dest_dir = dest_dir
        self.filename: str = filename
        self.file_id = file_id
        self.key_a32 = key_a32
        self.file_size = file_size
        self.folder_id = folder_id
        self.chunk_size = chunk_size
        self.progress_callback = safe_callback(progress_callback)

        self._is_paused = False
        self._is_stopped = False

    def pause(self):
        """다운로드 일시중지를 요청합니다."""
        self._is_paused = True
        self._is_stopped = True

    def resume(self):
        """다운로드를 재개합니다."""
        self._is_paused = False
        self._is_stopped = False

    def cancel(self):
        """다운로드 취소를 요청합니다."""
        self._is_paused = False
        self._is_stopped = True

    def stop(self):
        self.cancel()

    def run(self) -> dict:
        """스트리밍 다운로드 및 복호화를 수행합니다."""
        os.makedirs(self.dest_dir, exist_ok=True)

        # file_id 및 key_a32 확보
        if not self.file_id or not self.key_a32:
            parsed = parse_mega_url(self.url)
            if not parsed:
                raise ValueError(f"유효하지 않은 MEGA URL입니다: {self.url}")
            if parsed.get("type") == "folder":
                self.folder_id = parsed["id"]
                folder_key = parsed["key"]
                target_file_id = parsed.get("target_file_id")
                master_key = a32_to_bytes(b64_to_a32(folder_key))
                cipher = AES.new(master_key, AES.MODE_ECB)
                resp = requests.post(
                    f"{MEGA_API_URL}?id=0&n={self.folder_id}",
                    json=[{"a": "f", "c": 1, "r": 1, "ca": 1}],
                    timeout=20,
                )
                resp.raise_for_status()
                res_data = resp.json()[0]
                if isinstance(res_data, int):
                    err_text = MEGA_ERRORS.get(res_data, f"에러 코드 {res_data}")
                    raise ValueError(f"MEGA 폴더 정보를 가져올 수 없습니다: {err_text}")
                nodes = res_data.get("f", [])
                found_node = None
                for n in nodes:
                    if n.get("t") == 0:
                        if not target_file_id or n.get("h") == target_file_id:
                            found_node = n
                            break
                if not found_node:
                    raise FileNotFoundError(
                        "MEGA 폴더 내에서 다운로드할 파일을 찾을 수 없습니다."
                    )
                self.file_id = found_node["h"]
                self.file_size = int(found_node.get("s") or 0)
                dec_ints = decrypt_node_key(found_node.get("k", ""), cipher)
                if not dec_ints:
                    raise ValueError("MEGA 파일 키 복호화에 실패했습니다.")
                self.key_a32 = dec_ints
                if not self.filename and found_node.get("a"):
                    try:
                        attrs = decrypt_attr(found_node["a"], dec_ints)
                        if attrs and attrs.get("n"):
                            self.filename = attrs["n"]
                    except Exception:
                        pass
            elif parsed.get("type") == "file":
                self.file_id = parsed["id"]
                self.key_a32 = b64_to_a32(parsed["key"])
            else:
                raise ValueError(f"유효하지 않은 MEGA URL입니다: {self.url}")

        # 최신 다운로드 정보 및 URL 요청
        try:
            if self.folder_id:
                api_req_url = f"{MEGA_API_URL}?id=0&n={self.folder_id}"
                payload = [{"a": "g", "g": 1, "ssl": 2, "n": self.file_id}]
            else:
                api_req_url = MEGA_API_URL
                payload = [{"a": "g", "g": 1, "ssl": 2, "p": self.file_id}]
            resp = requests.post(
                api_req_url,
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            res_data = resp.json()[0]
        except Exception as e:
            return {
                "success": False,
                "status": "failed",
                "error": f"MEGA 다운로드 주소 발급 요청 실패: {e}",
            }

        if isinstance(res_data, int):
            err_text = MEGA_ERRORS.get(res_data, f"MEGA 에러 코드 {res_data}")
            return {
                "success": False,
                "status": "failed",
                "error": f"MEGA 다운로드 실패: {err_text}",
            }

        dl_url = res_data.get("g")
        if not dl_url:
            return {
                "success": False,
                "status": "failed",
                "error": "MEGA 다운로드 직링크(g)가 응답에 없습니다.",
            }

        total_size = res_data.get("s") or self.file_size or 0

        # 파일명 복호화 (아직 파일명이 없는 경우)
        if not self.filename and res_data.get("at"):
            try:
                attrs = decrypt_attr(res_data["at"], self.key_a32)
                if attrs and attrs.get("n"):
                    self.filename = attrs["n"]
            except Exception as e:
                logger.warning(f"MEGA 파일명 속성 복호화 경고: {e}")

        dest_filename = sanitize_filename(self.filename or f"mega_{self.file_id}.bin")
        part_path = os.path.join(
            self.dest_dir, f"{dest_filename}.{self.partial_id}.part"
        )

        # 이어받기(Resume) 계산 (AES-CTR 16바이트 블록 정렬)
        existing_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        aligned_size = (existing_size // 16) * 16 if existing_size > 0 else 0
        if existing_size > aligned_size:
            with open(part_path, "a+b") as f:
                f.truncate(aligned_size)
            existing_size = aligned_size

        aes_key = attr_key(self.key_a32)
        nonce = a32_to_bytes(self.key_a32[4:6])
        initial_block = existing_size // 16
        ctr = Counter.new(64, prefix=nonce, initial_value=initial_block)
        cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)

        req_headers = {}
        if existing_size > 0:
            req_headers["Range"] = f"bytes={existing_size}-"
            logger.info(f"MEGA 이어받기 시도: {existing_size} bytes ({dest_filename})")

        downloaded_bytes = existing_size
        last_report_time = time.time()
        speed_calc_start = last_report_time
        bytes_since_calc = 0
        current_speed = 0.0

        try:
            with ExitStack() as stack:
                stream_resp = stack.enter_context(
                    httpx.stream(
                        "GET",
                        dl_url,
                        headers=req_headers,
                        timeout=httpx.Timeout(60.0, connect=30.0),
                    )
                )

                if stream_resp.status_code == 416 and existing_size > 0:
                    logger.warning("MEGA Range 416: 처음부터 다시 다운로드합니다.")
                    existing_size = 0
                    downloaded_bytes = 0
                    ctr = Counter.new(64, prefix=nonce, initial_value=0)
                    cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)
                    stream_resp.close()
                    stream_resp = stack.enter_context(
                        httpx.stream(
                            "GET", dl_url, timeout=httpx.Timeout(60.0, connect=30.0)
                        )
                    )

                stream_resp.raise_for_status()

                if existing_size and stream_resp.status_code == 200:
                    existing_size = downloaded_bytes = 0
                    cipher = AES.new(
                        aes_key,
                        AES.MODE_CTR,
                        counter=Counter.new(64, prefix=nonce, initial_value=0),
                    )

                if stream_resp.status_code == 206:
                    content_range = _parse_content_range(
                        stream_resp.headers.get("Content-Range", "")
                    )
                    if not content_range or content_range[0] != existing_size:
                        raise ValueError("Invalid MEGA Content-Range")

                file_mode = "ab" if existing_size > 0 else "wb"
                with open(part_path, file_mode) as f:
                    for chunk in stream_resp.iter_bytes(self.chunk_size):
                        if self._is_stopped:
                            stream_resp.close()
                            if self._is_paused:
                                logger.info(
                                    f"MEGA 다운로드 일시중지됨: {dest_filename}"
                                )
                                return {
                                    "success": False,
                                    "status": "paused",
                                    "message": "일시중지되었습니다.",
                                    "downloaded_bytes": downloaded_bytes,
                                    "total_bytes": total_size,
                                }
                            else:
                                logger.info(f"MEGA 다운로드 취소됨: {dest_filename}")
                                f.close()
                                if os.path.exists(part_path):
                                    try:
                                        os.remove(part_path)
                                    except Exception:
                                        pass
                                return {
                                    "success": False,
                                    "status": "cancelled",
                                    "message": "다운로드가 취소되었습니다.",
                                }

                        if not chunk:
                            continue

                        decrypted_chunk = cipher.decrypt(chunk)
                        f.write(decrypted_chunk)

                        chunk_len = len(chunk)
                        downloaded_bytes += chunk_len
                        bytes_since_calc += chunk_len

                        now = time.time()
                        time_since_calc = now - speed_calc_start
                        if time_since_calc >= 0.5:
                            current_speed = bytes_since_calc / time_since_calc
                            speed_calc_start = now
                            bytes_since_calc = 0

                        if now - last_report_time >= 0.25 or (
                            total_size and downloaded_bytes >= total_size
                        ):
                            last_report_time = now
                            progress = (
                                (downloaded_bytes / total_size * 100.0)
                                if total_size
                                else 0.0
                            )
                            eta_seconds = (
                                (total_size - downloaded_bytes) / current_speed
                                if (
                                    total_size
                                    and current_speed > 0
                                    and downloaded_bytes < total_size
                                )
                                else None
                            )
                            if self.progress_callback:
                                self.progress_callback(
                                    downloaded_bytes,
                                    total_size,
                                    current_speed,
                                    progress,
                                    eta_seconds,
                                )

            # 완료 후 최종 파일명으로 원자적 교체
            if self._is_stopped:
                if not self._is_paused and os.path.exists(part_path):
                    os.remove(part_path)
                return {
                    "success": False,
                    "status": "paused" if self._is_paused else "cancelled",
                }

            if total_size and downloaded_bytes != total_size:
                raise ValueError(
                    f"Incomplete MEGA download ({downloaded_bytes}/{total_size} bytes)"
                )

            with _FINALIZE_LOCK:
                dest_filename = get_unique_filename(self.dest_dir, dest_filename)
                target_path = os.path.join(self.dest_dir, dest_filename)
                os.replace(part_path, target_path)

            logger.info(
                f"MEGA 다운로드 완료: {dest_filename} ({format_bytes(downloaded_bytes)})"
            )
            return {
                "success": True,
                "status": "completed",
                "filename": dest_filename,
                "path": target_path,
                "size": downloaded_bytes,
            }

        except Exception as e:
            logger.exception(f"MEGA 다운로드 실패 ({dest_filename}): {e}")
            return {
                "success": False,
                "status": "failed",
                "error": str(e),
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_size,
            }


class MegaResolver(BaseResolver):
    """MEGA (mega.nz / mega.co.nz) 다운로드 링크 리졸버."""

    name: str = "MEGA"

    def can_handle(self, url: str) -> bool:
        return parse_mega_url(url) is not None

    def resolve(self, url: str, options: dict | None = None) -> dict:
        opts = options or {}

        # 1. 후속 작업(extra_jobs) 또는 옵션으로 파일 ID 및 복호화 키가 직접 전달된 경우 바로 발급
        if opts.get("mega_file_id") and opts.get("mega_key_a32"):
            file_id = opts["mega_file_id"]
            key_a32 = opts["mega_key_a32"]
            file_size = int(opts.get("mega_file_size") or 0)
            folder_id = opts.get("mega_folder_id")
            filename = opts.get("filename") or f"mega_{file_id}.bin"
            subfolder = opts.get("subfolder") or ""
            article_title = opts.get("article_title") or ""
            source_page_url = opts.get("source_page_url") or url

            logger.info(f"MEGA 지정 파일 다운로드 주소 발급 중 (ID: {file_id})...")
            if folder_id:
                api_req_url = f"{MEGA_API_URL}?id=0&n={folder_id}"
                payload = [{"a": "g", "g": 1, "ssl": 2, "n": file_id}]
            else:
                api_req_url = MEGA_API_URL
                payload = [{"a": "g", "g": 1, "ssl": 2, "p": file_id}]

            try:
                resp = requests.post(api_req_url, json=payload, timeout=20)
                resp.raise_for_status()
                res_list = resp.json()
                if not isinstance(res_list, list) or not res_list:
                    raise ValueError("MEGA API로부터 비어있는 응답을 받았습니다.")
                res_data = res_list[0]
            except Exception as e:
                raise ValueError(f"MEGA 링크 정보를 가져올 수 없습니다: {e}")

            if isinstance(res_data, int):
                err_text = MEGA_ERRORS.get(res_data, f"에러 코드 {res_data}")
                raise ValueError(f"MEGA 파일 정보를 불러오지 못했습니다: {err_text}")

            dl_url = res_data.get("g")
            if not dl_url:
                raise ValueError("MEGA 다운로드 직링크(g)가 응답에 없습니다.")

            file_size = int(res_data.get("s") or file_size or 0)
            if not filename or filename.startswith("mega_"):
                enc_attr = res_data.get("at")
                if enc_attr:
                    try:
                        attrs = decrypt_attr(enc_attr, key_a32)
                        if attrs and attrs.get("n"):
                            filename = sanitize_filename(attrs["n"])
                    except Exception as e:
                        logger.warning(f"MEGA 파일명 속성 복호화 실패: {e}")

            return {
                "url": dl_url,
                "filename": filename,
                "size": file_size,
                "file_size": file_size,
                "subfolder": subfolder,
                "article_title": article_title,
                "source_page_url": source_page_url,
                "source_service": "MEGA",
                "options": {
                    "mega_file_id": file_id,
                    "mega_key_a32": key_a32,
                    "mega_file_size": file_size,
                    "mega_folder_id": folder_id,
                    "subfolder": subfolder,
                    "article_title": article_title,
                    "source_page_url": source_page_url,
                },
                "task_factory": lambda job, dest_dir, progress_cb: MegaDownloadTask(
                    url=url,
                    dest_dir=dest_dir,
                    filename=filename,
                    file_id=file_id,
                    key_a32=key_a32,
                    file_size=file_size,
                    folder_id=folder_id,
                    progress_callback=progress_cb,
                    partial_id=job.id,
                ),
            }

        parsed = parse_mega_url(url)
        if not parsed:
            raise ValueError(f"지원하지 않는 MEGA URL 형식입니다: {url}")

        # 2. MEGA 공유 폴더 링크 처리
        if parsed.get("type") == "folder":
            folder_id = parsed["id"]
            folder_key = parsed["key"]
            target_file_id = parsed.get("target_file_id")

            logger.info(f"MEGA 공유 폴더 조회 중 (Folder ID: {folder_id})...")
            try:
                resp = requests.post(
                    f"{MEGA_API_URL}?id=0&n={folder_id}",
                    json=[{"a": "f", "c": 1, "r": 1, "ca": 1}],
                    timeout=20,
                )
                resp.raise_for_status()
                res_list = resp.json()
                if not isinstance(res_list, list) or not res_list:
                    raise ValueError("MEGA API로부터 비어있는 응답을 받았습니다.")
                res_data = res_list[0]
            except Exception as e:
                raise ValueError(f"MEGA 폴더 정보를 가져올 수 없습니다: {e}")

            if isinstance(res_data, int):
                err_text = MEGA_ERRORS.get(res_data, f"에러 코드 {res_data}")
                raise ValueError(f"MEGA 폴더 정보를 불러오지 못했습니다: {err_text}")

            nodes = res_data.get("f", [])
            master_key = a32_to_bytes(b64_to_a32(folder_key))
            cipher = AES.new(master_key, AES.MODE_ECB)

            folder_map = {}  # handle -> {'name': name, 'parent': parent}
            file_nodes = []
            detected_folder_name = ""

            for n in nodes:
                t = n.get("t")
                h = n.get("h")
                p = n.get("p")
                k_str = n.get("k", "")
                dec_ints = decrypt_node_key(k_str, cipher)
                if not dec_ints:
                    continue
                attrs = {}
                if n.get("a"):
                    try:
                        attrs = decrypt_attr(n["a"], dec_ints) or {}
                    except Exception:
                        pass
                name = attrs.get("n", "")

                if t == 1:
                    folder_map[h] = {"name": name, "parent": p}
                    if not detected_folder_name and name:
                        detected_folder_name = sanitize_filename(name)
                elif t == 0:
                    file_nodes.append(
                        {
                            "id": h,
                            "parent": p,
                            "name": sanitize_filename(name or f"mega_{h}.bin"),
                            "size": int(n.get("s") or 0),
                            "key_a32": dec_ints,
                        }
                    )

            folder_title = detected_folder_name or f"mega_{folder_id}"

            def get_rel_path(parent_handle: str) -> str:
                path_parts = []
                curr = parent_handle
                while curr in folder_map:
                    f_info = folder_map[curr]
                    p = f_info.get("parent")
                    if p in folder_map:
                        path_parts.append(f_info["name"])
                        curr = p
                    else:
                        break
                path_parts.reverse()
                return os.path.join(*path_parts) if path_parts else ""

            if target_file_id:
                matching = [f for f in file_nodes if f["id"] == target_file_id]
                if not matching:
                    raise FileNotFoundError(
                        f"MEGA 폴더 내에서 파일 {target_file_id}을 찾을 수 없습니다."
                    )
                file_nodes = matching

            if not file_nodes:
                raise FileNotFoundError(
                    "MEGA 폴더에 다운로드 가능한 파일이 존재하지 않습니다."
                )

            primary_file = file_nodes[0]
            primary_rel = get_rel_path(primary_file["parent"])
            primary_subfolder = (
                os.path.join(folder_title, primary_rel)
                if primary_rel
                else folder_title
            )

            # 첫 번째 파일의 다운로드 직링크 요청
            try:
                dl_resp = requests.post(
                    f"{MEGA_API_URL}?id=0&n={folder_id}",
                    json=[{"a": "g", "g": 1, "ssl": 2, "n": primary_file["id"]}],
                    timeout=20,
                )
                dl_resp.raise_for_status()
                dl_data = dl_resp.json()[0]
                if isinstance(dl_data, int):
                    err_text = MEGA_ERRORS.get(dl_data, f"에러 코드 {dl_data}")
                    raise ValueError(
                        f"MEGA 파일 다운로드 주소를 발급하지 못했습니다: {err_text}"
                    )
                dl_url = dl_data.get("g")
                if not dl_url:
                    raise ValueError("MEGA 다운로드 직링크(g)가 응답에 없습니다.")
            except Exception as e:
                raise ValueError(f"MEGA 파일 직링크 요청 실패: {e}")

            extra_jobs = []
            if len(file_nodes) > 1 and not target_file_id:
                for f in file_nodes[1:]:
                    f_rel = get_rel_path(f["parent"])
                    f_sub = (
                        os.path.join(folder_title, f_rel)
                        if f_rel
                        else folder_title
                    )
                    extra_jobs.append(
                        {
                            "url": f"https://mega.nz/folder/{folder_id}#{folder_key}/file/{f['id']}",
                            "filename": f["name"],
                            "size": f["size"],
                            "options": {
                                "mega_file_id": f["id"],
                                "mega_key_a32": f["key_a32"],
                                "mega_file_size": f["size"],
                                "mega_folder_id": folder_id,
                                "filename": f["name"],
                                "subfolder": f_sub,
                                "article_title": folder_title,
                                "source_page_url": url,
                            },
                        }
                    )

            logger.info(
                f"MEGA 폴더 분석 완료: '{folder_title}' 총 {len(file_nodes)}개 파일 "
                f"(첫 파일: {primary_file['name']}, 추가 큐: {len(extra_jobs)}개)"
            )

            return {
                "url": dl_url,
                "filename": primary_file["name"],
                "size": primary_file["size"],
                "file_size": primary_file["size"],
                "subfolder": primary_subfolder,
                "article_title": folder_title,
                "source_page_url": url,
                "source_service": "MEGA",
                "options": {
                    "mega_file_id": primary_file["id"],
                    "mega_key_a32": primary_file["key_a32"],
                    "mega_file_size": primary_file["size"],
                    "mega_folder_id": folder_id,
                    "subfolder": primary_subfolder,
                    "article_title": folder_title,
                    "source_page_url": url,
                },
                "task_factory": lambda job, dest_dir, progress_cb: MegaDownloadTask(
                    url=url,
                    dest_dir=dest_dir,
                    filename=primary_file["name"],
                    file_id=primary_file["id"],
                    key_a32=primary_file["key_a32"],
                    file_size=primary_file["size"],
                    folder_id=folder_id,
                    progress_callback=progress_cb,
                    partial_id=job.id,
                ),
                "extra_jobs": extra_jobs,
            }

        # 3. MEGA 단일 파일 링크 처리
        file_id = parsed["id"]
        file_key_b64 = parsed["key"]
        key_a32 = b64_to_a32(file_key_b64)

        logger.info(f"MEGA 파일 정보 조회 중 (ID: {file_id})...")
        try:
            resp = requests.post(
                MEGA_API_URL,
                json=[{"a": "g", "g": 1, "ssl": 2, "p": file_id}],
                timeout=20,
            )
            resp.raise_for_status()
            res_list = resp.json()
            if not isinstance(res_list, list) or not res_list:
                raise ValueError("MEGA API로부터 비어있는 응답을 받았습니다.")
            res_data = res_list[0]
        except Exception as e:
            raise ValueError(f"MEGA 링크 정보를 가져올 수 없습니다: {e}")

        if isinstance(res_data, int):
            err_text = MEGA_ERRORS.get(res_data, f"에러 코드 {res_data}")
            raise ValueError(f"MEGA 파일 정보를 불러오지 못했습니다: {err_text}")

        dl_url = res_data.get("g")
        if not dl_url:
            raise ValueError("MEGA 다운로드 직링크(g)가 응답에 없습니다.")

        file_size = int(res_data.get("s") or 0)
        filename = f"mega_{file_id}.bin"

        # 파일명 속성 복호화
        enc_attr = res_data.get("at")
        if enc_attr:
            try:
                attrs = decrypt_attr(enc_attr, key_a32)
                if attrs and attrs.get("n"):
                    filename = sanitize_filename(attrs["n"])
            except Exception as e:
                logger.warning(f"MEGA 파일명 속성 복호화 실패: {e}")

        logger.info(f"MEGA 링크 분석 완료: {filename} ({format_bytes(file_size)})")

        return {
            "url": dl_url,
            "filename": filename,
            "size": file_size,
            "file_size": file_size,
            "source_service": "MEGA",
            "options": {
                "mega_file_id": file_id,
                "mega_key_a32": key_a32,
                "mega_file_size": file_size,
            },
            "task_factory": lambda job, dest_dir, progress_cb: MegaDownloadTask(
                url=url,
                dest_dir=dest_dir,
                filename=filename,
                file_id=file_id,
                key_a32=key_a32,
                file_size=file_size,
                progress_callback=progress_cb,
                partial_id=job.id,
            ),
        }
