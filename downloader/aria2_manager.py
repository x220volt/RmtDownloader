"""aria2c 실행 파일 경로 감지 및 자동 다운로드 관리 모듈."""

import io
import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ARIA2_DOWNLOAD_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"


def get_aria2_path() -> str:
    """사용 가능한 aria2c 실행 파일의 절대 경로를 반환합니다.

    1. ARIA2C_PATH 환경 변수
    2. 프로젝트 내 bin/aria2c.exe
    3. 실행 바이너리 디렉토리 내 bin/aria2c.exe
    4. 시스템 PATH (where/which)
    5. 없으면 자동 다운로드 시도
    """
    env_path = os.environ.get("ARIA2C_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    # 1. 프로젝트 루트 / bin / aria2c.exe
    project_root = Path(__file__).resolve().parent.parent
    local_bin = project_root / "bin" / ("aria2c.exe" if os.name == "nt" else "aria2c")
    if local_bin.is_file():
        return str(local_bin)

    # 2. PyInstaller 실행 파일 기준
    frozen_bin = (
        Path(sys.executable).parent
        / "bin"
        / ("aria2c.exe" if os.name == "nt" else "aria2c")
    )
    if frozen_bin.is_file():
        return str(frozen_bin)

    # 3. 시스템 PATH
    which_path = shutil.which("aria2c")
    if which_path:
        return which_path

    # 4. Windows 환경이면 bin 폴더에 자동 다운로드
    if os.name == "nt":
        try:
            return ensure_aria2c(str(local_bin))
        except Exception as e:
            logger.error(f"aria2c 자동 다운로드 실패: {e}")

    raise FileNotFoundError(
        "aria2c 실행 파일을 찾을 수 없습니다. (bin/aria2c.exe 또는 시스템 PATH)"
    )


def ensure_aria2c(target_path: str) -> str:
    """지정된 위치에 aria2c.exe가 없으면 공식 GitHub 릴리즈에서 다운로드하여 설치합니다."""
    if os.path.isfile(target_path):
        return target_path

    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"aria2c 실행 파일이 없어 다운로드를 시작합니다: {ARIA2_DOWNLOAD_URL}")
    resp = requests.get(ARIA2_DOWNLOAD_URL, timeout=30)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        for name in z.namelist():
            if name.endswith("aria2c.exe"):
                with open(target, "wb") as f:
                    f.write(z.read(name))
                logger.info(f"aria2c.exe 압축 해제 및 설치 완료: {target}")
                return str(target)

    raise ValueError("다운로드한 아카이브에서 aria2c.exe를 찾을 수 없습니다.")
