"""gigafile hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.gigafile import GigaFileResolver

        return GigaFileResolver()
