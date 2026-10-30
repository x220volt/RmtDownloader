"""Cloudflare Turnstile 우회 및 웹페이지 전체 화면 캡처(Full-Page Screenshot)를 위한 브라우저 헬퍼 모듈.

DrissionPage와 시스템에 설치된 Chromium 계열 브라우저(Chrome / Edge)를
화면 밖(-2400, -2400) 가상 위치에서 무음으로 구동하여 안전하고 일관된 환경을 제공합니다.
"""

import logging
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Optional

logger = logging.getLogger(__name__)


def find_browser_path() -> Optional[str]:
    """시스템에 설치된 Chrome 또는 Edge 브라우저 실행 경로를 탐색합니다."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    for exe in ("chrome", "google-chrome", "chromium", "msedge"):
        p = shutil.which(exe)
        if p and os.path.exists(p):
            return p
    return None


@contextmanager
def stealth_browser_session(
    prefix: str = "stealth_browser_",
    window_size: str = "800,600",
    window_position: str = "-2400,-2400",
) -> Generator[Any, None, None]:
    """DrissionPage ChromiumPage를 임시 디렉터리 및 화면 밖 무음 설정으로 안전하게 생성하고 정리합니다."""
    from DrissionPage import ChromiumOptions, ChromiumPage

    browser_path = find_browser_path()
    temp_dir = tempfile.mkdtemp(prefix=prefix)
    co = ChromiumOptions()
    co.headless(
        False
    )  # Turnstile 신뢰성 우회를 위해 headed 모드 사용 (위치를 화면 밖으로 배치)
    if browser_path:
        co.set_browser_path(browser_path)
    co.set_user_data_path(temp_dir)
    co.auto_port()
    co.set_argument(f"--window-position={window_position}")
    co.set_argument(f"--window-size={window_size}")
    co.set_argument("--mute-audio")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")

    page = None
    try:
        page = ChromiumPage(co)
        yield page
    finally:
        if page:
            try:
                page.quit()
            except Exception as e:
                logger.debug(f"브라우저 종료 중 예외 (무시됨): {e}")
        shutil.rmtree(temp_dir, ignore_errors=True)


def capture_full_page_screenshot(
    url: str,
    dest_dir: str,
    title_hint: str = "",
) -> Optional[str]:
    """주어진 웹페이지(게시글 등) 전체를 스크롤하며 렌더링한 뒤 전체 화면 스크린샷(.png)으로 저장합니다."""
    if not url or not url.lower().startswith(("http://", "https://")):
        return None

    os.makedirs(dest_dir, exist_ok=True)
    safe_base = re.sub(
        r'[\\/*?:"<>|\r\n\t]+', "_", (title_hint or "page_capture")
    ).strip(" ._")
    if len(safe_base) > 90:
        safe_base = safe_base[:90].rstrip(" ._")
    screenshot_name = f"[페이지캡처] {safe_base or 'page'}.png"
    target_path = os.path.join(dest_dir, screenshot_name)

    if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        logger.info("페이지 캡처가 이미 존재하여 생략합니다: %s", target_path)
        return target_path

    logger.info("원본 페이지 전체 캡처 시작: %s -> %s", url, target_path)
    try:
        with stealth_browser_session(
            prefix="page_capture_", window_size="1280,1080"
        ) as page:
            page.get(url)

            # Cloudflare Turnstile / 로딩 대기 (최대 10초)
            for _ in range(10):
                title = (page.title or "").lower()
                if "just a moment" not in title and "잠시만" not in title:
                    break
                time.sleep(1.0)

            time.sleep(1.5)

            # 네이버 블로그 프레임(mainFrame) 구조인 경우 내부 프레임 URL로 이동하여 전체 본문 캡처
            try:
                frame_el = page.ele("#mainFrame", timeout=0.5)
                if frame_el:
                    frame_src = frame_el.attr("src")
                    if frame_src:
                        page.get(frame_src)
                        time.sleep(1.5)
            except Exception:
                pass

            # 페이지 하단까지 단계별 스크롤하여 지연 로딩(lazy-load) 이미지 깨우기
            try:
                page.run_js(
                    """
                    (async () => {
                        const delay = ms => new Promise(r => setTimeout(r, ms));
                        const maxScroll = Math.min(document.documentElement.scrollHeight || 4000, 15000);
                        for (let y = 0; y < maxScroll; y += 900) {
                            window.scrollTo(0, y);
                            await delay(120);
                        }
                        window.scrollTo(0, 0);
                    })();
                    """
                )
                time.sleep(1.2)
            except Exception:
                pass

            # 제목 힌트가 비어 있었다면 실제 브라우저 타이틀 활용
            if not title_hint and page.title:
                clean_t = re.sub(r'[\\/*?:"<>|\r\n\t]+', "_", page.title).strip(" ._")
                if clean_t:
                    if len(clean_t) > 90:
                        clean_t = clean_t[:90].rstrip(" ._")
                    screenshot_name = f"[페이지캡처] {clean_t}.png"
                    target_path = os.path.join(dest_dir, screenshot_name)

            saved = page.get_screenshot(
                path=dest_dir, name=screenshot_name, full_page=True
            )
            if saved and os.path.exists(target_path):
                logger.info("원본 페이지 전체 캡처 저장 완료: %s", target_path)
                return target_path
    except Exception as e:
        logger.warning("원본 페이지 전체 캡처 실패 (%s): %s", url, e)

    return None


class StealthBrowserSolver:
    """스텔스 브라우저 기반 HTML 패치 및 캡처 유틸리티 래퍼."""

    @staticmethod
    def fetch_html(
        url: str,
        flaresolverr_url: str = "",
        wait_selector: str = "body",
        extra_wait_sec: float = 2.0,
        headless: bool = True,
    ) -> str:
        with stealth_browser_session(
            prefix="webpage_solver_", window_size="1280,900"
        ) as page:
            page.get(url)
            for _ in range(12):
                title = (page.title or "").lower()
                if "just a moment" not in title and "잠시만" not in title:
                    break
                time.sleep(1.0)
            time.sleep(max(0.5, extra_wait_sec))
            return page.html or ""
