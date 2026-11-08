# 🚀 원격 다운로더 (Remote Downloader) `v0.1.54`

스마트폰(iPhone / Android)이나 다른 PC의 웹 브라우저에서 링크·마그넷·Base64 코드를 전송하면, 서버 PC에서 해당 파일을 자동으로 해석·다운로드해 주는 파이썬 기반 원격 다운로드 센터입니다.

---

## ✨ 주요 기능

1. **반응형 웹 대시보드 & 모바일 PWA 앱**:
   - 같은 Wi-Fi/공유기 네트워크 또는 Tailscale(외부 VPN)로 연결된 **스마트폰**이나 **다른 PC**에서 웹 브라우저로 즉시 접속할 수 있습니다.
   - 스마트폰 브라우저에서 **[홈 화면에 추가]** 시 주소창 없는 독립 실행형 전체 화면 앱(PWA)으로 작동합니다.
   - 실시간 SSE(Server-Sent Events) 스트리밍으로 새로고침 없이 다운로드 속도, 진행률, ETA를 실시간 표시합니다.

2. **다중 호스팅 & 토렌트·포털 첨부파일 지원**:
   - **TeraBox (`1024terabox.com`, `terabox.app` 등)**: 서버 PC 직접 다운로드 및 내 테라박스 클라우드 즉시 저장(`save` 모드) 지원, 공유 암호 자동 풀이.
   - **Google Drive (`drive.google.com`, `docs.google.com`)**: 소용량 직다운로드 및 대용량 파일 바이러스 검사 경고 토큰 자동 우회.
   - **GoFile (`gofile.io/d/...`)**: `QJSEngine` 기반 웹 토큰(`wt`) 및 게스트/계정 토큰 자동 연동, 비밀번호 보호 링크 자동 풀이(수동 입력 또는 기본 TeraBox 비밀번호 자동 적용) 및 상태 안내.
   - **1fichier (`1fichier.com/?...`)**: 무료 다운로드 대기/제휴 파라미터 자동 처리 및 직링크 추출.
   - **kio.ac (`kio.ac/c/...`, `kio.ac/f/...`)**: CBOR 컬렉션/디렉터리 API 해석 및 비밀번호 해제.
   - **Transfer.it (`transfer.it/t/...`)**: MEGA 기반 AES-128-CTR 실시간 스트리밍 복호화 및 이어받기 지원.
   - **MEGA (`mega.nz/file/...`, `mega.co.nz/#!...`)**: MEGA 파일 직링크 발급, 실시간 AES-128-CTR 스트리밍 복호화 및 이어받기 지원.
   - **GigaFile (`gigafile.nu/...`)**: 기가파일 토큰 및 다운로드 키(`dlkey`) 자동 파싱.
   - **VikingFile (`vikingfile.com`, `vik1ngfile.site`)**: 공식 다운로드 엔드포인트 및 리다이렉트 추적.
   - **MegaUp (`megaup.net`)**: Cloudflare 우회 및 다운로드 토큰 URL 해석.
   - **X-Share (`x-share.net/s/...`, `x-share.net/b/...`)**: 단일 파일 및 묶음(Bundle) API 토큰 발급·일괄 다운로드.
   - **Romsfun (`romsfun.com`)**: 레트로 게임 ROM 다운로드 페이지 자동 직링크 발급 및 멀티 파트 다운로드 지원.
   - **Romspure (`romspure.cc`)**: 레트로 게임 ROM 다운로드 페이지 동적 AJAX 토큰 해석, Cloudflare 우회 및 멀티 파트 다운로드 지원.
   - **PlanetEmu (`planetemu.net`)**: 레트로 게임 ROM 자동 쿼터 인증 및 고속 직링크 다운로드 지원.
   - **WoWroms (`wowroms.com`)**: 레트로 게임 ROM, ISO, 에뮬레이터 다운로드 페이지 자동 직링크 발급 및 스트리밍 다운로드 지원.
   - **Rootz (`rootz.so/d/...`)**: 프록시 다운로드 API 및 고속 CDN 직링크 자동 추출, 이어받기 지원.
   - **네이버 MYBOX (`mybox.naver.com/share/...`)**: 공유 링크 및 파일 속성 API를 통한 고속 직링크 발급 및 이어받기 지원.
   - **네이버 카페 & 티스토리 (`cafe.naver.com`, `*.tistory.com`, `kakaocdn`, `daumcdn`)**: 게시글 본문 내 모든 첨부파일 일괄 추출 및 게시글 제목별 하위 폴더 자동 분류.
   - **BitTorrent & Magnet (`magnet:?xt=...`, `.torrent` 파일)**: 내장 `aria2c` 엔진 연동을 통한 고속 토렌트/마그넷 다운로드 및 `.torrent` 파일 드래그 앤 드롭 업로드.
   - **Base64 & 혼합 텍스트 자동 해독**: 다중 중첩 Base64 링크, 한글 설명과 추출코드(`비번:1234`, `추출 코드:xxxx` 등)가 섞인 텍스트에서 URL과 암호를 자동 분리·매핑.

3. **Kone.gg 게시판 새 글 자동 감시 (`KoneWatcher`)**:
   - 지정한 게시판을 설정 주기마다 백그라운드 감시하여 새 글의 다운로드 링크(Base64 포함)를 자동으로 큐에 등록하고 중복 URL은 건너뜁니다.

---

## 🛠 설치 및 실행 방법

### 1. 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. 서버 실행
- **Windows 원클릭 실행**: `run.bat` 더블 클릭
- **터미널 실행**:
  ```bash
  python app.py
  ```
- 기본 접속 포트: `7971` (`http://localhost:7971`)
- 초기 로그인 계정: 아이디 `admin` / 비밀번호 `password1234`
  *(로그인 후 우측 상단 **⚙️ 시스템 환경설정 ➔ 🔒 보안 & 연동** 탭에서 아이디와 비밀번호를 자유롭게 변경할 수 있으며, 비밀번호는 `scrypt` 단방향 해시로 암호화 저장됩니다.)*

### 3. 🐳 도커(Docker) 및 시놀로지 NAS(Synology Container Manager) 설치
시놀로지 NAS, QNAP, Unraid 또는 일반 Linux 서버에서 Docker Compose를 사용해 간편하게 실행할 수 있습니다.

#### A. `docker-compose.yml` 파일 작성
```yaml
version: '3.8'

services:
  remote-downloader:
    container_name: remote-downloader
    build: .
    image: remote-downloader:latest
    restart: unless-stopped
    ports:
      - "7971:7971"
    environment:
      # 시놀로지/Linux 호스트 계정의 UID / GID (SSH에서 `id` 입력 시 확인 가능)
      # 시놀로지 일반 사용자: 보통 1026 / 100, 리눅스: 보통 1000 / 1000
      - PUID=1000
      - PGID=1000
      - TZ=Asia/Seoul
      - UMASK=022
      - CONFIG_DIR=/config
      - DOWNLOAD_DIR=/downloads
    volumes:
      # 설정 및 큐/히스토리 저장 경로 (시놀로지 예: /volume1/docker/remote_downloader/config:/config)
      - ./config:/config
      # 다운로드 파일 저장 실제 경로 (시놀로지 예: /volume1/downloads:/downloads)
      - ./downloads:/downloads
    # Cloudflare 우회 및 웹 캡처 브라우저 메모리 확보 (512MB 권장)
    shm_size: "512mb"
```

#### B. 실행 명령어
```bash
# 컨테이너 빌드 및 백그라운드 실행
docker compose up -d --build

# 실행 로그 확인
docker compose logs -f
```

#### C. 시놀로지 NAS (Container Manager) 설정 팁
1. **Container Manager** 실행 ➔ **[프로젝트]** ➔ **[생성]** 클릭
2. 프로젝트 이름(예: `remote-downloader`) 및 경로(예: `/docker/remote_downloader`) 지정
3. 소스 코드 및 `docker-compose.yml`, `Dockerfile`을 해당 경로에 복사하거나 웹 에디터에 붙여넣기
4. 볼륨 매핑에서 `/downloads`를 실제 파일이 저장될 NAS 공유 폴더(예: `/volume1/downloads`)로 연결
5. 환경변수 `PUID`와 `PGID`를 본인의 시놀로지 사용자 계정 ID(기본 `1026:100`)로 설정하면 다운로드된 파일을 NAS 파일 스테이션이나 SMB 네트워크 드라이브에서 권한 문제 없이 자유롭게 읽기/수정/삭제할 수 있습니다.

---

## 📱 스마트폰 원격 다운로드 설정 가이드

웹 대시보드 우측 상단 **⚙️ 설정 ➔ 📱 스마트폰 가이드** 탭에서 현재 서버 주소와 전용 API 토큰이 적용된 복사 버튼을 바로 사용할 수 있습니다.

### 1) 🍎 아이폰 / iPad 단축어 (공유 시트 원터치 전송)
1. 아이폰 기본 **[단축어]** 앱 실행 ➔ 우측 상단 **`+`** 탭
2. 하단 중앙 **`(i)` 정보** 아이콘 ➔ **[공유 시트에서 보기]** 켜기 ➔ **[완료]**
3. **[동작 추가]** ➔ **`URL의 콘텐츠 가져오기`** 검색 후 추가 (GET 기본값 유지)
4. **URL** 자리에 **⚙️ 설정 ➔ 📱 스마트폰 가이드 ➔ 🍎 아이폰 단축어**의 주소를 복사해 붙여넣기:
   ```text
   http://<서버IP>:7971/add?token=<API토큰>&url=[단축어 입력]
   ```
   *(주소 맨 끝의 `[단축어 입력]` 자리에 키보드 상단 파란색 변수 **'단축어 입력'**을 끼워 넣으면 완성! 웹페이지 공유뿐만 아니라 본문 내 Base64 코드나 추출코드 텍스트를 드래그해 [공유]해도 자동 해독됩니다.)*

### 2) 🤖 안드로이드 설정 (북마클릿 & HTTP Shortcuts 공유 메뉴)
- **방법 A. 모바일 브라우저 북마클릿 (별도 앱 불필요)**
  1. 삼성 인터넷 또는 모바일 크롬에서 아무 페이지나 북마크 추가 후 **[편집/수정]** 선택
  2. 이름을 `PC로 전송`으로 지정하고 URL 칸에 **⚙️ 설정 ➔ 📱 스마트폰 가이드**의 북마클릿 코드를 붙여넣기
  3. 다운로드할 글에서 북마크를 터치(또는 Base64/링크 텍스트를 드래그 선택한 뒤 북마크 터치)하면 PC로 전송 후 1초 만에 원래 화면으로 돌아옵니다.
- **방법 B. 안드로이드 [공유] 메뉴 연동 (`HTTP Shortcuts` 무료 오픈소스 앱)**
  1. 플레이스토어에서 **HTTP Shortcuts** 설치 ➔ 우측 하단 **`+`** ➔ **Standard Shortcut** 생성
  2. 이름: `PC로 다운로드`, Method: `GET`, URL에 아래 형식의 주소 붙여넣기:
     ```text
     http://<서버IP>:7971/add?token=<API토큰>&url={text}
     ```
  3. **Trigger / Miscellaneous ➔ [Share into shortcut]** 옵션을 켜고 저장하면, 모든 앱의 **[공유]** 메뉴에서 원터치 백그라운드 전송이 가능합니다.

### 3) 📲 홈 화면 웹앱 (PWA) 설치
- **아이폰(사파리)**: 서버 접속 후 하단 **[공유]** 버튼 ➔ **[홈 화면에 추가]**
- **안드로이드(크롬/삼성인터넷)**: 서버 접속 후 메뉴(`⋮` 또는 `≡`) ➔ **[홈 화면에 추가 / 앱 설치]**
- 주소창 없는 전체 화면 앱으로 구동되어 실시간 속도 확인 및 작업 제어가 가능합니다.

### 4) 🌐 외부(LTE / 5G) 접속 방법
- **Tailscale (권장)**: 서버 PC와 스마트폰에 무료 **Tailscale** 앱을 설치하고 동일 계정으로 로그인하면 부여되는 `100.x.x.x` 사설 IP(`http://100.x.x.x:7971`)로 외출 중에도 집 Wi-Fi와 동일하게 접속·전송할 수 있습니다.
- **공유기 포트포워딩**: 공유기 관리자 페이지에서 외부 포트를 서버 PC 포트로 포워딩하고 DDNS 주소를 사용합니다.

---

## 🧩 크롬 확장 프로그램 (Chrome Extension) 설정 가이드

1. **다운로드 및 압축 해제**:
   - 프로젝트 내 [`extension/`](file:///c:/_coding/remote_downloader/extension) 폴더를 그대로 사용하거나, 웹 대시보드 **⚙️ 설정 ➔ 🧩 크롬 확장** 탭에서 `RemoteDownloader-Extension.zip`을 다운로드해 압축을 해제합니다.
2. **브라우저에 로드**:
   - 크롬/엣지/웨일/브레이브 주소창에 `chrome://extensions` 입력 ➔ 우측 상단 **[개발자 모드]** 켜기 ➔ 좌측 상단 **[압축해제된 확장 프로그램을 로드합니다]** 클릭 후 폴더 선택
3. **서버 및 계정 연동**:
   - 확장 프로그램 팝업을 열고 하단 **[⚙️ 서버 및 계정 설정]**에서 서버 주소(`http://127.0.0.1:7971` 또는 내부 IP), 아이디, 비밀번호 입력 후 **[설정 저장]** 클릭
4. **주요 기능**:
   - 웹페이지 내 아무 링크나 드래그한 텍스트(Base64/링크) 위에서 **마우스 우클릭 ➔ [원격 다운로더로 보내기]** (현재 페이지 Referer 자동 포함)
   - 확장 팝업에서 **[현재 탭 URL 서버로 보내기]**, **[➕ 링크 직접 추가 / 드래그]**, 실시간 다운로드 진행률/속도 모니터링 및 **[📂 폴더 열기]** 지원

---

## 🧪 테스트 실행

오프라인 단위 및 회귀 테스트 전체 실행:
```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

실제 외부 서비스 라이브 통합 테스트 포함 실행(선택):
```powershell
$env:RUN_LIVE_TESTS = '1'
.\.venv\Scripts\python.exe tests/test_all.py
Remove-Item Env:RUN_LIVE_TESTS
```

코드 리뷰 및 리팩토링 상세 내역은 [REVIEW.md](file:///c:/_coding/remote_downloader/REVIEW.md), 전체 버전별 세부 변경 기록은 [CHANGELOG.md](file:///c:/_coding/remote_downloader/CHANGELOG.md)를 참고하세요.
