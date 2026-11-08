# 원격 다운로더 (Remote Downloader) Dockerfile
FROM python:3.11-slim-bookworm

# 환경 변수 기본 설정
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    CONFIG_DIR=/config \
    DOWNLOAD_DIR=/downloads \
    IS_DOCKER=1 \
    CHROME_PATH=/usr/bin/chromium \
    DISPLAY=:99

WORKDIR /app

# 시스템 필수 패키지 설치
# - aria2: 고속 멀티커넥션 분할 다운로드 및 비트토렌트 엔진
# - chromium, chromium-driver, xvfb: Cloudflare Turnstile 자동 우회 및 웹페이지 전체 캡처용 무음 브라우저
# - fonts-nanum, fonts-noto-cjk: 웹페이지 캡처 시 한국어/한글 글꼴 깨짐(tofu) 방지
# - gosu: Synology NAS UID/GID 일치 및 안전한 권한 강등 실행
# - nodejs: 고속 자바스크립트 엔진 (GoFile 토큰 파싱 보조)
# - Qt6 라이브러리: PyQt6 실행 환경
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 \
    chromium \
    chromium-driver \
    xvfb \
    gosu \
    fonts-nanum \
    fonts-noto-cjk \
    nodejs \
    ca-certificates \
    curl \
    procps \
    libglib2.0-0 \
    libdbus-1-3 \
    libfontconfig1 \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxkbcommon0 \
    libegl1 \
    libgl1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 의존성 설치
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# 애플리케이션 소스 복사
COPY . /app/

# 실행 권한 부여 및 줄바꿈 호환성 보장
RUN chmod +x /app/docker-entrypoint.sh \
    && sed -i 's/\r$//' /app/docker-entrypoint.sh

# 영구 보존 볼륨 및 웹 서비스 포트 정의
VOLUME ["/config", "/downloads"]
EXPOSE 7971

# 컨테이너 상태 모니터링 (30초 주기 비인증 헬스체크)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7971/api/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]

