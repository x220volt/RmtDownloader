"""원격 다운로더 전체 기능 통합 테스트 스크립트."""

import os
import sys
import unittest

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, "reconfigure", None)
    if callable(reconf):
        try:
            reconf(encoding="utf-8")
        except Exception:
            pass

# 프로젝트 루트 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, download_manager
from config import get_local_ip_addresses, load_settings
from downloader.resolvers.direct import DirectResolver
from downloader.resolvers.dispatcher import default_dispatcher
from downloader.resolvers.gofile import GofileResolver
from downloader.resolvers.megaup import MegaUpResolver
from downloader.resolvers.vikingfile import VikingFileResolver


@unittest.skipUnless(
    os.environ.get("RUN_LIVE_TESTS") == "1",
    "Set RUN_LIVE_TESTS=1 for live integration tests",
)
class TestRemoteDownloader(unittest.TestCase):
    def test_01_config_and_ips(self):
        settings = load_settings()
        self.assertIn("download_dir", settings)
        self.assertIn("port", settings)
        self.assertIn("auth_username", settings)
        self.assertIn("auth_password", settings)
        ips = get_local_ip_addresses()
        self.assertTrue(len(ips) > 0, "로컬 IP 주소가 최소 1개 이상 감지되어야 합니다.")
        print(f"✅ 설정 로드 및 감지된 로컬 IP: {ips}")

    def test_02_resolver_matching(self):
        # 1. GoFile URL
        gofile_url = "https://gofile.io/d/AbCd12"
        r1 = default_dispatcher.get_resolver(gofile_url)
        self.assertIsInstance(r1, GofileResolver)

        # 2. VikingFile URL
        viking_url = "https://vikingfile.com/f/xyz789"
        r2 = default_dispatcher.get_resolver(viking_url)
        self.assertIsInstance(r2, VikingFileResolver)

        # 3. MegaUp URL
        megaup_url = "https://megaup.net/1234/testfile.zip"
        r3 = default_dispatcher.get_resolver(megaup_url)
        self.assertIsInstance(r3, MegaUpResolver)

        # 4. Naver Cafe URL
        naver_cafe_url = "https://cafe.naver.com/hansicgu/36206"
        r4 = default_dispatcher.get_resolver(naver_cafe_url)
        from downloader.resolvers.naver_cafe import NaverCafeResolver

        self.assertIsInstance(r4, NaverCafeResolver)

        # 5. Tistory URL
        tistory_url = "https://oldgamebox.tistory.com/7601"
        r5 = default_dispatcher.get_resolver(tistory_url)
        from downloader.resolvers.tistory import TistoryResolver

        self.assertIsInstance(r5, TistoryResolver)

        # 6. Tistory Daum CDN attachment URL
        daum_cdn_url = "https://t1.daumcdn.net/cfile/tistory/155FFB0F4C7F11B302"
        r6 = default_dispatcher.get_resolver(daum_cdn_url)
        self.assertIsInstance(r6, TistoryResolver)

        # 7. TeraBox URLs
        tb_url1 = "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg"
        tb_url2 = "https://terabox.app/s/1HSEb8PZRUE7Z1Tvd3ZtT0g"
        r7_1 = default_dispatcher.get_resolver(tb_url1)
        r7_2 = default_dispatcher.get_resolver(tb_url2)
        from downloader.resolvers.terabox import TeraBoxResolver

        self.assertIsInstance(r7_1, TeraBoxResolver)
        self.assertIsInstance(r7_2, TeraBoxResolver)

        # 8. GigaFile URL
        gf_url1 = "https://38.gigafile.nu/0203-s6576c8859c8e68bd1a83c47f382e03e1"
        gf_url2 = "https://gigafile.nu/1234-abcdef"
        r8_1 = default_dispatcher.get_resolver(gf_url1)
        r8_2 = default_dispatcher.get_resolver(gf_url2)
        from downloader.resolvers.gigafile import GigaFileResolver

        self.assertIsInstance(r8_1, GigaFileResolver)
        self.assertIsInstance(r8_2, GigaFileResolver)

        # 9. X-Share URLs
        xs_url1 = "https://x-share.net/b/cZjQgdSaNfCwKFgT"
        xs_url2 = "https://x-share.net/s/3y7JvEPa"
        r9_1 = default_dispatcher.get_resolver(xs_url1)
        r9_2 = default_dispatcher.get_resolver(xs_url2)
        from downloader.resolvers.xshare import XShareResolver

        self.assertIsInstance(r9_1, XShareResolver)
        self.assertIsInstance(r9_2, XShareResolver)

        # 10. BitTorrent & Magnet
        magnet_url = (
            "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Ubuntu_ISO"
        )
        r_mag = default_dispatcher.get_resolver(magnet_url)
        from downloader.resolvers.torrent import TorrentResolver

        self.assertIsInstance(r_mag, TorrentResolver)

        torrent_web_url = (
            "https://releases.ubuntu.com/22.04/ubuntu-22.04.4-desktop-amd64.iso.torrent"
        )
        r_tor = default_dispatcher.get_resolver(torrent_web_url)
        self.assertIsInstance(r_tor, TorrentResolver)

        # 11. Direct URL
        direct_url = "https://speed.hetzner.de/100MB.bin"
        r10 = default_dispatcher.get_resolver(direct_url)
        self.assertIsInstance(r10, DirectResolver)

        print(
            "✅ 모든 리졸버 URL 라우팅 검증 성공 (Naver Cafe, Tistory, TeraBox, GigaFile, X-Share, BitTorrent 포함)"
        )

    def test_04_tistory_resolver_live(self):
        """실제 티스토리 게시글(oldgamebox/7601) 첨부파일 매칭 테스트."""
        from downloader.resolvers.tistory import TistoryResolver

        resolver = TistoryResolver()
        target_url = "https://oldgamebox.tistory.com/7601"

        resolved = resolver.resolve(target_url)
        self.assertEqual(resolved["source_service"], "Tistory")
        self.assertIn("files", resolved)
        files = resolved["files"]
        self.assertEqual(
            len(files), 50, "게시글 7601에는 50개의 첨부파일이 존재해야 합니다."
        )

        # 파일명 매칭 검증
        self.assertEqual(files[0]["name"], "Ys.part01.rar")
        self.assertTrue(
            files[0]["url"].startswith("https://t1.daumcdn.net/cfile/tistory/")
        )
        self.assertEqual(files[49]["name"], "Ys.part50.rar")
        self.assertIn("subfolder", resolved)
        self.assertTrue(len(resolved["subfolder"]) > 0)
        print(
            f"✅ 티스토리 첨부파일 50개 링크-파일명 매칭 및 서브폴더({resolved['subfolder'][:20]}...) 검증 성공"
        )

    def test_03_auth_and_flask_endpoints(self):
        client = app.test_client()

        # 1. 비인증 상태에서 대시보드 접근 -> /login 으로 302 리다이렉트
        resp_unauth = client.get("/")
        self.assertEqual(resp_unauth.status_code, 302)
        self.assertIn("/login", resp_unauth.headers.get("Location", ""))

        # 2. 비인증 상태에서 API 접근 -> 401 Unauthorized
        resp_api_unauth = client.get("/api/info")
        self.assertEqual(resp_api_unauth.status_code, 401)

        # 3. 로그인 페이지 GET
        resp_login_page = client.get("/login")
        self.assertEqual(resp_login_page.status_code, 200)
        self.assertIn(b"Remote Downloader", resp_login_page.data)

        # 4. 잘못된 계정으로 로그인 시도 -> 200 (에러 메시지 포함)
        resp_bad_login = client.post(
            "/login", data={"username": "wrong", "password": "bad"}
        )
        self.assertEqual(resp_bad_login.status_code, 200)
        self.assertIn("올바르지 않습니다".encode("utf-8"), resp_bad_login.data)

        # 5. 올바른 계정으로 로그인 (현재 설정된 계정 사용)
        settings = load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        resp_good_login = client.post(
            "/login", data={"username": curr_user, "password": curr_pw}
        )
        self.assertEqual(resp_good_login.status_code, 302)

        # 6. 인증된 세션으로 대시보드 접근 -> 200 OK
        resp_dashboard = client.get("/")
        self.assertEqual(resp_dashboard.status_code, 200)
        self.assertIn(b"Remote Downloader", resp_dashboard.data)

        # 7. GET /api/info
        resp_info = client.get("/api/info")
        self.assertEqual(resp_info.status_code, 200)
        data_info = resp_info.get_json()
        self.assertEqual(data_info.get("status"), "online")
        self.assertTrue(len(data_info.get("connect_urls")) > 0)

        # 8. POST /api/jobs (다이렉트 링크 추가)
        test_url = "https://example.com/testfile.zip"
        resp_add = client.post("/api/jobs", json={"url": test_url})
        self.assertEqual(resp_add.status_code, 200)
        data_add = resp_add.get_json()
        self.assertTrue(data_add.get("success"))
        job_id = data_add["jobs"][0]["id"]

        # 9. GET /api/jobs
        resp_jobs = client.get("/api/jobs")
        self.assertEqual(resp_jobs.status_code, 200)
        data_jobs = resp_jobs.get_json()
        job_ids = [j["id"] for j in data_jobs["jobs"]]
        self.assertIn(job_id, job_ids)

        # 10. DELETE /api/jobs/<job_id>
        resp_del = client.delete(f"/api/jobs/{job_id}")
        self.assertEqual(resp_del.status_code, 200)

        # 11. 크롬 확장 프로그램용 헤더 인증 테스트 (X-Auth-User, X-Auth-Password)
        resp_ext_auth = client.post(
            "/api/auth/test",
            headers={"X-Auth-User": curr_user, "X-Auth-Password": curr_pw},
        )
        self.assertEqual(resp_ext_auth.status_code, 200)
        self.assertTrue(resp_ext_auth.get_json().get("success"))

        # 12. 크롬 확장 프로그램 zip 패키지 다운로드 테스트
        resp_zip = client.get(
            "/api/extension/download",
            headers={"X-Auth-User": curr_user, "X-Auth-Password": curr_pw},
        )
        self.assertEqual(resp_zip.status_code, 200)
        self.assertEqual(resp_zip.mimetype, "application/zip")
        self.assertTrue(len(resp_zip.data) > 0)

        # 14. 아이폰 단축어 및 북마클릿용 /add 엔드포인트 테스트 (비인증 클라이언트로 검증)
        client_unauth = app.test_client()
        resp_add_bad = client_unauth.get(
            "/add?url=https://example.com/file.zip&token=wrong_token"
        )
        self.assertEqual(resp_add_bad.status_code, 401)

        # 전용 API Token 및 하위 호환 비밀번호 테스트
        api_token = settings.get("api_token")
        self.assertTrue(bool(api_token))
        self.assertNotEqual(
            api_token, curr_pw
        )  # 마스터 비밀번호와 API 토큰이 분리되어 있어야 함

        resp_add_good = client_unauth.get(
            f"/add?url=https://example.com/iphone_file.zip&token={api_token}"
        )
        self.assertEqual(resp_add_good.status_code, 200)
        self.assertTrue(resp_add_good.get_json().get("success"))

        # 헤더 기반 X-API-Key 인증 테스트
        resp_add_header = client_unauth.get(
            "/add?url=https://example.com/header_file.zip",
            headers={"X-API-Key": api_token},
        )
        self.assertEqual(resp_add_header.status_code, 200)
        self.assertTrue(resp_add_header.get_json().get("success"))

        # 하위 호환: 기존 비밀번호로도 작동 확인
        resp_add_legacy = client_unauth.get(
            f"/add?url=https://example.com/legacy_file.zip&token={curr_pw}"
        )
        self.assertEqual(resp_add_legacy.status_code, 200)

        resp_add_bookmarklet = client_unauth.get(
            f"/add?url=https://example.com/bm_file.zip&token={api_token}&back=1"
        )
        self.assertEqual(resp_add_bookmarklet.status_code, 200)
        self.assertIn(
            "다운로더로 전송되었습니다", resp_add_bookmarklet.data.decode("utf-8")
        )

        # 토큰 재발급 테스트
        resp_regen = client.post("/api/token/regenerate")
        self.assertEqual(resp_regen.status_code, 200)
        new_token = resp_regen.get_json().get("api_token")
        self.assertTrue(bool(new_token))
        self.assertNotEqual(new_token, api_token)

        # 이전 토큰은 즉시 거부되어야 함
        resp_old_token = client_unauth.get(
            f"/add?url=https://example.com/invalidated.zip&token={api_token}"
        )
        self.assertEqual(resp_old_token.status_code, 401)

        # 새 토큰으로 추가 성공
        resp_new_token = client_unauth.get(
            f"/add?url=https://example.com/new_token_ok.zip&token={new_token}"
        )
        self.assertEqual(resp_new_token.status_code, 200)

        # /api/info에 마스터 비밀번호가 노출되지 않음을 검증
        info_resp = client.get("/api/info")
        info_data = info_resp.get_json()
        self.assertEqual(info_data.get("api_token"), new_token)
        self.assertNotEqual(info_data.get("auth_token"), curr_pw)

        print(
            "✅ 크롬 확장 인증/다운로드, 독립 API 토큰 분리 및 재발급, /add 엔드포인트 동작 검증 성공"
        )

    def test_05_base64_decoding_and_dispatch(self):
        """Base64 형식의 다운로드 링크 자동 디코딩 및 작업 수행 검증."""
        import base64

        client = app.test_client()
        settings = load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        client.post("/login", data={"username": curr_user, "password": curr_pw})

        # 1. 단일 표준 Base64 링크 추가
        raw_target = "https://example.com/b64_test_file.zip"
        b64_encoded = base64.b64encode(raw_target.encode()).decode()
        resp = client.post("/api/jobs", json={"url": b64_encoded})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data["jobs"][0]["original_url"], raw_target)

        # 2. Base64 링크 + 파일명 파이프 ('URL | 파일명')
        b64_piped = f"{b64_encoded} | CustomDecodedName.zip"
        resp_piped = client.post("/api/jobs", json={"url": b64_piped})
        self.assertEqual(resp_piped.status_code, 200)
        data_piped = resp_piped.get_json()
        self.assertEqual(data_piped["jobs"][0]["original_url"], raw_target)
        self.assertEqual(data_piped["jobs"][0]["filename"], "CustomDecodedName.zip")

        # 3. 2중 중첩 Base64 인코딩 링크
        double_b64 = base64.b64encode(b64_encoded.encode()).decode()
        resp_double = client.post("/api/jobs", json={"url": double_b64})
        self.assertEqual(resp_double.status_code, 200)
        data_double = resp_double.get_json()
        self.assertEqual(data_double["jobs"][0]["original_url"], raw_target)

        # 4. 패딩 생략(= 부재) 및 URL-safe Base64 링크
        no_pad_b64 = b64_encoded.rstrip("=")
        resp_nopad = client.post("/api/jobs", json={"url": no_pad_b64})
        self.assertEqual(resp_nopad.status_code, 200)
        self.assertEqual(resp_nopad.get_json()["jobs"][0]["original_url"], raw_target)

        # 5. 아이폰 /add 엔드포인트에 Base64 URL 전달
        resp_add_b64 = client.get(f"/add?url={b64_encoded}&token={curr_pw}")
        self.assertEqual(resp_add_b64.status_code, 200)
        self.assertEqual(resp_add_b64.get_json()["job"]["original_url"], raw_target)

        # 6. default_dispatcher.resolve()에 직접 Base64 URL 전달 시 자동 디코딩 검증
        resolved = default_dispatcher.resolve(b64_encoded)
        self.assertEqual(resolved["url"], raw_target)
        self.assertEqual(resolved["source_service"], "Direct")

        print(
            "✅ Base64 인코딩 링크 자동 디코딩 (단일/파이프/중첩/URL-safe/아이폰/디스패처) 검증 성공"
        )

    def test_06_terabox_resolver(self):
        """테라박스(TeraBox) 링크 해석 및 암호 추출, 에러 핸들링 검증."""
        from downloader.resolvers.terabox import (
            TeraBoxResolver,
            _extract_pwd,
            _extract_surl,
        )

        # 1. _extract_pwd 검증
        url_a, pwd_a = _extract_pwd(
            "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg?pwd=abcd"
        )
        self.assertEqual(pwd_a, "abcd")

        url_b, pwd_b = _extract_pwd(
            "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg | pwd: efgh"
        )
        self.assertEqual(pwd_b, "efgh")
        self.assertEqual(url_b, "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg")

        url_c, pwd_c = _extract_pwd(
            "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg#pwd=1234"
        )
        self.assertEqual(pwd_c, "1234")

        # 2. _extract_surl 검증
        surl = _extract_surl("https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg")
        self.assertEqual(surl, "dClmVO8LenqxP0VP5vJddg")

        # 3. 암호가 필요한 사용자 링크(비밀번호 미제공 시 ValueError 발생 및 암호/비밀번호 안내 확인)
        resolver = TeraBoxResolver()
        user_link = "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg"
        with self.assertRaises(ValueError) as ctx:
            resolver.resolve(
                user_link, options={"password": "", "terabox_password": ""}
            )
        self.assertTrue(
            "비밀번호" in str(ctx.exception) or "암호" in str(ctx.exception)
        )

        # 4. 잘못된 비밀번호 제공 시 ValueError 발생 확인
        with self.assertRaises(ValueError) as ctx_wrong:
            resolver.resolve(user_link + "?pwd=invalid_pass")
        self.assertIn("일치하지 않습니다", str(ctx_wrong.exception))

        # 5. /api/jobs에 분리된 password 파라미터 전달 및 설정 검증
        client = app.test_client()
        settings = load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        client.post("/login", data={"username": curr_user, "password": curr_pw})

        # 설정 조회에 terabox_password 포함 여부
        resp_settings = client.get("/api/settings")
        self.assertEqual(resp_settings.status_code, 200)
        self.assertIn("terabox_password", resp_settings.get_json())

        # 분리된 입력칸(password)으로 작업 추가 시 job.options에 비밀번호 보존 검증
        resp_add = client.post(
            "/api/jobs",
            json={
                "url": "https://1024terabox.com/s/1dClmVO8LenqxP0VP5vJddg",
                "password": "testpassword123",
            },
        )
        self.assertEqual(resp_add.status_code, 200)
        self.assertTrue(resp_add.get_json()["success"])

        print(
            "✅ 테라박스(TeraBox) URL/암호 추출 및 보호 링크 핸들링, 분리된 비밀번호 입력칸 검증 성공"
        )

    def test_08_gigafile_resolver_live(self):
        """실제 기가파일(GigaFile) 링크 해석 및 직링크 생성 라이브 테스트."""
        from downloader.resolvers.gigafile import GigaFileResolver

        resolver = GigaFileResolver()
        target_url = "https://38.gigafile.nu/0203-s6576c8859c8e68bd1a83c47f382e03e1"

        resolved = resolver.resolve(target_url)
        self.assertEqual(resolved["source_service"], "GigaFile")
        self.assertIn(
            "download.php?file=0203-s6576c8859c8e68bd1a83c47f382e03e1", resolved["url"]
        )
        self.assertIn(".nsp", resolved["filename"])
        self.assertIn("prog_key=", resolved["cookies"])
        self.assertGreater(resolved["size"], 900_000_000)
        print(
            f"✅ 기가파일(GigaFile) 실시간 링크 분석 및 파일명('{resolved['filename']}') 추출 검증 성공"
        )

    def test_09_base64_mixed_text_and_password(self):
        """Base64 디코딩 시 한글 설명, 추출 코드 등이 섞여 있을 때 URL 및 비밀번호 분리 추출 검증."""
        from downloader.url_helper import (
            parse_download_candidates,
        )

        client = app.test_client()
        settings = load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        client.post("/login", data={"username": curr_user, "password": curr_pw})

        # 사용자가 제공한 실제 Base64 문자열:
        # aHR0cHM6Ly8xMDI0dGVyYWJveC5jb20vcy8xTUgyUXNRNEN6SHVtc3FpWWFJX2VhZyDstpTstpwg7L2U65OcOmRhbzU=
        # 디코드 결과: 'https://1024terabox.com/s/1MH2QsQ4CzHumsqiYaI_eag 추출 코드:dao5'
        b64_sample = "aHR0cHM6Ly8xMDI0dGVyYWJveC5jb20vcy8xTUgyUXNRNEN6SHVtc3FpWWFJX2VhZyDstpTstpwg7L2U65OcOmRhbzU="

        candidates = parse_download_candidates(b64_sample)
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertIn("1024terabox.com/s/1MH2QsQ4CzHumsqiYaI_eag", cand["url"])
        self.assertIn("pwd=dao5", cand["url"])
        self.assertEqual(cand["options"].get("password"), "dao5")

        # API를 통한 등록 검증
        resp = client.post("/api/jobs", json={"url": b64_sample})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data.get("success"))
        created_job = data["jobs"][0]
        self.assertIn(
            "1024terabox.com/s/1MH2QsQ4CzHumsqiYaI_eag", created_job["original_url"]
        )
        # 2. 복수 링크와 각각 다른 비밀번호가 포함된 Base64 인코딩 문자열 검증
        import base64

        multi_text = (
            "링크1: https://1024terabox.com/s/1AAA 추출코드:1111\n"
            "링크2: https://1024terabox.com/s/1BBB 비번:2222\n"
            "링크3: https://38.gigafile.nu/0203-abc 合言葉:3333"
        )
        b64_multi = base64.b64encode(multi_text.encode("utf-8")).decode("utf-8")
        cands_multi = parse_download_candidates(b64_multi)
        self.assertEqual(len(cands_multi), 3)
        self.assertIn("pwd=1111", cands_multi[0]["url"])
        self.assertEqual(cands_multi[0]["options"]["password"], "1111")
        self.assertIn("pwd=2222", cands_multi[1]["url"])
        self.assertEqual(cands_multi[1]["options"]["password"], "2222")
        self.assertIn("dlkey=3333", cands_multi[2]["url"])
        self.assertEqual(cands_multi[2]["options"]["password"], "3333")

        print(
            "✅ Base64 혼합 텍스트/추출코드 자동 정제 및 복수 링크 개별 암호 분리 검증 성공"
        )

    def test_10_transferit_resolver(self):
        """Transfer.it (MEGA 전송 서비스) 링크 해석 및 메타데이터 추출 검증."""
        from downloader.resolvers.dispatcher import default_dispatcher

        target_url = "https://transfer.it/t/M1h1g6ghXDwB"
        resolver = default_dispatcher.get_resolver(target_url)
        self.assertIsNotNone(resolver)
        assert resolver is not None
        self.assertEqual(resolver.name, "Transfer.it")

        resolved = resolver.resolve(target_url)
        self.assertEqual(resolved["source_service"], "Transfer.it")
        self.assertEqual(resolved["filename"], "Stranglehold-BLUS30080_[KOR].zip")
        self.assertGreater(resolved["size"], 6_000_000_000)
        self.assertIsNotNone(resolved.get("task_factory"))
        print(
            f"✅ Transfer.it 실시간 링크 분석 및 파일명('{resolved['filename']}'), 크기({resolved['size']} B) 추출 검증 성공"
        )

    def test_11_onefichier_resolver(self):
        """1fichier.com 무료(Free/Guest) 링크 해석 및 직다운로드 URL 추출 검증."""
        from downloader.resolvers.dispatcher import default_dispatcher

        target_url = "https://1fichier.com/?bfa124m2gi9e4alsarzi&af=1951572"
        resolver = default_dispatcher.get_resolver(target_url)
        self.assertIsNotNone(resolver)
        assert resolver is not None
        self.assertEqual(resolver.name, "1fichier")

        try:
            resolved = resolver.resolve(target_url)
            self.assertEqual(resolved["source_service"], "1fichier")
            self.assertEqual(
                resolved["filename"], "DK-TFS-NSwTcH-[BASE]-NSP-Ziperto.rar"
            )
            self.assertGreater(resolved["size"], 500_000_000)
            self.assertIn("1fichier.com/c", resolved["url"])
            print(
                f"✅ 1fichier 실시간 무료 링크 분석 및 파일명('{resolved['filename']}'), 직링크('{resolved['url']}') 추출 검증 성공"
            )
        except ValueError as e:
            if "쿨다운" in str(e) or "대기 시간" in str(e):
                print(
                    f"✅ 1fichier 무료 다운로드 쿨다운 정상 감지 및 안내 검증 성공: {e}"
                )
            else:
                raise

    def test_12_kio_resolver(self):
        """kio.ac 링크 해석, CBOR API 및 비밀번호 해제 검증."""
        from downloader.resolvers.dispatcher import default_dispatcher

        target_url = "https://kio.ac/c/aH9MT3gW09PLLZXLBjD6eb"
        resolver = default_dispatcher.get_resolver(target_url)
        self.assertIsNotNone(resolver)
        assert resolver is not None
        self.assertEqual(resolver.name, "kio")

        # 1. 비밀번호 없이 요청 시 안내 에러 확인
        with self.assertRaises(ValueError) as ctx:
            resolver.resolve(
                target_url, options={"password": "", "terabox_password": ""}
            )
        self.assertIn("비밀번호", str(ctx.exception))

        # 2. 올바른 비밀번호('dao5')로 요청 시 정상 해석 확인
        resolved = resolver.resolve(target_url, options={"password": "dao5"})
        self.assertEqual(resolved["source_service"], "kio.ac")
        self.assertEqual(resolved["filename"], "SHIN GUNDAM MUSOU JPN ALL DLC JPN.rar")
        self.assertEqual(resolved["size"], 72982919)
        self.assertIn("downloadFile", resolved["url"])
        print(
            f"✅ kio.ac 실시간 링크 분석 및 파일명('{resolved['filename']}'), 크기({resolved['size']} B) 추출 검증 성공"
        )

    def test_13_gdrive_resolver(self):
        """Google Drive 파일 공유 링크 해석 및 직링크 추출 검증."""
        from downloader.resolvers.dispatcher import default_dispatcher

        target_url = (
            "https://drive.google.com/file/d/15yOJYamC2atrHRS4ldzuDsdf_6gKejdb/view"
        )
        resolver = default_dispatcher.get_resolver(target_url)
        self.assertIsNotNone(resolver)
        assert resolver is not None
        self.assertEqual(resolver.name, "gdrive")

        # 1. 폴더 링크 감지 및 안내 에러 확인
        folder_url = "https://drive.google.com/drive/folders/1abc123xyz"
        folder_resolver = default_dispatcher.get_resolver(folder_url)
        self.assertIsNotNone(folder_resolver)
        assert folder_resolver is not None
        with self.assertRaises(ValueError) as ctx:
            folder_resolver.resolve(folder_url)
        self.assertIn("폴더 링크는", str(ctx.exception))

        # 2. 실제 공유 파일 링크 해석 확인
        resolved = resolver.resolve(target_url)
        self.assertEqual(resolved["source_service"], "Google Drive")
        self.assertEqual(
            resolved["filename"],
            "Spriggan-Mark2-Voice-Chapter-Review-PATCHES-20261018.zip",
        )
        self.assertEqual(resolved.get("size"), 242564)
        self.assertIn("drive.usercontent.google.com/download", resolved["url"])
        print(
            f"✅ Google Drive 실시간 링크 분석 및 파일명('{resolved['filename']}'), 크기({resolved.get('size')} B) 추출 검증 성공"
        )

    def test_clear_jobs(self):
        """완료 및 실패 작업 일괄 삭제 API 검증."""
        from downloader.manager import DownloadJob

        client = app.test_client()
        settings = load_settings()
        curr_user = settings.get("auth_username", "admin")
        curr_pw = settings.get("auth_password", "password1234")
        client.post("/login", data={"username": curr_user, "password": curr_pw})

        # 가상 작업 등록 (워커 스레드가 임의로 상태를 바꾸지 않도록 큐 대신 jobs 딕셔너리에 직접 등록)
        with download_manager._lock:
            download_manager.jobs.clear()
            j1 = DownloadJob(
                url="https://example.com/file1.zip", download_dir="downloads"
            )
            j1.status = "completed"
            j2 = DownloadJob(
                url="https://example.com/file2.zip", download_dir="downloads"
            )
            j2.status = "completed"
            j3 = DownloadJob(
                url="https://example.com/file3.zip", download_dir="downloads"
            )
            j3.status = "failed"
            j4 = DownloadJob(
                url="https://example.com/file4.zip", download_dir="downloads"
            )
            j4.status = "cancelled"
            download_manager.jobs[j1.id] = j1
            download_manager.jobs[j2.id] = j2
            download_manager.jobs[j3.id] = j3
            download_manager.jobs[j4.id] = j4

        # 1. 완료된 작업만 일괄 삭제 테스트
        resp_clear_completed = client.post(
            "/api/jobs/clear", json={"filter": "completed"}
        )
        self.assertEqual(resp_clear_completed.status_code, 200)
        data = resp_clear_completed.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["removed_count"], 2)

        remaining_statuses = [j["status"] for j in download_manager.get_all_jobs()]
        self.assertNotIn("completed", remaining_statuses)
        self.assertIn("failed", remaining_statuses)
        self.assertIn("cancelled", remaining_statuses)

        # 2. 실패/취소된 작업 일괄 삭제 테스트
        resp_clear_failed = client.post("/api/jobs/clear", json={"filter": "failed"})
        self.assertEqual(resp_clear_failed.status_code, 200)
        data_f = resp_clear_failed.get_json()
        self.assertTrue(data_f["success"])
        self.assertEqual(data_f["removed_count"], 2)

        self.assertEqual(len(download_manager.get_all_jobs()), 0)
        print("✅ 다운로드 완료/실패 목록 일괄 삭제(/api/jobs/clear) 검증 성공")


if __name__ == "__main__":
    unittest.main()
