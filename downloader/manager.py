import atexit
import json
import logging
import math
import os
import queue
import tempfile
import threading
import time
import socket
import sys
import uuid
import requests
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import CONFIG_DIR

from .engine import (
    DownloadTask,
    format_bytes,
    format_eta,
    format_speed,
    sanitize_folder_name,
)
from .transfer_policy import HttpTransferPolicy
from .resolvers.dispatcher import default_dispatcher
from .url_helper import canonical_url_key, clean_url

logger = logging.getLogger(__name__)

HISTORY_FILE = CONFIG_DIR / "download_history.json"
MAX_HISTORY_ENTRIES = 5000

JOBS_FILE = CONFIG_DIR / "jobs.json"
MAX_JOBS_ENTRIES = 5000


class DownloadJob:
    """단일 다운로드 작업 객체."""

    def __init__(
        self,
        url: str,
        download_dir: str,
        options: dict | None = None,
        job_id: str | None = None,
    ):
        self.id = job_id or str(uuid.uuid4())
        self.original_url = clean_url(url)
        self.download_dir = download_dir
        self.options = options or {}

        # 상태: queued, resolving, downloading, paused, completed, failed, cancelled
        self.status = "queued"
        self.service_name = "Auto"
        self.resolved_url = ""
        self.filename = ""
        self.file_path = ""
        self.error_message = ""

        # 진행 지표
        self.downloaded_bytes = 0
        self.total_bytes: Optional[int] = None
        self.progress = 0.0  # 0.0 ~ 100.0 %
        self.speed_bps = 0.0
        self.speed_str = "0 KB/s"
        self.eta_seconds: Optional[int | float] = None
        self.eta_str = "--:--"

        # 자동/수동 다운로드 구분 (is_auto: True -> 자동, False -> 수동)
        self.is_auto: bool = bool(self.options.get("is_auto", False))
        self.download_type: str = "auto" if self.is_auto else "manual"

        # 시간
        self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.completed_at: Optional[str] = None

        # 출처 게시글 정보 (코네 등 자동 수집 시 글 제목/URL)
        self.referer: str = str(self.options.get("referer") or "").strip()
        self.source_title: str = str(
            self.options.get("source_title") or self.options.get("post_title") or ""
        ).strip()
        self.source_url: str = str(
            self.options.get("source_url")
            or self.options.get("post_url")
            or self.referer
            or ""
        ).strip()

        # 자동 재시도 제어 (대용량 다운로드 중단 대비 이어받기 재시도)
        self.retry_count: int = 0
        self.max_retries: int = 3

        # 내부 제어 객체
        self.task: Optional[Any] = None
        self.thread: Optional[threading.Thread] = None
        self._last_log_time: float = 0.0

    def to_dict(self) -> dict:
        """클라이언트 전달용 직렬화 딕셔너리 반환."""
        return {
            "id": self.id,
            "original_url": self.original_url,
            "status": self.status,
            "service_name": self.service_name,
            "filename": self.filename
            or self.original_url.split("?")[0].split("/")[-1]
            or "파일",
            "file_path": self.file_path,
            "download_dir": self.download_dir,
            "error_message": self.error_message,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "referer": self.referer,
            "subfolder": self.options.get("subfolder", ""),
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "downloaded_str": format_bytes(self.downloaded_bytes),
            "total_str": format_bytes(self.total_bytes),
            "progress": round(self.progress, 1),
            "speed_bps": self.speed_bps,
            "speed_str": self.speed_str,
            "eta_seconds": self.eta_seconds,
            "eta_str": self.eta_str,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "is_auto": self.is_auto,
            "download_type": self.download_type,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
        }

    def to_storage_dict(self) -> dict:
        """디스크 영구 저장(jobs.json)용 직렬화 딕셔너리 반환."""
        clean_opts = {}
        if self.options and isinstance(self.options, dict):
            for k, v in self.options.items():
                if str(k).startswith("_"):
                    continue
                if hasattr(v, "_mock_return_value") or hasattr(v, "mock_calls"):
                    continue
                if isinstance(v, (str, int, float, bool, list, dict)) or v is None:
                    clean_opts[k] = v
                else:
                    clean_opts[k] = str(v)

        return {
            "id": str(self.id),
            "original_url": str(self.original_url),
            "download_dir": str(self.download_dir or ""),
            "options": clean_opts,
            "status": str(self.status),
            "service_name": str(self.service_name),
            "filename": str(self.filename or ""),
            "file_path": str(self.file_path or ""),
            "error_message": str(self.error_message or ""),
            "downloaded_bytes": int(self.downloaded_bytes or 0),
            "total_bytes": int(self.total_bytes) if self.total_bytes is not None else None,
            "progress": round(float(self.progress or 0.0), 1),
            "is_auto": bool(self.is_auto),
            "download_type": str(self.download_type),
            "created_at": str(self.created_at),
            "completed_at": str(self.completed_at) if self.completed_at else None,
            "referer": str(self.referer or ""),
            "source_title": str(self.source_title or ""),
            "source_url": str(self.source_url or ""),
            "retry_count": int(self.retry_count or 0),
            "max_retries": int(self.max_retries or 3),
        }

    @classmethod
    def from_storage_dict(cls, data: dict) -> "DownloadJob":
        """디스크 저장 데이터로부터 DownloadJob 인스턴스를 복원합니다."""
        for key in ("id", "original_url", "download_dir"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"Invalid stored job {key}")
        if data.get("options") is not None and not isinstance(data["options"], dict):
            raise ValueError("Invalid stored job options")
        for key in ("downloaded_bytes", "retry_count", "max_retries", "total_bytes"):
            value = data.get(key)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"Invalid stored job {key}")
        progress = data.get("progress", 0.0)
        if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 100:
            raise ValueError("Invalid stored job progress")
        if data.get("status", "queued") not in {
            "queued", "resolving", "downloading", "paused", "completed", "failed", "cancelled"
        }:
            raise ValueError("Invalid stored job status")
        for key in ("service_name", "filename", "file_path", "error_message", "referer", "source_title", "source_url"):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"Invalid stored job {key}")
        job = cls(
            url=data.get("original_url", ""),
            download_dir=data.get("download_dir", ""),
            options=data.get("options") or {},
            job_id=data.get("id"),
        )
        job.status = data.get("status", "queued")
        job.service_name = data.get("service_name", "Auto")
        job.filename = data.get("filename", "")
        job.file_path = data.get("file_path", "")
        job.error_message = data.get("error_message", "")
        job.downloaded_bytes = data.get("downloaded_bytes", 0)
        job.total_bytes = data.get("total_bytes")
        job.progress = data.get("progress", 0.0)
        job.is_auto = bool(data.get("is_auto", False))
        job.download_type = data.get(
            "download_type", "auto" if job.is_auto else "manual"
        )
        job.created_at = data.get("created_at") or job.created_at
        job.completed_at = data.get("completed_at")
        job.referer = data.get("referer", "")
        job.source_title = data.get("source_title", "")
        job.source_url = data.get("source_url", "")
        job.retry_count = data.get("retry_count", 0)
        job.max_retries = data.get("max_retries", 3)
        return job


class DownloadManager:
    """다운로드 큐 및 작업 생명주기를 총괄하는 매니저."""

    def __init__(self, settings_getter, jobs_file: Optional[Path] = None):
        self.settings_getter = settings_getter
        if jobs_file is not None:
            self.jobs_file: Optional[Path] = jobs_file
            self._should_persist = True
        elif not ("unittest" in sys.modules or "pytest" in sys.modules):
            self.jobs_file = JOBS_FILE
            self._should_persist = True
        else:
            self.jobs_file = None
            self._should_persist = False
        self.jobs: Dict[str, DownloadJob] = {}
        self.queue = queue.Queue()
        self._lock = threading.RLock()
        self._listeners: List[queue.Queue] = []
        self._worker_threads = []
        self._stop_event = threading.Event()
        self._last_progress_save_time = 0.0
        self._completed_history: Dict[str, dict] = {}

        # 1. 완료 히스토리 로드
        self._load_history()

        # 2. 이전 작업 목록 복원 및 미완료 작업 자동 이어받기 대기열 등록
        self._load_jobs()

        # 3. 워커 스레드 초기화
        self._start_workers()

        # 4. 프로세스 정상 종료 시 상태 자동 보존 등록
        try:
            atexit.register(self.stop)
        except Exception:
            pass

    @staticmethod
    def _is_permanent_error(err_msg: str) -> bool:
        """재시도해도 성공할 수 없는 영구적인 오류인지 확인합니다."""
        if not err_msg:
            return False
        msg = str(err_msg).lower()
        permanent_keywords = (
            "404 not found",
            "not found",
            "파일을 찾을 수 없습니다",
            "존재하지 않는 파일",
            "비밀번호가 올바르지 않습니다",
            "wrong password",
            "incorrect password",
            "접근 권한이 없습니다",
            "사용자 요청으로 다운로드가 취소",
            "용량이 부족합니다",
            "disk full",
            "no space left",
            "웹페이지(html)",
            "turnstile",
        )
        return any(k in msg for k in permanent_keywords)

    @classmethod
    def _is_retryable_error(cls, exc: Any) -> bool:
        """다운로드 중단 시 자동 이어받기 재시도가 가능한 네트워크/서버 오류인지 확인합니다."""
        if exc is None:
            return True
        if cls._is_permanent_error(str(exc)):
            return False
        # 버그성 프로그래밍 예외는 재시도 대상에서 제외 (즉시 실패 처리)
        if isinstance(
            exc,
            (
                TypeError,
                KeyError,
                AttributeError,
                IndexError,
                NotImplementedError,
                RuntimeError,
            ),
        ):
            return False
        if isinstance(exc, (requests.exceptions.RequestException, socket.error, OSError)):
            return True
        err_str = str(exc).lower()
        retryable_keywords = (
            "connection",
            "timeout",
            "timed out",
            "reset",
            "closed",
            "abort",
            "broken pipe",
            "chunk",
            "incomplete",
            "disconnect",
            "network",
            "eof",
            "failed",
            "다운로드 실패",
            "500",
            "502",
            "503",
            "504",
            "403",
            "401",
            "410",
        )
        return any(k in err_str for k in retryable_keywords)

    def _load_history(self):
        """이전에 다운로드 완료된 URL 히스토리를 로드합니다."""
        is_testing = "unittest" in sys.modules or "pytest" in sys.modules
        if is_testing and HISTORY_FILE == CONFIG_DIR / "download_history.json":
            return
        with self._lock:
            if HISTORY_FILE.exists():
                try:
                    with open(HISTORY_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        data = {key: value for key, value in data.items() if isinstance(value, dict)}
                        for v in data.values():
                            if isinstance(v, dict):
                                if "is_auto" not in v:
                                    v["is_auto"] = False
                                if "download_type" not in v:
                                    v["download_type"] = (
                                        "auto" if v["is_auto"] else "manual"
                                    )
                        self._completed_history = data
                        logger.info(
                            "완료된 다운로드 히스토리 %d건 로드 완료",
                            len(self._completed_history),
                        )
                except Exception as e:
                    logger.warning("다운로드 히스토리 로드 실패: %s", e)
                    self._completed_history = {}

    def _save_history(self):
        """다운로드 히스토리를 원자적으로 파일에 저장합니다."""
        is_testing = "unittest" in sys.modules or "pytest" in sys.modules
        if is_testing and HISTORY_FILE == CONFIG_DIR / "download_history.json":
            return
        with self._lock:
            temp_path = None
            try:
                # 최대 엔트리 수 제한
                if len(self._completed_history) > MAX_HISTORY_ENTRIES:
                    keys_to_keep = list(self._completed_history.keys())[
                        -MAX_HISTORY_ENTRIES:
                    ]
                    self._completed_history = {
                        k: self._completed_history[k] for k in keys_to_keep
                    }

                HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=str(HISTORY_FILE.parent),
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temp_path = stream.name
                    json.dump(
                        self._completed_history, stream, indent=2, ensure_ascii=False
                    )
                os.replace(temp_path, HISTORY_FILE)
            except Exception as e:
                logger.warning("다운로드 히스토리 저장 실패: %s", e)
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def _load_jobs(self):
        """저장된 작업 목록(jobs.json)을 로드하고 미완료 작업은 이어받기 대기열로 복원합니다."""
        if not getattr(self, "_should_persist", True):
            return
        target_file = self.jobs_file or JOBS_FILE
        with self._lock:
            if not target_file or not target_file.exists():
                return

            try:
                with open(target_file, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    return

                resumed_count = 0
                for item in data:
                    if not isinstance(item, dict) or not item.get("id") or not item.get("original_url"):
                        continue
                    try:
                        job = DownloadJob.from_storage_dict(item)
                    except Exception as ex:
                        logger.warning(f"작업 데이터 복원 실패: {ex}")
                        continue

                    # 디스크에 .part 파일이 존재하는지 검사하여 실제 다운로드된 바이트 수 보정
                    part_path = self._find_part_path(job)
                    if part_path and os.path.exists(part_path):
                        actual_size = os.path.getsize(part_path)
                        job.downloaded_bytes = actual_size
                        if job.total_bytes and job.total_bytes > 0:
                            job.progress = min(
                                100.0, (actual_size / job.total_bytes) * 100.0
                            )

                    # 상태별 분기
                    if job.status in ("downloading", "resolving", "queued"):
                        # 이미 완료 히스토리에 기록되어 있는 작업이면 중복 다운로드 방지 -> completed로 보정
                        c_key = canonical_url_key(job.original_url)
                        if c_key and c_key in self._completed_history:
                            job.status = "completed"
                            job.speed_bps = 0.0
                            job.speed_str = "0 KB/s"
                            job.eta_seconds = None
                            job.eta_str = "완료"
                            job.task = None
                            job.thread = None
                            self.jobs[job.id] = job
                            logger.info(
                                f"서버 재시작 감지: 이미 완료된 작업 확인 (ID={job.id}, 파일명={job.filename or '미정'}) -> 대기열 복원 제외"
                            )
                            continue

                        # 서버 종료 시점에 진행 중이거나 대기 중이던 작업 -> 이어받기 자동 재개
                        job.status = "queued"
                        job.speed_bps = 0.0
                        job.speed_str = "0 KB/s"
                        job.eta_seconds = None
                        job.eta_str = "--:--"
                        job.task = None
                        job.thread = None
                        self.jobs[job.id] = job
                        self.queue.put(job.id)
                        resumed_count += 1
                        logger.info(
                            f"서버 재시작 감지: 미완료 작업 자동 이어받기 대기열 등록 (ID={job.id}, 파일명={job.filename or '미정'}, 기존 수신={format_bytes(job.downloaded_bytes)})"
                        )
                    else:
                        # completed, failed, cancelled, paused 상태 유지
                        job.speed_bps = 0.0
                        job.speed_str = "0 KB/s"
                        job.eta_seconds = None
                        job.eta_str = "--:--"
                        job.task = None
                        job.thread = None
                        self.jobs[job.id] = job

                        # completed 작업인 경우 완료 히스토리에도 동기화
                        if job.status == "completed":
                            c_key = canonical_url_key(job.original_url)
                            if c_key and c_key not in self._completed_history:
                                self._completed_history[c_key] = {
                                    "url": job.original_url,
                                    "canonical_key": c_key,
                                    "filename": job.filename,
                                    "file_path": job.file_path,
                                    "source_title": job.source_title,
                                    "source_url": job.source_url,
                                    "size": job.total_bytes or job.downloaded_bytes,
                                    "completed_at": job.completed_at
                                    or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    "is_auto": job.is_auto,
                                    "download_type": job.download_type,
                                }

                logger.info(
                    f"이전 작업 목록 {len(self.jobs)}개 복원 완료 (다운로드 자동 재개: {resumed_count}개)"
                )
            except Exception as e:
                logger.error(f"이전 작업 목록 로드 실패: {e}", exc_info=True)

    def _save_jobs(self):
        """현재 작업 목록을 jobs.json 파일에 안전하게 저장합니다."""
        if not getattr(self, "_should_persist", True):
            return
        target_file = self.jobs_file or JOBS_FILE
        if not target_file:
            return
        with self._lock:
            if len(self.jobs) > MAX_JOBS_ENTRIES:
                active_keys = {
                    k
                    for k, j in self.jobs.items()
                    if j.status in ("downloading", "resolving", "queued", "paused")
                }
                droppable_keys = [
                    k for k in self.jobs.keys() if k not in active_keys
                ]
                num_to_drop = len(self.jobs) - MAX_JOBS_ENTRIES
                if num_to_drop > 0:
                    drop_set = set(droppable_keys[:num_to_drop])
                    self.jobs = {k: v for k, v in self.jobs.items() if k not in drop_set}

            jobs_data = []
            for job in self.jobs.values():
                try:
                    jobs_data.append(job.to_storage_dict())
                except Exception:
                    pass
            temp_path = None
            try:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=str(target_file.parent),
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temp_path = stream.name
                    json.dump(jobs_data, stream, indent=2, ensure_ascii=False)
                os.replace(temp_path, target_file)
            except Exception as e:
                logger.warning(f"작업 목록 저장 실패: {e}")
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def stop(self):
        """매니저 종료 시 모든 워커 중지 및 최종 상태 파일 저장."""
        self._stop_event.set()
        with self._lock:
            for job in self.jobs.values():
                if job.status in ("downloading", "resolving") and job.task:
                    try:
                        job.task.pause()
                    except Exception:
                        pass
            self._save_jobs()
            self._save_history()

    def _record_completed_history(self, job: DownloadJob):
        """완료된 작업을 히스토리에 기록합니다."""
        canonical_key = canonical_url_key(job.original_url)
        if not canonical_key:
            return
        with self._lock:
            self._completed_history[canonical_key] = {
                "url": job.original_url,
                "canonical_key": canonical_key,
                "filename": job.filename,
                "file_path": job.file_path,
                "source_title": job.source_title,
                "source_url": job.source_url,
                "size": job.total_bytes or job.downloaded_bytes,
                "completed_at": job.completed_at
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_auto": job.is_auto,
                "download_type": job.download_type,
            }
            self._save_history()

    def is_duplicate_url(self, url: str) -> tuple[bool, str, dict | None]:
        """
        주어진 URL이 이미 대기열/다운로드 중이거나 완료된 내역이 있는지 검사합니다.
        반환값: (is_duplicate: bool, reason: str, info: dict | None)
        """
        canonical_key = canonical_url_key(url)
        if not canonical_key:
            return False, "", None

        with self._lock:
            # 1. 현재 메모리 상의 작업 큐 검사
            for job in self.jobs.values():
                if canonical_url_key(job.original_url) == canonical_key:
                    if job.status in ("queued", "resolving", "downloading", "paused"):
                        return (
                            True,
                            "이미 다운로드 대기 또는 진행 중인 링크입니다.",
                            job.to_dict(),
                        )
                    elif job.status == "completed":
                        return True, "이미 다운로드가 완료된 링크입니다.", job.to_dict()

            # 2. 영구 보관된 완료 다운로드 히스토리 검사
            if canonical_key in self._completed_history:
                hist_info = self._completed_history[canonical_key]
                return True, "이전에 이미 다운로드가 완료된 링크입니다.", hist_info

        return False, "", None

    def clear_history(self) -> int:
        """다운로드 완료 히스토리를 비웁니다."""
        with self._lock:
            count = len(self._completed_history)
            self._completed_history.clear()
            if HISTORY_FILE.exists():
                try:
                    os.remove(HISTORY_FILE)
                except OSError:
                    pass
            logger.info("다운로드 완료 히스토리 %d건 삭제됨", count)
            return count

    def get_history(self) -> list[dict]:
        """완료 히스토리 목록을 반환합니다."""
        with self._lock:
            return list(self._completed_history.values())

    def _start_workers(self):
        self.configure_workers(
            self.settings_getter().get("max_concurrent_downloads", 3)
        )

    def configure_workers(self, count):
        """Apply concurrency changes; running downloads finish before workers retire."""
        if type(count) is not int or not 1 <= count <= 32:
            raise ValueError("Worker count must be between 1 and 32")
        with self._lock:
            self._max_workers = count
            for index in range(len(self._worker_threads), count):
                worker = threading.Thread(
                    target=self._worker_loop,
                    args=(index,),
                    name=f"DownloaderWorker-{index}",
                    daemon=True,
                )
                self._worker_threads.append(worker)
                worker.start()

    def subscribe(self) -> queue.Queue:
        """SSE 클라이언트를 위한 이벤트 큐를 등록합니다."""
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        """SSE 클라이언트 연결 해제 시 큐를 제거합니다."""
        with self._lock:
            if q in self._listeners:
                self._listeners.remove(q)

    def _broadcast(self, event_type: str, data: dict | None = None):
        """모든 연결된 SSE 클라이언트에게 이벤트를 푸시합니다."""
        payload = {
            "type": event_type,
            "timestamp": time.time(),
            "data": data or {},
        }
        with self._lock:
            for q in list(self._listeners):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    pass

    def add_url(
        self,
        url: str,
        filename: str = "",
        options: dict | None = None,
        subfolder: str = "",
        skip_duplicate: bool = False,
        register_duplicate_as_failed: bool = False,
        is_auto: bool = False,
    ) -> DownloadJob | None:
        """새로운 다운로드 링크를 큐에 추가합니다 (skip_duplicate=True 시 중복 검사)."""
        url = clean_url(url)
        if not url:
            raise ValueError("A valid download URL is required")

        settings = self.settings_getter()
        should_check_dup = is_auto or (
            skip_duplicate and bool(settings.get("check_duplicate_url", True))
        )

        download_dir = settings.get("download_dir")

        job_options = dict(settings)
        if options:
            job_options.update(options)
        if is_auto:
            job_options["is_auto"] = True
        elif "is_auto" not in job_options:
            job_options["is_auto"] = False
        if subfolder:
            job_options["subfolder"] = subfolder

        job = DownloadJob(url=url, download_dir=download_dir, options=job_options)
        if filename:
            job.filename = filename
        with self._lock:
            if should_check_dup:
                is_dup, reason, _ = self.is_duplicate_url(url)
                if is_dup:
                    logger.info("중복 다운로드 링크 자동 제외: %s (%s)", url, reason)
                    if not register_duplicate_as_failed:
                        return None
                    job.status = "failed"
                    job.error_message = f"중복 다운로드 링크 자동 제외 ({reason})"
                    job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    job.speed_bps = 0.0
                    job.speed_str = "0 KB/s"
                    self.jobs[job.id] = job
                    self._save_jobs()
                    self._broadcast("job_added", job.to_dict())
                    self._broadcast("job_updated", job.to_dict())
                    self._broadcast("stats_updated", self.get_stats())
                    return job
            self.jobs[job.id] = job
            self.queue.put(job.id)
            self._save_jobs()
            self._broadcast("job_added", job.to_dict())

        logger.info(
            f"작업 추가됨: ID={job.id}, URL={url}, 파일명={filename}, 서브폴더={subfolder}"
        )
        return job

    def add_urls(
        self,
        urls: List,
        subfolder: str = "",
        force: bool = False,
        register_duplicate_as_failed: bool = False,
        is_auto: bool = False,
    ) -> List[DownloadJob]:
        """여러 URL을 일괄 추가합니다 (force=False 시 중복 링크는 자동 제외)."""
        check_dup = not force and (
            is_auto or bool(self.settings_getter().get("check_duplicate_url", True))
        )
        added = []
        for item in urls:
            if isinstance(item, dict):
                u = item.get("url", "").strip()
                fn = item.get("name") or item.get("filename") or ""
                opt = dict(item.get("options") or {})
                if is_auto and "is_auto" not in opt:
                    opt["is_auto"] = True
                sf = item.get("subfolder") or subfolder
                if u and not u.startswith("#"):
                    job = self.add_url(
                        u,
                        filename=fn,
                        options=opt,
                        subfolder=sf,
                        skip_duplicate=check_dup,
                        register_duplicate_as_failed=register_duplicate_as_failed,
                        is_auto=opt.get("is_auto", is_auto),
                    )
                    if job is not None:
                        added.append(job)
            elif isinstance(item, str):
                u_clean = item.strip()
                if u_clean and not u_clean.startswith("#"):
                    job = self.add_url(
                        u_clean,
                        subfolder=subfolder,
                        skip_duplicate=check_dup,
                        register_duplicate_as_failed=register_duplicate_as_failed,
                        is_auto=is_auto,
                    )
                    if job is not None:
                        added.append(job)
        return added

    def pause_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if job.status == "downloading" and job.task:
                job.task.pause()
                job.status = "paused"
                self._save_jobs()
                self._broadcast("job_updated", job.to_dict())
                return True
            elif job.status == "queued":
                job.status = "paused"
                job.error_message = ""
                self._save_jobs()
                self._broadcast("job_updated", job.to_dict())
                return True
        return False

    def resume_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if (
                not job
                or job.status != "paused"
                or job.task is not None
                or job.thread is not None
            ):
                return False
            job.status = "queued"
            job.error_message = ""
            self.queue.put(job.id)
            self._save_jobs()
            self._broadcast("job_updated", job.to_dict())
            return True

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if job.task:
                job.task.stop()
            job.status = "cancelled"
            job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            job.speed_bps = 0.0
            job.speed_str = "0 KB/s"
            self._save_jobs()
            self._broadcast("job_updated", job.to_dict())
            return True

    def _find_part_path(self, job: DownloadJob) -> Optional[str]:
        """작업의 임시 .part 파일 경로를 찾습니다."""
        if not job.download_dir or not os.path.isdir(job.download_dir):
            return None
        # 1. job.id 기반 part 파일 탐색 (폴더명에 [], () 등 특수문자가 포함되어도 안전하도록 os.listdir 사용)
        target_suffix = f".{job.id}.part"
        try:
            for fname in os.listdir(job.download_dir):
                if fname.endswith(target_suffix):
                    full_p = os.path.join(job.download_dir, fname)
                    if os.path.isfile(full_p):
                        return full_p
        except OSError:
            pass
        return None

    def _cleanup_part_file(self, job: DownloadJob):
        """사용자가 작업을 삭제하거나 목록을 비울 때 임시 .part 파일을 삭제합니다."""
        try:
            part_path = self._find_part_path(job)
            if part_path and os.path.exists(part_path):
                os.remove(part_path)
                logger.info("임시 .part 파일 정리 완료: %s", part_path)
            from .aria2_http_engine import Aria2HttpDownloadTask

            Aria2HttpDownloadTask.cleanup_partial(job.download_dir, job.id)
        except Exception as e:
            logger.warning("임시 .part 파일 정리 중 오류: %s", e)

    def retry_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if (
                not job
                or job.status not in ("failed", "cancelled", "completed")
                or job.task is not None
                or job.thread is not None
            ):
                return False

            # 재시도 시 영구 완료 내역에서 제거하여 중복 차단 방지
            c_key = canonical_url_key(job.original_url)
            if c_key and c_key in self._completed_history:
                del self._completed_history[c_key]
                self._save_history()

            job.status = "queued"
            job.error_message = ""
            job.retry_count = 0
            job.completed_at = None
            job.speed_bps = 0.0
            job.speed_str = "0 KB/s"

            # .part 파일이 디스크에 남아있다면 크기를 확인하여 진행도 유지 (이어받기 준비)
            part_path = self._find_part_path(job)
            if part_path and os.path.exists(part_path):
                part_size = os.path.getsize(part_path)
                job.downloaded_bytes = part_size
                if job.total_bytes and job.total_bytes > 0:
                    job.progress = min(100.0, (part_size / job.total_bytes) * 100.0)
                logger.info(
                    "작업 %s 재시도: 기존 .part 파일(%d bytes) 감지하여 이어받기 대기",
                    job.id,
                    part_size,
                )
            else:
                job.downloaded_bytes = 0
                job.progress = 0.0

            self.queue.put(job.id)
            self._save_jobs()
            self._broadcast("job_updated", job.to_dict())
            self._broadcast("stats_updated", self.get_stats())
            return True

    def remove_job(self, job_id: str) -> bool:
        with self._lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if job.task:
                job.task.stop()
            self._cleanup_part_file(job)
            del self.jobs[job_id]
            self._save_jobs()
            self._broadcast("job_removed", {"id": job_id})
            return True

    def clear_jobs(self, status_filter: str = "finished") -> int:
        """완료, 실패 또는 전체 작업들을 일괄 삭제합니다.

        status_filter:
          - 'completed': 완료된 작업만 일괄 삭제
          - 'failed': 실패 및 취소된 작업만 일괄 삭제
          - 'finished': 완료 및 실패/취소된 모든 작업 삭제
          - 'all': 전체 작업 일괄 삭제 (진행 중인 작업은 중지 후 삭제)
        """
        with self._lock:
            to_delete = []
            for job_id, job in self.jobs.items():
                if status_filter == "completed":
                    if job.status == "completed":
                        to_delete.append(job_id)
                elif status_filter == "failed":
                    if job.status in ("failed", "cancelled"):
                        to_delete.append(job_id)
                elif status_filter == "finished":
                    if job.status in ("completed", "failed", "cancelled"):
                        to_delete.append(job_id)
                elif status_filter == "all":
                    if job.task:
                        job.task.stop()
                    to_delete.append(job_id)

            for jid in to_delete:
                job = self.jobs.get(jid)
                if job:
                    if job.task:
                        job.task.stop()
                    self._cleanup_part_file(job)
                del self.jobs[jid]
                self._broadcast("job_removed", {"id": jid})

            if to_delete:
                self._save_jobs()
                self._broadcast("stats_updated", self.get_stats())
            return len(to_delete)

    def get_job(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self.jobs.get(job_id)
            return job.to_dict() if job else None

    def get_all_jobs(self) -> List[dict]:
        with self._lock:
            # 등록 역순(최신 등록이 위로) 반환
            return [j.to_dict() for j in reversed(list(self.jobs.values()))]

    def get_stats(self) -> dict:
        with self._lock:
            total_count = len(self.jobs)
            downloading_count = sum(
                1 for j in self.jobs.values() if j.status == "downloading"
            )
            completed_count = sum(
                1 for j in self.jobs.values() if j.status == "completed"
            )
            failed_count = sum(
                1 for j in self.jobs.values() if j.status in ("failed", "cancelled")
            )
            total_speed = sum(
                j.speed_bps for j in self.jobs.values() if j.status == "downloading"
            )
            total_downloaded = sum(j.downloaded_bytes for j in self.jobs.values())

            return {
                "total_count": total_count,
                "downloading_count": downloading_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "total_speed_str": format_speed(total_speed),
                "total_speed_bps": total_speed,
                "total_downloaded_str": format_bytes(total_downloaded),
            }

    def _worker_loop(self, worker_index=0):
        """백그라운드에서 큐를 감시하며 작업을 수행하는 워커 루프."""
        while not self._stop_event.is_set():
            if worker_index >= self._max_workers:
                self._stop_event.wait(0.5)
                continue
            try:
                job_id = self.queue.get(timeout=1.0)
            except queue.Empty:
                continue

            with self._lock:
                job = self.jobs.get(job_id)
                if not job or job.status != "queued":
                    self.queue.task_done()
                    continue
                job.thread = threading.current_thread()
                job.status = "resolving"
                job.error_message = ""
                self._save_jobs()
                self._broadcast("job_updated", job.to_dict())

            # 1. 링크 해석 (Resolving)
            try:
                settings = self.settings_getter()
                resolve_opts = dict(settings)
                if job.options:
                    resolve_opts.update(job.options)
                if job.filename:
                    resolve_opts["filename"] = job.filename

                pre_resolved = (
                    job.options.pop("_pre_resolved", None) if job.options else None
                )
                resolved = pre_resolved or default_dispatcher.resolve(
                    job.original_url, options=resolve_opts
                )
                with self._lock:
                    if (
                        self._stop_event.is_set()
                        or self.jobs.get(job_id) is not job
                        or job.status != "resolving"
                    ):
                        job.thread = None
                        self.queue.task_done()
                        continue

                # 클라우드 저장 작업인 경우 (예: 테라박스 내 계정에 담기) 로컬 다운로드 스킵 후 즉시 완료 처리
                if resolved.get("is_cloud_transfer"):
                    with self._lock:
                        if (
                            self.jobs.get(job_id) is not job
                            or job.status != "resolving"
                        ):
                            job.thread = None
                            self.queue.task_done()
                            continue
                        job.service_name = resolved.get(
                            "source_service", "TeraBox (클라우드 저장)"
                        )
                        job.status = "completed"
                        job.filename = resolved.get("filename", job.filename)
                        job.file_path = resolved.get(
                            "cloud_path", "내 테라박스 클라우드"
                        )
                        size_b = resolved.get("size", 0)
                        job.downloaded_bytes = size_b
                        job.total_bytes = size_b
                        job.progress = 100.0
                        job.speed_bps = 0.0
                        job.speed_str = "완료"
                        job.eta_str = "저장 완료"
                        job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        self._record_completed_history(job)
                        self._save_jobs()
                        self._broadcast("job_updated", job.to_dict())
                    logger.info(
                        f"작업 {job.id} 테라박스 클라우드 저장 완료: {job.filename} -> {job.file_path}"
                    )
                    job.thread = None
                    self.queue.task_done()
                    continue

                # 서브폴더 결정 및 격리 적용
                target_subfolder = ""
                if not job.options.get("subfolder_applied"):
                    user_subfolder = job.options.get("subfolder", "").strip()
                    auto_subfolder = settings.get("auto_subfolder", True)
                    rule = settings.get("subfolder_rule", "package_or_title")

                    if user_subfolder:
                        target_subfolder = sanitize_folder_name(user_subfolder)
                    elif (
                        auto_subfolder
                        and rule == "package_or_title"
                        and (resolved.get("subfolder") or resolved.get("article_title"))
                    ):
                        target_subfolder = sanitize_folder_name(
                            resolved.get("subfolder")
                            or resolved.get("article_title")
                            or ""
                        )
                    elif auto_subfolder and rule == "date":
                        target_subfolder = datetime.now().strftime("%Y-%m-%d")
                    elif auto_subfolder and rule == "service":
                        target_subfolder = sanitize_folder_name(
                            str(resolved.get("source_service") or "Direct")
                        )

                    if target_subfolder:
                        job.download_dir = os.path.join(
                            job.download_dir, target_subfolder
                        )
                        job.options["subfolder_applied"] = True
                        os.makedirs(job.download_dir, exist_ok=True)
                        logger.info(
                            f"작업 {job.id} 저장 서브폴더 적용: {job.download_dir}"
                        )

                should_capture = (
                    bool(job.options.get("capture_page"))
                    if "capture_page" in (job.options or {})
                    else bool(settings.get("capture_source_page", False))
                )
                if should_capture and not job.options.get("_page_captured"):
                    job.options["_page_captured"] = True
                    capture_target_url = str(
                        resolved.get("source_page_url")
                        or (job.options or {}).get("referer")
                        or job.original_url
                        or ""
                    ).strip()
                    capture_title = str(
                        resolved.get("article_title")
                        or resolved.get("subfolder")
                        or target_subfolder
                        or os.path.splitext(
                            resolved.get("filename") or job.filename or "page"
                        )[0]
                    ).strip()
                    if capture_target_url.lower().startswith(("http://", "https://")):
                        from .browser_solver import capture_full_page_screenshot

                        threading.Thread(
                            target=capture_full_page_screenshot,
                            args=(capture_target_url, job.download_dir, capture_title),
                            name=f"PageCapture-{job.id[:8]}",
                            daemon=True,
                        ).start()

                with self._lock:
                    if (
                        self._stop_event.is_set()
                        or self.jobs.get(job_id) is not job
                        or job.status != "resolving"
                    ):
                        job.thread = None
                        self.queue.task_done()
                        continue
                    job.service_name = resolved.get("source_service", "Direct")
                    job.resolved_url = resolved["url"]
                    if resolved.get("filename"):
                        job.filename = resolved["filename"]
                    resolved_source_title = str(
                        resolved.get("article_title") or ""
                    ).strip()
                    resolved_source_url = str(
                        resolved.get("source_page_url") or ""
                    ).strip()
                    if resolved_source_title and not job.source_title:
                        job.source_title = resolved_source_title
                        job.options["source_title"] = resolved_source_title
                    if resolved_source_url and not job.source_url:
                        job.source_url = resolved_source_url
                        job.options["source_url"] = resolved_source_url
                    if resolved.get("options"):
                        job.options.update(resolved["options"])
                    if resolved.get("method"):
                        job.options["method"] = resolved["method"]
                    if resolved.get("post_data"):
                        job.options["post_data"] = resolved["post_data"]
                    for key in ("allow_parallel", "max_connections"):
                        if key in resolved:
                            job.options[key] = resolved[key]
                    job.status = "downloading"
                    job.error_message = ""
                    self._broadcast("job_updated", job.to_dict())

                # 다중 첨부파일이 발견된 경우 (예: 네이버 카페 / 티스토리 분할 압축 등) 나머지 파일들도 큐에 자동 등록
                extra_files = resolved.get("files", [])
                if len(extra_files) > 1:
                    logger.info(
                        f"다중 첨부파일 감지: 추가 {len(extra_files) - 1}개 파일을 큐에 자동 등록합니다 (폴더: {job.download_dir})."
                    )
                    for extra in extra_files[1:]:
                        extra_opts = dict(job.options)
                        for key in ("headers", "cookies", "referer", "allow_parallel", "max_connections"):
                            if key in resolved:
                                extra_opts[key] = resolved[key]
                        extra_opts["subfolder_applied"] = True
                        if "referer" in extra:
                            extra_opts["referer"] = extra["referer"]
                        if "headers" in extra:
                            extra_opts["headers"] = extra["headers"]
                        if "cookies" in extra:
                            extra_opts["cookies"] = extra["cookies"]
                        new_job = DownloadJob(
                            url=extra["url"],
                            download_dir=job.download_dir,
                            options=extra_opts,
                        )
                        new_job.filename = extra.get("name", "")
                        new_job.resolved_url = extra["url"]
                        new_job.service_name = resolved.get("source_service", "Direct")
                        with self._lock:
                            if (
                                self.jobs.get(job_id) is not job
                                or job.status != "downloading"
                            ):
                                break
                            self.jobs[new_job.id] = new_job
                            self._broadcast("job_added", new_job.to_dict())
                            self.queue.put(new_job.id)

                for extra in resolved.get("extra_jobs", []):
                    with self._lock:
                        if (
                            self.jobs.get(job_id) is not job
                            or job.status != "downloading"
                        ):
                            break
                        extra_opts = dict(job.options)
                        extra_opts.update(extra.get("options", {}))
                        extra_opts["subfolder_applied"] = True
                        new_job = DownloadJob(
                            extra["url"], job.download_dir, extra_opts
                        )
                        new_job.filename = extra.get("filename", "")
                        self.jobs[new_job.id] = new_job
                        self._broadcast("job_added", new_job.to_dict())
                        self.queue.put(new_job.id)

            except Exception as e:
                logger.exception(f"작업 {job_id} 링크 분석 실패: {e}")
                with self._lock:
                    if self.jobs.get(job_id) is job and job.status in (
                        "resolving",
                        "downloading",
                    ):
                        job.status = "failed"
                        job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        job.error_message = f"링크 분석 실패: {e}"
                        self._save_jobs()
                        self._broadcast("job_updated", job.to_dict())
                job.thread = None
                self.queue.task_done()
                continue

            # 2. 다운로드 수행 (Downloading)
            def on_progress(
                downloaded_bytes, total_bytes, speed_bps, progress, eta_seconds
            ):
                if job is None:
                    return
                had_error = False
                with self._lock:
                    if self._stop_event.is_set() or self.jobs.get(job_id) is not job or job.status != "downloading":
                        return
                    if job.error_message:
                        job.error_message = ""
                        had_error = True
                    job.downloaded_bytes = downloaded_bytes
                    job.total_bytes = total_bytes
                    job.speed_bps = speed_bps
                    job.speed_str = format_speed(speed_bps)
                    job.progress = progress
                    job.eta_seconds = eta_seconds
                    job.eta_str = format_eta(eta_seconds)

                    now = time.time()
                    last_log = getattr(job, "_last_log_time", 0.0)
                    if now - last_log >= 30.0:
                        job._last_log_time = now
                        logger.info(
                            "작업 %s 진행 중: %s (%s / %s, %.1f%%, %s, 남은시간: %s)",
                            job.id[:8],
                            job.filename,
                            format_bytes(downloaded_bytes),
                            format_bytes(total_bytes),
                            progress,
                            job.speed_str,
                            job.eta_str,
                        )
                    # Each checkpoint serializes every job. Share the interval
                    # across workers to avoid N full writes every 15 seconds.
                    checkpoint_time = time.monotonic()
                    if checkpoint_time - self._last_progress_save_time >= 15.0:
                        self._last_progress_save_time = checkpoint_time
                        self._save_jobs()

                if had_error:
                    self._broadcast("job_updated", job.to_dict())

                self._broadcast(
                    "job_progress",
                    {
                        "id": job.id,
                        "error_message": "",
                        "downloaded_bytes": downloaded_bytes,
                        "total_bytes": total_bytes,
                        "downloaded_str": format_bytes(downloaded_bytes),
                        "total_str": format_bytes(total_bytes),
                        "speed_bps": speed_bps,
                        "speed_str": job.speed_str,
                        "progress": round(progress, 1),
                        "eta_str": job.eta_str,
                    },
                )

            def on_status(msg: str):
                if job is None:
                    return
                with self._lock:
                    if self.jobs.get(job_id) is not job or job.status != "downloading":
                        return
                    job.error_message = msg
                self._broadcast(
                    "job_progress",
                    {
                        "id": job.id,
                        "error_message": msg,
                        "downloaded_bytes": job.downloaded_bytes,
                        "total_bytes": job.total_bytes,
                        "downloaded_str": format_bytes(job.downloaded_bytes),
                        "total_str": format_bytes(job.total_bytes),
                        "speed_bps": 0.0,
                        "speed_str": "0 KB/s",
                        "progress": round(job.progress, 1),
                        "eta_str": job.eta_str,
                    },
                )


            task_exc = None
            use_aria2 = False
            try:
                if resolved.get("task_factory"):
                    task = resolved["task_factory"](job, job.download_dir, on_progress)
                else:
                    from .addons import addon_registry

                    addon = addon_registry.find_task_addon(job)
                    if addon:
                        task = addon.restore_task(job, on_progress, settings)
                    else:
                        policy = HttpTransferPolicy.from_job(job, resolved, settings)
                        use_aria2 = policy.use_aria2

                        if use_aria2:
                            from .aria2_http_engine import Aria2HttpDownloadTask

                            task = Aria2HttpDownloadTask(
                                **policy.request_kwargs,
                                split=policy.split,
                                progress_callback=on_progress,
                                status_callback=on_status,
                            )
                        else:
                            task = DownloadTask(
                                **policy.standard_kwargs,
                                progress_callback=on_progress,
                                status_callback=on_status,
                            )
                with self._lock:
                    if self._stop_event.is_set() or self.jobs.get(job_id) is not job or job.status != "downloading":
                        job.thread = None
                        self.queue.task_done()
                        continue
                    job.task = task
                    self._save_jobs()
                result = task.run()
                if not isinstance(result, dict):
                    raise ValueError("Download task returned an invalid result")

                # aria2c 고속 분할 다운로드 실패 시 표준 DownloadTask로 자동 전환 (Fallback)
                if (
                    use_aria2
                    and result.get("status") == "failed"
                    and not getattr(task, "_is_stopped", False)
                ):
                    logger.warning(
                        f"[Aria2HTTP] 고속 분할 다운로드 실패 ({result.get('error')}). "
                        f"파이썬 내장 다운로드 엔진으로 자동 전환(Fallback)합니다: {job.filename or job.resolved_url}"
                    )
                    on_status("고속 분할 다운로드 실패 -> 표준 엔진으로 자동 전환 중...")
                    fallback_task = DownloadTask(
                        **policy.standard_kwargs,
                        progress_callback=on_progress,
                        status_callback=on_status,
                    )
                    with self._lock:
                        if self.jobs.get(job_id) is not job or job.status != "downloading":
                            job.thread = None
                            self.queue.task_done()
                            continue
                        job.task = fallback_task
                    result = fallback_task.run()
                    if isinstance(result, dict) and result.get("success"):
                        from .aria2_http_engine import Aria2HttpDownloadTask

                        try:
                            Aria2HttpDownloadTask.cleanup_partial(job.download_dir, job.id)
                        except OSError as cleanup_error:
                            logger.warning("aria2 임시 파일 정리 실패: %s", cleanup_error)
                if not isinstance(result, dict):
                    raise ValueError("Download task returned an invalid result")
                # DownloadTask reports failures as results rather than exceptions.
                if result.get("status") == "failed" and "웹페이지(HTML)" in result.get(
                    "error", ""
                ):
                    raise ValueError(result["error"])
            except Exception as exc:
                task_exc = exc
                if (
                    "웹페이지(HTML)" in str(exc)
                    and resolved.get("source_service") == "Direct"
                    and not job.options.get("_webpage_fallback_tried")
                ):
                    try:
                        from .resolvers.webpage import WebPageResolver

                        wp_resolved = WebPageResolver().resolve(
                            job.original_url, options=resolve_opts
                        )
                        if wp_resolved and wp_resolved.get("url"):
                            logger.info(
                                "웹페이지(%s)에서 다운로드 링크 자동 검출 성공 -> 작업 재큐잉",
                                job.original_url,
                            )
                            with self._lock:
                                if (
                                    self.jobs.get(job_id) is not job
                                    or job.status != "downloading"
                                ):
                                    job.task = None
                                    job.thread = None
                                    self.queue.task_done()
                                    continue
                                job.options["_webpage_fallback_tried"] = True
                                job.options["_pre_resolved"] = wp_resolved
                                job.status = "queued"
                                job.task = None
                                job.thread = None
                                self._save_jobs()
                                self._broadcast("job_updated", job.to_dict())
                                self.queue.put(job.id)
                            self.queue.task_done()
                            continue
                    except Exception as wp_err:
                        logger.warning(
                            "웹페이지 내 다운로드 링크 검출 실패: %s", wp_err
                        )
                logger.exception("작업 %s 다운로드 실패", job_id)
                part_size = 0
                p_file = self._find_part_path(job)
                if p_file and os.path.exists(p_file):
                    try:
                        part_size = os.path.getsize(p_file)
                    except OSError:
                        pass
                result = {
                    "status": "failed",
                    "error": str(exc),
                    "downloaded_bytes": part_size or job.downloaded_bytes,
                    "total_bytes": job.total_bytes,
                }

            with self._lock:
                if self.jobs.get(job_id) is not job or job.status == "cancelled":
                    job.task = None
                    job.thread = None
                    self.queue.task_done()
                    continue
                if job.task and getattr(job.task, "filename", ""):
                    job.filename = job.task.filename
                final_status = result.get("status")
                # A late transfer error must not undo an explicit pause.
                if job.status == "paused" and final_status != "completed":
                    final_status = "paused"
                if final_status == "completed":
                    job.status = "completed"
                    job.filename = str(result.get("filename") or job.filename)
                    job.file_path = str(result.get("file_path") or "")
                    job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    job.speed_bps = 0.0
                    job.speed_str = "0 KB/s"
                    job.eta_str = "완료"
                    self._record_completed_history(job)
                elif final_status == "paused":
                    job.status = "paused"
                    job.speed_bps = 0.0
                    job.speed_str = "0 KB/s"
                elif final_status == "cancelled":
                    job.status = "cancelled"
                    job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    job.speed_bps = 0.0
                    job.speed_str = "0 KB/s"
                else:
                    err_msg = str(result.get("error") or "다운로드 실패")
                    is_retryable = self._is_retryable_error(task_exc if task_exc is not None else err_msg)
                    can_retry = (
                        is_retryable
                        and job.status not in ("cancelled", "paused")
                        and job.retry_count < job.max_retries
                        and not self._stop_event.is_set()
                    )

                    if can_retry:
                        job.retry_count += 1
                        delay_sec = min(3 * job.retry_count, 15)
                        job.status = "queued"
                        job.error_message = (
                            f"다운로드 중단: {delay_sec}초 후 이어받기 재시도 ({job.retry_count}/{job.max_retries})"
                        )
                        job.speed_bps = 0.0
                        job.speed_str = "0 KB/s"
                        # 이전 만료되었을 수 있는 직링크 정보 정리하여 리졸버 재호출 유도
                        job.resolved_url = ""
                        job.options.pop("_pre_resolved", None)

                        # 디스크의 .part 크기를 현재 다운로드 진행도로 갱신
                        p_file = self._find_part_path(job)
                        if p_file and os.path.exists(p_file):
                            try:
                                part_size = os.path.getsize(p_file)
                                job.downloaded_bytes = part_size
                                if job.total_bytes and job.total_bytes > 0:
                                    job.progress = min(
                                        100.0, (part_size / job.total_bytes) * 100.0
                                    )
                            except OSError:
                                pass

                        logger.warning(
                            "작업 %s 다운로드 중단 (%s). %d초 후 이어받기 재시도 (%d/%d)...",
                            job.id,
                            err_msg,
                            delay_sec,
                            job.retry_count,
                            job.max_retries,
                        )

                        job.task = None
                        job.thread = None
                        self._save_jobs()
                        self._broadcast("job_updated", job.to_dict())

                        def _requeue_timer(j_id):
                            with self._lock:
                                j = self.jobs.get(j_id)
                                if (
                                    not j
                                    or j.status != "queued"
                                    or self._stop_event.is_set()
                                ):
                                    return
                                logger.info(
                                    "작업 %s 이어받기 재시도 큐 진입 (시도 %d/%d)",
                                    j.id,
                                    j.retry_count,
                                    j.max_retries,
                                )
                                self.queue.put(j.id)
                                self._broadcast("job_updated", j.to_dict())

                        timer = threading.Timer(
                            float(delay_sec), _requeue_timer, args=[job.id]
                        )
                        timer.daemon = True
                        timer.start()

                        self.queue.task_done()
                        continue
                    else:
                        job.status = "failed"
                        job.completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        job.error_message = err_msg
                        job.speed_bps = 0.0
                        job.speed_str = "0 KB/s"
                        if (
                            "downloaded_bytes" in result
                            and result["downloaded_bytes"] is not None
                        ):
                            job.downloaded_bytes = int(result["downloaded_bytes"])
                        if "total_bytes" in result and result["total_bytes"] is not None:
                            job.total_bytes = int(result["total_bytes"])
                        if job.total_bytes and job.total_bytes > 0:
                            job.progress = min(
                                100.0, (job.downloaded_bytes / job.total_bytes) * 100.0
                            )

                job.task = None
                job.thread = None
                self._save_jobs()
                self._broadcast("job_updated", job.to_dict())

            self.queue.task_done()
