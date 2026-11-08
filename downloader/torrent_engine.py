"""BitTorrent 마그넷 및 .torrent 파일 다운로드를 위한 전용 엔진 (aria2 기반)."""

import logging
import os
import re
import subprocess
import threading
from typing import Callable, Optional

from .aria2_manager import get_aria2_path

logger = logging.getLogger(__name__)


def bdecode(data: bytes):
    """순수 파이썬 bencode 디코더."""

    def decode_item(idx: int):
        if idx >= len(data):
            raise ValueError("Unexpected end of bencoded data")
        b = data[idx : idx + 1]
        if b == b"i":
            end = data.index(b"e", idx)
            return int(data[idx + 1 : end]), end + 1
        elif b.isdigit():
            colon = data.index(b":", idx)
            length = int(data[idx:colon])
            start = colon + 1
            return data[start : start + length], start + length
        elif b == b"l":
            idx += 1
            res = []
            while data[idx : idx + 1] != b"e":
                item, idx = decode_item(idx)
                res.append(item)
            return res, idx + 1
        elif b == b"d":
            idx += 1
            res = {}
            while data[idx : idx + 1] != b"e":
                key, idx = decode_item(idx)
                val, idx = decode_item(idx)
                res[key] = val
            return res, idx + 1
        raise ValueError(f"Unknown bencode type byte: {b}")

    return decode_item(0)[0]


def parse_torrent_file(torrent_path: str) -> dict:
    """.torrent 파일을 읽어 파일명 및 총 용량 정보를 추출합니다."""
    try:
        with open(torrent_path, "rb") as f:
            data = f.read()

        decoded = bdecode(data)
        info = decoded.get(b"info", {}) if isinstance(decoded, dict) else {}
        if not isinstance(info, dict):
            info = {}

        name = ""
        for k in (b"name.utf-8", b"name"):
            if k in info:
                val = info[k]
                for enc in ("utf-8", "cp949", "euc-kr", "latin-1"):
                    try:
                        name = val.decode(enc)
                        break
                    except Exception:
                        pass
                if name:
                    break

        total_size = 0
        if b"files" in info:
            for fi in info[b"files"]:
                total_size += fi.get(b"length", 0)
        else:
            total_size = info.get(b"length", 0)

        return {
            "name": name or os.path.basename(torrent_path),
            "total_size": total_size,
        }
    except Exception as e:
        logger.warning(f"토렌트 메타데이터 파싱 예외 발생({torrent_path}): {e}")
        return {
            "name": os.path.basename(torrent_path),
            "total_size": 0,
        }


def parse_bytes_string(s: str) -> int:
    """aria2 형식의 용량 문자열(예: '120MiB', '5.7GiB', '520KiB', '0B')을 바이트 숫자로 변환합니다."""
    if not s or s == "0B":
        return 0
    units = {
        "b": 1,
        "k": 1024,
        "kib": 1024,
        "kb": 1000,
        "m": 1024**2,
        "mib": 1024**2,
        "mb": 1000**2,
        "g": 1024**3,
        "gib": 1024**3,
        "gb": 1000**3,
        "t": 1024**4,
        "tib": 1024**4,
        "tb": 1000**4,
    }
    m = re.match(r"^([0-9\.]+)\s*([A-Za-z]+)?$", s.strip())
    if not m:
        return 0
    val = float(m.group(1))
    unit = (m.group(2) or "b").lower()
    return int(val * units.get(unit, 1))


def parse_eta_string(s: str) -> Optional[int]:
    """'7m30s', '12s', '1h20m' 형식의 남은 시간을 초(seconds) 단위로 파싱합니다."""
    if not s:
        return None
    total_sec = 0
    for part in re.finditer(r"(\d+)([hms])", s.lower()):
        val = int(part.group(1))
        unit = part.group(2)
        if unit == "h":
            total_sec += val * 3600
        elif unit == "m":
            total_sec += val * 60
        elif unit == "s":
            total_sec += val
    return total_sec if total_sec > 0 else None


def parse_aria2_summary_line(line: str) -> Optional[dict]:
    """[#24ffa4 120MiB/5.7GiB(2%) CN:15 SD:5 DL:12.5MiB ETA:7m30s] 요약 라인 파싱."""
    if not line.startswith("[#") or "]" not in line:
        return None
    content = line[line.find("[") + 1 : line.find("]")]
    parts = content.split()
    if len(parts) < 2:
        return None

    size_part = parts[1]
    pct = None
    if "(" in size_part and size_part.endswith("%)"):
        s_part, p_part = size_part.split("(")
        try:
            pct = float(p_part[:-2])
        except Exception:
            pct = None
        size_part = s_part

    if "/" in size_part:
        dl_str, tot_str = size_part.split("/", 1)
    else:
        dl_str, tot_str = size_part, ""

    speed_str = "0B"
    eta_str = ""
    for p in parts[2:]:
        if p.startswith("DL:"):
            speed_str = p[3:]
        elif p.startswith("ETA:"):
            eta_str = p[4:]

    return {
        "dl_bytes": parse_bytes_string(dl_str),
        "total_bytes": parse_bytes_string(tot_str),
        "progress": pct,
        "speed_bps": float(parse_bytes_string(speed_str)),
        "eta_seconds": parse_eta_string(eta_str),
    }


class TorrentDownloadTask:
    """aria2c 백그라운드 프로세스를 구동하여 마그넷 및 .torrent 다운로드를 제어하는 태스크."""

    def __init__(
        self,
        source: str,
        dest_dir: str,
        filename: str = "",
        partial_id: str = "",
        progress_callback: Optional[Callable] = None,
    ):
        self.source = source
        self.dest_dir = dest_dir
        self.filename = filename or "Torrent Download"
        self.partial_id = partial_id
        self.progress_callback = progress_callback

        self._process: Optional[subprocess.Popen] = None
        self._is_stopped = False
        self._is_paused = False
        self._lock = threading.Lock()

    def pause(self):
        with self._lock:
            self._is_paused = True
            self._is_stopped = True
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                except Exception:
                    pass

    def stop(self):
        with self._lock:
            self._is_paused = False
            self._is_stopped = True
            if self._process and self._process.poll() is None:
                try:
                    self._process.terminate()
                except Exception:
                    pass

    def run(self) -> dict:
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
            "--seed-time=0",
            "--summary-interval=1",
            "--file-allocation=none",
            "--enable-dht=true",
            "--enable-peer-exchange=true",
            "--bt-enable-lpd=true",
            "--bt-max-peers=55",
            "--max-connection-per-server=16",
            "--split=16",
            "--console-log-level=notice",
            "--auto-file-renaming=false",
            "--allow-overwrite=true",
            "--",
            self.source,
        ]

        logger.info(
            f"토렌트 다운로드 시작 (aria2c): {self.filename} -> {self.dest_dir}"
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

        # 출력 스트림 모니터링
        try:
            for raw_line in iter(proc_stdout.readline, ""):
                if self._is_stopped:
                    with self._lock:
                        if self._process and self._process.poll() is None:
                            try:
                                self._process.terminate()
                            except Exception:
                                pass
                    break
                line = raw_line.strip()
                if not line:
                    continue

                # 1. 메타데이터/파일명 감지 (FILE: [MEMORY][METADATA]name 또는 FILE: name)
                if line.startswith("FILE:"):
                    clean_name = re.sub(
                        r"^FILE:\s*(\[MEMORY\])?(\[METADATA\])?", "", line
                    ).strip()
                    if clean_name and clean_name != self.filename:
                        self.filename = clean_name
                        logger.info(f"토렌트 파일명 감지: {self.filename}")

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
            logger.exception(f"토렌트 스트림 처리 중 오류: {e}")
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
                logger.info(f"토렌트 다운로드 일시중지됨: {self.filename}")
                return {
                    "success": False,
                    "status": "paused",
                    "message": "일시중지되었습니다.",
                    "downloaded_bytes": last_dl_bytes,
                    "total_bytes": last_tot_bytes,
                }
            else:
                logger.info(f"토렌트 다운로드 취소됨: {self.filename}")
                return {
                    "success": False,
                    "status": "cancelled",
                    "message": "다운로드가 취소되었습니다.",
                }

        if ret == 0:
            final_path = os.path.join(self.dest_dir, self.filename)
            # 만약 self.filename과 일치하지 않는 경우 dest_dir에서 최근 수정된 파일/폴더 확인
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

            if self.progress_callback:
                self.progress_callback(
                    downloaded_bytes=last_tot_bytes or last_dl_bytes,
                    total_bytes=last_tot_bytes or last_dl_bytes,
                    speed_bps=0.0,
                    progress=100.0,
                    eta_seconds=0,
                )

            logger.info(f"토렌트 다운로드 완료: {self.filename} -> {final_path}")
            return {
                "success": True,
                "status": "completed",
                "filename": self.filename,
                "file_path": final_path,
                "total_bytes": last_tot_bytes or last_dl_bytes,
            }
        else:
            logger.error(f"aria2c 프로세스가 오류 코드 {ret}로 종료되었습니다.")
            return {
                "success": False,
                "status": "failed",
                "error": f"aria2c 비트토렌트 다운로드 실패 (종료 코드 {ret})",
            }
