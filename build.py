"""원격 다운로더 빌드 스크립트.

- `python build.py --bump` 실행 시 패치 버전(0.0.1)을 자동으로 1 올린 뒤 모든 파일의 버전을 동기화하고 빌드합니다.
- `python build.py` 실행 시 현재 config.APP_VERSION 기준으로 모든 파일 버전을 동기화하고 빌드합니다.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, "reconfigure", None)
    if callable(reconf):
        try:
            reconf(encoding="utf-8")
        except Exception:
            pass

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"
BUILD_DIR = ROOT_DIR / "build"
EXT_DIR = ROOT_DIR / "extension"


def get_current_version() -> str:
    cfg_text = (ROOT_DIR / "config.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', cfg_text)
    return m.group(1) if m else "0.1.0"


def bump_patch_version(ver: str) -> str:
    parts = [int(x) for x in ver.split(".")]
    while len(parts) < 3:
        parts.append(0)
    parts[2] += 1
    return f"{parts[0]}.{parts[1]}.{parts[2]}"


def sync_version_files(version: str) -> str:
    """config.py, templates/index.html, extension/manifest.json, extension/popup.html, README.md 버전을 일괄 동기화합니다."""
    vtag = f"v{version}"

    # 1. config.py
    cfg_path = ROOT_DIR / "config.py"
    cfg_text = cfg_path.read_text(encoding="utf-8")
    cfg_text = re.sub(
        r'APP_VERSION\s*=\s*"[^"]+"', f'APP_VERSION = "{version}"', cfg_text
    )
    cfg_path.write_text(cfg_text, encoding="utf-8")

    # 2. templates/index.html
    idx_path = ROOT_DIR / "templates" / "index.html"
    if idx_path.exists():
        idx_text = idx_path.read_text(encoding="utf-8")
        idx_text = re.sub(
            r"default\('v\d+\.\d+\.\d+'\)",
            f"default('{vtag}')",
            idx_text,
        )
        idx_path.write_text(idx_text, encoding="utf-8")

    # 3. extension/manifest.json
    manifest_path = EXT_DIR / "manifest.json"
    if manifest_path.exists():
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["version"] = version
        manifest_path.write_text(
            json.dumps(manifest_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 4. extension/popup.html
    popup_path = EXT_DIR / "popup.html"
    if popup_path.exists():
        popup_text = popup_path.read_text(encoding="utf-8")
        popup_text = re.sub(
            r"Chrome Companion(?:\s+v\d+\.\d+\.\d+)?",
            f"Chrome Companion {vtag}",
            popup_text,
        )
        popup_path.write_text(popup_text, encoding="utf-8")

    # 5. README.md
    readme_path = ROOT_DIR / "README.md"
    if readme_path.exists():
        readme_text = readme_path.read_text(encoding="utf-8")
        readme_text = re.sub(
            r"(# 🚀 원격 다운로더 \(Remote Downloader\))(?:\s+`v\d+\.\d+\.\d+`)?",
            f"\\1 `{vtag}`",
            readme_text,
            count=1,
        )
        readme_path.write_text(readme_text, encoding="utf-8")

    return vtag


def build_extension_zip(version_tag: str) -> Path:
    """크롬 확장 프로그램을 zip 압축 파일로 패키징합니다 (파일명에 버전 제외)."""
    print(f"\n[1/2] 크롬 확장 프로그램 패키징 시작 ({version_tag})...")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    # 기존 버전 번호가 붙은 zip 파일들 삭제
    for old_zip in DIST_DIR.glob("RemoteDownloader-Extension-v*.zip"):
        try:
            old_zip.unlink()
        except OSError:
            pass

    default_zip = DIST_DIR / "RemoteDownloader-Extension.zip"
    if default_zip.exists():
        try:
            default_zip.unlink()
        except OSError:
            pass

    with zipfile.ZipFile(default_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(EXT_DIR):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, EXT_DIR)
                zf.write(abs_path, arcname=rel_path)

    size_kb = default_zip.stat().st_size / 1024
    print(
        f"[OK] 크롬 확장 프로그램 패키징 완료: {default_zip.name} ({size_kb:.1f} KB)"
    )

    doc_ext_dir = Path.home() / "Documents" / "RemoteDownloader-Extension"
    if doc_ext_dir.exists():
        for root, _dirs, files in os.walk(EXT_DIR):
            for file in files:
                src_path = os.path.join(root, file)
                rel_path = os.path.relpath(src_path, EXT_DIR)
                dst_path = doc_ext_dir / rel_path
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
        print(f"[OK] 문서 폴더 확장 프로그램 동기화 완료: {doc_ext_dir}")

    return default_zip


def build_pyinstaller_exe(version_tag: str) -> Path:
    """PyInstaller를 사용하여 단일 실행 파일(RemoteDownloader.exe)을 생성합니다."""
    print(f"\n[2/2] PyInstaller 실행 파일(.exe) 빌드 시작 ({version_tag})...")
    # 기존 버전 번호가 붙은 exe 파일들 삭제
    for old_exe in DIST_DIR.glob("RemoteDownloader-v*.exe"):
        try:
            old_exe.unlink()
        except OSError:
            pass

    venv_pyinstaller = ROOT_DIR / ".venv" / "Scripts" / "pyinstaller.exe"
    pyinstaller_cmd = (
        str(venv_pyinstaller) if venv_pyinstaller.exists() else "pyinstaller"
    )

    exe_base_name = "RemoteDownloader"
    cmd = [
        pyinstaller_cmd,
        "--noconfirm",
        "--clean",
        "--name",
        exe_base_name,
        "--onefile",
        "--console",
        "--add-data",
        f"{ROOT_DIR / 'templates'};templates",
        "--add-data",
        f"{ROOT_DIR / 'static'};static",
        "--add-data",
        f"{ROOT_DIR / 'extension'};extension",
        "--add-data",
        f"{ROOT_DIR / 'bin'};bin",
        "--hidden-import",
        "auth",
        "--hidden-import",
        "config",
        "--hidden-import",
        "cbor2",
        "--hidden-import",
        "transferit",
        "--hidden-import",
        "transferit._crypto",
        "--hidden-import",
        "transferit._models",
        "--hidden-import",
        "downloader",
        "--hidden-import",
        "downloader.engine",
        "--hidden-import",
        "downloader.manager",
        "--hidden-import",
        "downloader.url_helper",
        "--hidden-import",
        "downloader.browser_solver",
        "--hidden-import",
        "downloader.kone_watcher",
        "--hidden-import",
        "downloader.torrent_engine",
        "--hidden-import",
        "downloader.aria2_manager",
        "--hidden-import",
        "downloader.aria2_http_engine",
        "--hidden-import",
        "downloader.resolvers",
        "--collect-submodules",
        "downloader",
        "--collect-submodules",
        "transferit",
        str(ROOT_DIR / "app.py"),
    ]

    print(f"실행 명령: {' '.join(cmd)}\n")
    proc = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if proc.returncode != 0:
        raise RuntimeError(f"PyInstaller 빌드 실패 (Exit code: {proc.returncode})")

    bin_dir = ROOT_DIR / "bin"
    dist_bin_dir = DIST_DIR / "bin"
    if bin_dir.exists():
        dist_bin_dir.mkdir(parents=True, exist_ok=True)
        for item in bin_dir.glob("*"):
            if item.is_file():
                shutil.copy2(item, dist_bin_dir / item.name)
        print(f"[OK] bin 바이너리 복사 완료: {dist_bin_dir}")

    default_exe = DIST_DIR / f"{exe_base_name}.exe"
    if default_exe.exists():
        size_mb = default_exe.stat().st_size / (1024 * 1024)
        print(f"\n[OK] PyInstaller 빌드 성공: {default_exe.name} ({size_mb:.1f} MB)")
        # 임시 .spec 파일 자동 정리
        for spec_f in ROOT_DIR.glob("*.spec"):
            try:
                spec_f.unlink()
            except Exception:
                pass

        return default_exe
    else:
        raise FileNotFoundError(f"빌드 결과물이 생성되지 않았습니다: {default_exe}")


def main():
    cur_ver = get_current_version()
    if "--bump" in sys.argv:
        cur_ver = bump_patch_version(cur_ver)
    version_tag = sync_version_files(cur_ver)

    print("=" * 65)
    print(f"   [Remote Downloader {version_tag}] 배포용 통합 빌드 시작")
    print("=" * 65)

    try:
        ext_zip = build_extension_zip(version_tag)
        exe_file = build_pyinstaller_exe(version_tag)

        print("\n" + "=" * 65)
        print(f"[SUCCESS] {version_tag} 배포용 빌드가 성공적으로 완료되었습니다!")
        print("=" * 65)
        print(f"결과물 폴더: {DIST_DIR}")
        print(
            f"  - 실행 파일     : {exe_file.name} ({exe_file.stat().st_size / (1024 * 1024):.1f} MB)"
        )
        print(
            f"  - 확장 프로그램 : {ext_zip.name} ({ext_zip.stat().st_size / 1024:.1f} KB)"
        )
        print("=" * 65)
    except Exception as e:
        print(f"\n[ERROR] 빌드 중 오류 발생: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
