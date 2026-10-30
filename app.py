"""원격 다운로더 (Remote Downloader) Flask 웹 서버 메인 애플리케이션."""

import io
import json
import logging
import os
import queue
import re
import subprocess
import sys
import time
import zipfile
from datetime import timedelta

from markupsafe import escape

for stream in (sys.stdout, sys.stderr):
    reconf = getattr(stream, "reconfigure", None)
    if callable(reconf):
        try:
            reconf(encoding="utf-8")
        except Exception:
            pass

# Qt QML 난독화 JS 호이스팅 경고(usedbeforedeclared) 터미널 출력 억제
os.environ.setdefault(
    "QT_LOGGING_RULES", "qt.qml.usedbeforedeclared=false;qt.qml.compiler=false"
)

# QJSEngine 사용을 위해 메인 스레드에서 QCoreApplication 인스턴스를 미리 생성합니다.
try:
    from PyQt6.QtCore import QCoreApplication

    _qcore_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
except Exception:
    _qcore_app = None

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from auth import (
    credential_signature,
    hash_password,
    is_token_authorized,
    login_required,
    session_authenticated,
    validate_credentials,
)
from config import (
    APP_VERSION,
    APP_VERSION_TAG,
    generate_api_token,
    get_local_ip_addresses,
    get_or_create_api_token,
    get_or_create_secret_key,
    load_settings,
    save_settings,
)
from downloader.kone_watcher import KoneWatcher
from downloader.manager import DownloadManager
from downloader.url_helper import clean_url, parse_download_candidates

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(threadName)s) %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("remote_downloader")


class _IgnorePollingLogFilter(logging.Filter):
    """주기적인 UI/익스텐션 폴링 요청으로 콘솔 로그가 도배되는 것을 방지합니다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if (
                any(
                    ep in msg
                    for ep in (
                        "/api/jobs",
                        "/api/status",
                        "/api/kone/status",
                        "/api/events",
                    )
                )
                and " 200 " in msg
            ):
                return False
        except Exception:
            pass
        return True


logging.getLogger("werkzeug").addFilter(_IgnorePollingLogFilter())


from config import TORRENTS_DIR

BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.config["SECRET_KEY"] = get_or_create_secret_key()
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    days=int(load_settings().get("session_lifetime_days", 30))
)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


# 크롬 확장 프로그램을 위한 순수 Flask CORS 처리 (별도 외부 패키지 불필요)
@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = Response(status=200)
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Auth-User, X-Auth-Password, X-Auth-Pass, X-Auth-Token"
        )
        res.headers["Access-Control-Allow-Private-Network"] = "true"
        return res


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Auth-User, X-Auth-Password, X-Auth-Pass, X-Auth-Token"
    )
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.before_request
def validate_json_body():
    if request.method not in ("POST", "PUT", "PATCH"):
        return None
    raw_data = request.get_data(cache=True)
    if not raw_data or not raw_data.strip():
        return None
    if request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "JSON 객체가 필요합니다."}), 400
        string_fields = (
            "url",
            "filename",
            "referer",
            "password",
            "pwd",
            "terabox_password",
            "terabox_mode",
            "mode",
            "subfolder",
            "auth_password",
        )
        if any(key in data and not isinstance(data[key], str) for key in string_fields):
            return jsonify(
                {"success": False, "error": "문자열 필드의 형식이 올바르지 않습니다."}
            ), 400
        for key in ("urls", "items"):
            if key in data and not isinstance(data[key], list):
                return jsonify(
                    {"success": False, "error": f"{key}는 배열이어야 합니다."}
                ), 400


@app.errorhandler(ValueError)
def invalid_input(error):
    return jsonify({"success": False, "error": str(error)}), 400


# 다운로드 매니저 초기화
download_manager = DownloadManager(settings_getter=load_settings)

# Kone.gg 게시판 자동 감시 워처 초기화
kone_watcher = KoneWatcher(
    settings_getter=load_settings, download_manager=download_manager
)
if __name__ == "__main__" and load_settings().get("kone_auto_check", False):
    kone_watcher.start()


@app.route("/api/auth/test", methods=["GET", "POST"])
@login_required
def test_auth():
    """크롬 확장 프로그램 등에서 인증 및 연결 상태를 테스트하는 엔드포인트."""
    settings = load_settings()
    return jsonify(
        {
            "success": True,
            "message": "서버 연결 및 인증 성공",
            "username": settings.get("auth_username", "admin"),
            "capture_source_page": settings.get("capture_source_page", False),
            "download_dir": settings.get("download_dir"),
        }
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    """사용자 로그인 페이지."""
    settings = load_settings()
    if not settings.get("auth_enabled", True):
        return redirect(url_for("index"))

    if session_authenticated(settings):
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        input_user = request.form.get("username", "").strip()
        input_pw = request.form.get("password", "").strip()

        if validate_credentials(input_user, input_pw, settings):
            session.clear()
            session.permanent = True
            session["authenticated"] = True
            session["credential_signature"] = credential_signature(settings)
            session["username"] = input_user
            logger.info(f"사용자 로그인 성공: {input_user} (IP: {request.remote_addr})")
            return redirect(url_for("index"))
        else:
            logger.warning(
                f"로그인 실패 시도: {input_user} (IP: {request.remote_addr})"
            )
            error = "아이디 또는 비밀번호가 올바르지 않습니다."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    """로그아웃 및 세션 제거."""
    user = session.get("username")
    session.clear()
    logger.info(f"사용자 로그아웃: {user}")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    """메인 웹 대시보드 페이지."""
    return render_template("index.html", app_version=APP_VERSION_TAG)


@app.route("/manifest.webmanifest")
def web_manifest():
    """스마트폰 홈 화면 웹앱(PWA) 설치를 위한 Web App Manifest."""
    return jsonify(
        {
            "name": "원격 다운로더 (Remote Downloader)",
            "short_name": "원격 다운로더",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#0f1420",
            "theme_color": "#0f1420",
            "icons": [
                {
                    "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='%236366f1'><path d='M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 14.59V8a1 1 0 0 0-2 0v8.59l-2.29-2.3a1 1 0 0 0-1.42 1.42l4 4a1 1 0 0 0 1.42 0l4-4a1 1 0 0 0-1.42-1.42z'/></svg>",
                    "sizes": "192x192",
                    "type": "image/svg+xml",
                    "purpose": "any",
                }
            ],
        }
    )


@app.route("/api/info", methods=["GET"])
@login_required
def get_info():
    """서버 정보 및 접속 IP 목록을 반환합니다."""
    settings = load_settings()
    local_ips = get_local_ip_addresses()
    port = settings.get("port", 7971)
    connect_urls = [f"http://{ip}:{port}" for ip in local_ips]
    api_token = get_or_create_api_token()
    return jsonify(
        {
            "status": "online",
            "version": APP_VERSION,
            "version_tag": APP_VERSION_TAG,
            "local_ips": local_ips,
            "port": port,
            "connect_urls": connect_urls,
            "download_dir": settings.get("download_dir"),
            "terabox_mode": settings.get("terabox_mode", "download"),
            "terabox_password": settings.get("terabox_password", ""),
            "auth_enabled": settings.get("auth_enabled", True),
            "capture_source_page": settings.get("capture_source_page", False),
            "api_token": api_token,
            "auth_token": api_token,
            "username": session.get("username", "admin"),
        }
    )


@app.route("/api/jobs", methods=["GET"])
@login_required
def list_jobs():
    """전체 작업 목록과 통계를 반환합니다."""
    jobs = download_manager.get_all_jobs()
    stats = download_manager.get_stats()
    return jsonify(
        {
            "jobs": jobs,
            "stats": stats,
        }
    )


@app.route("/api/jobs", methods=["POST"])
@login_required
def add_jobs():
    """새로운 다운로드 링크(단일/복수/Base64 인코딩)를 추가합니다."""
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    raw_text = str(data.get("url") or "").strip()
    raw_urls = data.get("urls", [])
    raw_items = data.get("items", [])
    single_filename = str(data.get("filename") or "").strip()
    single_referer = str(data.get("referer") or "").strip()
    single_password = str(
        data.get("password")
        or data.get("pwd")
        or data.get("terabox_password")
        or settings.get("terabox_password", "")
        or ""
    ).strip()
    terabox_mode = str(data.get("terabox_mode") or data.get("mode") or "").strip()
    subfolder = str(data.get("subfolder") or "").strip()
    source_title = str(data.get("source_title") or data.get("title") or "").strip()
    source_url = str(
        data.get("source_url") or data.get("post_url") or single_referer or ""
    ).strip()

    force = bool(data.get("force", False))
    capture_page = bool(data.get("capture_page")) if "capture_page" in data else None
    is_auto = bool(data.get("is_auto", False))

    candidates = parse_download_candidates(
        raw_text=raw_text,
        raw_urls=raw_urls,
        raw_items=raw_items,
        single_filename=single_filename,
        single_referer=single_referer,
        single_password=single_password,
        single_terabox_mode=terabox_mode,
        single_source_title=source_title,
        single_source_url=source_url,
        single_is_auto=is_auto,
    )

    if candidates:
        if capture_page is not None:
            for c in candidates:
                c.setdefault("options", {})["capture_page"] = capture_page
        added_jobs = download_manager.add_urls(
            candidates,
            subfolder=subfolder,
            force=force,
            register_duplicate_as_failed=True,
            is_auto=is_auto,
        )
        dup_failed_count = sum(
            1 for j in added_jobs if j.status == "failed" and "중복" in j.error_message
        )
        normal_added_count = len(added_jobs) - dup_failed_count
        skipped_count = len(candidates) - len(added_jobs)
        return jsonify(
            {
                "success": True,
                "added_count": normal_added_count,
                "dup_failed_count": dup_failed_count,
                "skipped_count": skipped_count,
                "jobs": [j.to_dict() for j in added_jobs],
            }
        )

    return jsonify(
        {
            "success": False,
            "error": "유효한 다운로드 URL 또는 Base64 링크를 입력해 주세요.",
        }
    ), 400


@app.route("/api/upload-torrent", methods=["POST"])
@login_required
def upload_torrent():
    """사용자가 직접 업로드한 .torrent 파일을 저장하고 다운로드 대기열에 등록합니다."""
    if "file" not in request.files:
        return jsonify(
            {"success": False, "error": ".torrent 파일이 첨부되지 않았습니다."}
        ), 400

    uploaded = request.files["file"]
    if not uploaded or not uploaded.filename:
        return jsonify({"success": False, "error": "파일명이 비어 있습니다."}), 400

    filename = uploaded.filename
    if not filename.lower().endswith(".torrent"):
        return jsonify(
            {"success": False, "error": ".torrent 확장자 파일만 업로드할 수 있습니다."}
        ), 400

    subfolder = request.form.get("subfolder", "").strip()

    torrents_dir = TORRENTS_DIR
    torrents_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r'[\\/*?:"<>|]', "_", filename)
    saved_path = torrents_dir / f"{int(time.time())}_{safe_name}"
    uploaded.save(str(saved_path))

    try:
        from downloader.torrent_engine import parse_torrent_file

        info = parse_torrent_file(str(saved_path))
        target_name = info.get("name") or filename
    except Exception:
        target_name = filename

    job = download_manager.add_url(
        str(saved_path), filename=target_name, subfolder=subfolder
    )
    if job:
        return jsonify(
            {
                "success": True,
                "message": f".torrent 작업이 추가되었습니다: {target_name}",
                "job": job.to_dict(),
            }
        )
    else:
        return jsonify(
            {"success": False, "error": "중복 링크이거나 작업 등록에 실패했습니다."}
        ), 400


@app.route("/add", methods=["GET", "POST"])
def quick_add():
    """아이폰 단축어 및 사파리 북마클릿을 위한 초간단 원클릭 추가 엔드포인트."""
    settings = load_settings()

    # 인증 확인 (세션 로그인 or 쿼리스트링 token/api_key/pw or 헤더)
    token = (
        request.args.get("token")
        or request.args.get("api_key")
        or request.args.get("pw")
        or request.headers.get("X-API-Key")
        or request.headers.get("X-Auth-Token")
        or request.headers.get("X-Auth-Password")
    )
    is_authenticated = (
        session_authenticated(settings)
        or (not settings.get("auth_enabled", True))
        or is_token_authorized(token, settings)
    )

    if not is_authenticated:
        return jsonify(
            {"success": False, "error": "인증 토큰(token)이 올바르지 않습니다."}
        ), 401

    json_body = request.get_json(silent=True) or {}
    raw_target_url = request.args.get("url") or json_body.get("url")
    # 단축어/HTTP Shortcuts에서 URL 인코딩 없이 ?token=...&url=https://example.com?a=1&b=2 형태로 보낸 경우 복원
    if raw_target_url and request.query_string:
        from urllib.parse import unquote

        raw_qs = request.query_string.decode("utf-8", errors="ignore")
        m_tail = re.search(r"(?:^|&)url=(.+)$", raw_qs)
        if m_tail:
            tail_val = m_tail.group(1)
            reserved_split = re.split(
                r"&(?=(?:token|api_key|pw|subfolder|folder|password|pwd|terabox_mode|mode|back|referer)=)",
                tail_val,
                maxsplit=1,
            )
            candidate_tail = unquote(reserved_split[0].replace("+", " ")).strip()
            if len(candidate_tail) > len(str(raw_target_url)):
                raw_target_url = candidate_tail
    subfolder = (
        request.args.get("subfolder")
        or request.args.get("folder")
        or json_body.get("subfolder", "")
    )
    pwd = (
        request.args.get("password")
        or request.args.get("pwd")
        or json_body.get("password")
        or ""
    ).strip()
    terabox_mode = (
        request.args.get("terabox_mode")
        or request.args.get("mode")
        or json_body.get("terabox_mode")
        or ""
    ).strip()
    referer = (request.args.get("referer") or json_body.get("referer") or "").strip()
    capture_raw = (
        request.args.get("capture_page")
        or request.args.get("capture")
        or json_body.get("capture_page")
    )
    capture_page = (
        str(capture_raw).lower() in ("1", "true", "yes", "on")
        if capture_raw is not None
        else None
    )

    if not raw_target_url:
        return jsonify(
            {
                "success": False,
                "error": "URL 파라미터가 비어 있습니다. ?url=... 형태로 입력하세요.",
            }
        ), 400

    target_url = clean_url(raw_target_url)
    candidates = parse_download_candidates(
        raw_text=str(raw_target_url),
        single_referer=referer,
        single_password=pwd,
        single_terabox_mode=terabox_mode,
    )
    if not target_url and not candidates:
        return jsonify(
            {
                "success": False,
                "error": "유효한 다운로드 링크 또는 Base64 URL이 아닙니다.",
            }
        ), 400

    try:
        job_options = {}
        if pwd:
            job_options["password"] = pwd
        if terabox_mode:
            job_options["terabox_mode"] = terabox_mode
        candidates = parse_download_candidates(
            raw_text=str(raw_target_url),
            single_referer=referer,
            single_password=pwd,
            single_terabox_mode=terabox_mode,
        )
        if capture_page is not None:
            for c in candidates:
                c.setdefault("options", {})["capture_page"] = capture_page
        if len(candidates) > 1:
            added_jobs = download_manager.add_urls(
                candidates,
                subfolder=subfolder.strip(),
                register_duplicate_as_failed=True,
            )
            job = added_jobs[0] if added_jobs else None
        else:
            cand_opts = dict(candidates[0].get("options") or {}) if candidates else {}
            job_options = dict(cand_opts)
            if referer:
                job_options["referer"] = referer
            if pwd:
                job_options["password"] = pwd
            if terabox_mode:
                job_options["terabox_mode"] = terabox_mode
            final_url = candidates[0]["url"] if candidates else target_url
            job = download_manager.add_url(
                final_url,
                options=job_options,
                subfolder=subfolder.strip(),
                skip_duplicate=bool(settings.get("check_duplicate_url", True)),
                register_duplicate_as_failed=True,
            )
        if not job:
            return jsonify(
                {
                    "success": False,
                    "error": "중복 링크이거나 다운로드 작업을 추가하지 못했습니다.",
                }
            ), 400

        # 사파리 북마클릿에서 호출한 경우 알림 띄우고 이전 페이지로 복귀
        if request.args.get("back") and request.method == "GET":
            alert_msg = (
                f"⚠️ 이미 다운로드된 중복 링크입니다. 실패 목록에 등록되었습니다.\n{job.original_url}"
                if job.status == "failed" and "중복" in job.error_message
                else f"✅ 다운로더로 전송되었습니다!\n{job.original_url}"
            )
            return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>원격 다운로더</title>
<script>
alert({json.dumps(alert_msg).replace("<", chr(92) + "u003c")});
history.back();
</script>
</head><body style="background:#111;color:#eee;font-family:sans-serif;text-align:center;padding:60px 20px;">
<h3>{"⚠️ 중복 다운로드 (실패 목록에 등록됨)" if job.status == "failed" else "✅ 원격 다운로더로 전송 완료!"}</h3>
<p style="color:#aaa;word-break:break-all;">{escape(job.original_url)}</p>
<p><a href="javascript:history.back()" style="display:inline-block;margin-top:20px;padding:10px 20px;background:#6366f1;color:#fff;text-decoration:none;border-radius:8px;">원래 페이지로 돌아가기</a></p>
</body></html>"""

        return jsonify(
            {
                "success": True,
                "message": f"다운로드 작업이 추가되었습니다: {job.original_url}",
                "job": job.to_dict(),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/jobs/<job_id>/pause", methods=["POST"])
@login_required
def pause_job(job_id):
    success = download_manager.pause_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>/resume", methods=["POST"])
@login_required
def resume_job(job_id):
    success = download_manager.resume_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
@login_required
def cancel_job(job_id):
    success = download_manager.cancel_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>/retry", methods=["POST"])
@login_required
def retry_job(job_id):
    success = download_manager.retry_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/<job_id>", methods=["DELETE"])
@login_required
def remove_job(job_id):
    success = download_manager.remove_job(job_id)
    return jsonify({"success": success})


@app.route("/api/jobs/clear", methods=["POST"])
@app.route("/api/jobs", methods=["DELETE"])
@login_required
def clear_jobs():
    """조건(completed/failed/finished/all)에 따라 작업 목록을 일괄 삭제합니다."""
    data = request.get_json(silent=True) or {}
    status_filter = data.get("filter") or request.args.get("filter", "completed")
    if status_filter not in ("completed", "failed", "finished", "all"):
        raise ValueError("Invalid job filter")
    removed_count = download_manager.clear_jobs(status_filter)
    return jsonify(
        {
            "success": True,
            "removed_count": removed_count,
            "filter": status_filter,
        }
    )


@app.route("/api/events")
@login_required
def events():
    """Server-Sent Events (SSE) 엔드포인트: 실시간 다운로드 상태 푸시."""
    client_queue = download_manager.subscribe()

    def event_stream():
        # 연결 즉시 현재 통계 및 작업 상태 최초 전송
        initial_data = {
            "type": "initial_state",
            "jobs": download_manager.get_all_jobs(),
            "stats": download_manager.get_stats(),
        }
        try:
            yield f"data: {json.dumps(initial_data)}\n\n"
            while True:
                try:
                    event = client_queue.get(timeout=15.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    # 연결 유지를 위한 하트비트 ping
                    yield f": ping {time.time()}\n\n"
        except GeneratorExit:
            pass
        finally:
            download_manager.unsubscribe(client_queue)

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/api/addons", methods=["GET"])
@login_required
def get_addons():
    """등록된 호스팅/클라우드 애드온 메타데이터 목록을 반환합니다."""
    from downloader.addons import addon_registry

    return jsonify(
        {
            "success": True,
            "addons": addon_registry.get_ui_manifest(),
        }
    )


@app.route("/api/settings", methods=["GET"])
@login_required
def get_settings():
    s = load_settings()
    # 보안상 현재 비밀번호 값은 비워서 전송
    return jsonify(
        {
            "port": s.get("port", 7971),
            "download_dir": s.get("download_dir"),
            "max_concurrent_downloads": s.get("max_concurrent_downloads", 3),
            "gofile_token": s.get("gofile_token", ""),
            "terabox_cookie": s.get("terabox_cookie", ""),
            "terabox_password": s.get("terabox_password", ""),
            "terabox_mode": s.get("terabox_mode", "download"),
            "terabox_save_path": s.get("terabox_save_path", "/"),
            "auth_enabled": s.get("auth_enabled", True),
            "auth_username": s.get("auth_username", "admin"),
            "auto_subfolder": s.get("auto_subfolder", True),
            "subfolder_rule": s.get("subfolder_rule", "package_or_title"),
            "kone_auto_check": s.get("kone_auto_check", False),
            "kone_board_url": s.get("kone_board_url", "https://kone.gg/s/retrogame"),
            "kone_check_interval_sec": s.get("kone_check_interval_sec", 180),
            "kone_subfolder": s.get("kone_subfolder", True),
            "check_duplicate_url": s.get("check_duplicate_url", True),
            "capture_source_page": s.get("capture_source_page", False),
            "ignore_ssl_certs": s.get("ignore_ssl_certs", False),
            "session_lifetime_days": s.get("session_lifetime_days", 30),
            "api_token": s.get("api_token") or get_or_create_api_token(),
        }
    )


@app.route("/api/token/regenerate", methods=["POST"])
@login_required
def regenerate_token():
    """API 전용 전송 토큰을 새로 발급하고 이전 토큰을 무효화합니다."""
    new_token = generate_api_token()
    updated = save_settings({"api_token": new_token})
    return jsonify(
        {
            "success": True,
            "api_token": updated.get("api_token", new_token),
            "message": "새로운 API 전송 토큰이 발급되었습니다. 이전 토큰은 즉시 무효화되었습니다.",
        }
    )


@app.route("/api/settings", methods=["POST"])
@login_required
def update_settings():
    data = request.get_json(silent=True) or {}

    # 0. 서버 포트 번호 변경 검증 (1 ~ 65535)
    if "port" in data:
        try:
            port_val = int(data["port"])
            if 1 <= port_val <= 65535:
                data["port"] = port_val
            else:
                return jsonify(
                    {
                        "success": False,
                        "error": "포트 번호는 1 ~ 65535 사이의 정수여야 합니다.",
                    }
                ), 400
        except (ValueError, TypeError):
            return jsonify(
                {"success": False, "error": "유효한 포트 번호를 입력해주세요."}
            ), 400

    # 1. 아이디 변경 검증
    if "auth_username" in data:
        new_user = str(data["auth_username"]).strip()
        if not new_user:
            return jsonify(
                {"success": False, "error": "로그인 아이디는 비워둘 수 없습니다."}
            ), 400
        data["auth_username"] = new_user

    # 2. 비밀번호 변경 요청 시 확인 일치 여부 검증 및 scrypt 단방향 암호화 해시 저장 (비워둔 경우 기존 유지)
    new_pw = str(data.get("auth_password", "")).strip()
    if new_pw:
        if "auth_password_confirm" in data:
            confirm_pw = str(data.get("auth_password_confirm", "")).strip()
            if new_pw != confirm_pw:
                return jsonify(
                    {
                        "success": False,
                        "error": "새 비밀번호와 비밀번호 확인이 일치하지 않습니다.",
                    }
                ), 400
        data["auth_password"] = hash_password(new_pw)
    else:
        data.pop("auth_password", None)
    data.pop("auth_password_confirm", None)

    # 3. 브라우저 세션 유지 기간 설정 검증 및 Flask 앱 반영
    if "session_lifetime_days" in data:
        try:
            days = int(data["session_lifetime_days"])
            if 1 <= days <= 365:
                data["session_lifetime_days"] = days
        except (ValueError, TypeError):
            pass

    updated = save_settings(data)
    app.permanent_session_lifetime = timedelta(
        days=int(updated.get("session_lifetime_days", 30))
    )
    download_manager.configure_workers(int(updated.get("max_concurrent_downloads", 3)))
    kone_watcher.restart()

    # 현재 로그인된 브라우저 세션의 아이디 및 자격증명 서명을 즉시 동기화 (로그아웃 튕김 방지)
    session["username"] = updated.get("auth_username", "admin")
    session["credential_signature"] = credential_signature(updated)
    return jsonify(
        {
            "success": True,
            "settings": {
                "port": updated.get("port", 7971),
                "download_dir": updated.get("download_dir"),
                "max_concurrent_downloads": updated.get("max_concurrent_downloads"),
                "auth_username": updated.get("auth_username"),
                "terabox_cookie": updated.get("terabox_cookie"),
                "terabox_password": updated.get("terabox_password"),
                "terabox_mode": updated.get("terabox_mode"),
                "terabox_save_path": updated.get("terabox_save_path"),
                "auto_subfolder": updated.get("auto_subfolder"),
                "subfolder_rule": updated.get("subfolder_rule"),
                "kone_auto_check": updated.get("kone_auto_check"),
                "kone_board_url": updated.get("kone_board_url"),
                "kone_check_interval_sec": updated.get("kone_check_interval_sec"),
                "kone_subfolder": updated.get("kone_subfolder"),
                "check_duplicate_url": updated.get("check_duplicate_url"),
                "capture_source_page": updated.get("capture_source_page", False),
                "ignore_ssl_certs": updated.get("ignore_ssl_certs", False),
                "session_lifetime_days": updated.get("session_lifetime_days", 30),
            },
        }
    )


@app.route("/api/kone/status", methods=["GET"])
@login_required
def kone_status():
    """Kone.gg 자동 감시자 상태 조회."""
    return jsonify(kone_watcher.get_status())


@app.route("/api/kone/check-now", methods=["POST"])
@login_required
def kone_check_now():
    """Kone.gg 게시판의 새 글을 지금 즉시 확인하고 다운로드 링크를 큐에 등록합니다."""
    res = kone_watcher.check_board(is_manual=True)
    return jsonify(res)


@app.route("/api/kone/reset", methods=["POST"])
@login_required
def kone_reset():
    """Kone.gg 감시 기준점을 현재 게시판으로 재설정하거나 최근 N개 게시글을 다시 스캔합니다."""
    data = request.get_json(silent=True) or {}
    recheck_count = int(data.get("recheck_latest_count", 0) or 0)
    return jsonify(kone_watcher.reset_watermark(recheck_latest_count=recheck_count))


@app.route("/api/kone/download-post", methods=["POST"])
@login_required
def kone_download_post():
    """Kone.gg 특정 게시글의 다운로드 링크를 즉시 추출하여 대기열에 추가합니다."""
    data = request.get_json(silent=True) or {}
    post_url = str(data.get("url") or "").strip()
    post_title = str(data.get("title") or "").strip()
    post_id = str(data.get("id") or "").strip()
    return jsonify(
        kone_watcher.download_post_by_url(
            post_url=post_url, post_title=post_title, post_id=post_id
        )
    )


@app.route("/api/check-duplicate", methods=["POST"])
@login_required
def check_duplicate():
    """URL의 중복 여부를 사전 검사합니다."""
    data = request.get_json(silent=True) or {}
    url = str(data.get("url") or "").strip()
    is_dup, reason, info = download_manager.is_duplicate_url(url)
    return jsonify(
        {
            "is_duplicate": is_dup,
            "reason": reason,
            "info": info,
        }
    )


@app.route("/api/history/clear", methods=["POST"])
@login_required
def clear_download_history():
    """완료된 다운로드 URL 히스토리 캐시를 초기화합니다."""
    cleared = download_manager.clear_history()
    return jsonify({"success": True, "cleared_count": cleared})


@app.route("/api/open-folder", methods=["POST"])
@login_required
def open_download_folder():
    """서버 PC의 윈도우 파일 탐색기에서 다운로드 폴더를 엽니다."""
    settings = load_settings()
    d_dir = str(settings.get("download_dir") or "")
    if not d_dir:
        return jsonify(
            {"success": False, "error": "다운로드 경로가 설정되지 않았습니다."}
        ), 400
    try:
        os.makedirs(d_dir, exist_ok=True)
        if os.name == "nt":
            os.startfile(d_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", d_dir], check=False)
        else:
            subprocess.run(["xdg-open", d_dir], check=False)
        return jsonify({"success": True, "path": d_dir})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/extension/download")
def download_extension_zip():
    """크롬 확장 프로그램 폴더를 zip 압축 파일로 패키징하여 다운로드 제공."""
    dist_zip = os.path.join(BASE_DIR, "dist", "RemoteDownloader-Extension.zip")
    if not os.path.isdir(os.path.join(BASE_DIR, "extension")) and os.path.isfile(
        dist_zip
    ):
        return send_file(
            dist_zip,
            as_attachment=True,
            download_name="RemoteDownloader-Extension.zip",
            mimetype="application/zip",
        )

    ext_dir = os.path.join(BASE_DIR, "extension")
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(ext_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, ext_dir)
                zf.write(abs_path, arcname=rel_path)
    memory_file.seek(0)
    return Response(
        memory_file.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=RemoteDownloader-Extension.zip"
        },
    )


def print_banner(host, port, local_ips, username, password):
    """콘솔에 눈에 띄는 접속 가이드 배너를 출력합니다."""
    line = "=" * 65
    print("\n" + line)
    print(
        f"   🚀 [원격 다운로더 (Remote Downloader) {APP_VERSION_TAG}] 서버가 구동되었습니다! 🚀"
    )
    print(line)
    print("  🖥️  서버 PC 브라우저 접속:")
    print(f"      👉 http://localhost:{port}")
    print(f"      👉 http://127.0.0.1:{port}")
    print("\n  📱 스마트폰 또는 다른 PC에서 접속:")
    for ip in local_ips:
        print(f"      👉 http://{ip}:{port}")
    print(line)
    print("  🔒 [보안 로그인 계정 정보]")
    print(f"      • 아이디 (ID)    : {username}")
    if password.startswith(("scrypt:", "pbkdf2:")):
        print("      • 비밀번호 (PW) : [암호화(해시)되어 안전하게 보호 중]")
    elif password == "password1234":
        print(
            "      • 비밀번호 (PW) : password1234 (초기 기본값 - ⚙️ 설정에서 변경 권장)"
        )
    else:
        print("      • 비밀번호 (PW) : [설정된 비밀번호로 보호 중]")
    print(
        "      ※ 웹 화면 ⚙️ 설정에서 언제든 아이디/비밀번호를 자유롭게 변경할 수 있습니다."
    )
    print(line)
    print("  💡 같은 Wi-Fi나 공유기 네트워크에 연결된 기기에서 위 주소를 입력하세요.")
    print("  💡 서버를 종료하려면 Ctrl + C를 누르세요.")
    print(line + "\n")


if __name__ == "__main__":
    settings = load_settings()
    host = str(settings.get("host", "0.0.0.0"))
    port = int(settings.get("port", 7971))
    username = str(settings.get("auth_username", "admin"))
    password = str(settings.get("auth_password", "password1234"))
    local_ips = get_local_ip_addresses()

    print_banner(host, port, local_ips, username, password)

    # threaded=True 로 멀티스레드 요청 및 SSE 완벽 지원
    app.run(host=host, port=port, threaded=True, debug=False)
