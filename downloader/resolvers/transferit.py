"""Transfer.it (MEGA 전송 서비스) 링크 리졸버 및 스트리밍 복호화 다운로더 모듈."""

import logging
import os
import re
import time
from contextlib import ExitStack
from urllib.parse import urlparse

from ..engine import (
    _FINALIZE_LOCK,
    _parse_content_range,
    format_bytes,
    get_unique_filename,
    sanitize_filename,
)
from .base import BaseResolver

from ..callbacks import safe_callback

logger = logging.getLogger(__name__)


class TransferitDownloadTask:
    """Transfer.it (MEGA AES-128-CTR 스트리밍 복호화) 다운로드 태스크."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: str = "",
        password: str | None = None,
        node_handle: str | None = None,
        node_key: list[int] | None = None,
        node_size: int | None = None,
        pw_token: str | None = None,
        xh: str | None = None,
        chunk_size: int = 512 * 1024,
        progress_callback=None,
        partial_id="",
    ):
        self.partial_id = re.sub(r"[^a-zA-Z0-9_-]", "", partial_id)
        self.url = url
        self.dest_dir = dest_dir
        self.filename: str = filename
        self.password = password
        self.node_handle = node_handle
        self.node_key = node_key
        self.node_size: int | None = node_size
        self.pw_token = pw_token
        self.xh = xh
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
        import httpx
        from Cryptodome.Cipher import AES
        from Cryptodome.Util import Counter
        from transferit import MegaAPI
        from transferit._crypto import a32_to_bytes, attr_key
        from transferit._models import TransferNode

        os.makedirs(self.dest_dir, exist_ok=True)
        api = MegaAPI()

        if not self.xh:
            self.xh = MegaAPI.parse_xh(self.url)

        if not self.node_handle or not self.node_key:
            try:
                node_dicts, pw_tok = api.fetch_transfer(self.xh, password=self.password)
                if pw_tok:
                    self.pw_token = pw_tok
                nodes = [TransferNode.from_dict(n) for n in node_dicts]
                files = [n for n in nodes if n.is_file]
                if not files:
                    raise FileNotFoundError(
                        "Transfer.it 전송에 다운로드 가능한 파일이 없습니다."
                    )
                target_node = files[0]
                self.node_handle = target_node.handle
                self.node_key = target_node.key
                if target_node.size is not None:
                    self.node_size = target_node.size
                if not self.filename and target_node.name:
                    self.filename = target_node.name
            except Exception as e:
                return {
                    "success": False,
                    "status": "failed",
                    "error": f"전송 정보 조회 실패: {e}",
                }

        dest_filename = sanitize_filename(self.filename or "transferit_file.dat")
        target_path = os.path.join(self.dest_dir, dest_filename)
        part_path = target_path + (
            f".{self.partial_id}.part" if self.partial_id else ".part"
        )

        # 다운로드 URL 발급
        try:
            dl_info = api.get_download_url(
                self.xh, self.node_handle, pw_token=self.pw_token
            )
            dl_url = dl_info["g"]
            total_size = dl_info.get("s") or self.node_size or 0
        except Exception as e:
            return {
                "success": False,
                "status": "failed",
                "error": f"다운로드 URL 발급 실패: {e}",
            }

        # 이어받기(Resume) 계산 (AES-CTR 16바이트 블록 정렬)
        existing_size = os.path.getsize(part_path) if os.path.exists(part_path) else 0
        aligned_size = (existing_size // 16) * 16 if existing_size > 0 else 0
        if existing_size > aligned_size:
            with open(part_path, "a+b") as f:
                f.truncate(aligned_size)
            existing_size = aligned_size

        aes_key = attr_key(self.node_key)
        nonce = a32_to_bytes(self.node_key[4:6])
        initial_block = existing_size // 16
        ctr = Counter.new(64, prefix=nonce, initial_value=initial_block)
        cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)

        req_headers = {}
        if existing_size > 0:
            req_headers["Range"] = f"bytes={existing_size}-"
            logger.info(
                f"Transfer.it 이어받기 시도: {existing_size} bytes ({dest_filename})"
            )

        downloaded_bytes = existing_size
        last_report_time = time.time()
        speed_calc_start = last_report_time
        bytes_since_calc = 0
        current_speed = 0.0

        try:
            with ExitStack() as stack:
                resp = stack.enter_context(
                    httpx.stream(
                        "GET",
                        dl_url,
                        headers=req_headers,
                        timeout=httpx.Timeout(60.0, connect=30.0),
                    )
                )
                if resp.status_code == 416 and existing_size > 0:
                    # 416 발생 시 처음부터 재시작
                    logger.warning(
                        "Transfer.it Range 416: 처음부터 다시 다운로드합니다."
                    )
                    existing_size = 0
                    downloaded_bytes = 0
                    ctr = Counter.new(64, prefix=nonce, initial_value=0)
                    cipher = AES.new(aes_key, AES.MODE_CTR, counter=ctr)
                    resp.close()
                    resp = stack.enter_context(
                        httpx.stream(
                            "GET", dl_url, timeout=httpx.Timeout(60.0, connect=30.0)
                        )
                    )

                resp.raise_for_status()
                if existing_size and resp.status_code == 200:
                    existing_size = downloaded_bytes = 0
                    cipher = AES.new(
                        aes_key,
                        AES.MODE_CTR,
                        counter=Counter.new(64, prefix=nonce, initial_value=0),
                    )
                if resp.status_code == 206:
                    content_range = _parse_content_range(
                        resp.headers.get("Content-Range", "")
                    )
                    if not content_range or content_range[0] != existing_size:
                        raise ValueError("Invalid Transfer.it Content-Range")

                file_mode = "ab" if existing_size > 0 else "wb"
                with open(part_path, file_mode) as f:
                    for chunk in resp.iter_bytes(self.chunk_size):
                        if self._is_stopped:
                            resp.close()
                            if self._is_paused:
                                logger.info(
                                    f"Transfer.it 다운로드 일시중지됨: {dest_filename}"
                                )
                                return {
                                    "success": False,
                                    "status": "paused",
                                    "message": "일시중지되었습니다.",
                                    "downloaded_bytes": downloaded_bytes,
                                    "total_bytes": total_size,
                                }
                            else:
                                logger.info(
                                    f"Transfer.it 다운로드 취소됨: {dest_filename}"
                                )
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

            # 다운로드 완료: .part -> 최종 파일명
            if self._is_stopped:
                if not self._is_paused and os.path.exists(part_path):
                    os.remove(part_path)
                return {
                    "success": False,
                    "status": "paused" if self._is_paused else "cancelled",
                }
            if total_size and downloaded_bytes != total_size:
                raise ValueError("Incomplete Transfer.it download")
            with _FINALIZE_LOCK:
                dest_filename = get_unique_filename(self.dest_dir, dest_filename)
                target_path = os.path.join(self.dest_dir, dest_filename)
                os.replace(part_path, target_path)

            final_size = os.path.getsize(target_path)
            logger.info(
                f"Transfer.it 다운로드 완료: {target_path} ({format_bytes(final_size)})"
            )

            if self.progress_callback:
                self.progress_callback(
                    final_size,
                    total_size or final_size,
                    0.0,
                    100.0,
                    0,
                )

            return {
                "success": True,
                "status": "completed",
                "file_path": target_path,
                "filename": dest_filename,
                "downloaded_bytes": final_size,
                "total_bytes": total_size or final_size,
            }

        except Exception as e:
            logger.exception(f"Transfer.it 다운로드 중 오류: {e}")
            return {
                "success": False,
                "status": "failed",
                "error": str(e),
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_size,
            }


class TransferitResolver(BaseResolver):
    """Transfer.it (MEGA 기반 파일 전송) 링크 리졸버."""

    name = "Transfer.it"

    def can_handle(self, url: str) -> bool:
        if not url:
            return False
        parsed = urlparse(url.strip())
        netloc = (parsed.hostname or "").lower()
        return netloc == "transfer.it" or netloc.endswith(".transfer.it")

    def resolve(self, url: str, options: dict | None = None) -> dict:
        from transferit import MegaAPI
        from transferit._models import TransferNode

        opts = options or {}
        if opts.get("node_handle") and opts.get("node_key"):
            return {
                "url": url,
                "filename": opts.get("filename", ""),
                "source_service": "Transfer.it",
            }
        from ..url_helper import get_candidate_passwords, parse_password_list

        candidates = get_candidate_passwords(url, opts)
        raw_manual = (
            opts.get("password") or opts.get("pwd") or opts.get("code") or ""
        ).strip()
        parsed_manual = parse_password_list(raw_manual)
        manual_pwd = parsed_manual[0] if parsed_manual else None

        api = MegaAPI()
        try:
            xh = MegaAPI.parse_xh(url)
        except Exception as e:
            raise ValueError(f"유효하지 않은 Transfer.it 링크입니다: {e}")

        logger.info(f"Transfer.it 전송 정보 조회 중 (xh: {xh})...")

        # 시도할 비밀번호 목록 구성: 수동 암호가 있으면 1순위, 없으면 None(암호 없음) 먼저 시도 후 사전등록 후보군 순차 시도
        try_pwds: list[str | None] = []
        if manual_pwd:
            try_pwds.append(manual_pwd)
        else:
            try_pwds.append(None)
        for cand in candidates:
            if cand != manual_pwd and cand not in try_pwds:
                try_pwds.append(cand)

        node_dicts = None
        pw_token = None
        password = None
        last_err = None
        tried_pwds = []

        for cand in try_pwds:
            if cand is not None:
                tried_pwds.append(cand)
            try:
                node_dicts, pw_token = api.fetch_transfer(xh, password=cand)
                password = cand
                if cand:
                    logger.info(f"Transfer.it 비밀번호 인증 성공: '{cand}'")
                break
            except Exception as e:
                err_msg = str(e).lower()
                last_err = e
                if "password" in err_msg or "credential" in err_msg or "decrypt" in err_msg:
                    continue
                else:
                    raise ValueError(f"Transfer.it 링크 정보를 가져올 수 없습니다: {e}")

        if node_dicts is None:
            if tried_pwds:
                tried_str = ", ".join(repr(p) for p in tried_pwds)
                raise ValueError(
                    f"이 Transfer.it 링크는 비밀번호가 필요하거나 시도한 암호({tried_str})가 일치하지 않습니다."
                )
            raise ValueError(f"이 Transfer.it 링크는 비밀번호가 필요합니다: {last_err}")

        nodes = [TransferNode.from_dict(n) for n in node_dicts]
        files = [n for n in nodes if n.is_file]
        if not files:
            raise FileNotFoundError(
                "Transfer.it 전송에 다운로드 가능한 파일이 존재하지 않습니다."
            )

        primary_file = files[0]
        filename = sanitize_filename(
            primary_file.name or f"transfer_{primary_file.handle}"
        )
        size = primary_file.size or 0

        # 복수 파일인 경우 추가 작업(extra_jobs) 구성
        extra_jobs = []
        if len(files) > 1:
            for extra_f in files[1:]:
                extra_jobs.append(
                    {
                        "url": url,
                        "filename": extra_f.name,
                        "size": extra_f.size,
                        "options": {
                            "node_handle": extra_f.handle,
                            "node_key": extra_f.key,
                            "node_size": extra_f.size,
                            "pw_token": pw_token,
                            "xh": xh,
                            "password": password,
                        },
                    }
                )

        logger.info(f"Transfer.it 링크 분석 완료: {filename} ({format_bytes(size)})")

        return {
            "url": url,
            "filename": filename,
            "size": size,
            "source_service": "Transfer.it",
            "task_factory": lambda job, dest_dir, progress_cb: TransferitDownloadTask(
                url=url,
                dest_dir=dest_dir,
                filename=filename,
                password=password,
                node_handle=primary_file.handle,
                node_key=primary_file.key,
                node_size=size,
                pw_token=pw_token,
                xh=xh,
                progress_callback=progress_cb,
                partial_id=job.id,
            ),
            "extra_jobs": extra_jobs,
        }
