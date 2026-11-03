"""각 호스팅 및 클라우드 서비스별 애드온 정의 모듈."""

from typing import Any, Callable, Dict, Optional

from ..resolvers.base import BaseResolver
from .base import HostingAddon


# -------------------------------------------------------------
# 1. P2P & 토렌트
# -------------------------------------------------------------
class TorrentAddon(HostingAddon):
    addon_id = "torrent"
    name = "BitTorrent"
    category = "p2p"
    category_name = "토렌트 & P2P"
    domains = ["magnet:", ".torrent"]
    badge_class = "badge-torrent"
    badge_label = "BitTorrent & Magnet"
    badge_style = {
        "background": "rgba(99, 102, 241, 0.15)",
        "color": "#818cf8",
        "border": "1px solid rgba(99, 102, 241, 0.3)",
    }
    description = "내장 aria2c 엔진을 통한 고속 BitTorrent 및 Magnet 다운로드"
    priority = 10

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.torrent import TorrentResolver

        return TorrentResolver()


# -------------------------------------------------------------
# 2. 해외 클라우드 & 파일 호스팅
# -------------------------------------------------------------
class GofileAddon(HostingAddon):
    addon_id = "gofile"
    name = "GoFile"
    category = "cloud"
    domains = ["gofile.io"]
    badge_class = "badge-gofile"
    badge_label = "GoFile"
    badge_style = {
        "background": "rgba(14, 165, 233, 0.15)",
        "color": "#38bdf8",
        "border": "1px solid rgba(14, 165, 233, 0.3)",
    }
    description = "QJSEngine 기반 웹 토큰(wt) 발급 및 비밀번호 자동 풀이 지원"
    priority = 20

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.gofile import GofileResolver

        return GofileResolver()


class VikingFileAddon(HostingAddon):
    addon_id = "vikingfile"
    name = "VikingFile"
    category = "cloud"
    domains = ["vikingfile.com", "vik1ngfile.site"]
    badge_class = "badge-viking"
    badge_label = "VikingFile"
    badge_style = {
        "background": "rgba(234, 88, 12, 0.15)",
        "color": "#fb923c",
        "border": "1px solid rgba(234, 88, 12, 0.3)",
    }
    description = "공식 다운로드 엔드포인트 및 Cloudflare 추적 지원"
    priority = 30

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.vikingfile import VikingFileResolver

        return VikingFileResolver()


class MediaFireAddon(HostingAddon):
    addon_id = "mediafire"
    name = "MediaFire"
    category = "cloud"
    domains = ["mediafire.com"]
    badge_class = "badge-mediafire"
    badge_label = "MediaFire (mediafire.com)"
    badge_style = {
        "background": "rgba(0, 114, 255, 0.15)",
        "color": "#38bdf8",
        "border": "1px solid rgba(0, 114, 255, 0.3)",
    }
    description = "MediaFire 직링크 자동 추출 및 고속 분할 다운로드 지원"
    priority = 35

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.mediafire import MediaFireResolver

        return MediaFireResolver()


class MegaUpAddon(HostingAddon):
    addon_id = "megaup"
    name = "MegaUp"
    category = "cloud"
    domains = ["megaup.net"]
    badge_class = "badge-megaup"
    badge_label = "MegaUp"
    badge_style = {
        "background": "rgba(249, 115, 22, 0.15)",
        "color": "#fb923c",
        "border": "1px solid rgba(249, 115, 22, 0.3)",
    }
    description = "Cloudflare 우회 및 다운로드 토큰 자동 추출"
    priority = 40

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.megaup import MegaUpResolver

        return MegaUpResolver()


class TeraBoxAddon(HostingAddon):
    addon_id = "terabox"
    name = "TeraBox"
    category = "cloud"
    domains = ["1024terabox.com", "terabox.app", "terabox.com", "freeterabox.com"]
    badge_class = "badge-terabox"
    badge_label = "TeraBox"
    badge_style = {
        "background": "rgba(59, 130, 246, 0.15)",
        "color": "#60a5fa",
        "border": "1px solid rgba(59, 130, 246, 0.3)",
    }
    description = "직접 다운로드 및 내 계정 즉시 담기(save 모드), 공유 암호 자동 풀이"
    priority = 80

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.terabox import TeraBoxResolver

        return TeraBoxResolver()


class GigaFileAddon(HostingAddon):
    addon_id = "gigafile"
    name = "GigaFile"
    category = "cloud"
    domains = ["gigafile.nu", "xgf.nu"]
    badge_class = "badge-gigafile"
    badge_label = "GigaFile"
    badge_style = {
        "background": "rgba(236, 72, 153, 0.15)",
        "color": "#f472b6",
        "border": "1px solid rgba(236, 72, 153, 0.3)",
    }
    description = "기가파일 토큰 및 다운로드 키(dlkey) 자동 파싱"
    priority = 90

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.gigafile import GigaFileResolver

        return GigaFileResolver()


class TransferitAddon(HostingAddon):
    addon_id = "transferit"
    name = "Transfer.it"
    category = "cloud"
    domains = ["transfer.it"]
    badge_class = "badge-transferit"
    badge_label = "Transfer.it"
    badge_style = {
        "background": "rgba(239, 68, 68, 0.15)",
        "color": "#f87171",
        "border": "1px solid rgba(239, 68, 68, 0.3)",
    }
    description = "MEGA 기반 AES-128-CTR 실시간 스트리밍 복호화 및 이어받기 지원"
    priority = 100

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.transferit import TransferitResolver

        return TransferitResolver()

    def can_restore_task(self, job: Any) -> bool:
        opts = getattr(job, "options", {}) or {}
        return bool(opts.get("node_handle") and opts.get("node_key"))

    def restore_task(
        self,
        job: Any,
        progress_callback: Optional[Callable] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from ..resolvers.transferit import TransferitDownloadTask

        opts = getattr(job, "options", {}) or {}
        chunk_kb = (settings or {}).get("chunk_size_kb", 512)
        return TransferitDownloadTask(
            url=getattr(job, "resolved_url", "") or getattr(job, "original_url", ""),
            dest_dir=getattr(job, "download_dir", ""),
            filename=getattr(job, "filename", ""),
            partial_id=getattr(job, "id", ""),
            password=opts.get("password"),
            node_handle=opts.get("node_handle"),
            node_key=opts.get("node_key"),
            node_size=opts.get("node_size"),
            pw_token=opts.get("pw_token"),
            xh=opts.get("xh"),
            chunk_size=chunk_kb * 1024,
            progress_callback=progress_callback,
        )


class MegaAddon(HostingAddon):
    addon_id = "mega"
    name = "MEGA"
    category = "cloud"
    domains = ["mega.nz", "mega.co.nz", "mega.io"]
    badge_class = "badge-mega"
    badge_label = "MEGA (mega.nz)"
    badge_style = {
        "background": "rgba(239, 68, 68, 0.15)",
        "color": "#ef4444",
        "border": "1px solid rgba(239, 68, 68, 0.3)",
    }
    description = (
        "MEGA 직링크 발급, 실시간 AES-128-CTR 스트리밍 복호화 및 이어받기 지원"
    )
    priority = 110

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.mega import MegaResolver

        return MegaResolver()

    def can_restore_task(self, job: Any) -> bool:
        opts = getattr(job, "options", {}) or {}
        return bool(opts.get("mega_file_id") and opts.get("mega_key_a32"))

    def restore_task(
        self,
        job: Any,
        progress_callback: Optional[Callable] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from ..resolvers.mega import MegaDownloadTask

        opts = getattr(job, "options", {}) or {}
        chunk_kb = (settings or {}).get("chunk_size_kb", 512)
        return MegaDownloadTask(
            url=getattr(job, "resolved_url", "") or getattr(job, "original_url", ""),
            dest_dir=getattr(job, "download_dir", ""),
            filename=getattr(job, "filename", ""),
            partial_id=getattr(job, "id", ""),
            file_id=opts.get("mega_file_id"),
            key_a32=opts.get("mega_key_a32"),
            file_size=opts.get("mega_file_size"),
            chunk_size=chunk_kb * 1024,
            progress_callback=progress_callback,
        )


class OneFichierAddon(HostingAddon):
    addon_id = "onefichier"
    name = "1fichier"
    category = "cloud"
    domains = ["1fichier.com"]
    badge_class = "badge-onefichier"
    badge_label = "1fichier"
    badge_style = {
        "background": "rgba(245, 158, 11, 0.15)",
        "color": "#fbbf24",
        "border": "1px solid rgba(245, 158, 11, 0.3)",
    }
    description = "무료 다운로드 대기 파라미터 자동 처리 및 직링크 추출"
    priority = 120

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.onefichier import OneFichierResolver

        return OneFichierResolver()


class KioAddon(HostingAddon):
    addon_id = "kio"
    name = "kio.ac"
    category = "cloud"
    domains = ["kio.ac"]
    badge_class = "badge-kio"
    badge_label = "kio.ac"
    badge_style = {
        "background": "rgba(20, 184, 166, 0.15)",
        "color": "#2dd4bf",
        "border": "1px solid rgba(20, 184, 166, 0.3)",
    }
    description = "CBOR 컬렉션/디렉터리 API 해석 및 비밀번호 해제"
    priority = 130

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.kio import KioResolver

        return KioResolver()


class PixeldrainAddon(HostingAddon):
    addon_id = "pixeldrain"
    name = "Pixeldrain"
    category = "cloud"
    domains = ["pixeldrain.com", "pixeldrain.net", "pixeldra.in"]
    badge_class = "badge-pixeldrain"
    badge_label = "Pixeldrain"
    badge_style = {
        "background": "rgba(139, 92, 246, 0.15)",
        "color": "#a78bfa",
        "border": "1px solid rgba(139, 92, 246, 0.3)",
    }
    description = "공식 파일 정보 API 및 고속 직다운로드"
    priority = 140

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.pixeldrain import PixeldrainResolver

        return PixeldrainResolver()


class GoogleDriveAddon(HostingAddon):
    addon_id = "gdrive"
    name = "Google Drive"
    category = "cloud"
    domains = ["drive.google.com", "docs.google.com"]
    badge_class = "badge-gdrive"
    badge_label = "Google Drive"
    badge_style = {
        "background": "rgba(34, 197, 94, 0.15)",
        "color": "#4ade80",
        "border": "1px solid rgba(34, 197, 94, 0.3)",
    }
    description = "대용량 파일 바이러스 검사 경고 토큰 자동 우회 및 직다운로드"
    priority = 150

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.gdrive import GoogleDriveResolver

        return GoogleDriveResolver()


class XShareAddon(HostingAddon):
    addon_id = "xshare"
    name = "X-Share"
    category = "cloud"
    domains = ["x-share.net"]
    badge_class = "badge-xshare"
    badge_label = "X-Share (x-share.net)"
    badge_style = {
        "background": "rgba(168, 85, 247, 0.15)",
        "color": "#c084fc",
        "border": "1px solid rgba(168, 85, 247, 0.3)",
    }
    description = "단일 파일 및 묶음(Bundle) API 토큰 발급 및 일괄 다운로드"
    priority = 160

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.xshare import XShareResolver

        return XShareResolver()


class RomsfunAddon(HostingAddon):
    addon_id = "romsfun"
    name = "Romsfun"
    category = "cloud"
    domains = ["romsfun.com"]
    badge_class = "badge-romsfun"
    badge_label = "Romsfun (romsfun.com)"
    badge_style = {
        "background": "rgba(236, 72, 153, 0.15)",
        "color": "#f472b6",
        "border": "1px solid rgba(236, 72, 153, 0.3)",
    }
    description = "레트로 게임 ROM 다운로드 페이지 자동 직링크 발급 및 멀티 파트 지원"
    priority = 170

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.romsfun import RomsfunResolver

        return RomsfunResolver()


class RomspureAddon(HostingAddon):
    addon_id = "romspure"
    name = "Romspure"
    category = "cloud"
    domains = ["romspure.cc"]
    badge_class = "badge-romspure"
    badge_label = "Romspure (romspure.cc)"
    badge_style = {
        "background": "rgba(244, 63, 94, 0.15)",
        "color": "#fb7185",
        "border": "1px solid rgba(244, 63, 94, 0.3)",
    }
    description = "Romspure 레트로 게임 ROM 자동 직링크 발급 및 Cloudflare 우회 지원"
    priority = 172

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.romspure import RomspureResolver

        return RomspureResolver()


class WowromsAddon(HostingAddon):
    addon_id = "wowroms"
    name = "WoWroms"
    category = "cloud"
    domains = ["wowroms.com"]
    badge_class = "badge-wowroms"
    badge_label = "WoWroms (wowroms.com)"
    badge_style = {
        "background": "rgba(245, 158, 11, 0.15)",
        "color": "#fbbf24",
        "border": "1px solid rgba(245, 158, 11, 0.3)",
    }
    description = "WoWroms 레트로 게임 ROM, ISO 및 에뮬레이터 다운로드 지원"
    priority = 175

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.wowroms import WowromsResolver

        return WowromsResolver()


class PlanetEmuAddon(HostingAddon):
    addon_id = "planetemu"
    name = "PlanetEmu"
    category = "cloud"
    domains = ["planetemu.net"]
    badge_class = "badge-planetemu"
    badge_label = "PlanetEmu (planetemu.net)"
    badge_style = {
        "background": "rgba(59, 130, 246, 0.15)",
        "color": "#60a5fa",
        "border": "1px solid rgba(59, 130, 246, 0.3)",
    }
    description = "PlanetEmu 레트로 게임 ROM 자동 쿼터 인증 및 고속 직링크 다운로드 지원"
    priority = 178

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.planetemu import PlanetEmuResolver

        return PlanetEmuResolver()


class VimmAddon(HostingAddon):
    addon_id = "vimm"
    name = "Vimm's Lair"
    category = "cloud"
    domains = ["vimm.net"]
    badge_class = "badge-vimm"
    badge_label = "Vimm's Lair (vimm.net)"
    badge_style = {
        "background": "rgba(168, 85, 247, 0.15)",
        "color": "#c084fc",
        "border": "1px solid rgba(168, 85, 247, 0.3)",
    }
    description = "Vimm's Lair 레트로 게임 ROM 자동 직링크 발급 및 다운로드 지원"
    priority = 179

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.vimm import VimmResolver

        return VimmResolver()


class RootzAddon(HostingAddon):
    addon_id = "rootz"
    name = "Rootz"
    category = "cloud"
    domains = ["rootz.so"]
    badge_class = "badge-rootz"
    badge_label = "Rootz (rootz.so)"
    badge_style = {
        "background": "rgba(16, 185, 129, 0.15)",
        "color": "#34d399",
        "border": "1px solid rgba(16, 185, 129, 0.3)",
    }
    description = "프록시 다운로드 API 및 고속 CDN 직링크 자동 추출, 이어받기 지원"
    priority = 180

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.rootz import RootzResolver

        return RootzResolver()


# -------------------------------------------------------------
# 3. 국내 포털 & 블로그
# -------------------------------------------------------------
class NaverMyBoxAddon(HostingAddon):
    addon_id = "naver_mybox"
    name = "Naver MYBOX"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["mybox.naver.com"]
    badge_class = "badge-mybox"
    badge_label = "Naver MYBOX (mybox.naver.com)"
    badge_style = {
        "background": "rgba(3, 199, 90, 0.15)",
        "color": "#00c73c",
        "border": "1px solid rgba(3, 199, 90, 0.3)",
    }
    description = "공유 링크 및 파일 속성 API를 통한 고속 직링크 발급 및 이어받기 지원"
    priority = 50

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.naver_mybox import NaverMyBoxResolver

        return NaverMyBoxResolver()


class NaverCafeAddon(HostingAddon):
    addon_id = "naver_cafe"
    name = "Naver Cafe"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["cafe.naver.com"]
    badge_class = "badge-naver"
    badge_label = "Naver Cafe (네이버 카페 첨부파일)"
    badge_style = {
        "background": "rgba(3, 199, 90, 0.15)",
        "color": "#03c75a",
        "border": "1px solid rgba(3, 199, 90, 0.3)",
    }
    description = "게시글 본문 내 모든 첨부파일 일괄 추출 및 게시글 제목별 자동 분류"
    priority = 60

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.naver_cafe import NaverCafeResolver

        return NaverCafeResolver()


class TistoryAddon(HostingAddon):
    addon_id = "tistory"
    name = "Tistory"
    category = "portal"
    category_name = "국내 포털 & 블로그"
    domains = ["tistory.com", "kakaocdn.net", "daumcdn.net"]
    badge_class = "badge-tistory"
    badge_label = "Tistory (티스토리 첨부파일)"
    badge_style = {
        "background": "rgba(255, 86, 56, 0.15)",
        "color": "#ff6a4d",
        "border": "1px solid rgba(255, 86, 56, 0.3)",
    }
    description = "티스토리 첨부파일 일괄 추출 및 게시글 제목별 하위 폴더 자동 분류"
    priority = 70

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.tistory import TistoryResolver

        return TistoryResolver()


# -------------------------------------------------------------
# 4. 웹페이지 & 범용 직링크
# -------------------------------------------------------------
class WebPageAddon(HostingAddon):
    addon_id = "webpage"
    name = "WebPage"
    category = "misc"
    category_name = "웹페이지 및 직접 링크"
    domains = []
    badge_class = "badge-webpage"
    badge_label = "웹페이지 링크 분석"
    badge_style = {
        "background": "rgba(100, 116, 139, 0.15)",
        "color": "#94a3b8",
        "border": "1px solid rgba(100, 116, 139, 0.3)",
    }
    description = "일반 웹페이지/게시글 내 포함된 다운로드 링크 자동 탐색 및 재큐잉"
    priority = 900

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.webpage import WebPageResolver

        return WebPageResolver()


class DirectAddon(HostingAddon):
    addon_id = "direct"
    name = "Direct"
    category = "direct"
    category_name = "웹페이지 및 직접 링크"
    domains = []
    badge_class = "badge-direct"
    badge_label = "Direct Link (직접 다운로드)"
    badge_style = {
        "background": "rgba(148, 163, 184, 0.15)",
        "color": "#cbd5e1",
        "border": "1px solid rgba(148, 163, 184, 0.3)",
    }
    description = "표준 HTTP/HTTPS 파일 직접 다운로드 (멀티스레드 청크 스트리밍)"
    priority = 999

    def create_resolver(self) -> BaseResolver:
        from ..resolvers.direct import DirectResolver

        return DirectResolver()


def register_default_addons(registry) -> None:
    """모든 기본 호스팅 애드온을 레지스트리에 등록합니다."""
    default_addons = [
        TorrentAddon(),
        GofileAddon(),
        VikingFileAddon(),
        MegaUpAddon(),
        NaverMyBoxAddon(),
        NaverCafeAddon(),
        TistoryAddon(),
        TeraBoxAddon(),
        GigaFileAddon(),
        TransferitAddon(),
        MegaAddon(),
        OneFichierAddon(),
        KioAddon(),
        PixeldrainAddon(),
        GoogleDriveAddon(),
        MediaFireAddon(),
        XShareAddon(),
        RomsfunAddon(),
        RomspureAddon(),
        WowromsAddon(),
        PlanetEmuAddon(),
        VimmAddon(),
        RootzAddon(),
        WebPageAddon(),
        DirectAddon(),
    ]
    for addon in default_addons:
        registry.register(addon)
