"""megaup hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.megaup import MegaUpResolver

        return MegaUpResolver()
