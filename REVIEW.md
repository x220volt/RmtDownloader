# 코드 리뷰 및 리팩토링 — 2026-10-29

API·인증·설정, 큐/다운로드 엔진, 감시자, 리졸버, 웹 UI/확장 프로그램, 빌드·의존성과 테스트를 검토했다. 이번 변경은 재현 가능한 인증·상태 전이·동시성 문제를 중심으로 적용했다. 기존 2026-10-28 기록은 아래에 보존한다.

## 발견 사항과 수정

| 우선순위 | 문제 | 수정 및 검증 |
|---|---|---|
| 높음 | TeraBox 호스트를 부분 문자열로 판정하여 `terabox.com.evil.test` 같은 주소에 저장된 계정 쿠키를 전달 가능 | 서비스 도메인 목록 및 하위 도메인 일치로 제한. 직접 resolve 호출과 리다이렉트에도 검증. 유사 도메인 거부 테스트 추가 |
| 높음 | 일반 엔진이 HTML 오류를 실패 결과로 반환하지만 매니저는 예외만 처리하여 게시글 재해석에 진입하지 않음 | 실패 결과를 기존 HTML 재해석 경로에 연결. 추출된 파일로 재실행하는 테스트 추가 |
| 높음 | HTML 재해석 도중 취소/삭제된 작업을 다시 queued 상태로 변경 | 재큐잉 직전 잠금 안에서 작업 존재와 downloading 상태 재검사. 취소 및 삭제 회귀 테스트 추가 |
| 보통 | 중복 검사와 큐 등록 사이 경쟁으로 동일 URL 동시 등록 가능 | 검사와 등록을 동일 RLock 구간에서 처리. 8개 동시 제출 중 1개만 등록되는지 검증 |
| 보통 | `hmac.compare_digest(str, str)`에 한글이 포함되면 TypeError | UTF-8 바이트 비교 헬퍼로 인증 비교 통일. 계정·레거시 암호·토큰·세션 서명 테스트 |
| 보통 | 토큰/세션 키 첫 생성 경쟁, 저장 실패를 무시하여 실제 저장값과 다른 키 반환 가능 | 생성·저장을 공유 잠금으로 묶고 저장 실패 전파. 동시 생성과 디스크 오류 테스트 |
| 보통 | 감시 스레드 종료 대기 시간 초과 뒤 restart가 이전 종료 이벤트를 초기화하여 이전 루프가 살아남음 | 실행 세대별 Event 분리, 이전 루프가 새 running 상태를 덮어쓰지 않도록 보호 |
| 보통 | 설정 저장 실패 이전에 Flask 세션 만료 시간이 변경됨 | 저장 성공 이후 반환된 설정으로 적용. 실패 시 기존 값 보존 테스트 |
| 보통 | 작업 종료 이벤트 발행 당시 thread가 남아 있어 즉시 재시도/재개가 거부될 수 있음 | 잠금 안에서 task/thread를 정리한 뒤 업데이트 이벤트 발행 |
| 보통 | 브라우저 솔버 필수 모듈 DrissionPage가 requirements에 없음 | 설치 의존성 추가. 실제 패키지 신규 설치는 수행하지 않음 |
| 낮음 | 사용하지 않는 레거시 암호 추출 래퍼 중복 | Kio/TeraBox 미사용 래퍼 삭제, 공통 추출 함수 유지 |
| 낮음 | UI 테스트가 개인 브라우저 프로필을 사용하고 실패 진단 정보가 없음 | 공통 브라우저 탐색 함수 재사용, 임시 프로필 격리, 종료 코드/stderr 표시 |

## 호환성과 검증 범위

- TeraBox의 알려지지 않은 새 미러 도메인은 계정 쿠키를 자동으로 받지 않는다. 지원을 추가하려면 `TERABOX_DOMAINS`를 검토하여 확장해야 한다.
- 토큰/키 저장 실패는 이제 숨기지 않고 오류로 보고한다. 세션 키를 저장할 수 없는 첫 실행은 시작에 실패할 수 있으며 환경변수 키 지정은 유지된다.
- 외부 서비스 라이브 다운로드, 실제 토렌트 데이터 전송, PyInstaller 배포 빌드는 이번에 실행하지 않았다.
- 전체 모듈 분할이나 API 재설계는 하지 않았다. 큰 모듈의 구조 변경보다 기존 동작을 보존하면서 결함 경로를 줄이는 데 집중했다.
- 설정 파일과 다운로드 기록을 직접 편집하지 않았다. Git 저장소가 없으므로 수정 전 파일은 `.review-backup-20261029/`에 보관했다.

## 검증

- 새 회귀 테스트 11개: `tests/test_review_fixes.py`.
- Python 컴파일 검사 및 JavaScript 3개 파일의 `node --check` 통과.
- 샌드박스의 Windows 임시 폴더 접근 오류를 피하도록 TEMP/TMP를 작업 폴더 `.test-tmp`로 지정했다.
- Chrome은 샌드박스 안에서 Crashpad 시작에 실패했다. 격리된 임시 프로필을 사용하여 승인된 제한 밖 실행에서 UI 테스트 2개 통과를 확인했다.
- 전체 80개 중 67개 통과, 라이브 테스트 13개 제외: `OK (skipped=13)`. 로그: `.test-tmp/verified-tests.log`.

재실행 (PowerShell):

```powershell
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$env:TEMP = (Resolve-Path .test-tmp).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

---

# 코드 리뷰 및 리팩토링 결과

검토일: 2026-10-28

Flask API/인증(`app.py`, `auth.py`), 설정(`config.py`), 다운로드 큐와 엔진(`downloader/`), 서비스별 리졸버(`downloader/resolvers/`), 웹 대시보드(`static/`, `templates/`), Chrome 확장(`extension/`), 실행 스크립트와 테스트(`tests/`)를 전반적으로 검토 및 리팩토링했다. 사용자 `settings.json` 값은 변경하지 않았다.

## 수정 및 리팩토링 항목

| 우선순위 | 문제 | 수정 내용 |
|---|---|---|
| 높음 | `auth.verify_password()`에서 `scrypt:`/`pbkdf2:` 해시 문자열 자체를 비밀번호로 입력해도 통과하는 Pass-the-Hash 취약점 | 해시 접두사(`scrypt:`, `pbkdf2:`)가 있는 경우 해시 검증 분기 후 즉시 반환하여 평문 비교 폴백 차단 |
| 높음 | `app.py` 모듈 import 시점에 `kone_watcher.start()`가 즉시 실행되어 테스트 및 모듈 로드 시 백그라운드 네트워크 스레드가 구동됨 | `if __name__ == "__main__":` 진입점에서만 자동 시작되도록 변경 |
| 높음 | PyInstaller 단일 실행파일(`.exe`) 환경에서 `.torrent` 업로드 및 캐시 경로가 임시 압축 해제 폴더(`_MEIPASS`)를 가리켜 종료 시 유실될 위험 | `config.TORRENTS_DIR`(`CONFIG_DIR / "data" / "torrents"`)로 경로를 일원화하고 `app.py`와 `downloader/resolvers/torrent.py`에서 공통 참조 |
| 높음 | 공개된 고정 SECRET_KEY로 로그인 세션 위조 가능 | `settings.json`에 영구 난수 키 자동 생성·보관(`get_or_create_secret_key`), 환경변수로 고정 키 지정 가능 |
| 높음 | 계정 변경 후 이전 로그인 세션이 계속 유효 | 현재 계정 정보에 대한 HMAC 서명을 세션마다 확인 |
| 높음 | `/add`의 `back` 응답에 URL을 그대로 삽입해 HTML/스크립트 주입 가능 | HTML escape와 script 문맥 JSON escape 적용 |
| 높음 | 링크 해석 중 취소/삭제한 작업이 다운로드 상태로 복귀 | 해석 후, 상태 전환 및 태스크 등록 시 작업 존재와 상태 재검사 |
| 높음 | 실행 중 재시도로 같은 작업을 중복 실행할 가능성 | 재시도는 실패/취소 상태에서 이전 실행 종료 후 허용 |
| 높음 | 커스텀 태스크 생성/실행 예외가 워커를 종료 | 예외를 실패 결과로 변환하고 다음 큐 항목 처리 |
| 높음 | 동일 파일명을 가진 작업들이 같은 `.part` 파일을 사용 | 작업 ID별 임시 파일 경로 분리 |
| 높음 | 파일명 충돌 검사와 최종 이동 사이 경쟁 | 프로세스 내 공용 잠금으로 최종 파일명 결정과 이동 보호 |
| 높음 | 짧게 종료된 응답을 다운로드 완료로 처리 | 알려진 전체 크기와 수신 크기 비교 후 최종 파일로 이동 |
| 높음 | Transfer.it 태스크에 `stop()`이 없어 취소/삭제 실패 | 매니저와 동일한 취소 인터페이스 제공 |
| 높음 | Transfer.it에서 Range 무시 응답을 이전 AES 카운터로 복호화 | 200 응답 시 파일/바이트 수/암호 카운터를 처음부터 재설정 |
| 높음 | Transfer.it 완료 시 기존 파일 삭제 | 일반 엔진과 같은 잠금 및 중복 번호 파일명 사용 |
| 보통 | `engine.sanitize_folder_name()`에서 제목 내 슬래시(`/`) 앞부분을 경로로 오인해 잘라내는 버그 및 `kone_watcher.py`와의 중복 정의 | `engine.sanitize_folder_name()`이 슬래시를 `_`로 치환하고 예약어(`CON`, `NUL` 등)를 방어하도록 개선 후 `kone_watcher.py`에서 공통 함수 재사용 |
| 보통 | `terabox.py`(`_extract_pwd`)와 `kio.py`(`_extract_kio_pwd`)의 URL/비밀번호 분리 로직 중복 | `downloader/url_helper.py`에 `extract_service_password()` 공통 헬퍼를 추출하여 일원화 |
| 보통 | `tistory.py`와 `engine.py`에 `DEFAULT_USER_AGENT` 및 `_MIME_MAP` 상수 중복 정의 | `tistory.py`에서 `engine.py`의 상수를 import하여 재사용 |
| 보통 | 웹 대시보드(`static/js/app.js`) 및 Chrome 확장(`extension/popup.js`, `popup.css`)의 신규 서비스 배지 스타일 누락 및 구형 비밀번호 힌트 잔존 | X-Share, GoFile, VikingFile, MegaUp, BitTorrent 배지 클래스 매핑·스타일 보강 및 기본 비밀번호 안내 문구 일관화 |
| 보통 | `app.py`, `megaup.py`, `xshare.py` 내 미사용 import 및 불필요한 지역 변수 | 미사용 모듈(`secrets`, `hashlib`, `hmac`, `wraps`, `shutil`, `tempfile` 등)과 죽은 변수 제거 |
| 보통 | `tests/test_regressions.py`의 `test_upload_torrent_endpoint`가 로컬 `settings.json` 상태 및 해시 비교 버그에 의존 | 테스트 격리를 위해 `load_settings` 모킹 적용 |

## 검증

- Python 3.14 가상환경: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
- 총 60개 테스트 중 오프라인 단위/회귀 테스트 47개 전원 통과 (`OK (skipped=13)`), 라이브 통합 테스트 13개 명시적 제외.
- `.\.venv\Scripts\python.exe -m compileall -q app.py auth.py config.py build.py downloader tests` 바이트코드 컴파일 통과.
- `node --check`로 `static/js/app.js`, `extension/popup.js`, `extension/background.js` 문법 검사 통과.
