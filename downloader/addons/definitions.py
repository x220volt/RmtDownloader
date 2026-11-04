"""Compatibility exports and explicit built-in addon registration.

Service implementations live in services/<addon_id>.py.
"""

from .services.torrent import TorrentAddon
from .services.gofile import GofileAddon
from .services.vikingfile import VikingFileAddon
from .services.mediafire import MediaFireAddon
from .services.filekeeper import FileKeeperAddon
from .services.datanodes import DataNodesAddon
from .services.megaup import MegaUpAddon
from .services.terabox import TeraBoxAddon
from .services.gigafile import GigaFileAddon
from .services.transferit import TransferitAddon
from .services.mega import MegaAddon
from .services.onefichier import OneFichierAddon
from .services.kio import KioAddon
from .services.pixeldrain import PixeldrainAddon
from .services.gdrive import GoogleDriveAddon
from .services.xshare import XShareAddon
from .services.romsfun import RomsfunAddon
from .services.romspure import RomspureAddon
from .services.wowroms import WowromsAddon
from .services.planetemu import PlanetEmuAddon
from .services.vimm import VimmAddon
from .services.rootz import RootzAddon
from .services.naver_mybox import NaverMyBoxAddon
from .services.naver_cafe import NaverCafeAddon
from .services.tistory import TistoryAddon
from .services.webpage import WebPageAddon
from .services.direct import DirectAddon

__all__ = [
    "DataNodesAddon",
    "DirectAddon",
    "FileKeeperAddon",
    "GigaFileAddon",
    "GofileAddon",
    "GoogleDriveAddon",
    "KioAddon",
    "MediaFireAddon",
    "MegaAddon",
    "MegaUpAddon",
    "NaverCafeAddon",
    "NaverMyBoxAddon",
    "OneFichierAddon",
    "PixeldrainAddon",
    "PlanetEmuAddon",
    "RomsfunAddon",
    "RomspureAddon",
    "RootzAddon",
    "TeraBoxAddon",
    "TistoryAddon",
    "TorrentAddon",
    "TransferitAddon",
    "VikingFileAddon",
    "VimmAddon",
    "WebPageAddon",
    "WowromsAddon",
    "XShareAddon",
    "register_default_addons",
]


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
        FileKeeperAddon(),
        DataNodesAddon(),
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
