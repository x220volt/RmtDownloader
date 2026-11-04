"""rootz hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.rootz import RootzResolver

        return RootzResolver()
