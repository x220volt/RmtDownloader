"""vikingfile hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.vikingfile import VikingFileResolver

        return VikingFileResolver()
