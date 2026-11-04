"""pixeldrain hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.pixeldrain import PixeldrainResolver

        return PixeldrainResolver()
