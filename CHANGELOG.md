# 📋 변경 내역 (Changelog)

원격 다운로더 (Remote Downloader)의 버전별 주요 변경 사항 및 기능 개선 내역입니다.

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

