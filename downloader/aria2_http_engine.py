"""aria2c 기반 HTTP/HTTPS 고속 분할(멀티 커넥션) 다운로드 엔진."""

import logging
import os
import re
import subprocess
import threading
from typing import Any, Callable, Mapping, Optional

from .aria2_manager import get_aria2_path
from .engine import sanitize_filename
from .torrent_engine import parse_aria2_summary_line

logger = logging.getLogger(__name__)


class Aria2HttpDownloadTask:
    """aria2c 멀티 커넥션을 활용하여 HTTP/HTTPS 파일을 분할 다운로드하는 태스크."""

    def __init__(
        self,
        url: str,
        dest_dir: str,
        filename: str = "",
        partial_id: str = "",
        headers: Mapping[str, Any] | None = None,
        cookies: str | dict | None = None,
        referer: str | None = None,
        split: int = 8,
        progress_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
    ):
        self.url = url
        self.dest_dir = dest_dir
        self.filename = sanitize_filename(filename) if filename else ""
        self.partial_id = re.sub(r"[^a-zA-Z0-9_-]", "", partial_id)
        self.headers = dict(headers or {})
        self.cookies = cookies or ""
        self.referer = referer or ""
        self.split = max(1, min(16, int(split)))
        self.progress_callback = progress_callback
        self.status_callback = status_callback

        self._process: Optional[subprocess.Popen] = None
        self._is_stopped = False
        self._is_paused = False
        self._lock = threading.Lock()
        self.final_file_path = ""

    def pause(self):
        """다운로드를 일시중지합니다 (이어받기 파일 보존)."""
        with self._lock:
            self._is_paused = True
            self._is_stopped = True
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                except Exception:
                    pass

    def stop(self):
        """다운로드를 취소/중지합니다."""
        with self._lock:
            self._is_paused = False
            self._is_stopped = True
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                except Exception:
                    pass
        # 취소 시 불완전 파일 및 .aria2 제어 파일 정리
        if self.filename:
            target = os.path.join(self.dest_dir, self.filename)
            aria2_ctrl = target + ".aria2"
            for fpath in (target, aria2_ctrl):
                if os.path.exists(fpath):
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass

    def run(self) -> dict:
        """aria2c 프로세스를 구동하여 고속 분할 다운로드를 수행합니다."""
        with self._lock:
            if self._is_stopped:
                return {
                    "success": False,
                    "status": "paused" if self._is_paused else "cancelled",
                    "message": "다운로드가 시작 전 중지되었습니다.",
                }

        os.makedirs(self.dest_dir, exist_ok=True)

        try:
            aria2_exe = get_aria2_path()
        except Exception as e:
            logger.error(f"aria2c 실행 파일 획득 실패: {e}")
            return {
                "success": False,
                "status": "failed",
                "error": f"aria2c 실행 파일을 찾을 수 없습니다: {e}",
            }

        cmd = [
            aria2_exe,
            f"--dir={self.dest_dir}",
            "--summary-interval=1",
            "--console-log-level=notice",
            "--file-allocation=none",
            "--continue=true",
            "--allow-overwrite=true",
            "--auto-file-renaming=false",
            f"--split={self.split}",
            f"--max-connection-per-server={self.split}",
            "--min-split-size=1M",
            "--stream-piece-selector=default",
            "--check-certificate=false",
        ]

        if self.filename:
            cmd.append(f"--out={self.filename}")

        if self.referer:
            cmd.append(f"--referer={self.referer}")

        if self.cookies:
            if isinstance(self.cookies, str):
                c_str = self.cookies
            elif isinstance(self.cookies, dict):
                c_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            else:
                c_str = ""
            if c_str:
                cmd.append(f"--header=Cookie: {c_str}")

        if self.headers:
            for k, v in self.headers.items():
                k_lower = str(k).lower()
                if k_lower in ("cookie", "referer"):
                    continue
                cmd.append(f"--header={k}: {v}")

        cmd.append("--")
        cmd.append(self.url)

        logger.info(
            f"[Aria2HTTP] 고속 분할 다운로드 시작 (분할 수: {self.split}): {self.filename or self.url}"
        )

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
            )
            with self._lock:
                if self._is_stopped:
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
        except Exception as e:
            logger.exception(f"aria2c 프로세스 실행 실패: {e}")
            return {
                "success": False,
                "status": "failed",
                "error": f"aria2c 구동 실패: {e}",
            }

        if not self._process or not self._process.stdout:
            return {
                "success": False,
                "status": "failed",
                "error": "aria2c stdout 스트림을 열 수 없습니다.",
            }

        proc_stdout = self._process.stdout
        last_dl_bytes = 0
        last_tot_bytes = 0

        readline_func = getattr(proc_stdout, "readline", None)
        lines_iter = iter(readline_func, "") if callable(readline_func) else proc_stdout

        try:
            for raw_line in lines_iter:
                if self._is_stopped:
                    with self._lock:
                        if self._process and self._process.poll() is None:
                            try:
                                self._process.terminate()
                            except Exception:
                                pass
                    break

                line = str(raw_line).strip()
                if not line:
                    continue

                # 1. 파일명 감지 (FILE: path/filename)
                if line.startswith("FILE:"):
                    clean_name = re.sub(
                        r"^FILE:\s*(\[MEMORY\])?(\[METADATA\])?", "", line
                    ).strip()
                    extracted_base = os.path.basename(clean_name)
                    if extracted_base and extracted_base != self.filename:
                        self.filename = sanitize_filename(extracted_base)
                        logger.info(f"[Aria2HTTP] 파일명 감지: {self.filename}")

                # 2. 진행 상태 요약 파싱
                parsed = parse_aria2_summary_line(line)
                if parsed:
                    last_dl_bytes = parsed["dl_bytes"]
                    if parsed["total_bytes"] > 0:
                        last_tot_bytes = parsed["total_bytes"]

                    pct = parsed["progress"]
                    if pct is None and last_tot_bytes > 0:
                        pct = (last_dl_bytes / last_tot_bytes) * 100.0
                    elif pct is None:
                        pct = 0.0

                    if self.progress_callback:
                        self.progress_callback(
                            downloaded_bytes=last_dl_bytes,
                            total_bytes=last_tot_bytes,
                            speed_bps=parsed["speed_bps"],
                            progress=pct,
                            eta_seconds=parsed["eta_seconds"],
                        )

            try:
                proc_stdout.close()
            except Exception:
                pass

            try:
                ret = self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if self._process.poll() is None:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                ret = self._process.wait()
        except Exception as e:
            logger.exception(f"[Aria2HTTP] 스트림 처리 중 오류: {e}")
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=5)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
            return {"success": False, "status": "failed", "error": str(e)}
        finally:
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=3)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass

        if self._is_stopped:
            if self._is_paused:
                logger.info(f"[Aria2HTTP] 다운로드 일시중지됨: {self.filename}")
                return {
                    "success": False,
                    "status": "paused",
                    "message": "일시중지되었습니다.",
                    "downloaded_bytes": last_dl_bytes,
                    "total_bytes": last_tot_bytes,
                }
            else:
                logger.info(f"[Aria2HTTP] 다운로드 취소됨: {self.filename}")
                return {
                    "success": False,
                    "status": "cancelled",
                    "message": "다운로드가 취소되었습니다.",
                }

        if ret == 0:
            final_path = os.path.join(self.dest_dir, self.filename)
            if not os.path.exists(final_path):
                entries = [
                    os.path.join(self.dest_dir, x)
                    for x in os.listdir(self.dest_dir)
                    if not x.endswith(".aria2")
                ]
                if entries:
                    latest = max(entries, key=os.path.getmtime)
                    final_path = latest
                    self.filename = os.path.basename(latest)

            self.final_file_path = final_path
            if self.progress_callback:
                self.progress_callback(
                    downloaded_bytes=last_tot_bytes or last_dl_bytes,
                    total_bytes=last_tot_bytes or last_dl_bytes,
                    speed_bps=0.0,
                    progress=100.0,
                    eta_seconds=0,
                )

            logger.info(
                f"[Aria2HTTP] 고속 분할 다운로드 성공 완료: {self.filename} -> {final_path}"
            )
            return {
                "success": True,
                "status": "completed",
                "filename": self.filename,
                "file_path": final_path,
                "total_bytes": last_tot_bytes or last_dl_bytes,
            }
        else:
            logger.error(
                f"[Aria2HTTP] aria2c 프로세스가 오류 코드 {ret}로 종료되었습니다."
            )
            return {
                "success": False,
                "status": "failed",
                "error": f"aria2c 프로세스 종료 오류 (코드 {ret})",
            }
