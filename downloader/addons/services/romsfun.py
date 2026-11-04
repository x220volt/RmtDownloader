"""romsfun hosting addon metadata and task integration."""

from ...resolvers.base import BaseResolver
from ..base import HostingAddon


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
        from ...resolvers.romsfun import RomsfunResolver

        return RomsfunResolver()
