# 📋 변경 내역 (Changelog)

원격 다운로더 (Remote Downloader)의 버전별 주요 변경 사항 및 기능 개선 내역입니다.

---

## [v0.1.41] - 2026-11-01

### 💾 다운로드 목록 영구 저장(Persistence) 및 서버 재부팅 시 자동 이어받기
* **작업 목록 영구 보존 (downloader/manager.py)**:
  * 서버를 종료하거나 재시작하더라도 대기 중(queued), 진행 중(downloading), 완료(done), 실패(ailed) 상태의 모든 작업 목록이 jobs.json에 안전하게 보존됩니다.
  * 불필요한 IO 지연을 최소화하기 위해 비동기 백그라운드 큐 방식으로 상태를 원자적(os.replace)으로 안전하게 디스크에 기록합니다.
* **미완료 작업 자동 재개**:
  * 서버 재시작 시 이전에 다운로드 중이던 작업을 감지하여 대기열 상단으로 복원하고, 기존에 임시 저장된 파일 크기부터 HTTP Range 기반 청크 이어받기를 자동으로 시작합니다.

### 🎮 PlanetEmu (planetemu.net) 레트로 게임 ROM 다운로드 지원
* **PlanetEmu 다운로드 리졸버 추가 (downloader/resolvers/planetemu.py)**:
  * planetemu.net의 ROM 상세 페이지(/rom/..., /roms/...) 및 발급된 직링크(/download/rom?token=...) 자동 감지 및 변환.
  * GET /download/quota 엔드포인트를 통한 쿼터 확인 및 일회용 인증 패스(pass) 자동 발급.
  * POST /download/rom 일회용 토큰 직링크 생성 및 Nginx 속도 제한(429) 감지 시 지수 백오프 자동 재시도 로직 적용.
* **UI 및 확장 프로그램 연동**:
  * 웹 대시보드 배지 스타일(.badge-planetemu) 및 크롬 확장 프로그램 서비스 태그(.tag-planetemu) 지원.

---

## [v0.1.40] - 2026-11-01

### 🛡️ 전체 소스코드 보안 강화 및 숨은 결함 리팩토링 (Security & Bug Fixes)
* **비트토렌트(aria2c) 프로세스 누수 및 스레드 무한 대기 방지 (`downloader/torrent_engine.py`)**:
  * 다운로드 시작 직후 또는 초기화 중 취소/일시중지 시 aria2c 프로세스가 종료되지 않아 `wait()`에서 스레드가 정체되던 현상 해결.
  * 커맨드라인 옵션 주입 방지를 위한 `--` 구분자 적용 및 프로세스 종료 대기 타임아웃(10초) 적용.
* **aria2c 실행 파일 원자적(Atomic) 설치 (`downloader/aria2_manager.py`)**:
  * 다중 스레드 동시 다운로드 시 파일 충돌 방지를 위한 스레드 락 적용 및 `.tmp` 파일 생성 후 `os.replace` 교체로 실행 바이너리 손상 방지.
* **Kone.gg 감시 캐시 순서 보존 및 스레드 안정화 (`downloader/kone_watcher.py`)**:
  * 비순서형 `set` 슬라이싱으로 인해 최신 확인 글이 유실되어 중복 다운로드가 발생하던 결함을 순서 보장 `dict` 구조로 전면 리팩토링.
  * 감시 주기 파싱 오류 대비 안전 fallback 및 예외 시 워처 스레드 상태 초기화 보장.
* **단축 URL 전개 SSRF(Server-Side Request Forgery) 방어 (`downloader/url_helper.py`)**:
  * 단축 URL 및 리다이렉트가 사설망/로컬 호스트(127.0.0.1, 10.x, 192.168.x 등)나 DNS Rebinding으로 연결되는 것을 사전에 검증 및 차단.
* **웹 대시보드 XSS 취약점 제거 (`static/js/app.js`)**:
  * 인라인 `onclick` 이벤트 내 문자열 이스케이프 우회 취약점을 제거하고 `data-copy-url` 이벤트 위임 방식으로 개선.
  * 허용된 안전 스킴(`http://`, `https://`, `magnet:?`) 외의 위험한 URI(`javascript:` 등) 렌더링 원천 차단.
* **브라우저 확장 프로그램 자격증명 보안 강화 (`extension/popup.js`, `extension/background.js`)**:
  * 비밀번호가 `chrome.storage.sync`를 통해 Google 클라우드 계정으로 동기화되지 않도록 로컬 저장소(`chrome.storage.local`)에만 격리 보관.
  * 백그라운드 서비스 워커의 `fetch` 통신에 타임아웃(`AbortSignal.timeout(8000)`)을 부여하여 서버 무응답 시 서비스 워커 정체 방지.

---

## [v0.1.39] - 2026-10-31

### 🔄 원클릭 자가 업데이트 프로세스 재시작 결함 수정 (`app.py`)
* **PyInstaller 원파일(Onefile) 부모 환경 상속 차단**:
  * Windows 단일 실행 파일 환경에서 자가 업데이트 배치 스크립트 실행 시 기존 프로세스의 임시 압축 해제 환경 변수(`_MEIPASS2`, `_PYI_*`)가 상속되어 새 실행 파일이 구 버전의 삭제된 임시 폴더를 참조하다 강제 종료되는 결함 해결.
  * `updater.bat` 및 `subprocess.Popen` 환경 변수에서 PyInstaller 내부 식별자를 초기화하고 `PYINSTALLER_RESET_ENVIRONMENT=1`을 설정하여 새 프로세스가 온전하게 자체 임시 디렉터리를 생성하고 자동 재시작되도록 수정.
  * 대상 실행 파일의 전체 절대 경로로 실행하여 작업 디렉터리 혼선 방지.

### ⚡ 웹 대시보드 실시간 진행률 브로드캐스트 복구 및 터미널 로깅 개선 (`downloader/manager.py`)
* **`on_progress` 실시간 브로드캐스트 복구**:
  * 스트리밍 다운로드 진행 중 웹 대시보드와 브라우저 확장 프로그램으로 `job_progress` 이벤트를 즉시 전파하도록 복원하여 다운로드 속도 및 진행률이 `0 B/s`로 멈춰 보이던 표시 결함 해결.
* **대용량 파일 터미널 콘솔 로그 추가**:
  * 30초 간격으로 현재 다운로드 중인 파일명, 수신 용량/전체 크기, 퍼센트, 전송 속도, 남은 예상 시간을 터미널에 주기적으로 출력하여 백그라운드 진행 상태를 직관적으로 확인 가능.

---

## [v0.1.38] - 2026-10-31

### 🐛 다운로드 엔진 및 매니저 안정화 (Bug Fixes)
* **다운로드 재시도 알림 스코프 결함 수정 (downloader/manager.py)**:
  * on_status 콜백 내 중복 삽입된 브로드캐스트 구문으로 인해 발생하던 NameError 결함을 제거하여 네트워크 끊김 알림 및 자동 이어받기가 중단 없이 안정적으로 동작하도록 수정.
* **다운로드 세션 종료 안전 가드 보강 (downloader/engine.py)**:
  * 스트리밍 요청 전 이미 완료된 파일 판정 등으로 세션이 종료될 때 
esp가 None인 상태에서 
esp.close()를 호출해 발생할 수 있던 AttributeError 예외 방지.
* **링크 분석 및 대기열 처리 안정화**:
  * 대용량 파일 및 복합 리졸버(GigaFile, WoWroms 등) 처리 시 큐 상태 전환 및 안정적인 다운로드 개시 보장.

---

## [v0.1.37] - 2026-10-31

### 🔄 대용량 파일 다운로드 자동 이어받기 및 재시도 (Auto-Resume & Retry)
* **2단계 자동 복구 메커니즘 탑재**:
  * **1단계 (인플라이트 스트리밍 이어받기)**: 다운로드 중 네트워크 순단(`ConnectionResetError`, `ReadTimeout`, `ChunkedEncodingError`, 패킷 손실 등) 발생 시 다운로드를 취소하지 않고, 기존 `.part` 크기를 확인하여 `Range: bytes={existing_size}-` (HTTP 206) 요청으로 즉시 이어받기 수행.
  * **진전도(Progress) 기반 연속 실패 카운트 리셋**: 수 GB ~ 수십 GB 대용량 파일이 여러 번 끊기더라도 1바이트라도 추가 수신된 진전이 있다면 재시도 카운트를 리셋하여 100% 끝까지 다운로드 완수.
  * **2단계 (워커 레벨 직링크 갱신 및 재시도)**: 장시간 다운로드로 직링크 토큰이 만료(403/401 등)되어 스트림이 종료되더라도, 기존 `.part` 파일을 보존한 채 리졸버를 재호출하여 새 직링크를 발급받아 이어받기 수행 (최대 3회).
  * **UI 실시간 알림**: 연결 끊김 발생 시 `"연결 끊김 감지: N초 후 이어받기 재시도 (N/M)..."` 상태를 대시보드에 실시간 브로드캐스트.

### 🧩 크롬 확장 프로그램 원클릭 업데이트 탐색 강화
* **브라우저 프로필 자동 탐색 (`_find_extension_in_browser_preferences`)**:
  * PyInstaller 단일 실행 파일의 일회성 임시 폴더(`%TEMP%\_MEI*`)를 설치 대상에서 원천 배제.
  * Chrome, Edge, Naver Whale, Brave 브라우저의 프로필 환경설정을 분석하여 사용자가 등록한 실제 확장 프로그램 폴더(`Documents/RemoteDownloader-Extension` 등)를 자동 감지 및 갱신하도록 개선.
  * 팝업 에러 메시지 프로퍼티 처리 강화 (`data.error || data.detail || data.message`).

---

## [v0.1.36] - 2026-10-31

### 🕹️ 신규 호스팅 서비스 지원
* **WoWroms (`wowroms.com`) 호스팅 서비스 추가**:
  * 레트로 게임 ROM, ISO, 에뮬레이터 다운로드 페이지 및 게임 소개 페이지 자동 직링크 발급 지원.
  * 9초 카운트다운 타이머 우회 및 동적 토큰 생성(`getKey()`, `getToken(t)`) 연동.
  * `DownloadTask`에 `method="POST"` 및 `post_data` 전송 지원을 탑재하여 폼 기반 직링크 파일 스트리밍 다운로드 지원.
  * 웹 대시보드 및 크롬 확장 프로그램에 WoWroms 골드 뱃지/태그 연동.

### ⚡ 호스팅 리졸버 개선 및 안정화
* **MegaUp (`megaup.net`) Cloudflare 우회 및 직링크 추출 고도화**:
  * CDP(`Network.setBlockedURLs`) 기반 악성 광고 납치 리다이렉트 스크립트 원천 차단.
  * `download.megaup.net/?url=...` 토큰 단독 URL 유입 시 원본 메인 URL 자동 복원 처리.
  * 카운트다운 완료 후 다운로드 버튼 자동 클릭 및 브라우저 네트워크 패킷 실시간 캡처(`page.listen`)를 통한 직링크 획득 구현.
  * `megaupdownup.org` 등 신규 CDN 도메인 감지 및 매핑 지원.

### 🔒 보안 및 UI 버그 수정
* **크롬 확장 프로그램 인증 간섭 방지**:
  * 연결 테스트 및 API 요청 시 브라우저 세션 쿠키 간섭을 방지하고 `X-Auth` 헤더를 우선 검증하도록 `credentials: "omit"` 옵션 강제 적용.
* **업데이트 알림 배너 조건 수정**:
  * 최신 버전을 이미 사용 중일 때 불필요하게 업데이트 배너가 노출되던 문제 해결.

---

## [v0.1.35] - 2026-10-31

### 🚀 본 프로그램 (Remote Downloader Core)
* **GitHub 최신 릴리즈 기반 본 프로그램 원클릭 자가 업데이트(In-Place Self Updater) 탑재**:
  * 웹 대시보드 접속 시 GitHub 공식 릴리즈를 백그라운드에서 자동 확인하여 상단 배너 및 설정창에 업데이트 안내.
  * **실행 바이너리(`RemoteDownloader.exe`) 무중단 자가 교체**:
    * 최신 바이너리를 스트리밍 다운로드 후 PE 매직넘버(`MZ`) 및 무결성 검증.
    * Windows의 파일 락(Lock)을 우회하는 독립 업데이터 배치 스크립트(`updater.bat`)를 통해 기존 exe를 최신 exe로 교체 후 자동 재실행.
    * 웹 UI에서 재시작 카운트다운 오버레이 노출 및 5초 후 자동 새로고침.
  * 개발자 환경(Git 저장소)에서는 `git pull origin main`을 통해 최신 코드 자동 동기화 지원.

### 🧩 크롬 확장 프로그램 (Chrome Extension)
* **GitHub Release 기반 안전한 원클릭 자동 업데이트(B방식) 탑재**:
  * 팝업 실행 시 GitHub 공식 최신 릴리즈를 실시간 감지하여 상단 배너 안내(`vX.X.X 출시`).
  * `[⚡ 원클릭 업데이트]` 클릭 시 파이썬 서버가 최신 `RemoteDownloader-Extension.zip`을 다운로드받아 로컬 확장 폴더에 자동 갱신.
  * **4대 보안 안전장치 적용**:
    1. GitHub 공식 HTTPS 도메인 출처 검증
    2. `@login_required` 인증 토큰 검증
    3. Zip Slip(상위 경로 조작) 방어
    4. 확장자 화이트리스트 검사(`.js`, `.html`, `.css`, `.json`, `.png` 등만 허용, `.exe`/`.bat`/`.dll` 차단)
  * 갱신 완료 즉시 `chrome.runtime.reload()`를 호출하여 브라우저 재시작 없이 즉각 새 버전 로드.

---

## [v0.1.34] - 2026-10-30

### 🏗️ 아키텍처 리팩토링 (Architecture Refactoring)
* **호스팅 및 클라우드 애드온(Addon) 모듈화 아키텍처 도입**:
  * 호스팅별 메타데이터, 리졸버, 커스텀 복원 태스크, UI 뱃지 스타일을 하나로 캡슐화하는 [`HostingAddon`](file:///c:/_coding/remote_downloader/downloader/addons/base.py) 추상 클래스 구현.
  * 전체 20개 플랫폼(BitTorrent, GoFile, MEGA, TeraBox, Naver MYBOX, Naver Cafe, Tistory, GigaFile, Transfer.it, 1fichier, kio.ac, Pixeldrain, Google Drive, X-Share, Romsfun, Rootz, VikingFile, MegaUp, WebPage, Direct)을 독립 애드온으로 등록 관리하는 [`AddonRegistry`](file:///c:/_coding/remote_downloader/downloader/addons/registry.py) 신설.
  * `ResolverDispatcher` 및 `DownloadManager`가 애드온 레지스트리를 통해 리졸버와 복원 태스크를 동적으로 주입받도록 디커플링 완료.
  * 프론트엔드 연동용 `/api/addons` API 엔드포인트 신설 및 동적 서비스 뱃지 매핑 지원.

### 🧩 크롬 확장 프로그램 (Chrome Extension)
* **현재 페이지 다운로드 링크 자동 스캔 및 선택 다운로드 기능 탑재**:
  * 확장 팝업 상단에 `[🔍 페이지 링크 스캔]` 버튼 추가.
  * 활성 탭의 `<a>` 태그, 본문 평문 링크, **Base64 인코딩 링크** 및 인접 **암호/추출코드** 자동 추출.
  * 스캔 결과 체크박스 리스트(서비스 뱃지, 제목, 암호 인풋 필드) 제공 및 **선택 일괄 다운로드** 기능 구현.

---

## [v0.1.33] - 2026-10-30

### ✨ 신규 기능 (New Features)
* **네이버 MYBOX (`mybox.naver.com`) 다운로드 지원**:
  * 네이버 MYBOX 공유 링크(`share/list?shareKey=...`) 및 폴더/파일 속성 API 분석 지원.
  * 고속 CDN 파일 다운로드 직링크(`fs.mybox.naver.com`) 자동 발급 및 이어받기 지원.

---

## [v0.1.32] - 2026-10-30

### ✨ 신규 기능 (New Features)
* **MEGA (`mega.nz` / `mega.co.nz`) 다운로드 지원**:
  * 파일 ID 및 Base64 키 파싱 (`/file/ID#KEY`, `/#ID!KEY`, `/#F!ID!KEY`).
  * 공식 MEGA 클라이언트 API 연동 및 직링크 발급.
  * 실시간 128-bit AES-CTR 블록 복호화 스트리밍 엔진 및 청크 이어받기 지원.

---

## [v0.1.31] - 2026-10-30

### 🔒 보안 및 기능 개선 (Improvements)
* **GoFile 패스워드 보호 링크 자동 해제 및 안내 강화**:
  * GoFile 보호 폴더(`passwordRequired`) 감지 시 수동 입력 암호 및 기본 TeraBox 비밀번호 자동 적용.
  * 비밀번호 불일치 시 명확한 에러 메시지("비밀번호가 보호되어 있거나 올바르지 않습니다") 표시.

---

## [v0.1.30] - 2026-10-30

### ✨ 신규 기능 (New Features)
* **Rootz (`rootz.so`) 다운로드 지원**:
  * `rootz.so/d/{id}` 공유 다운로드 페이지 프록시 API 자동 해석.
  * 고속 백본 CDN 직링크 추출 및 HTTP Range 기반 이어받기 지원.

---

## [v0.1.29] - 2026-10-30

### 🛡️ 네트워크 & 보안 개선 (Network)
* **SSL/TLS 인증서 검증 무시 (`ignore_ssl_certs`) 옵션 추가**:
  * 사내 프록시, 안티바이러스 SSL 검사, 사설 자체 서명 인증서 환경에서 발생하는 `SSLCertVerificationError` 해결.
  * 시스템 환경설정 ➔ 보안 & 연동 탭에서 On/Off 토글 지원.

---

## [v0.1.28] - 2026-10-30

### ✨ 신규 기능 (New Features)
* **Romsfun (`romsfun.com`) 레트로 롬 다운로드 지원**:
  * 게임 롬 상세 페이지 내 AJAX 동적 직링크 발급 로직 연동.
  * 대용량 멀티 파트 ROM 파일 직다운로드 지원.

---

## [v0.1.27] - 2026-10-30

### ⚡ 다운로드 엔진 개선 (Engine)
* **다운로드 유형 태그 구분 (자동 vs 수동)**:
  * 대시보드 진행/완료/실패 목록에 `[자동]`(KoneWatcher 감시) vs `[수동]`(웹/확장프로그램 직접 전송) 구분 배지 표기.
* **다운로드 이어받기 (Resume) 지원**:
  * 네트워크 끊김 또는 일시정지 후 재개 시 기존 임시 파일(`.part`)의 크기를 검사하여 `Range: bytes=N-` 헤더로 이어서 다운로드.

---

## [v0.1.26] - 2026-10-30

### 🛡️ 클라우드플레어 우회 개선 (Improvements)
* **VikingFile (`vikingfile.com`, `vik1ngfile.site`) 안정화**:
  * Cloudflare 세션 및 리다이렉트 추적 로직 개선으로 챌린지 실패 방지.

---

## [v0.1.20] ~ [v0.1.25] - 2026-10-29

### ✨ 신규 호스팅 지원 추가
* **X-Share (`x-share.net`)**: 단일 파일 및 번들(Bundle) 묶음 API 해석, 일괄 다운로드.
* **MegaUp (`megaup.net`)**: Cloudflare 우회 및 다운로드 토큰 자동 추출.
* **kio.ac (`kio.ac`)**: CBOR 인코딩 컬렉션/디렉터리 트리 파싱 및 비밀번호 해제.
* **Transfer.it (`transfer.it`)**: MEGA 기반 128-bit AES-CTR 스트리밍 복호화 다운로드.
* **GigaFile (`gigafile.nu`)**: 다운로드 키(`dlkey`) 및 만료 토큰 자동 파싱.

---

## [v0.1.10] ~ [v0.1.19] - 2026-10-28 ~ 2026-10-29

### 🤖 자동화 기능 (Automation)
* **Kone.gg 자동 게시판 감시 (`KoneWatcher`)**:
  * 주기적 백그라운드 크롤링, 신규 글 내 Base64 링크 자동 파싱 및 다운로드 큐 등록.
  * 중복 등록 방지 캐시 및 히스토리 관리.
* **네이버 카페 & 티스토리 첨부파일 추출**:
  * 게시글 본문 링크 분석을 통한 첨부파일 일괄 추출 및 제목별 하위 폴더 자동 분류.

---

## [v0.1.0] ~ [v0.1.9] - 2026-10-27 ~ 2026-10-28

### 🚀 최초 릴리즈 (Initial Release)
* **웹 기반 반응형 대시보드 & 모바일 PWA 지원**:
  * 실시간 SSE 속도/진행률/ETA 모니터링, 작업 일시정지/재개/취소.
* **TeraBox & Google Drive & 1fichier & BitTorrent 지원**:
  * TeraBox 직접 다운로드 및 내 클라우드 저장(`save` 모드).
  * 내장 `aria2c` 엔진 기반 고속 토렌트 및 마그넷 다운로드.
* **단축어 & 북마클릿 & 크롬 확장 프로그램 연동**:
  * iOS 공유 시트 단축어, 안드로이드 북마클릿/HTTP Shortcuts, Chrome Extension 연동 지원.
* **보안 인증**:
  * `scrypt` 비밀번호 해시, 세션 관리, API 전용 토큰 발급.

