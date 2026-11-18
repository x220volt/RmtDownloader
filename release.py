"""GitHub 릴리즈 자동 등록 및 에셋 업로드 스크립트.

사용법:
  python release.py              # 현재 config.APP_VERSION 기준으로 GitHub 릴리즈 생성 및 dist 에셋 업로드
  python release.py --bump       # 버전 올리고 빌드 후 GitHub 릴리즈까지 원클릭 수행

체인지로그 형식:
  Full Changelog: [v0.1.65...v0.1.66](https://github.com/x220volt/RmtDownloader/compare/v0.1.65...v0.1.66)
"""

import os
import re
import subprocess
import sys
from pathlib import Path
import requests

ROOT_DIR = Path(__file__).resolve().parent
DIST_DIR = ROOT_DIR / "dist"

OWNER = "x220volt"
REPO = "RmtDownloader"


def get_git_token() -> str:
    """Windows Credential Manager / git credential helper에서 GitHub 토큰을 가져옵니다."""
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            check=True,
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1].strip()
    except Exception as e:
        print(f"[!] git credential 조회 실패: {e}", file=sys.stderr)
    return ""


def get_current_app_version() -> str:
    cfg_text = (ROOT_DIR / "config.py").read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', cfg_text)
    return m.group(1) if m else "0.1.0"


def get_previous_tag(current_tag: str) -> str:
    """Git 태그 목록 또는 GitHub API에서 현재 태그 직전의 태그명을 가져옵니다."""
    try:
        proc = subprocess.run(
            ["git", "tag", "--sort=-creatordate"],
            capture_output=True,
            text=True,
            check=True,
        )
        tags = [t.strip() for t in proc.stdout.splitlines() if t.strip()]
        for t in tags:
            if t != current_tag and t.startswith("v"):
                return t
    except Exception:
        pass

    # 폴백: 버전 번호의 패치 번호를 1 감소
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", current_tag)
    if m:
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        prev_patch = max(0, patch - 1)
        return f"v{major}.{minor}.{prev_patch}"
    return "v0.1.0"


def publish_github_release(current_tag: str, prev_tag: str, token: str) -> bool:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 사용자 요청 규격: Full Changelog: [v0.1.65...v0.1.66](https://github.com/x220volt/RmtDownloader/compare/v0.1.65...v0.1.66)
    compare_url = f"https://github.com/{OWNER}/{REPO}/compare/{prev_tag}...{current_tag}"
    changelog_body = f"Full Changelog: [{prev_tag}...{current_tag}]({compare_url})"

    print(f"\n[GitHub Release] 태그: {current_tag} (이전: {prev_tag})")
    print(f"[GitHub Release] 본문: {changelog_body}")

    check_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/tags/{current_tag}"
    resp = requests.get(check_url, headers=headers)
    if resp.status_code == 200:
        release_data = resp.json()
        print(f"[OK] 기존 릴리즈 발견 (ID: {release_data['id']}), 본문 갱신 중...")
        patch_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/{release_data['id']}"
        patch_resp = requests.patch(patch_url, headers=headers, json={"body": changelog_body})
        patch_resp.raise_for_status()
        release_data = patch_resp.json()
    else:
        print(f"[*] 새 릴리즈 생성 중: {current_tag}...")
        create_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases"
        payload = {
            "tag_name": current_tag,
            "target_commitish": "main",
            "name": f"Release {current_tag}",
            "body": changelog_body,
            "draft": False,
            "prerelease": False,
        }
        create_resp = requests.post(create_url, headers=headers, json=payload)
        create_resp.raise_for_status()
        release_data = create_resp.json()
        print(f"[OK] 릴리즈 생성 완료: {release_data['html_url']}")

    upload_url_template = release_data["upload_url"]
    upload_base = upload_url_template.split("{")[0]

    files_to_upload = [
        DIST_DIR / "RemoteDownloader.exe",
        DIST_DIR / "RemoteDownloader-Extension.zip",
    ]

    existing_assets = {a["name"]: a["id"] for a in release_data.get("assets", [])}

    for file_path in files_to_upload:
        if not file_path.exists():
            print(f"[!] 파일 없음 (업로드 건너뜀): {file_path}")
            continue

        file_name = file_path.name
        if file_name in existing_assets:
            print(f"[*] 기존 에셋 덮어쓰기 삭제: {file_name}...")
            del_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/{existing_assets[file_name]}"
            requests.delete(del_url, headers=headers)

        size_mb = file_path.stat().st_size / (1024 * 1024)
        print(f"[*] 업로드 중: {file_name} ({size_mb:.1f} MB)...")
        content_type = "application/zip" if file_name.endswith(".zip") else "application/octet-stream"
        up_headers = {
            "Authorization": f"token {token}",
            "Content-Type": content_type,
        }
        with open(file_path, "rb") as f:
            up_resp = requests.post(
                f"{upload_base}?name={file_name}",
                headers=up_headers,
                data=f,
                timeout=300,
            )
        up_resp.raise_for_status()
        print(f"[OK] 업로드 완료: {file_name}")

    print(f"\n[SUCCESS] GitHub Release 완료: {release_data['html_url']}")
    return True


def main():
    token = get_git_token()
    if not token:
        print("[!] GitHub 인증 토큰을 찾을 수 없습니다. git 로그인이 필요합니다.", file=sys.stderr)
        sys.exit(1)

    cur_ver = get_current_app_version()
    current_tag = f"v{cur_ver}"
    prev_tag = get_previous_tag(current_tag)

    publish_github_release(current_tag, prev_tag, token)


if __name__ == "__main__":
    main()
