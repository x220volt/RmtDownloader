"""gdrive hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.gdrive import GoogleDriveResolver

        return GoogleDriveResolver()
