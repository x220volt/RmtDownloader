"""Kone.gg 게시판 자동 감시 및 신규 게시글 다운로드 링크 자동 수집 모듈."""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import CONFIG_DIR
from downloader.engine import fix_mojibake, sanitize_folder_name
from downloader.url_helper import (
    decode_base64_string,
    is_shortened_url,
    parse_download_candidates,
    unshorten_url,
)

logger = logging.getLogger("remote_downloader.kone_watcher")

SEEN_POSTS_FILE = CONFIG_DIR / "kone_seen_posts.json"
KONE_STATE_FILE = CONFIG_DIR / "kone_watcher_state.json"
MAX_SEEN_CACHE = 1000

# Base64 인코딩된 URL(http, https, magnet) 패턴
B64_URL_REGEX = re.compile(
    r"(?<![a-zA-Z0-9/])(?:(?:aHR0c|bWFnbmV0)[A-Za-z0-9+/_-]{4,}={0,2}|[A-Za-z0-9+/_-]{12,}={1,2})(?![a-zA-Z0-9/])"
)


def extract_kone_ssr_strings(raw_bytes: bytes) -> list[str]:
    """Kone.gg SSR 바이너리 페이로드(0x31 길이 접두사 TLV 포맷)에서 문자열들을 정확히 추출합니다.

    다음 필드의 타입 태그 바이트 0x31('1')이 URL이나 Base64 문자열 끝에 붙는 현상을 원천 방지합니다.
    """
    strings: list[str] = []
    seen_s: set[str] = set()
    i = 0
    n = len(raw_bytes)
    while i < n:
        if raw_bytes[i] == 0x31 and i + 1 < n:
            b0 = raw_bytes[i + 1]
            if b0 < 0x80:
                length = b0
                hdr = 2
            elif i + 2 < n:
                length = 128 + ((b0 & 0x7F) << 7) + raw_bytes[i + 2]
                hdr = 3
            else:
                length = -1
                hdr = 2
            if 0 < length <= n - (i + hdr):
                chunk = raw_bytes[i + hdr : i + hdr + length]
                try:
                    s = chunk.decode("utf-8").strip()
                    if s and s not in seen_s:
                        seen_s.add(s)
                        strings.append(s)
                    i += hdr + length
                    continue
                except Exception:
                    pass
        i += 1

    # 폴백: TLV 파서로 추출된 문자열이 없을 때만 정규식 폴백 수행
    if not strings:
        cleaned_bytes = re.sub(rb"1(?=[\x00-\x1f])", b"\n", raw_bytes)
        utf8_pat = re.compile(
            rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|[\xe0-\xef][\x80-\xbf]{2}|[\xf0-\xf4][\x80-\xbf]{3}){3,}"
        )
        for m in utf8_pat.finditer(cleaned_bytes):
            try:
                s = m.group(0).decode("utf-8").strip()
                if s and s not in seen_s:
                    seen_s.add(s)
                    strings.append(s)
            except Exception:
                pass
    return strings


class KoneWatcher:
    """Kone.gg 게시판을 주기적으로 모니터링하여 새 글의 다운로드 링크를 자동 추가하는 워처."""

    def __init__(self, settings_getter, download_manager, on_new_jobs_callback=None):
        self.settings_getter = settings_getter
        self.download_manager = download_manager
        self.on_new_jobs_callback = on_new_jobs_callback

        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._running = False
        self._is_checking = False

        self._seen_posts: set[str] = set()
        self._last_check_time: float = 0.0
        self._recent_logs: list[dict] = []
        self._last_checked_post: dict | None = None
        self._last_status: dict = {
            "status": "idle",
            "last_check_time": None,
            "new_posts_count": 0,
            "downloads_added": 0,
            "message": "대기 중",
        }

        self._load_seen_posts()

    @staticmethod
    def _get_state_file() -> Path:
        return SEEN_POSTS_FILE.parent / "kone_watcher_state.json"

    def _add_log(self, entry: dict):
        """최근 게시글 확인 이력을 보관합니다 (최대 50건)."""
        with self._lock:
            if "title" in entry:
                entry["title"] = fix_mojibake(entry["title"])
            post_id = entry.get("id")
            if post_id:
                self._recent_logs = [
                    x for x in self._recent_logs if x.get("id") != post_id
                ]
            self._recent_logs.append(entry)
            if len(self._recent_logs) > 50:
                self._recent_logs = self._recent_logs[-50:]

    def _load_seen_posts(self):
        """저장된 확인 완료 게시글 ID 목록 및 감시 상태(기준점/로그)를 로드합니다."""
        with self._lock:
            if SEEN_POSTS_FILE.exists():
                try:
                    with open(SEEN_POSTS_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        self._seen_posts = set(data)
                        logger.info(
                            "Kone.gg 이전 확인 게시글 %d개 로드 완료",
                            len(self._seen_posts),
                        )
                except Exception as e:
                    logger.warning("Kone.gg 확인 완료 목록 로드 실패: %s", e)
                    self._seen_posts = set()

            state_file = self._get_state_file()
            if state_file.exists():
                try:
                    with open(state_file, encoding="utf-8") as f:
                        state = json.load(f)
                    if isinstance(state, dict):
                        last_post = state.get("last_checked_post")
                        if isinstance(last_post, dict):
                            if "title" in last_post:
                                last_post["title"] = fix_mojibake(
                                    str(last_post["title"])
                                )
                            self._last_checked_post = last_post
                        logs = state.get("recent_logs")
                        if isinstance(logs, list):
                            cleaned_logs = []
                            for item in logs[-50:]:
                                if isinstance(item, dict):
                                    item_copy = dict(item)
                                    if "title" in item_copy:
                                        item_copy["title"] = fix_mojibake(
                                            str(item_copy["title"])
                                        )
                                    cleaned_logs.append(item_copy)
                            self._recent_logs = cleaned_logs
                        if isinstance(state.get("last_check_time"), (int, float)):
                            self._last_check_time = float(state["last_check_time"])
                        if isinstance(state.get("last_status"), dict):
                            self._last_status = dict(state["last_status"])
                except Exception as e:
                    logger.warning("Kone.gg 감시 상태 파일 로드 실패: %s", e)

    def _save_seen_posts(self):
        """확인 완료 게시글 ID 목록 및 감시 상태를 안전하게 파일로 저장합니다."""
        with self._lock:
            posts_list = list(self._seen_posts)[-MAX_SEEN_CACHE:]
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=SEEN_POSTS_FILE.parent,
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temp_path = stream.name
                    json.dump(posts_list, stream, indent=2, ensure_ascii=False)
                os.replace(temp_path, SEEN_POSTS_FILE)
            except Exception as e:
                logger.warning("Kone.gg 확인 완료 목록 저장 실패: %s", e)
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

            state_file = self._get_state_file()
            state_temp = None
            try:
                state_payload = {
                    "last_checked_post": self._last_checked_post,
                    "recent_logs": self._recent_logs[-50:],
                    "last_check_time": self._last_check_time,
                    "last_status": self._last_status,
                }
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=state_file.parent,
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    state_temp = stream.name
                    json.dump(state_payload, stream, indent=2, ensure_ascii=False)
                os.replace(state_temp, state_file)
            except Exception as e:
                logger.warning("Kone.gg 감시 상태 파일 저장 실패: %s", e)
            finally:
                if state_temp and os.path.exists(state_temp):
                    try:
                        os.remove(state_temp)
                    except OSError:
                        pass

    def start(self):
        """백그라운드 감시 루프 스레드를 시작합니다."""
        with self._lock:
            if self._running:
                return
            self._running = True
            # An older check may still be exiting after stop() timed out.
            # Never clear the event owned by that generation.
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_loop,
                args=(self._stop_event,),
                name="KoneWatcherThread",
                daemon=True,
            )
            self._thread.start()
            logger.info("Kone.gg 게시판 자동 감시 스레드가 시작되었습니다.")

    def stop(self):
        """백그라운드 감시 루프 스레드를 중지합니다."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        logger.info("Kone.gg 게시판 자동 감시 스레드가 중지되었습니다.")

    def restart(self):
        """설정 변경 시 워처를 재시작합니다."""
        self.stop()
        settings = self.settings_getter()
        if settings.get("kone_auto_check", False):
            self.start()

    def _run_loop(self, stop_event):
        """지정된 주기마다 게시판을 확인하는 스레드 루프."""
        while not stop_event.is_set():
            settings = self.settings_getter()
            if not settings.get("kone_auto_check", False):
                break

            interval = int(settings.get("kone_check_interval_sec", 180))
            try:
                self.check_board(is_manual=False)
            except Exception as e:
                logger.error("Kone.gg 게시판 자동 확인 중 오류: %s", e, exc_info=True)

            # 주기만큼 대기 (종료 이벤트 수신 시 즉시 탈출)
            if stop_event.wait(timeout=max(30, interval)):
                break

        with self._lock:
            if self._stop_event is stop_event:
                self._running = False

    def check_board(self, is_manual: bool = False) -> dict:
        """
        게시판을 확인하고 새 게시글의 다운로드 링크를 추출하여 큐에 추가합니다.
        - is_manual: 수동 '지금 즉시 확인' 클릭 여부
        """
        with self._lock:
            if self._is_checking:
                return {"success": False, "message": "이미 확인 작업이 진행 중입니다."}
            self._is_checking = True

        try:
            settings = self.settings_getter()
            board_url = settings.get(
                "kone_board_url", "https://kone.gg/s/retrogame"
            ).strip()
            if not board_url:
                board_url = "https://kone.gg/s/retrogame"

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }

            logger.info("Kone.gg 게시판 확인 시작: %s (수동=%s)", board_url, is_manual)
            res = requests.get(board_url, headers=headers, timeout=15)
            if res.status_code != 200:
                msg = f"게시판 접속 실패 (HTTP {res.status_code})"
                logger.warning(msg)
                return {"success": False, "error": msg}

            raw_content = getattr(res, "content", None)
            if isinstance(raw_content, (bytes, bytearray)) and raw_content:
                html_text = raw_content.decode("utf-8", errors="replace")
            else:
                res.encoding = "utf-8"
                html_text = res.text

            soup = BeautifulSoup(html_text, "html.parser")

            # 게시글 링크 및 제목 수집
            found_posts = []
            seen_in_page = set()

            # /s/<board_name>/<post_id> 정규식
            link_pattern = re.compile(r"/s/[^/]+/([a-zA-Z0-9_-]{10,})")
            for a in soup.find_all("a", href=link_pattern):
                raw_href = a.get("href")
                if not isinstance(raw_href, str) or not raw_href:
                    continue
                href = raw_href
                m = link_pattern.search(href)
                if not m:
                    continue
                post_id = m.group(1)
                if post_id in seen_in_page:
                    continue
                seen_in_page.add(post_id)

                full_url = urljoin(board_url, href)

                # 제목 추출: 내부의 title="..." 속성을 최우선 사용
                title_div = a.select_one("[title]")
                if title_div and title_div.get("title"):
                    post_title = fix_mojibake(str(title_div["title"]).strip())
                elif a.get("title"):
                    post_title = fix_mojibake(str(a["title"]).strip())
                else:
                    post_title = fix_mojibake(a.get_text(separator=" ", strip=True))

                # 카테고리(공지/정보/일반 등) 및 작성 시각 추출
                parts = [
                    fix_mojibake(p.strip())
                    for p in a.get_text(separator="|", strip=True).split("|")
                    if p.strip()
                ]
                category = ""
                post_time = ""
                if parts:
                    first_part = parts[0]
                    if first_part != post_title and len(first_part) <= 10:
                        category = first_part
                    for part in parts:
                        if re.search(
                            r"(?:ago|전|\d{4}[.-]\d{2}[.-]\d{2}|\d{1,2}:\d{2})",
                            part,
                            re.IGNORECASE,
                        ):
                            post_time = part
                            break
                is_notice = category == "공지" or post_title.startswith("[공지]")

                found_posts.append(
                    {
                        "id": post_id,
                        "url": full_url,
                        "title": post_title or f"Kone_{post_id}",
                        "category": category,
                        "post_time": post_time,
                        "is_notice": is_notice,
                    }
                )

            if not found_posts:
                logger.info("Kone.gg 게시판에서 게시글 목록을 찾지 못했습니다.")
                return {
                    "success": True,
                    "new_posts_count": 0,
                    "downloads_added": 0,
                    "message": "게시글 목록 없음",
                }

            non_notice_posts = [p for p in found_posts if not p.get("is_notice")]
            newest_regular_post = (
                non_notice_posts[0] if non_notice_posts else found_posts[0]
            )
            now_str = time.strftime("%H:%M:%S")

            # 최초 실행 시(저장된 기록이 없고 수동 실행이 아닌 경우):
            # 과거의 모든 글을 한번에 내려받아 대기열이 폭주하는 것을 방지하기 위해
            # 현재 게시판의 글들을 '이미 확인 완료'로 시딩(기록)합니다.
            if not self._seen_posts and not is_manual:
                for p in found_posts:
                    self._seen_posts.add(p["id"])
                for p in reversed((non_notice_posts or found_posts)[:5]):
                    st_code, st_msg, link_cnt, cand_urls = (
                        self._inspect_post_history_status(p, headers)
                    )
                    self._add_log(
                        {
                            "id": p["id"],
                            "title": p["title"],
                            "url": p["url"],
                            "status": st_code,
                            "links_count": link_cnt,
                            "candidate_urls": cand_urls,
                            "message": st_msg,
                            "time": now_str,
                        }
                    )
                top_log = next(
                    (
                        lg
                        for lg in reversed(self._recent_logs)
                        if lg.get("id") == newest_regular_post["id"]
                    ),
                    None,
                )
                self._last_checked_post = {
                    "id": newest_regular_post["id"],
                    "title": newest_regular_post["title"],
                    "url": newest_regular_post["url"],
                    "category": newest_regular_post.get("category", ""),
                    "post_time": newest_regular_post.get("post_time", ""),
                    "checked_at": now_str,
                    "status": top_log.get("status", "no_links")
                    if top_log
                    else "no_links",
                    "message": top_log.get("message", "기준점 등록 완료")
                    if top_log
                    else "기준점 등록 완료",
                }
                self._last_check_time = time.time()
                self._last_status = {
                    "status": "ready",
                    "last_check_time": self._last_check_time,
                    "new_posts_count": 0,
                    "downloads_added": 0,
                    "message": f"기준점 등록 완료 ('{newest_regular_post['title']}'까지 확인됨)",
                }
                self._save_seen_posts()
                logger.info(
                    "Kone.gg 워처 첫 실행 초기화: 기존 게시글 %d개를 확인 완료로 기록하였습니다. 기준점: [%s]",
                    len(found_posts),
                    newest_regular_post["title"],
                )
                return {
                    "success": True,
                    "initialized": True,
                    "seeded_count": len(found_posts),
                    "last_checked_post": self._last_checked_post,
                    "message": "초기 감시 등록 완료",
                }

            # 새로운 미확인 게시글 필터링
            new_posts = [p for p in found_posts if p["id"] not in self._seen_posts]
            logger.info(
                "Kone.gg 게시글 %d개 발견, 신규 미확인 글: %d개",
                len(found_posts),
                len(new_posts),
            )

            total_added_downloads = 0
            processed_posts_info = []
            failed_posts_info = []

            for post in reversed(new_posts):  # 과거 -> 최신 순 처리
                post_id = post["id"]
                post_url = post["url"]
                post_title = post["title"]
                check_time_str = time.strftime("%H:%M:%S")

                try:
                    candidates = self._extract_download_links_from_post(
                        post_url, headers
                    )
                    if candidates:
                        # 각 다운로드 링크에 출처 게시글 제목과 URL 첨부 및 자동 다운로드 표시
                        for c in candidates:
                            if isinstance(c, dict):
                                c_opts = c.setdefault("options", {})
                                c_opts["source_title"] = post_title
                                c_opts["source_url"] = post_url
                                c_opts["is_auto"] = True

                        subfolder_name = (
                            sanitize_folder_name(post_title)
                            if settings.get("kone_subfolder", True)
                            else ""
                        )
                        added_jobs = self.download_manager.add_urls(
                            candidates, subfolder=subfolder_name, is_auto=True
                        )
                        added_count = len(added_jobs)
                        skipped_dups = len(candidates) - added_count
                        total_added_downloads += added_count

                        if added_count > 0:
                            status_code = "success"
                            status_msg = f"{added_count}개 다운로드 추가" + (
                                f" (중복 {skipped_dups}개 제외)" if skipped_dups else ""
                            )
                            logger.info(
                                "Kone.gg 새 글 감지 및 다운로드 추가 완료: [%s] -> 추가 %d개 (중복 제외 %d개, 폴더: %s)",
                                post_title,
                                added_count,
                                skipped_dups,
                                subfolder_name or "루트",
                            )
                            processed_posts_info.append(
                                {
                                    "title": post_title,
                                    "url": post_url,
                                    "links_count": added_count,
                                    "skipped_dups": skipped_dups,
                                }
                            )
                        else:
                            status_code = "duplicate"
                            status_msg = f"{len(candidates)}개 링크 모두 중복 제외됨"
                            logger.info(
                                "Kone.gg 새 글 [%s]의 다운로드 링크 %d개는 이미 완료/진행 중인 중복 링크여서 건너뛰었습니다.",
                                post_title,
                                len(candidates),
                            )
                    else:
                        status_code = "no_links"
                        status_msg = "다운로드 링크 미발견 (일반 게시글)"
                        logger.debug(
                            "Kone.gg 새 글 [%s] 에 다운로드 링크가 없습니다.",
                            post_title,
                        )

                    self._add_log(
                        {
                            "id": post_id,
                            "title": post_title,
                            "url": post_url,
                            "status": status_code,
                            "links_count": len(candidates),
                            "message": status_msg,
                            "time": check_time_str,
                        }
                    )

                    # 게시글 확인 완료 기록 및 최신 감시 기준점 갱신
                    with self._lock:
                        self._seen_posts.add(post_id)
                        if not post.get("is_notice"):
                            self._last_checked_post = {
                                "id": post_id,
                                "title": post_title,
                                "url": post_url,
                                "category": post.get("category", ""),
                                "post_time": post.get("post_time", ""),
                                "checked_at": check_time_str,
                                "status": status_code,
                                "message": status_msg,
                            }
                except Exception as e:
                    logger.warning(
                        "Kone.gg 게시글 [%s] 처리 중 오류: %s", post_title, e
                    )
                    self._add_log(
                        {
                            "id": post_id,
                            "title": post_title,
                            "url": post_url,
                            "status": "failed",
                            "message": f"처리 오류: {str(e)}",
                            "time": check_time_str,
                        }
                    )
                    failed_posts_info.append(
                        {
                            "title": post_title,
                            "url": post_url,
                            "error": str(e),
                        }
                    )

            # 신규 글이 없었더라도 현재 게시판의 최신 확인 완료 글을 기준점으로 동기화하고,
            # 기존 감시 이력('seeded' 상태 또는 로그 누락)에 실제 다운로드 처리/링크 유무 정보를 채움
            seen_regular = [p for p in non_notice_posts if p["id"] in self._seen_posts]
            if not seen_regular:
                seen_regular = [p for p in found_posts if p["id"] in self._seen_posts]
            if seen_regular:
                if not self._recent_logs:
                    for p in reversed(seen_regular[:5]):
                        st_code, st_msg, link_cnt, cand_urls = (
                            self._inspect_post_history_status(p, headers)
                        )
                        self._add_log(
                            {
                                "id": p["id"],
                                "title": p["title"],
                                "url": p["url"],
                                "status": st_code,
                                "links_count": link_cnt,
                                "candidate_urls": cand_urls,
                                "message": st_msg,
                                "time": now_str,
                            }
                        )
                else:
                    existing_log_map = {
                        lg.get("id"): lg for lg in self._recent_logs if lg.get("id")
                    }
                    for p in seen_regular[:5]:
                        prev_entry = existing_log_map.get(p["id"])
                        if (
                            prev_entry is not None
                            and prev_entry.get("status") == "seeded"
                        ):
                            st_code, st_msg, link_cnt, cand_urls = (
                                self._inspect_post_history_status(p, headers)
                            )
                            with self._lock:
                                prev_entry["title"] = fix_mojibake(p["title"])
                                prev_entry["status"] = st_code
                                prev_entry["links_count"] = link_cnt
                                prev_entry["candidate_urls"] = cand_urls
                                prev_entry["message"] = st_msg
                        elif (
                            prev_entry is not None
                            and prev_entry.get("status") == "unprocessed_links"
                            and prev_entry.get("candidate_urls")
                        ):
                            # 네트워크 재요청 없이 메모리상의 다운로드 매니저 상태만 대조
                            st_code, st_msg, link_cnt, _ = (
                                self._inspect_post_history_status(
                                    p,
                                    headers,
                                    cached_urls=prev_entry.get("candidate_urls"),
                                )
                            )
                            with self._lock:
                                prev_entry["status"] = st_code
                                prev_entry["links_count"] = link_cnt
                                prev_entry["message"] = st_msg

                with self._lock:
                    top_seen = seen_regular[0]
                    prev_log = next(
                        (
                            lg
                            for lg in reversed(self._recent_logs)
                            if lg.get("id") == top_seen["id"]
                        ),
                        None,
                    )
                    if (
                        not self._last_checked_post
                        or self._last_checked_post.get("id") != top_seen["id"]
                        or self._last_checked_post.get("status") == "seeded"
                    ):
                        self._last_checked_post = {
                            "id": top_seen["id"],
                            "title": top_seen["title"],
                            "url": top_seen["url"],
                            "category": top_seen.get("category", ""),
                            "post_time": top_seen.get("post_time", ""),
                            "checked_at": prev_log.get("time", now_str)
                            if prev_log
                            else now_str,
                            "status": prev_log.get("status", "no_links")
                            if prev_log
                            else "no_links",
                            "message": prev_log.get(
                                "message", "확인 완료 (감시 기준점)"
                            )
                            if prev_log
                            else "확인 완료 (감시 기준점)",
                        }
                    else:
                        self._last_checked_post["title"] = fix_mojibake(
                            top_seen["title"]
                        )
                        if top_seen.get("post_time"):
                            self._last_checked_post["post_time"] = top_seen["post_time"]

            # 변경된 확인 목록 및 기준점 상태 저장
            self._last_check_time = time.time()
            self._last_status = {
                "status": "success",
                "last_check_time": self._last_check_time,
                "new_posts_count": len(new_posts),
                "downloads_added": total_added_downloads,
                "failures_count": len(failed_posts_info),
                "message": (
                    f"새 글 {len(new_posts)}개 확인, {total_added_downloads}개 파일 다운로드 추가됨"
                    + (
                        f" (실패 {len(failed_posts_info)}건)"
                        if failed_posts_info
                        else ""
                    )
                    if new_posts
                    else f"새 게시글 없음 (최신 확인 글: '{self._last_checked_post['title'] if self._last_checked_post else '-'}')"
                ),
            }
            self._save_seen_posts()

            if total_added_downloads > 0 and callable(self.on_new_jobs_callback):
                try:
                    self.on_new_jobs_callback(processed_posts_info)
                except Exception:
                    pass

            return {
                "success": True,
                "new_posts_count": len(new_posts),
                "downloads_added": total_added_downloads,
                "last_checked_post": self._last_checked_post,
                "posts": processed_posts_info,
                "failures": failed_posts_info,
            }

        finally:
            with self._lock:
                self._is_checking = False

    def reset_watermark(self, recheck_latest_count: int = 0) -> dict:
        """
        감시 기준점을 현재 게시판 상태로 재설정하거나 최근 N개 게시글을 다시 스캔합니다.
        - recheck_latest_count == 0: 현재 게시판의 모든 글을 확인 완료로 시딩하고 최신 글을 새 기준점으로 설정
        - recheck_latest_count > 0: 최신 일반 게시글 N개를 미확인 상태로 되돌린 뒤 즉시 재확인 및 다운로드 추출
        """
        recheck_latest_count = max(0, min(10, int(recheck_latest_count or 0)))
        if recheck_latest_count == 0:
            with self._lock:
                self._seen_posts.clear()
                self._recent_logs.clear()
                self._last_checked_post = None
            return self.check_board(is_manual=False)

        # 최근 N개 게시글을 다시 스캔하기 위해 게시판 목록을 먼저 가져와 상위 N개 ID를 _seen_posts에서 제거
        settings = self.settings_getter()
        board_url = (
            settings.get("kone_board_url", "https://kone.gg/s/retrogame").strip()
            or "https://kone.gg/s/retrogame"
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        res = requests.get(board_url, headers=headers, timeout=15)
        if res.status_code != 200:
            return {
                "success": False,
                "error": f"게시판 접속 실패 (HTTP {res.status_code})",
            }
        raw_content = getattr(res, "content", None)
        html_text = (
            raw_content.decode("utf-8", errors="replace")
            if isinstance(raw_content, (bytes, bytearray)) and raw_content
            else res.text
        )
        soup = BeautifulSoup(html_text, "html.parser")
        link_pattern = re.compile(r"/s/[^/]+/([a-zA-Z0-9_-]{10,})")
        regular_ids = []
        seen_in_page = set()
        for a in soup.find_all("a", href=link_pattern):
            raw_href = a.get("href")
            if not isinstance(raw_href, str):
                continue
            m = link_pattern.search(raw_href)
            if not m:
                continue
            pid = m.group(1)
            if pid in seen_in_page:
                continue
            seen_in_page.add(pid)
            parts = [
                fix_mojibake(p.strip())
                for p in a.get_text(separator="|", strip=True).split("|")
                if p.strip()
            ]
            if parts and parts[0] == "공지":
                continue
            regular_ids.append(pid)

        target_ids = set(regular_ids[:recheck_latest_count])
        with self._lock:
            for pid in seen_in_page:
                self._seen_posts.add(pid)
            for pid in target_ids:
                self._seen_posts.discard(pid)
        return self.check_board(is_manual=True)

    def _inspect_post_history_status(
        self, post: dict, headers: dict, cached_urls: list[str] | None = None
    ) -> tuple[str, str, int, list[str]]:
        """
        기존 확인된 게시글의 본문 및 다운로드 매니저 기록을 대조하여
        실제 다운로드 링크 유무 및 다운로드 처리 결과를 반환합니다.
        반환값: (status_code, status_message, links_count, candidate_urls)
        """
        post_url = post.get("url", "")
        if cached_urls is not None:
            candidates = [{"url": u} for u in cached_urls if u]
        else:
            try:
                candidates = self._extract_download_links_from_post(post_url, headers)
            except Exception as e:
                return "failed", f"게시글 조회 실패: {e}", 0, []
        cand_urls = [
            c.get("url", "") for c in candidates if isinstance(c, dict) and c.get("url")
        ]

        completed_files: list[str] = []
        active_count = 0
        failed_count = 0

        dm = self.download_manager
        if dm is not None:
            try:
                hist_list = dm.get_history() if hasattr(dm, "get_history") else []
                if isinstance(hist_list, list):
                    for h in hist_list:
                        if isinstance(h, dict) and (
                            h.get("source_url") == post_url or h.get("url") == post_url
                        ):
                            fname = str(h.get("filename") or "").strip() or "파일"
                            if fname not in completed_files:
                                completed_files.append(fname)
            except Exception:
                pass

            try:
                jobs_dict = getattr(dm, "jobs", None)
                if isinstance(jobs_dict, dict):
                    for job in jobs_dict.values():
                        if (
                            getattr(job, "source_url", "") == post_url
                            or getattr(job, "original_url", "") == post_url
                        ):
                            st = getattr(job, "status", "")
                            if st == "completed":
                                fname = (
                                    str(getattr(job, "filename", "") or "").strip()
                                    or "파일"
                                )
                                if fname not in completed_files:
                                    completed_files.append(fname)
                            elif st in ("queued", "resolving", "downloading", "paused"):
                                active_count += 1
                            elif st == "failed":
                                failed_count += 1
            except Exception:
                pass

            if hasattr(dm, "is_duplicate_url"):
                for c in candidates:
                    u = c.get("url", "") if isinstance(c, dict) else ""
                    if not u:
                        continue
                    try:
                        dup_res = dm.is_duplicate_url(u)
                        if (
                            isinstance(dup_res, tuple)
                            and len(dup_res) >= 3
                            and dup_res[0] is True
                        ):
                            _, reason, info = dup_res
                            if "완료" in str(reason):
                                fname = (
                                    str(info.get("filename") or "").strip()
                                    if isinstance(info, dict)
                                    else ""
                                ) or "파일"
                                if fname not in completed_files:
                                    completed_files.append(fname)
                            else:
                                active_count += 1
                    except Exception:
                        pass

        total_links = len(candidates)
        if total_links == 0 and not completed_files and active_count == 0:
            return "no_links", "다운로드 링크 미발견 (일반 게시글)", 0, []

        from urllib.parse import urlparse

        hosts = []
        for c in candidates:
            u = c.get("url", "") if isinstance(c, dict) else ""
            h = (urlparse(u).hostname or "").lower().removeprefix("www.")
            if h and h not in hosts:
                hosts.append(h)
        host_label = f" [{', '.join(hosts[:2])}]" if hosts else ""

        if completed_files:
            files_preview = ", ".join(completed_files[:2])
            if len(completed_files) > 2:
                files_preview += f" 외 {len(completed_files) - 2}건"
            link_cnt = max(total_links, len(completed_files))
            return (
                "success",
                f"다운로드 완료 (링크 {link_cnt}개{host_label} • {files_preview})",
                link_cnt,
                cand_urls,
            )

        if active_count > 0:
            link_cnt = max(total_links, active_count)
            return (
                "success",
                f"다운로드 진행/대기 중 (링크 {link_cnt}개{host_label})",
                link_cnt,
                cand_urls,
            )

        if failed_count > 0:
            return (
                "failed",
                f"다운로드 실패 이력 (링크 {total_links}개{host_label} 발견)",
                total_links,
                cand_urls,
            )

        return (
            "unprocessed_links",
            f"다운로드 링크 {total_links}개 발견{host_label} (미다운로드)",
            total_links,
            cand_urls,
        )

    def download_post_by_url(
        self, post_url: str, post_title: str = "", post_id: str = ""
    ) -> dict:
        """특정 게시글 URL의 다운로드 링크를 추출하여 즉시 대기열에 추가합니다."""
        post_url = str(post_url or "").strip()
        if not post_url:
            return {"success": False, "error": "게시글 주소가 비어 있습니다."}
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            candidates = self._extract_download_links_from_post(post_url, headers)
            if not candidates:
                return {
                    "success": False,
                    "error": "해당 게시글에서 다운로드 링크를 찾지 못했습니다.",
                }
            settings = self.settings_getter()
            clean_title = fix_mojibake(post_title.strip()) if post_title else ""
            for c in candidates:
                if isinstance(c, dict):
                    c_opts = c.setdefault("options", {})
                    if clean_title:
                        c_opts["source_title"] = clean_title
                    c_opts["source_url"] = post_url
                    c_opts["is_auto"] = False
            subfolder_name = (
                sanitize_folder_name(clean_title)
                if (clean_title and settings.get("kone_subfolder", True))
                else ""
            )
            added_jobs = self.download_manager.add_urls(
                candidates, subfolder=subfolder_name, force=True, is_auto=False
            )
            added_count = len(added_jobs)
            now_str = time.strftime("%H:%M:%S")
            target_id = post_id or post_url.rsplit("/", 1)[-1]
            cand_urls = [
                c.get("url", "")
                for c in candidates
                if isinstance(c, dict) and c.get("url")
            ]
            with self._lock:
                updated_in_place = False
                for lg in self._recent_logs:
                    if lg.get("id") == target_id or lg.get("url") == post_url:
                        lg["status"] = "success"
                        lg["links_count"] = len(candidates)
                        lg["candidate_urls"] = cand_urls
                        lg["message"] = f"{added_count}개 다운로드 추가 (수동 실행)"
                        lg["time"] = now_str
                        updated_in_place = True
                        break
                if not updated_in_place:
                    self._add_log(
                        {
                            "id": target_id,
                            "title": clean_title or post_url,
                            "url": post_url,
                            "status": "success",
                            "links_count": len(candidates),
                            "candidate_urls": cand_urls,
                            "message": f"{added_count}개 다운로드 추가 (수동 실행)",
                            "time": now_str,
                        }
                    )
                self._save_seen_posts()
            return {
                "success": True,
                "links_count": len(candidates),
                "downloads_added": added_count,
            }
        except Exception as e:
            logger.error(
                "Kone.gg 게시글 수동 다운로드 추가 중 오류: %s", e, exc_info=True
            )
            return {"success": False, "error": f"다운로드 추가 중 오류: {e}"}

    def _extract_download_links_from_post(
        self, post_url: str, headers: dict
    ) -> list[dict]:
        """
        게시글 상세 페이지를 열어 본문의 다운로드 링크(평문 URL 및 Base64 인코딩 링크)를 추출합니다.
        """
        fetch_headers = dict(headers)
        if "kone.gg" in post_url:
            fetch_headers["User-Agent"] = (
                "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
            )
        res = requests.get(post_url, headers=fetch_headers, timeout=15)
        if res.status_code != 200:
            return []

        raw_content = getattr(res, "content", None)
        if isinstance(raw_content, (bytes, bytearray)) and raw_content:
            html_text = raw_content.decode("utf-8", errors="replace")
        else:
            res.encoding = "utf-8"
            html_text = res.text

        soup = BeautifulSoup(html_text, "html.parser")
        texts_to_scan: list[str] = []

        # 1. SvelteKit SSR JSON 데이터 스크립트 탐색 (https://api.kone.gg/v1/article/get)
        ssr_script = soup.find(
            "script", attrs={"data-url": re.compile(r"api\.kone\.gg/v1/article/get")}
        )
        if ssr_script and ssr_script.text:
            try:
                data = json.loads(ssr_script.text)
                b64_body = data.get("body", "")
                if b64_body:
                    raw_bytes = base64.b64decode(b64_body)
                    texts_to_scan.extend(extract_kone_ssr_strings(raw_bytes))
            except Exception as e:
                logger.debug("SSR JSON 파싱 실패: %s", e)

        # 2. HTML 텍스트 전체
        texts_to_scan.append(soup.get_text("\n"))

        # 3. 모든 텍스트 블록에서 Base64 링크 및 평문 링크 파싱 (중복 디코딩 방지)
        candidates: list[dict] = []
        seen_b64_tokens: set[str] = set()
        for t in texts_to_scan:
            # 텍스트에 포함된 Base64 인코딩 URL 패턴 검색
            for m in B64_URL_REGEX.finditer(t):
                tok = m.group(0)
                if "=" in tok:
                    tok = tok[: tok.rfind("=") + 1]
                if tok in seen_b64_tokens:
                    continue
                seen_b64_tokens.add(tok)
                dec = decode_base64_string(tok)
                if dec and dec != tok:
                    candidates.extend(parse_download_candidates(raw_text=dec))

            # 이미 처리한 Base64 토큰을 제거한 나머지 평문에서 일반 URL 파싱
            plain_t = B64_URL_REGEX.sub(" ", t)
            candidates.extend(parse_download_candidates(raw_text=plain_t))

        # 내부 도메인, 동영상 스트리밍(유튜브 등) 및 비-다운로드 일반 웹페이지 필터링
        from downloader.resolvers.dispatcher import default_dispatcher
        from downloader.resolvers.webpage import (
            _has_downloadable_extension,
            _is_image_url_or_name,
        )

        specialized_resolvers = [
            r
            for r in default_dispatcher.resolvers
            if r.name not in ("WebPage Post", "Direct Link")
        ]

        ignored_domains = (
            "mittere.io",
            "kone.gg",
            "turnstile",
            "sentry.io",
            "cloudflare",
            "youtu.be",
            "youtube.com",
            "twitch.tv",
            "chzzk.naver.com",
            "namu.wiki",
            "x.com",
            "twitter.com",
            "discord.gg",
            "discord.com",
            "store.steampowered.com",
            "steamcommunity.com",
        )

        from urllib.parse import urlparse

        filtered = []
        seen_urls = set()
        for c in candidates:
            u = c.get("url", "")
            if not u or u in seen_urls:
                continue
            if is_shortened_url(u):
                expanded = unshorten_url(u)
                if expanded and expanded != u:
                    u = expanded
                    c["url"] = expanded
            u_lower = u.lower()
            host = (urlparse(u).hostname or "").lower()
            if any(
                host == ig
                or host.endswith("." + ig)
                or ig in ("turnstile", "cloudflare")
                and ig in u_lower
                for ig in ignored_domains
            ):
                continue
            if _is_image_url_or_name(u):
                continue
            is_supported_cloud = u_lower.startswith("magnet:") or any(
                r.can_handle(u) for r in specialized_resolvers
            )
            if not is_supported_cloud and not _has_downloadable_extension(u):
                continue
            seen_urls.add(u)
            filtered.append(c)

        return filtered

    def get_status(self) -> dict:
        """워처의 현재 상태 정보를 반환합니다."""
        settings = self.settings_getter()
        interval = int(settings.get("kone_check_interval_sec", 180))
        with self._lock:
            logs = []
            for item in self._recent_logs[-20:]:
                log_copy = dict(item)
                if "title" in log_copy:
                    log_copy["title"] = fix_mojibake(log_copy["title"])
                logs.append(log_copy)
            last_post = (
                dict(self._last_checked_post)
                if isinstance(self._last_checked_post, dict)
                else None
            )
            if last_post and "title" in last_post:
                last_post["title"] = fix_mojibake(str(last_post["title"]))
            next_check_time = (
                (self._last_check_time + max(30, interval))
                if (self._running and self._last_check_time > 0)
                else None
            )
            return {
                "enabled": bool(settings.get("kone_auto_check", False)),
                "running": self._running,
                "is_checking": self._is_checking,
                "board_url": settings.get(
                    "kone_board_url", "https://kone.gg/s/retrogame"
                ),
                "check_interval_sec": interval,
                "subfolder": settings.get("kone_subfolder", True),
                "seen_posts_count": len(self._seen_posts),
                "last_check_time": self._last_check_time,
                "next_check_time": next_check_time,
                "last_checked_post": last_post,
                "last_status": dict(self._last_status),
                "recent_logs": logs,
            }
