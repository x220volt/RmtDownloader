"""filekeeper hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


class FileKeeperAddon(HostingAddon):
    addon_id = "filekeeper"
    name = "FileKeeper"
    category = "cloud"
    domains = ["filekeeper.net"]
    badge_class = "badge-filekeeper"
    badge_label = "FileKeeper (filekeeper.net)"
    badge_style = {
        "background": "rgba(16, 185, 129, 0.15)",
        "color": "#34d399",
        "border": "1px solid rgba(16, 185, 129, 0.3)",
    }
    description = "FileKeeper 파일 링크 자동 해석 및 고속 다운로드 지원"
    priority = 36

    def create_resolver(self) -> BaseResolver:
        from ...resolvers.filekeeper import FileKeeperResolver

        return FileKeeperResolver()
