#!/bin/bash
set -e

PUID=${PUID:-1000}
PGID=${PGID:-1000}
UMASK=${UMASK:-022}
CONFIG_DIR=${CONFIG_DIR:-/config}
DOWNLOAD_DIR=${DOWNLOAD_DIR:-/downloads}

umask "$UMASK"

# 1. 필수 디렉터리 준비
mkdir -p "$CONFIG_DIR" "$DOWNLOAD_DIR" /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix 2>/dev/null || true

# 2. PUID / PGID 매핑 (Synology/Linux 권한 일치화)
if [ "$(id -u)" = "0" ]; then
    # Group 설정
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" appgroup 2>/dev/null || true
    fi
    GROUP_NAME=$(getent group "$PGID" | cut -d: -f1)

    # User 설정
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -d "$CONFIG_DIR" -s /bin/bash -M appuser 2>/dev/null || true
    fi
    USER_NAME=$(getent passwd "$PUID" | cut -d: -f1)

    # 소유권 조정 (/config 및 /downloads)
    chown -R "$PUID:$PGID" "$CONFIG_DIR" "$DOWNLOAD_DIR" 2>/dev/null || true
    chown -R "$PUID:$PGID" /tmp/.X11-unix 2>/dev/null || true
fi

# 3. 가상 X 디스플레이(Xvfb) 구동 (Cloudflare Turnstile 및 화면 캡처용 브라우저 무음 구동)
export DISPLAY=:99
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
Xvfb :99 -screen 0 1024x768x16 -ac 2>/dev/null &
XVFB_PID=$!

cleanup() {
    if [ -n "$XVFB_PID" ]; then
        kill "$XVFB_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# 4. 환경 변수 기본값 전달
export CHROME_PATH="${CHROME_PATH:-/usr/bin/chromium}"
export IS_DOCKER=1
export CONFIG_DIR="$CONFIG_DIR"
export DOWNLOAD_DIR="$DOWNLOAD_DIR"
export PYTHONUNBUFFERED=1

echo "======================================================================"
echo "🚀 Remote Downloader Starting in Docker Container"
echo "   User UID/GID: $PUID:$PGID (${USER_NAME:-root}:${GROUP_NAME:-root})"
echo "   Config Dir:   $CONFIG_DIR"
echo "   Download Dir: $DOWNLOAD_DIR"
echo "   Display (Xvfb): $DISPLAY"
echo "   Chrome Path:  $CHROME_PATH"
echo "======================================================================"

# 5. 애플리케이션 시작 (root일 경우 gosu로 권한 강등 실행)
if [ "$(id -u)" = "0" ] && [ -n "$USER_NAME" ]; then
    exec gosu "$USER_NAME" python app.py "$@"
else
    exec python app.py "$@"
fi

